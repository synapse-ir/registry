from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from registry.db.database import get_db, init_db
from registry.middleware.rate_limit import RateLimitMiddleware
from registry.models.manifest import ManifestORM
from registry.routers import auth, models
from registry.routers import routing


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


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
