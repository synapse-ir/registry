from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from registry.db.database import get_db, init_db
from registry.middleware.rate_limit import RateLimitMiddleware
from registry.models.manifest import ManifestORM
from registry.routers import auth, models
from registry.routers import calibration, routing
from registry.services.calibration_svc import calibration_buffer
from registry.services.heartbeat_svc import heartbeat_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Seed heartbeat cache with all active models before starting the polling thread
    from registry.db.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ManifestORM).where(ManifestORM.is_deprecated == False)  # noqa: E712
        )
        for row in result.scalars().all():
            heartbeat_service.register_model(row.model_id, row.heartbeat_endpoint)

    heartbeat_service.start()
    await calibration_buffer.start()

    yield

    await calibration_buffer.stop()
    heartbeat_service.stop()


app = FastAPI(
    title="SYNAPSE Registry",
    description="Capability manifest store and adapter discovery service",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)

app.include_router(auth.router)
app.include_router(models.router)
app.include_router(routing.router)
app.include_router(calibration.router)

_LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>SYNAPSE Registry</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #0d1117; --surface: #161b22; --border: #30363d;
      --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
      --green: #3fb950; --purple: #bc8cff;
    }
    body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.6; }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .container { max-width: 860px; margin: 0 auto; padding: 60px 24px; }
    .badge { display: inline-block; font-family: monospace; font-size: 11px; background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 2px 8px; color: var(--green); margin-bottom: 24px; }
    h1 { font-size: 2.4rem; font-weight: 700; letter-spacing: -0.5px; margin-bottom: 12px; }
    h1 span { color: var(--purple); }
    .tagline { font-size: 1.1rem; color: var(--muted); margin-bottom: 40px; max-width: 560px; }
    .equation { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 20px 28px; margin-bottom: 40px; font-family: monospace; font-size: 1rem; }
    .equation .problem { color: #f85149; }
    .equation .solution { color: var(--green); }
    .equation .comment { color: var(--muted); }
    .stats { display: flex; gap: 24px; margin-bottom: 48px; flex-wrap: wrap; }
    .stat { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 20px 28px; flex: 1; min-width: 160px; }
    .stat .value { font-size: 2rem; font-weight: 700; color: var(--accent); font-family: monospace; }
    .stat .label { font-size: 0.85rem; color: var(--muted); margin-top: 4px; }
    .links { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 56px; }
    .btn { display: inline-block; padding: 10px 20px; border-radius: 6px; font-size: 0.9rem; font-weight: 600; border: 1px solid var(--border); background: var(--surface); color: var(--text); transition: border-color .15s; }
    .btn:hover { border-color: var(--accent); text-decoration: none; }
    .btn.primary { background: var(--accent); color: #0d1117; border-color: var(--accent); }
    .btn.primary:hover { background: #79c0ff; border-color: #79c0ff; }
    .section-title { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin-bottom: 16px; }
    .snippet { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 20px 24px; font-family: monospace; font-size: 0.85rem; overflow-x: auto; margin-bottom: 48px; white-space: pre; }
    .snippet .cmd { color: var(--green); }
    .snippet .comment { color: var(--muted); }
    .snippet .string { color: #a5d6ff; }
    footer { border-top: 1px solid var(--border); padding-top: 32px; color: var(--muted); font-size: 0.85rem; display: flex; gap: 24px; flex-wrap: wrap; }
    footer a { color: var(--muted); }
    footer a:hover { color: var(--text); }
  </style>
</head>
<body>
<div class="container">
  <div class="badge">● live</div>
  <h1>SYNAPSE <span>Registry</span></h1>
  <p class="tagline">
    Canonical IR protocol for AI model interoperability.
    Write two functions — connect your model to every other model in the ecosystem.
  </p>

  <div class="equation">
<span class="comment"># Without SYNAPSE: N models need N×(N-1)/2 custom connectors</span>
<span class="problem">connectors = N * (N - 1) / 2   # 10 models = 45 connectors, each breaks on schema change</span>

<span class="comment"># With SYNAPSE: write ingress() + egress() once</span>
<span class="solution">connectors = 2 * N             # 10 models = 20 adapters, all composable</span>
  </div>

  <div class="stats" id="stats">
    <div class="stat"><div class="value" id="stat-models">—</div><div class="label">registered models</div></div>
    <div class="stat"><div class="value">live</div><div class="label">routing engine</div></div>
    <div class="stat"><div class="value">MIT</div><div class="label">open source</div></div>
  </div>

  <div class="links">
    <a class="btn primary" href="/docs">API docs</a>
    <a class="btn" href="https://github.com/synapse-ir/registry">Registry</a>
    <a class="btn" href="https://github.com/synapse-ir/adapter-sdk">Adapter SDK</a>
    <a class="btn" href="https://github.com/synapse-ir/spec">Spec</a>
    <a class="btn" href="https://synapse-ir.github.io/adapter-sdk/">Documentation</a>
  </div>

  <div class="section-title">Quick start</div>
  <div class="snippet"><span class="cmd">pip install synapse-adapter-sdk</span>

<span class="comment"># Write your adapter</span>
<span class="cmd">from</span> synapse_sdk <span class="cmd">import</span> AdapterBase, CanonicalIR

<span class="cmd">class</span> MyModelAdapter(AdapterBase):
    MODEL_ID = <span class="string">"my-org/my-model-v1"</span>
    ADAPTER_VERSION = <span class="string">"1.0.0"</span>

    <span class="cmd">def</span> ingress(self, ir: CanonicalIR) -> dict:
        <span class="cmd">return</span> {<span class="string">"input"</span>: ir.payload.content}

    <span class="cmd">def</span> egress(self, output: dict, ir: CanonicalIR, latency_ms: int) -> CanonicalIR:
        <span class="cmd">return</span> self.build_response(ir, output[<span class="string">"result"</span>], latency_ms)

<span class="comment"># Validate before registering</span>
<span class="cmd">synapse-validate --adapter</span> my_module.MyModelAdapter <span class="cmd">--check-registry</span></div>

  <footer>
    <a href="https://github.com/synapse-ir">GitHub</a>
    <a href="/docs">API Reference</a>
    <a href="https://github.com/synapse-ir/spec">Specification</a>
    <a href="https://github.com/synapse-ir/adapter-sdk/blob/main/SECURITY.md">Security</a>
    <span>MIT License &nbsp;·&nbsp; Built with FastAPI</span>
  </footer>
</div>
<script>
  fetch('/metrics').then(r => r.json()).then(d => {
    document.getElementById('stat-models').textContent = d.models_active ?? '—';
  }).catch(() => {});
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing() -> HTMLResponse:
    return HTMLResponse(content=_LANDING_HTML)


@app.get("/healthz", tags=["ops"])
async def health() -> dict:
    return {"status": "ok"}


@app.get("/metrics", tags=["ops"])
async def metrics(db: AsyncSession = Depends(get_db)) -> dict:
    total = (await db.execute(select(func.count()).select_from(ManifestORM))).scalar_one()
    active = (await db.execute(
        select(func.count()).select_from(ManifestORM).where(ManifestORM.is_deprecated == False)  # noqa: E712
    )).scalar_one()
    return {
        "models_total": total,
        "models_active": active,
        "models_deprecated": total - active,
    }
