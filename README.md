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

## Documentation

- [Canonical IR specification](https://github.com/synapse-ir/spec)
- [Adapter SDK (Python)](https://github.com/synapse-ir/adapter-sdk)
- [Adapter SDK (TypeScript)](https://github.com/synapse-ir/adapter-sdk-ts)
- [Community adapters](https://github.com/synapse-ir/adapters)

## License

MIT. See [LICENSE](LICENSE).
