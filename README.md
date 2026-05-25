# SYNAPSE Registry

The coordination layer for the SYNAPSE adapter ecosystem.
Stores capability manifests, routes tasks to the best-fit model,
and improves routing decisions over time from real execution data.

## Hosted registry

A public hosted registry is live and available now:

**`https://registry-production-4b29.up.railway.app`**

- API docs: https://registry-production-4b29.up.railway.app/docs
- Health: https://registry-production-4b29.up.railway.app/healthz

No setup required — point your adapter SDK at the hosted endpoint to register models and query the routing engine immediately.

## Quick start (hosted)

```bash
# Register a model
curl -X POST https://registry-production-4b29.up.railway.app/v1/models \
  -H 'Authorization: Bearer <your-token>' \
  -H 'Content-Type: application/json' \
  -d @manifest.json

# Query the routing engine
curl -X GET https://registry-production-4b29.up.railway.app/v1/route \
  -H 'Authorization: Bearer <your-token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "task_type": "extract",
    "domain": "legal",
    "latency_budget_ms": 200,
    "compliance_tags": ["gdpr-eu"]
  }'
```

## Run locally

```bash
git clone https://github.com/synapse-ir/registry
cd registry
cp .env.example .env
docker compose up
```

Registry running at http://localhost:8000
API docs at http://localhost:8000/docs

## Register a model (local)

```bash
curl -X POST http://localhost:8000/v1/models \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d @manifest.json
```

## Query the routing engine (local)

```bash
curl -X GET http://localhost:8000/v1/route \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d '{
    "task_type": "extract",
    "domain": "legal",
    "latency_budget_ms": 200,
    "compliance_tags": ["gdpr-eu"]
  }'
```

## Architecture

- FastAPI + SQLAlchemy (SQLite dev, PostgreSQL prod)
- Five-layer caching architecture (C1–C5 per the specification)
- Heartbeat-based model availability monitoring
- Exponential-decay calibration for routing weight updates

## Registration modes

The registry supports two registration modes depending on whether the model has a live deployed endpoint:

**Catalog entry** (no `heartbeat_endpoint`)
Register adapter metadata — the package, capabilities, compliance tags, and performance profile — without a live inference endpoint. The routing engine treats catalog entries as available and uses declared `perf_profile` for scoring. Use this for community adapters, reference implementations, and models you want discoverable before deploying.

```json
{
  "model_id": "org/model-name",
  ...
  // omit heartbeat_endpoint entirely
}
```

**Live service** (with `heartbeat_endpoint`)
Register a deployed adapter that has a live HTTP endpoint returning a heartbeat JSON payload. The registry polls the endpoint every 30 seconds, marks the model degraded after 30 s staleness and unavailable after 90 s or 3 consecutive failures. Only live services participate in real-time availability routing.

```json
{
  "model_id": "org/model-name",
  ...
  "heartbeat_endpoint": "https://your-adapter-host/healthz"
}
```

The heartbeat endpoint must return JSON (any valid JSON object is accepted). A typical response:
```json
{"status": "ok", "capacity_pct": 0.85}
```

## Documentation

- [Canonical IR specification](https://github.com/synapse-ir/spec)
- [Adapter SDK (Python)](https://github.com/synapse-ir/adapter-sdk)
- [Adapter SDK (TypeScript)](https://github.com/synapse-ir/adapter-sdk-ts)
- [Community adapters](https://github.com/synapse-ir/adapters)

## License

MIT. See [LICENSE](LICENSE).
