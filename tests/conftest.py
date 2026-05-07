"""
Shared pytest fixtures.

Each test function gets a fresh in-memory SQLite database so tests are
fully isolated even when run in parallel.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Point at in-memory SQLite before importing the app so the engine is created
# with the right URL.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SYNAPSE_ENV", "test")

# Must import after env vars are set.
from registry.db import database as db_module  # noqa: E402
from registry.db.database import Base, get_db  # noqa: E402
from registry.main import app  # noqa: E402
from registry.middleware import auth as auth_cache  # noqa: E402
from registry.middleware import rate_limit as rl_cache  # noqa: E402


@pytest.fixture(scope="function")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def client(db_engine):
    """AsyncClient wired to an isolated in-memory DB."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    # Clear in-process caches between tests
    auth_cache._cache.clear()
    rl_cache._windows.clear()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    auth_cache._cache.clear()
    rl_cache._windows.clear()


def _make_pypi_mock(status_code: int = 200, sha256: str = "a" * 64) -> MagicMock:
    """Return a mock httpx.AsyncClient class that simulates a PyPI JSON API response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {
        "urls": [{"packagetype": "sdist", "digests": {"sha256": sha256}}]
    }

    mock_instance = AsyncMock()
    mock_instance.get = AsyncMock(return_value=mock_resp)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=None)

    return MagicMock(return_value=mock_instance)


@pytest.fixture(autouse=True)
def _default_pypi_mock():
    """Prevent real PyPI network calls in every test; override per-test as needed."""
    with patch("registry.routers.models.httpx.AsyncClient", _make_pypi_mock()):
        yield
