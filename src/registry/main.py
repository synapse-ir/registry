from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
