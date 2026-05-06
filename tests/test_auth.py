"""
Tests for §9 G-C01 — token issuance, listing, revocation, and validation.
"""

import re

import pytest
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def issue_token(client: AsyncClient, **kwargs) -> dict:
    resp = await client.post("/v1/auth/tokens", json={"scopes": ["full"], "description": "test", **kwargs})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Issuance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_token_returns_201(client: AsyncClient):
    resp = await client.post("/v1/auth/tokens", json={"scopes": ["full"], "description": "bootstrap"})
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_token_format(client: AsyncClient):
    data = await issue_token(client)
    # syn_{env}_{32 hex chars}
    assert re.match(r"^syn_test_[0-9a-f]{32}$", data["token"]), data["token"]


@pytest.mark.asyncio
async def test_token_id_is_uuid(client: AsyncClient):
    data = await issue_token(client)
    assert re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        data["token_id"],
    )


@pytest.mark.asyncio
async def test_test_env_token_has_expiry(client: AsyncClient):
    """SYNAPSE_ENV=test → auto 24-hour expiry."""
    data = await issue_token(client)
    assert data["expires_at"] is not None


@pytest.mark.asyncio
async def test_explicit_expiry(client: AsyncClient):
    data = await issue_token(client, expires_in_days=7)
    assert data["expires_at"] is not None


@pytest.mark.asyncio
async def test_unknown_scope_rejected(client: AsyncClient):
    resp = await client.post("/v1/auth/tokens", json={"scopes": ["invalid:scope"]})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_valid_scopes_accepted(client: AsyncClient):
    for scope in ["registry:read", "registry:write", "registry:admin", "calibration:write", "full"]:
        resp = await client.post("/v1/auth/tokens", json={"scopes": [scope]})
        assert resp.status_code == 201, f"scope {scope!r} rejected: {resp.text}"


# ---------------------------------------------------------------------------
# Token value never exposed after creation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_tokens_never_returns_token_value(client: AsyncClient):
    data = await issue_token(client)
    token = data["token"]

    resp = await client.get("/v1/auth/tokens", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.text
    assert token not in body, "raw token value must not appear in list response"


@pytest.mark.asyncio
async def test_list_tokens_returns_metadata_fields(client: AsyncClient):
    data = await issue_token(client)
    token = data["token"]

    resp = await client.get("/v1/auth/tokens", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    first = items[0]
    for field in ("token_id", "scopes", "description", "created_at", "expires_at", "last_used_at", "is_revoked"):
        assert field in first, f"missing field: {field}"
    assert "token" not in first
    assert "token_hash" not in first


# ---------------------------------------------------------------------------
# Authentication enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_tokens_requires_auth(client: AsyncClient):
    resp = await client.get("/v1/auth/tokens")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_tokens_rejects_bad_token(client: AsyncClient):
    resp = await client.get("/v1/auth/tokens", headers={"Authorization": "Bearer syn_test_bad"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revoke_returns_204(client: AsyncClient):
    data = await issue_token(client)
    token = data["token"]
    token_id = data["token_id"]

    resp = await client.delete(
        f"/v1/auth/tokens/{token_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_revoke_is_idempotent(client: AsyncClient):
    data = await issue_token(client)
    token, token_id = data["token"], data["token_id"]
    hdrs = {"Authorization": f"Bearer {token}"}

    await client.delete(f"/v1/auth/tokens/{token_id}", headers=hdrs)
    resp = await client.delete(f"/v1/auth/tokens/{token_id}", headers=hdrs)
    # Second revoke: token is already revoked so auth fails with 401, not 500
    assert resp.status_code in (204, 401)


@pytest.mark.asyncio
async def test_revoked_token_rejected_after_cache_cleared(client: AsyncClient):
    """After revocation + cache eviction, token must be rejected."""
    from registry.middleware.auth import _cache, evict_token

    data = await issue_token(client)
    token, token_id = data["token"], data["token_id"]
    hdrs = {"Authorization": f"Bearer {token}"}

    # Verify token works before revocation
    resp = await client.get("/v1/auth/tokens", headers=hdrs)
    assert resp.status_code == 200

    # Revoke — this also evicts from cache
    resp = await client.delete(f"/v1/auth/tokens/{token_id}", headers=hdrs)
    assert resp.status_code == 204

    # After eviction, next request must fail
    resp = await client.get("/v1/auth/tokens", headers=hdrs)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_revoke_nonexistent_returns_404(client: AsyncClient):
    data = await issue_token(client)
    resp = await client.delete(
        "/v1/auth/tokens/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {data['token']}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cannot_revoke_other_token_without_admin(client: AsyncClient):
    owner = await issue_token(client, scopes=["registry:write"])
    victim = await issue_token(client, scopes=["registry:read"])

    resp = await client.delete(
        f"/v1/auth/tokens/{victim['token_id']}",
        headers={"Authorization": f"Bearer {owner['token']}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_revoke_any_token(client: AsyncClient):
    admin = await issue_token(client, scopes=["registry:admin"])
    victim = await issue_token(client, scopes=["registry:read"])

    resp = await client.delete(
        f"/v1/auth/tokens/{victim['token_id']}",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Scope enforcement on protected endpoints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_scope_can_register_model(client: AsyncClient):
    data = await issue_token(client, scopes=["registry:write"])
    manifest = _sample_manifest()
    resp = await client.post(
        "/v1/models",
        json=manifest,
        headers={"Authorization": f"Bearer {data['token']}"},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_read_only_scope_cannot_register_model(client: AsyncClient):
    data = await issue_token(client, scopes=["registry:read"])
    resp = await client.post(
        "/v1/models",
        json=_sample_manifest(),
        headers={"Authorization": f"Bearer {data['token']}"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Rotation — two tokens simultaneously valid
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rotation_both_tokens_valid_before_delete(client: AsyncClient):
    """New token issued; old token must still work until explicitly deleted."""
    old = await issue_token(client)
    new = await issue_token(client)

    # Both should be able to list tokens
    r1 = await client.get("/v1/auth/tokens", headers={"Authorization": f"Bearer {old['token']}"})
    r2 = await client.get("/v1/auth/tokens", headers={"Authorization": f"Bearer {new['token']}"})
    assert r1.status_code == 200
    assert r2.status_code == 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample_manifest() -> dict:
    return {
        "manifest_version": "1.0.0",
        "model_id": "test-org/model-v1.0",
        "display_name": "Test Model",
        "model_version": "1.0.0",
        "description": "A test model.",
        "task_types": ["text-generation"],
        "domains": ["general"],
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "perf_profile": {"p50_latency_ms": 100, "p95_latency_ms": 300},
        "compliance_tags": ["SOC2"],
        "data_residency": ["US"],
        "adapters": {"python": {"package": "test-adapter", "version": "0.1.0", "class_": "TestAdapter"}},
        "heartbeat_endpoint": "http://localhost:9999/heartbeat",
        "contact_email": "test@example.com",
        "license": "Apache-2.0",
    }
