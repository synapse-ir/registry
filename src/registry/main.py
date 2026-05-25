import hashlib
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

# Stable sentinel used as owner_hash for built-in catalog entries.
# These models are owned by the registry itself — not by any user token.
_CATALOG_OWNER_HASH = "dec5ef58104c3b8a25447f20e393c668c70133fdcb75732b0a67114666513f58"

# ---------------------------------------------------------------------------
# Catalog seed — 14 community adapter entries auto-inserted on startup
# (catalog mode: no heartbeat_endpoint, treated as always-available)
# ---------------------------------------------------------------------------
_CATALOG_SEED = [
    {"model_id": "openai/gpt-4o-mini", "display_name": "GPT-4o Mini",
     "model_version": "gpt-4o-mini-2024-07-18",
     "description": "OpenAI GPT-4o Mini for text classification and extraction via SYNAPSE canonical IR",
     "task_types": ["classify", "extract"], "domains": ["general", "legal", "finance"],
     "input_modalities": ["text"], "output_modalities": ["text"],
     "perf_profile": {"p50_latency_ms": 180, "p95_latency_ms": 400, "max_throughput_rps": 50, "cost_per_1k_tokens": 0.00015},
     "compliance_tags": ["gdpr-eu", "ccpa"], "data_residency": ["us", "eu"],
     "adapters": {"python": {"package": "synapse-adapter-sdk", "version": "0.1.1"}},
     "contact_email": "tfagent1111@gmail.com", "license": "MIT"},
    {"model_id": "sentence-transformers/all-MiniLM-L6-v2", "display_name": "all-MiniLM-L6-v2 Embeddings",
     "model_version": "1.0.0", "description": "Sentence embeddings via SYNAPSE canonical IR",
     "task_types": ["embed"], "domains": ["general", "semantic-search"],
     "input_modalities": ["text"], "output_modalities": ["embedding"],
     "perf_profile": {"p50_latency_ms": 20, "p95_latency_ms": 60, "max_throughput_rps": 200, "cost_per_1k_tokens": 0.0},
     "compliance_tags": ["gdpr-eu"], "data_residency": ["us"],
     "adapters": {"python": {"package": "synapse-adapter-sdk", "version": "0.1.1"}},
     "contact_email": "tfagent1111@gmail.com", "license": "Apache-2.0"},
    {"model_id": "facebook/bart-large-cnn", "display_name": "BART Large CNN Summarizer",
     "model_version": "1.0.0", "description": "Abstractive summarization via SYNAPSE canonical IR",
     "task_types": ["summarize"], "domains": ["general", "legal", "news"],
     "input_modalities": ["text"], "output_modalities": ["text"],
     "perf_profile": {"p50_latency_ms": 800, "p95_latency_ms": 2000, "max_throughput_rps": 5, "cost_per_1k_tokens": 0.0},
     "compliance_tags": ["gdpr-eu"], "data_residency": ["us"],
     "adapters": {"python": {"package": "synapse-adapter-sdk", "version": "0.1.1"}},
     "contact_email": "tfagent1111@gmail.com", "license": "MIT"},
    {"model_id": "medicalai/ClinicalBERT", "display_name": "ClinicalBERT",
     "model_version": "1.0.0", "description": "Clinical NLP for medical entity extraction via SYNAPSE canonical IR",
     "task_types": ["classify", "extract"], "domains": ["medical", "clinical"],
     "input_modalities": ["text"], "output_modalities": ["text"],
     "perf_profile": {"p50_latency_ms": 120, "p95_latency_ms": 300, "max_throughput_rps": 20, "cost_per_1k_tokens": 0.0},
     "compliance_tags": ["hipaa"], "data_residency": ["us"],
     "adapters": {"python": {"package": "synapse-adapter-sdk", "version": "0.1.1"}},
     "contact_email": "tfagent1111@gmail.com", "license": "MIT"},
    {"model_id": "openai/clip-vit-base-patch32", "display_name": "CLIP ViT-B/32",
     "model_version": "1.0.0", "description": "Multimodal image-text embeddings via SYNAPSE canonical IR",
     "task_types": ["embed", "classify"], "domains": ["general", "vision"],
     "input_modalities": ["text", "image"], "output_modalities": ["embedding"],
     "perf_profile": {"p50_latency_ms": 50, "p95_latency_ms": 150, "max_throughput_rps": 50, "cost_per_1k_tokens": 0.0},
     "compliance_tags": ["gdpr-eu"], "data_residency": ["us"],
     "adapters": {"python": {"package": "synapse-adapter-sdk", "version": "0.1.1"}},
     "contact_email": "tfagent1111@gmail.com", "license": "MIT"},
    {"model_id": "docling-project/docling", "display_name": "Docling Document Parser",
     "model_version": "1.0.0", "description": "Document parsing and extraction via SYNAPSE canonical IR",
     "task_types": ["extract", "classify"], "domains": ["general", "legal", "finance"],
     "input_modalities": ["text", "document"], "output_modalities": ["text"],
     "perf_profile": {"p50_latency_ms": 500, "p95_latency_ms": 2000, "max_throughput_rps": 10, "cost_per_1k_tokens": 0.0},
     "compliance_tags": ["gdpr-eu"], "data_residency": ["us", "eu"],
     "adapters": {"python": {"package": "synapse-adapter-sdk", "version": "0.1.1"}},
     "contact_email": "tfagent1111@gmail.com", "license": "MIT"},
    {"model_id": "facebook/bart-large-mnli", "display_name": "BART Large MNLI (Zero-Shot)",
     "model_version": "1.0.0", "description": "Zero-shot text classification via SYNAPSE canonical IR",
     "task_types": ["classify"], "domains": ["general", "legal", "finance"],
     "input_modalities": ["text"], "output_modalities": ["text"],
     "perf_profile": {"p50_latency_ms": 600, "p95_latency_ms": 1500, "max_throughput_rps": 8, "cost_per_1k_tokens": 0.0},
     "compliance_tags": ["gdpr-eu"], "data_residency": ["us"],
     "adapters": {"python": {"package": "synapse-adapter-sdk", "version": "0.1.1"}},
     "contact_email": "tfagent1111@gmail.com", "license": "MIT"},
    {"model_id": "ProsusAI/finbert", "display_name": "FinBERT Financial Sentiment",
     "model_version": "1.0.0", "description": "Financial sentiment analysis via SYNAPSE canonical IR",
     "task_types": ["classify"], "domains": ["finance"],
     "input_modalities": ["text"], "output_modalities": ["text"],
     "perf_profile": {"p50_latency_ms": 80, "p95_latency_ms": 200, "max_throughput_rps": 40, "cost_per_1k_tokens": 0.0},
     "compliance_tags": ["gdpr-eu"], "data_residency": ["us"],
     "adapters": {"python": {"package": "synapse-adapter-sdk", "version": "0.1.1"}},
     "contact_email": "tfagent1111@gmail.com", "license": "Apache-2.0"},
    {"model_id": "cross-encoder/ms-marco-MiniLM-L6-v2", "display_name": "MS MARCO Cross-Encoder Re-ranker",
     "model_version": "1.0.0", "description": "Passage re-ranking for search via SYNAPSE canonical IR",
     "task_types": ["rerank"], "domains": ["general", "semantic-search"],
     "input_modalities": ["text"], "output_modalities": ["text"],
     "perf_profile": {"p50_latency_ms": 30, "p95_latency_ms": 80, "max_throughput_rps": 100, "cost_per_1k_tokens": 0.0},
     "compliance_tags": ["gdpr-eu"], "data_residency": ["us"],
     "adapters": {"python": {"package": "synapse-adapter-sdk", "version": "0.1.1"}},
     "contact_email": "tfagent1111@gmail.com", "license": "Apache-2.0"},
    {"model_id": "dslim/bert-base-NER", "display_name": "BERT Base NER",
     "model_version": "1.0.0", "description": "Named entity recognition via SYNAPSE canonical IR",
     "task_types": ["extract"], "domains": ["general", "legal"],
     "input_modalities": ["text"], "output_modalities": ["text"],
     "perf_profile": {"p50_latency_ms": 60, "p95_latency_ms": 150, "max_throughput_rps": 50, "cost_per_1k_tokens": 0.0},
     "compliance_tags": ["gdpr-eu"], "data_residency": ["us"],
     "adapters": {"python": {"package": "synapse-adapter-sdk", "version": "0.1.1"}},
     "contact_email": "tfagent1111@gmail.com", "license": "Apache-2.0"},
    {"model_id": "johnsnowlabs/ner_clinical", "display_name": "John Snow Labs Clinical NER",
     "model_version": "1.0.0", "description": "Clinical named entity recognition via SYNAPSE canonical IR",
     "task_types": ["extract"], "domains": ["medical", "clinical"],
     "input_modalities": ["text"], "output_modalities": ["text"],
     "perf_profile": {"p50_latency_ms": 150, "p95_latency_ms": 400, "max_throughput_rps": 15, "cost_per_1k_tokens": 0.0},
     "compliance_tags": ["hipaa"], "data_residency": ["us"],
     "adapters": {"python": {"package": "synapse-adapter-sdk", "version": "0.1.1"}},
     "contact_email": "tfagent1111@gmail.com", "license": "Apache-2.0"},
    {"model_id": "Helsinki-NLP/opus-mt-en-fr", "display_name": "Helsinki NLP EN->FR Translation",
     "model_version": "1.0.0", "description": "English to French machine translation via SYNAPSE canonical IR",
     "task_types": ["translate"], "domains": ["general"],
     "input_modalities": ["text"], "output_modalities": ["text"],
     "perf_profile": {"p50_latency_ms": 200, "p95_latency_ms": 500, "max_throughput_rps": 20, "cost_per_1k_tokens": 0.0},
     "compliance_tags": ["gdpr-eu"], "data_residency": ["eu"],
     "adapters": {"python": {"package": "synapse-adapter-sdk", "version": "0.1.1"}},
     "contact_email": "tfagent1111@gmail.com", "license": "Apache-2.0"},
    {"model_id": "cardiffnlp/twitter-roberta-base-sentiment-latest",
     "display_name": "Twitter RoBERTa Sentiment",
     "model_version": "1.0.0", "description": "Social media sentiment analysis via SYNAPSE canonical IR",
     "task_types": ["classify"], "domains": ["general", "social"],
     "input_modalities": ["text"], "output_modalities": ["text"],
     "perf_profile": {"p50_latency_ms": 80, "p95_latency_ms": 200, "max_throughput_rps": 40, "cost_per_1k_tokens": 0.0},
     "compliance_tags": ["gdpr-eu"], "data_residency": ["us"],
     "adapters": {"python": {"package": "synapse-adapter-sdk", "version": "0.1.1"}},
     "contact_email": "tfagent1111@gmail.com", "license": "MIT"},
    {"model_id": "openai/whisper-large-v3", "display_name": "Whisper Large v3",
     "model_version": "large-v3", "description": "Multilingual speech-to-text via SYNAPSE canonical IR",
     "task_types": ["transcribe"], "domains": ["general", "media"],
     "input_modalities": ["audio"], "output_modalities": ["text"],
     "perf_profile": {"p50_latency_ms": 2000, "p95_latency_ms": 5000, "max_throughput_rps": 2, "cost_per_1k_tokens": 0.006},
     "compliance_tags": ["gdpr-eu"], "data_residency": ["us"],
     "adapters": {"python": {"package": "synapse-adapter-sdk", "version": "0.1.1"}},
     "contact_email": "tfagent1111@gmail.com", "license": "MIT"},
]


async def _seed_catalog(session: AsyncSession) -> None:
    """Insert catalog models that are not yet in the database (idempotent)."""
    import uuid as _uuid
    from datetime import datetime, timezone

    existing = {
        row.model_id
        for row in (await session.execute(select(ManifestORM))).scalars().all()
    }

    added = 0
    for entry in _CATALOG_SEED:
        if entry["model_id"] in existing:
            continue
        row = ManifestORM(
            id=str(_uuid.uuid4()),
            manifest_version="1.0.0",
            model_id=entry["model_id"],
            display_name=entry["display_name"],
            model_version=entry["model_version"],
            description=entry["description"],
            task_types=entry["task_types"],
            domains=entry["domains"],
            input_modalities=entry["input_modalities"],
            output_modalities=entry["output_modalities"],
            perf_profile=entry["perf_profile"],
            compliance_tags=entry.get("compliance_tags", []),
            data_residency=entry.get("data_residency", []),
            adapters=entry.get("adapters", {}),
            heartbeat_endpoint=None,
            contact_email=entry.get("contact_email", ""),
            license=entry.get("license", "MIT"),
            owner_hash=_CATALOG_OWNER_HASH,
            is_deprecated=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(row)
        added += 1

    if added:
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    from registry.db.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        # Seed catalog models on every startup (idempotent — skips existing)
        await _seed_catalog(session)

        # Seed heartbeat cache with all active models
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
  <div class="badge">&#9679; live</div>
  <h1>SYNAPSE <span>Registry</span></h1>
  <p class="tagline">
    Canonical IR protocol for AI model interoperability.
    Write two functions &mdash; connect your model to every other model in the ecosystem.
  </p>
  <div class="equation">
<span class="comment"># Without SYNAPSE: N models need N&times;(N-1)/2 custom connectors</span>
<span class="problem">connectors = N * (N - 1) / 2   # 10 models = 45 connectors, each breaks on schema change</span>

<span class="comment"># With SYNAPSE: write ingress() + egress() once</span>
<span class="solution">connectors = 2 * N             # 10 models = 20 adapters, all composable</span>
  </div>
  <div class="stats">
    <div class="stat"><div class="value" id="stat-models">&#8212;</div><div class="label">registered models</div></div>
    <div class="stat"><div class="value">live</div><div class="label">routing engine</div></div>
    <div class="stat"><div class="value">MIT</div><div class="label">open source</div></div>
  </div>
  <div class="links">
    <a class="btn primary" href="/docs">API docs</a>
    <a class="btn" href="https://synapse-ir.github.io">Project site</a>
    <a class="btn" href="https://github.com/synapse-ir/registry">Registry</a>
    <a class="btn" href="https://github.com/synapse-ir/adapter-sdk">Adapter SDK</a>
    <a class="btn" href="https://github.com/synapse-ir/spec">Spec</a>
  </div>
  <div class="section-title">Quick start</div>
  <div class="snippet"><span class="cmd">pip install synapse-adapter-sdk</span>

<span class="comment"># Write your adapter</span>
<span class="cmd">from</span> synapse_sdk <span class="cmd">import</span> AdapterBase, CanonicalIR

<span class="cmd">class</span> MyModelAdapter(AdapterBase):
    MODEL_ID = <span class="string">"my-org/my-model-v1"</span>
    ADAPTER_VERSION = <span class="string">"1.0.0"</span>

    <span class="cmd">def</span> ingress(self, ir: CanonicalIR) -&gt; dict:
        <span class="cmd">return</span> {"input": ir.payload.content}

    <span class="cmd">def</span> egress(self, output: dict, ir: CanonicalIR, latency_ms: int) -&gt; CanonicalIR:
        <span class="cmd">return</span> self.build_response(ir, output[<span class="string">"result"</span>], latency_ms)

<span class="comment"># Validate and check registry in one step</span>
<span class="cmd">synapse-validate --adapter</span> my_module.MyModelAdapter <span class="cmd">--check-registry</span></div>
  <footer>
    <a href="https://synapse-ir.github.io">Project site</a>
    <a href="/docs">API Reference</a>
    <a href="https://github.com/synapse-ir/spec">Specification</a>
    <a href="https://github.com/synapse-ir/adapter-sdk/blob/main/SECURITY.md">Security</a>
    <span>MIT License &nbsp;&middot;&nbsp; Built with FastAPI</span>
  </footer>
</div>
<script>
  fetch('/metrics').then(r => r.json()).then(d => {
    document.getElementById('stat-models').textContent = d.models_active ?? '\u2014';
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
