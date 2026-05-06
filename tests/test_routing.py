"""
Tests for §4.3 GET /v1/route + §9 G-C04 zero-candidate behaviour.
"""

import json as _json

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_PERF_FAST = {
    "p50_latency_ms": 40,
    "p95_latency_ms": 80,
    "max_throughput_rps": 300,
    "cost_per_1k_tokens": 0.001,
}

_PERF_SLOW = {
    "p50_latency_ms": 100,
    "p95_latency_ms": 250,
    "max_throughput_rps": 50,
    "cost_per_1k_tokens": 0.005,
}


def _manifest(model_id: str, perf: dict, domains: list[str], compliance_tags: list[str]) -> dict:
    return {
        "manifest_version": "1.0",
        "model_id": model_id,
        "display_name": model_id,
        "model_version": "1.0.0",
        "description": "test",
        "task_types": ["classify", "extract"],
        "domains": domains,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "perf_profile": perf,
        "compliance_tags": compliance_tags,
        "data_residency": ["us-east"],
        "adapters": {"python": {"package": "pkg", "version": "1.0"}},
        "heartbeat_endpoint": "http://localhost:9999/hb",
        "contact_email": "dev@example.com",
        "license": "MIT",
    }


async def _token(client: AsyncClient, scopes: list[str] = ["registry:write", "full"]) -> str:
    r = await client.post("/v1/auth/tokens", json={"scopes": scopes})
    assert r.status_code == 201
    return r.json()["token"]


async def _register(client: AsyncClient, token: str, m: dict) -> None:
    r = await client.post("/v1/models", json=m, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201, r.text


def _route_body(**kwargs) -> dict:
    defaults = {
        "task_type": "classify",
        "domain": "legal",
        "latency_budget_ms": 200,
        "compliance_tags": [],
    }
    return {**defaults, **kwargs}


async def _route(client: AsyncClient, body: dict, token: str | None = None):
    headers: dict = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return await client.request("GET", "/v1/route", content=_json.dumps(body), headers=headers)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_requires_auth(client: AsyncClient):
    r = await _route(client, _route_body())
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_returns_ranked_candidates(client: AsyncClient):
    tok = await _token(client)
    await _register(client, tok, _manifest("fast-model", _PERF_FAST, ["legal"], []))
    await _register(client, tok, _manifest("slow-model", _PERF_SLOW, ["legal"], []))

    r = await _route(client, _route_body(latency_budget_ms=300), token=tok)
    assert r.status_code == 200
    data = r.json()
    assert len(data["candidates"]) >= 1
    assert "composite_score" in data["candidates"][0]
    assert "score_breakdown" in data["candidates"][0]
    ids = [c["model_id"] for c in data["candidates"]]
    assert ids.index("fast-model") < ids.index("slow-model")


@pytest.mark.asyncio
async def test_route_composite_score_range(client: AsyncClient):
    tok = await _token(client)
    await _register(client, tok, _manifest("m1", _PERF_FAST, ["legal"], []))

    r = await _route(client, _route_body(), token=tok)
    assert r.status_code == 200
    for c in r.json()["candidates"]:
        assert 0.0 <= c["composite_score"] <= 1.0


@pytest.mark.asyncio
async def test_route_respects_limit(client: AsyncClient):
    tok = await _token(client)
    for i in range(5):
        await _register(client, tok, _manifest(f"model-{i}", _PERF_FAST, ["legal"], []))

    r = await _route(client, _route_body(limit=2), token=tok)
    assert r.status_code == 200
    assert len(r.json()["candidates"]) <= 2


@pytest.mark.asyncio
async def test_route_filters_wrong_domain(client: AsyncClient):
    tok = await _token(client)
    await _register(client, tok, _manifest("finance-only", _PERF_FAST, ["finance"], []))

    r = await _route(client, _route_body(domain="legal"), token=tok)
    assert r.status_code == 422
    data = r.json()
    assert data["error"] == "ROUTE_NO_CANDIDATES"
    reasons = [f["reason"] for f in data["filtered_out"]]
    assert "domain_mismatch" in reasons


@pytest.mark.asyncio
async def test_route_compliance_filter(client: AsyncClient):
    tok = await _token(client)
    await _register(client, tok, _manifest("gdpr-model", _PERF_FAST, ["legal"], ["gdpr-eu"]))

    r = await _route(client, _route_body(compliance_tags=["hipaa"]), token=tok)
    assert r.status_code == 422
    reasons = [f["reason"] for f in r.json()["filtered_out"]]
    assert "compliance_mismatch" in reasons


@pytest.mark.asyncio
async def test_route_latency_filter(client: AsyncClient):
    tok = await _token(client)
    await _register(client, tok, _manifest("slow-model", _PERF_SLOW, ["legal"], []))

    r = await _route(client, _route_body(latency_budget_ms=100), token=tok)
    assert r.status_code == 422
    reasons = [f["reason"] for f in r.json()["filtered_out"]]
    assert "latency_budget_exceeded" in reasons


@pytest.mark.asyncio
async def test_route_zero_latency_budget_means_no_constraint(client: AsyncClient):
    tok = await _token(client)
    await _register(client, tok, _manifest("slow-model", _PERF_SLOW, ["legal"], []))

    r = await _route(client, _route_body(latency_budget_ms=0), token=tok)
    assert r.status_code == 200
    ids = [c["model_id"] for c in r.json()["candidates"]]
    assert "slow-model" in ids


@pytest.mark.asyncio
async def test_route_exclude_models(client: AsyncClient):
    tok = await _token(client)
    await _register(client, tok, _manifest("m1", _PERF_FAST, ["legal"], []))
    await _register(client, tok, _manifest("m2", _PERF_FAST, ["legal"], []))

    r = await _route(client, _route_body(exclude_models=["m1"]), token=tok)
    assert r.status_code == 200
    ids = [c["model_id"] for c in r.json()["candidates"]]
    assert "m1" not in ids
    assert "m2" in ids


# ---------------------------------------------------------------------------
# G-C04 — zero-candidate response structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_candidate_response_structure(client: AsyncClient):
    tok = await _token(client)
    await _register(client, tok, _manifest("slow-model", _PERF_SLOW, ["legal"], []))

    r = await _route(client, _route_body(latency_budget_ms=50), token=tok)
    assert r.status_code == 422
    data = r.json()

    assert data["error"] == "ROUTE_NO_CANDIDATES"
    assert isinstance(data["message"], str)
    assert data["candidates"] == []
    assert isinstance(data["filtered_out"], list)
    assert isinstance(data["relaxation_suggestions"], list)
    assert isinstance(data["scoring_timestamp_unix"], int)
    assert "request_id" in data
    assert "timestamp_unix" in data


@pytest.mark.asyncio
async def test_relaxation_latency_suggestion(client: AsyncClient):
    tok = await _token(client)
    await _register(client, tok, _manifest("slow-model", _PERF_SLOW, ["legal"], []))

    r = await _route(client, _route_body(latency_budget_ms=50), token=tok)
    assert r.status_code == 422
    suggestions = r.json()["relaxation_suggestions"]
    latency_suggs = [s for s in suggestions if s["constraint"] == "latency_budget_ms"]
    assert len(latency_suggs) >= 1
    s = latency_suggs[0]
    assert s["current_value"] == 50
    assert s["suggested_value"] >= 50
    assert "slow-model" in s["models_unlocked"]


@pytest.mark.asyncio
async def test_relaxation_compliance_suggestion(client: AsyncClient):
    tok = await _token(client)
    await _register(client, tok, _manifest("gdpr-model", _PERF_FAST, ["legal"], ["gdpr-eu"]))

    r = await _route(client, _route_body(compliance_tags=["gdpr-eu", "hipaa"]), token=tok)
    assert r.status_code == 422
    suggestions = r.json()["relaxation_suggestions"]
    comp_suggs = [s for s in suggestions if s["constraint"] == "compliance_tags"]
    assert len(comp_suggs) >= 1
    hipaa_drop = next((s for s in comp_suggs if s.get("remove_tag") == "hipaa"), None)
    assert hipaa_drop is not None
    assert "gdpr-model" in hipaa_drop["models_unlocked"]


@pytest.mark.asyncio
async def test_relaxation_ordered_by_models_unlocked(client: AsyncClient):
    tok = await _token(client)
    await _register(client, tok, _manifest("m-latency-1", _PERF_SLOW, ["legal"], []))
    await _register(client, tok, _manifest("m-latency-2", _PERF_SLOW, ["legal"], []))
    await _register(client, tok, _manifest("m-hipaa", _PERF_FAST, ["legal"], ["hipaa"]))

    r = await _route(client, _route_body(latency_budget_ms=50, compliance_tags=["hipaa"]), token=tok)
    assert r.status_code == 422
    suggestions = r.json()["relaxation_suggestions"]
    unlocked_counts = [len(s["models_unlocked"]) for s in suggestions]
    assert unlocked_counts == sorted(unlocked_counts, reverse=True)


# ---------------------------------------------------------------------------
# /metrics and /healthz ops endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthz(client: AsyncClient):
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_metrics(client: AsyncClient):
    r = await client.get("/metrics")
    assert r.status_code == 200
    data = r.json()
    assert "models_total" in data
    assert "models_active" in data
    assert "models_deprecated" in data
