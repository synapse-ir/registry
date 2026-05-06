"""
Tests for §4.2 model registry endpoints.

GET    /v1/models
GET    /v1/models/:model_id
POST   /v1/models
PUT    /v1/models/:model_id
DELETE /v1/models/:model_id
GET    /v1/models/:model_id/adapters/:lang
"""

import pytest
from httpx import AsyncClient

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
