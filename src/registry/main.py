from contextlib import asynccontextmanager

from fastapi import FastAPI

from registry.db.database import init_db
from registry.middleware.rate_limit import RateLimitMiddleware
from registry.routers import auth, models


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

app.add_middleware(RateLimitMiddleware)

app.include_router(auth.router)
app.include_router(models.router)


@app.get("/healthz", tags=["ops"])
async def health() -> dict:
    return {"status": "ok"}
