"""
Tests for §4.2 model registry endpoints.

GET    /v1/models
GET    /v1/models/:model_id
POST   /v1/models
PUT    /v1/models/:model_id
DELETE /v1/models/:model_id
GET    /v1/models/:model_id/adapters/:lang
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


def _make_pypi_mock(status_code: int = 200, sha256: str = "a" * 64) -> MagicMock:
    """Return a mock httpx.AsyncClient class simulating a PyPI JSON API response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {
        "urls": [{"packagetype": "sdist", "digests": {"sha256": sha256}}]
    }
    mock_instance = AsyncMock()
    mock_instance.get = AsyncMock(return_value=mock_resp)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=mock_instance)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MANIFEST = {
    "manifest_version": "1.0",
    "model_id": "test-model-v1",
    "display_name": "Test Model",
    "model_version": "1.0.0",
    "description": "A test model",
    "task_types": ["classify", "extract"],
    "domains": ["legal", "finance"],
    "input_modalities": ["text"],
    "output_modalities": ["text"],
    "perf_profile": {
        "p50_latency_ms": 50,
        "p95_latency_ms": 120,
        "max_throughput_rps": 200,
        "cost_per_1k_tokens": 0.002,
    },
    "compliance_tags": ["gdpr-eu", "soc2"],
    "data_residency": ["eu-west"],
    "adapters": {
        "python": {"package": "synapse-test-adapter", "version": "1.0.0"},
    },
    "heartbeat_endpoint": "http://localhost:9999/heartbeat",
    "contact_email": "owner@example.com",
    "license": "MIT",
}


async def _issue_token(client: AsyncClient, scopes: list[str] = ["registry:write"]) -> str:
    r = await client.post("/v1/auth/tokens", json={"scopes": scopes})
    assert r.status_code == 201
    return r.json()["token"]


async def _register(client: AsyncClient, token: str, manifest: dict | None = None) -> dict:
    body = manifest or _MANIFEST
    r = await client.post(
        "/v1/models",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# GET /v1/models
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_models_empty(client: AsyncClient):
    r = await client.get("/v1/models")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_models_returns_registered(client: AsyncClient):
    token = await _issue_token(client)
    await _register(client, token)
    r = await client.get("/v1/models")
    assert r.status_code == 200
    ids = [m["model_id"] for m in r.json()]
    assert "test-model-v1" in ids


@pytest.mark.asyncio
async def test_list_models_filter_domain(client: AsyncClient):
    token = await _issue_token(client)
    await _register(client, token)

    r = await client.get("/v1/models", params={"domain": "legal"})
    assert r.status_code == 200
    assert all("legal" in m["domains"] for m in r.json())

    r2 = await client.get("/v1/models", params={"domain": "healthcare"})
    assert r2.status_code == 200
    assert r2.json() == []


@pytest.mark.asyncio
async def test_list_models_filter_task_type(client: AsyncClient):
    token = await _issue_token(client)
    await _register(client, token)

    r = await client.get("/v1/models", params={"task_type": "classify"})
    assert r.status_code == 200
    assert all("classify" in m["task_types"] for m in r.json())

    r2 = await client.get("/v1/models", params={"task_type": "generate"})
    assert r2.status_code == 200
    assert r2.json() == []


@pytest.mark.asyncio
async def test_list_models_filter_compliance_tag(client: AsyncClient):
    token = await _issue_token(client)
    await _register(client, token)

    r = await client.get("/v1/models", params={"compliance_tag": "gdpr-eu"})
    assert r.status_code == 200
    assert len(r.json()) == 1

    r2 = await client.get("/v1/models", params={"compliance_tag": "hipaa"})
    assert r2.status_code == 200
    assert r2.json() == []


@pytest.mark.asyncio
async def test_list_models_excludes_deprecated(client: AsyncClient):
    token = await _issue_token(client, scopes=["registry:write", "registry:admin"])
    await _register(client, token)
    await client.delete(
        "/v1/models/test-model-v1",
        headers={"Authorization": f"Bearer {token}"},
    )
    r = await client.get("/v1/models")
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# GET /v1/models/:model_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_model_found(client: AsyncClient):
    token = await _issue_token(client)
    await _register(client, token)

    r = await client.get("/v1/models/test-model-v1")
    assert r.status_code == 200
    data = r.json()
    assert data["model_id"] == "test-model-v1"
    assert data["display_name"] == "Test Model"


@pytest.mark.asyncio
async def test_get_model_not_found(client: AsyncClient):
    r = await client.get("/v1/models/does-not-exist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /v1/models
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_model_success(client: AsyncClient):
    token = await _issue_token(client)
    r = await client.post(
        "/v1/models",
        json=_MANIFEST,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["model_id"] == "test-model-v1"
    assert data["is_deprecated"] is False


@pytest.mark.asyncio
async def test_register_model_requires_auth(client: AsyncClient):
    r = await client.post("/v1/models", json=_MANIFEST)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_register_model_requires_write_scope(client: AsyncClient):
    token = await _issue_token(client, scopes=["registry:read"])
    r = await client.post(
        "/v1/models",
        json=_MANIFEST,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_register_model_conflict(client: AsyncClient):
    token = await _issue_token(client)
    await _register(client, token)

    r = await client.post(
        "/v1/models",
        json=_MANIFEST,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# PUT /v1/models/:model_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_model_success(client: AsyncClient):
    token = await _issue_token(client)
    await _register(client, token)

    r = await client.put(
        "/v1/models/test-model-v1",
        json={"display_name": "Updated Name"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["display_name"] == "Updated Name"


@pytest.mark.asyncio
async def test_update_model_forbidden_for_non_owner(client: AsyncClient):
    owner_token = await _issue_token(client)
    await _register(client, owner_token)

    other_token = await _issue_token(client)
    r = await client.put(
        "/v1/models/test-model-v1",
        json={"display_name": "Hacked"},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_update_model_allowed_for_admin(client: AsyncClient):
    owner_token = await _issue_token(client)
    await _register(client, owner_token)

    admin_token = await _issue_token(client, scopes=["registry:admin", "registry:write"])
    r = await client.put(
        "/v1/models/test-model-v1",
        json={"display_name": "Admin Override"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    assert r.json()["display_name"] == "Admin Override"


@pytest.mark.asyncio
async def test_update_model_not_found(client: AsyncClient):
    token = await _issue_token(client)
    r = await client.put(
        "/v1/models/ghost-model",
        json={"display_name": "Ghost"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /v1/models/:model_id  (soft-delete only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deprecate_model_sets_flag(client: AsyncClient):
    token = await _issue_token(client, scopes=["registry:admin", "registry:write"])
    await _register(client, token)

    r = await client.delete(
        "/v1/models/test-model-v1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 204

    # Record must still exist (audit trail)
    detail = await client.get("/v1/models/test-model-v1")
    assert detail.status_code == 200
    assert detail.json()["is_deprecated"] is True


@pytest.mark.asyncio
async def test_deprecate_requires_admin_scope(client: AsyncClient):
    write_token = await _issue_token(client, scopes=["registry:write"])
    await _register(client, write_token)

    r = await client.delete(
        "/v1/models/test-model-v1",
        headers={"Authorization": f"Bearer {write_token}"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /v1/models/:model_id/adapters/:lang
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_adapter_python(client: AsyncClient):
    token = await _issue_token(client)
    await _register(client, token)

    r = await client.get("/v1/models/test-model-v1/adapters/python")
    assert r.status_code == 200
    data = r.json()
    assert data["package"] == "synapse-test-adapter"


@pytest.mark.asyncio
async def test_get_adapter_missing_lang(client: AsyncClient):
    token = await _issue_token(client)
    await _register(client, token)

    r = await client.get("/v1/models/test-model-v1/adapters/ruby")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PyPI supply-chain verification — §9 G-S08
# ---------------------------------------------------------------------------

_PYPI_SHA256 = "b" * 64


@pytest.mark.asyncio
async def test_register_stores_pypi_hash_in_adapters(client: AsyncClient):
    """Successful registration: pypi_hash appears at adapters.python.pypi_hash."""
    token = await _issue_token(client)

    with patch(
        "registry.routers.models.httpx.AsyncClient",
        _make_pypi_mock(sha256=_PYPI_SHA256),
    ):
        r = await client.post(
            "/v1/models",
            json=_MANIFEST,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r.status_code == 201
    data = r.json()
    assert data["adapters"]["python"]["pypi_hash"] == _PYPI_SHA256


@pytest.mark.asyncio
async def test_register_pypi_hash_persisted_on_adapter_endpoint(client: AsyncClient):
    """Hash is readable via the per-language adapter endpoint after registration."""
    token = await _issue_token(client)

    with patch(
        "registry.routers.models.httpx.AsyncClient",
        _make_pypi_mock(sha256=_PYPI_SHA256),
    ):
        await _register(client, token)

    r = await client.get("/v1/models/test-model-v1/adapters/python")
    assert r.status_code == 200
    assert r.json()["pypi_hash"] == _PYPI_SHA256


@pytest.mark.asyncio
async def test_register_pypi_missing_returns_reg_adapter_missing(client: AsyncClient):
    """PyPI 404 → HTTP 422 with code REG_ADAPTER_MISSING."""
    token = await _issue_token(client)

    with patch(
        "registry.routers.models.httpx.AsyncClient",
        _make_pypi_mock(status_code=404),
    ):
        r = await client.post(
            "/v1/models",
            json=_MANIFEST,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "REG_ADAPTER_MISSING"
    assert "synapse-test-adapter" in detail["message"]
    assert "1.0.0" in detail["message"]


@pytest.mark.asyncio
async def test_register_pypi_missing_does_not_persist(client: AsyncClient):
    """A failed PyPI check must not create a manifest record."""
    token = await _issue_token(client)

    with patch(
        "registry.routers.models.httpx.AsyncClient",
        _make_pypi_mock(status_code=404),
    ):
        await client.post(
            "/v1/models",
            json=_MANIFEST,
            headers={"Authorization": f"Bearer {token}"},
        )

    r = await client.get("/v1/models/test-model-v1")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_register_no_python_adapter_skips_pypi(client: AsyncClient):
    """Manifests without a python adapter must not call PyPI."""
    token = await _issue_token(client)
    manifest_ts_only = {
        **_MANIFEST,
        "model_id": "ts-only-model",
        "adapters": {
            "typescript": {"package": "@synapse/ts-adapter", "version": "1.0.0"},
        },
    }

    called = []

    async def _should_not_be_called(*_a, **_kw):
        called.append(True)

    with patch("registry.routers.models._verify_pypi_package", _should_not_be_called):
        r = await client.post(
            "/v1/models",
            json=manifest_ts_only,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r.status_code == 201
    assert called == [], "PyPI verification must not run when no python adapter is present"
