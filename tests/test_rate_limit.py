"""
Tests for §9 G-C03 — fixed-window rate limiting.

Window state is injected directly into _windows to avoid running hundreds of
real HTTP requests.  The middleware is tested end-to-end via the real ASGI app.
"""

import time

import pytest
from httpx import AsyncClient

from registry.middleware import rate_limit as rl_module

# httpx 0.28 with ASGITransport presents as 127.0.0.1
_TEST_IP = "127.0.0.1"


def _clear_windows():
    rl_module._windows.clear()


def _ip_key(path: str, method: str = "GET") -> tuple[str, str]:
    return (f"{method} {path}", f"ip:{_TEST_IP}")


def _tok_key(path: str, token: str, method: str = "GET") -> tuple[str, str]:
    import hashlib
    tok_hash = hashlib.sha256(token.encode()).hexdigest()
    return (f"{method} {path}", f"tok:{tok_hash}")


# ---------------------------------------------------------------------------
# Headers always present on rate-limited endpoints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_headers_on_200(client: AsyncClient):
    _clear_windows()
    resp = await client.get("/v1/models")
    assert resp.status_code == 200
    assert "X-RateLimit-Limit" in resp.headers
    assert "X-RateLimit-Remaining" in resp.headers
    assert "X-RateLimit-Reset" in resp.headers


@pytest.mark.asyncio
async def test_rate_limit_headers_on_auth_endpoint(client: AsyncClient):
    _clear_windows()
    resp = await client.post("/v1/auth/tokens", json={"scopes": ["full"]})
    assert resp.status_code == 201
    assert "X-RateLimit-Limit" in resp.headers
    assert "X-RateLimit-Remaining" in resp.headers
    assert "X-RateLimit-Reset" in resp.headers


@pytest.mark.asyncio
async def test_reset_is_absolute_unix_timestamp(client: AsyncClient):
    _clear_windows()
    before = int(time.time())
    resp = await client.get("/v1/models")
    reset = int(resp.headers["X-RateLimit-Reset"])
    # Must be a future absolute timestamp, not a small countdown
    assert reset >= before, "X-RateLimit-Reset should be an absolute Unix timestamp"
    assert reset <= before + 65, "reset should be at most ~window seconds in the future"


@pytest.mark.asyncio
async def test_remaining_decrements(client: AsyncClient):
    _clear_windows()
    r1 = await client.get("/v1/models")
    r2 = await client.get("/v1/models")
    rem1 = int(r1.headers["X-RateLimit-Remaining"])
    rem2 = int(r2.headers["X-RateLimit-Remaining"])
    assert rem2 == rem1 - 1


@pytest.mark.asyncio
async def test_remaining_never_negative(client: AsyncClient):
    _clear_windows()
    for _ in range(5):
        resp = await client.get("/v1/models")
    assert int(resp.headers["X-RateLimit-Remaining"]) >= 0


# ---------------------------------------------------------------------------
# Exempt endpoints — no rate-limit headers, pass-through always
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_endpoint_exempt(client: AsyncClient):
    _clear_windows()
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert "X-RateLimit-Limit" not in resp.headers


@pytest.mark.asyncio
async def test_health_exempt_even_when_window_full(client: AsyncClient):
    _clear_windows()
    # Fill a fake window for /healthz — middleware should ignore it
    key = _ip_key("/healthz")
    rl_module._windows[key] = rl_module._WindowState(count=99999, window_start=time.monotonic())
    resp = await client.get("/healthz")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 429 behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_429_when_limit_exceeded(client: AsyncClient):
    _clear_windows()
    # Unauthed GET /v1/models limit is 60/min
    key = _ip_key("/v1/models")
    rl_module._windows[key] = rl_module._WindowState(count=60, window_start=time.monotonic())
    resp = await client.get("/v1/models")
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_429_response_format(client: AsyncClient):
    _clear_windows()
    key = _ip_key("/v1/models")
    rl_module._windows[key] = rl_module._WindowState(count=60, window_start=time.monotonic())
    resp = await client.get("/v1/models")
    assert resp.status_code == 429

    body = resp.json()
    # Flat format per G-C03 spec
    assert body["error"] == "RATE_LIMIT_EXCEEDED"
    assert "message" in body
    assert body["limit"] == 60
    assert body["window_seconds"] == 60
    assert "retry_after_seconds" in body
    assert isinstance(body["retry_after_seconds"], int)
    assert body["retry_after_seconds"] >= 1


@pytest.mark.asyncio
async def test_429_has_all_rate_limit_headers(client: AsyncClient):
    _clear_windows()
    key = _ip_key("/v1/models")
    rl_module._windows[key] = rl_module._WindowState(count=60, window_start=time.monotonic())
    resp = await client.get("/v1/models")
    assert resp.status_code == 429
    assert resp.headers["X-RateLimit-Remaining"] == "0"
    assert resp.headers["X-RateLimit-Limit"] == "60"
    assert "X-RateLimit-Reset" in resp.headers
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_retry_after_header_matches_body(client: AsyncClient):
    _clear_windows()
    key = _ip_key("/v1/models")
    rl_module._windows[key] = rl_module._WindowState(count=60, window_start=time.monotonic())
    resp = await client.get("/v1/models")
    assert resp.status_code == 429
    body = resp.json()
    assert resp.headers["Retry-After"] == str(body["retry_after_seconds"])


@pytest.mark.asyncio
async def test_message_contains_method_and_path(client: AsyncClient):
    _clear_windows()
    key = _ip_key("/v1/models")
    rl_module._windows[key] = rl_module._WindowState(count=60, window_start=time.monotonic())
    resp = await client.get("/v1/models")
    msg = resp.json()["message"]
    assert "GET" in msg
    assert "/v1/models" in msg
    assert "60" in msg


# ---------------------------------------------------------------------------
# Window reset
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_window_resets_after_expiry(client: AsyncClient):
    _clear_windows()
    expired_start = time.monotonic() - 120
    key = _ip_key("/v1/models")
    rl_module._windows[key] = rl_module._WindowState(count=60, window_start=expired_start)
    resp = await client.get("/v1/models")
    assert resp.status_code == 200
    assert int(resp.headers["X-RateLimit-Remaining"]) == 59  # 60 - 1


# ---------------------------------------------------------------------------
# Limits are independent per endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_limits_independent_per_endpoint(client: AsyncClient):
    _clear_windows()
    # Exhaust GET /v1/models — POST /v1/models should be unaffected
    key = _ip_key("/v1/models")
    rl_module._windows[key] = rl_module._WindowState(count=60, window_start=time.monotonic())
    resp = await client.get("/v1/models")
    assert resp.status_code == 429

    # POST /v1/models requires auth; no rate-limit applied to unauthed → gets 401 not 429
    resp2 = await client.post("/v1/models", json={})
    assert resp2.status_code == 401


# ---------------------------------------------------------------------------
# Dual limit: authenticated requests get a higher limit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_authed_models_read_higher_limit(client: AsyncClient):
    """Authenticated GET /v1/models should advertise limit=300, not 60."""
    _clear_windows()
    token_data = await _make_token(client)
    resp = await client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {token_data['token']}"},
    )
    assert resp.status_code == 200
    assert resp.headers["X-RateLimit-Limit"] == "300"


@pytest.mark.asyncio
async def test_unauthed_models_read_lower_limit(client: AsyncClient):
    """Unauthenticated GET /v1/models should advertise limit=60."""
    _clear_windows()
    resp = await client.get("/v1/models")
    assert resp.status_code == 200
    assert resp.headers["X-RateLimit-Limit"] == "60"


@pytest.mark.asyncio
async def test_authed_and_unauthed_windows_are_independent(client: AsyncClient):
    """Exhausting the unauthed window must not affect the authed window."""
    _clear_windows()
    token_data = await _make_token(client)
    tok = token_data["token"]

    # Exhaust unauthed window
    key = _ip_key("/v1/models")
    rl_module._windows[key] = rl_module._WindowState(count=60, window_start=time.monotonic())

    # Unauthed → 429
    resp_unauthed = await client.get("/v1/models")
    assert resp_unauthed.status_code == 429

    # Authed with same IP → different bucket, should still work
    resp_authed = await client.get("/v1/models", headers={"Authorization": f"Bearer {tok}"})
    assert resp_authed.status_code == 200
    assert resp_authed.headers["X-RateLimit-Limit"] == "300"


# ---------------------------------------------------------------------------
# Rule resolution unit tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,path,is_authed,expected_limit", [
    # Unauthenticated model reads
    ("GET", "/v1/models", False, 60),
    ("GET", "/v1/models/org/model-v1.0", False, 60),
    # Authenticated model reads
    ("GET", "/v1/models", True, 300),
    ("GET", "/v1/models/org/model-v1.0", True, 300),
    # Write endpoints (auth required)
    ("POST", "/v1/models", True, 20),
    ("PUT", "/v1/models/some-model", True, 20),
    ("DELETE", "/v1/models/some-model", True, 20),
    # Routing
    ("GET", "/v1/route", True, 120),
    # Calibration
    ("POST", "/v1/calibration/signal", True, 1000),
    # Auth token management
    ("POST", "/v1/auth/tokens", True, 10),
    ("GET", "/v1/auth/tokens", True, 10),
    ("DELETE", "/v1/auth/tokens/some-id", True, 10),
    # Auth token bootstrap (unauthed POST)
    ("POST", "/v1/auth/tokens", False, 10),
])
def test_resolve_rule(method: str, path: str, is_authed: bool, expected_limit: int):
    spec = rl_module._resolve_rule(method, path)
    limit = spec.authed_limit if is_authed and spec.authed_limit is not None else spec.unauthed_limit
    assert limit == expected_limit, f"{method} {path} (authed={is_authed}): expected {expected_limit}, got {limit}"


@pytest.mark.parametrize("path", ["/health", "/healthz", "/metrics"])
def test_exempt_endpoints(path: str):
    spec = rl_module._resolve_rule("GET", path)
    assert spec.unlimited, f"GET {path} should be exempt"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_token(client: AsyncClient) -> dict:
    resp = await client.post("/v1/auth/tokens", json={"scopes": ["full"]})
    assert resp.status_code == 201
    return resp.json()
