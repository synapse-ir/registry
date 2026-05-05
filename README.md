# SYNAPSE Registry

The coordination layer for the SYNAPSE adapter ecosystem.
Stores capability manifests, routes tasks to the best-fit model,
and improves routing decisions over time from real execution data.

## Run locally

```bash
git clone https://github.com/synapse-ir/registry
cd registry
cp .env.example .env
docker compose up
```

Registry running at http://localhost:8000
API docs at http://localhost:8000/docs

## Register a model

```bash
curl -X POST http://localhost:8000/v1/models \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d @manifest.json
```

## Query the routing engine

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

## Hosted registry

A hosted registry is available at registry.synapse-ir.io for projects
that prefer managed infrastructure.

## Architecture

- FastAPI + SQLAlchemy (SQLite dev, PostgreSQL prod)
- Five-layer caching architecture (C1–C5 per the specification)
- Heartbeat-based model availability monitoring
- Exponential-decay calibration for routing weight updates

## Documentation

- [Registry API reference](https://docs.synapse-ir.io/reference/registry-api)
- [Canonical IR specification](https://github.com/synapse-ir/spec)
- [Adapter SDK](https://github.com/synapse-ir/adapter-sdk)

## License

MIT. See [LICENSE](LICENSE).
