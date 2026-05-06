"""
Fixed-window rate limiting — §9 G-C03.

Key behaviours:
  - Per-token for authenticated requests (Bearer present), per-IP otherwise.
  - Some endpoints have different limits depending on auth status (e.g. GET /v1/models).
  - GET /health*, GET /metrics are explicitly exempt.
  - X-RateLimit-Reset is an absolute Unix timestamp, not a countdown.
  - Retry-After header is added on 429 responses.
"""

import hashlib
import re
import time
from dataclasses import dataclass, field

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


@dataclass
class _RuleSpec:
    window: int = 60
    # Per-IP limit for unauthenticated requests (None = not rate-limited unauthenticated)
    unauthed_limit: int | None = None
    # Per-token limit for authenticated requests (None = not rate-limited authenticated)
    authed_limit: int | None = None
    # Exempt from rate limiting entirely
    unlimited: bool = False


# Matched in-order against f"{METHOD} {path}".
_RULES: list[tuple[re.Pattern[str], _RuleSpec]] = [
    # Exempt
    (re.compile(r"^GET /(health|healthz|metrics)($|/)"), _RuleSpec(unlimited=True)),

    # Auth token endpoints — spec says "per token" but POST is open, so also cap per-IP
    (re.compile(r"^(GET|POST) /v1/auth/tokens$"), _RuleSpec(unauthed_limit=10, authed_limit=10, window=60)),
    (re.compile(r"^DELETE /v1/auth/tokens/"), _RuleSpec(authed_limit=10, window=60)),

    # Model write / admin (require auth — no meaningful unauthed limit)
    (re.compile(r"^POST /v1/models$"), _RuleSpec(authed_limit=20, window=60)),
    (re.compile(r"^PUT /v1/models/"), _RuleSpec(authed_limit=20, window=60)),
    (re.compile(r"^DELETE /v1/models/"), _RuleSpec(authed_limit=20, window=60)),

    # Model read — dual limit
    (re.compile(r"^GET /v1/models(/|$)"), _RuleSpec(unauthed_limit=60, authed_limit=300, window=60)),

    # Routing
    (re.compile(r"^GET /v1/route(/|$)"), _RuleSpec(authed_limit=120, window=60)),

    # Calibration
    (re.compile(r"^POST /v1/calibration/signal$"), _RuleSpec(authed_limit=1000, window=60)),
]

_DEFAULT = _RuleSpec(unauthed_limit=60, authed_limit=300, window=60)


@dataclass
class _WindowState:
    count: int = 0
    window_start: float = field(default_factory=time.monotonic)


# {(endpoint_key, client_identifier): _WindowState}
_windows: dict[tuple[str, str], _WindowState] = {}


def _resolve_rule(method: str, path: str) -> _RuleSpec:
    key = f"{method} {path}"
    for pattern, spec in _RULES:
        if pattern.match(key):
            return spec
    return _DEFAULT


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _identifier(request: Request) -> tuple[bool, str]:
    """Returns (is_authenticated, rate_limit_key).

    Authenticated → key is 'tok:<sha256-of-token>' (stable, never reveals the raw token).
    Unauthenticated → key is 'ip:<client-ip>'.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        if token:
            tok_hash = hashlib.sha256(token.encode()).hexdigest()
            return True, f"tok:{tok_hash}"
    return False, f"ip:{_client_ip(request)}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        method = request.method
        spec = _resolve_rule(method, path)

        if spec.unlimited:
            return await call_next(request)

        is_authed, ident = _identifier(request)
        limit: int | None = spec.authed_limit if is_authed and spec.authed_limit is not None else spec.unauthed_limit

        if limit is None:
            # No applicable limit for this auth state; let the router handle it (will 401)
            return await call_next(request)

        window = spec.window
        bucket_key = (f"{method} {path}", ident)
        now_mono = time.monotonic()

        state = _windows.get(bucket_key)
        if state is None or (now_mono - state.window_start) >= window:
            state = _WindowState(count=1, window_start=now_mono)
            _windows[bucket_key] = state
        else:
            state.count += 1

        elapsed = now_mono - state.window_start
        remaining_secs = max(0.0, window - elapsed)
        retry_after = max(1, int(remaining_secs) + 1)
        reset_at = int(time.time() + remaining_secs) + 1  # absolute Unix timestamp

        remaining_requests = max(0, limit - state.count)

        rl_headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(remaining_requests),
            "X-RateLimit-Reset": str(reset_at),
        }

        if state.count > limit:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "RATE_LIMIT_EXCEEDED",
                    "message": f"Rate limit of {limit} requests/minute exceeded for {method} {path}",
                    "limit": limit,
                    "window_seconds": window,
                    "retry_after_seconds": retry_after,
                },
                headers={**rl_headers, "Retry-After": str(retry_after)},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining_requests)
        response.headers["X-RateLimit-Reset"] = str(reset_at)
        return response
