"""
Token validation with 30-second in-process cache.

Cache behaviour matches §9 G-C01:
  - Valid token lookups are cached for CACHE_TTL seconds.
  - On explicit revocation the cache entry is evicted immediately so that
    the worst-case propagation delay is CACHE_TTL (30 s) only for stale
    positive hits that pre-date the DELETE call, not for hits that arrive
    after the DELETE response has been returned to the caller.
"""

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from registry.db.database import get_db
from registry.models.auth import AuthTokenORM, hash_token

if TYPE_CHECKING:
    pass

CACHE_TTL: float = 30.0  # seconds — §9 G-C01

# {token_hash: (AuthTokenORM | None, monotonic_expiry)}
_cache: dict[str, tuple[AuthTokenORM | None, float]] = {}

_bearer_scheme = HTTPBearer(auto_error=False)


def evict_token(token_hash: str) -> None:
    """Remove a token from the validation cache (called on revocation)."""
    _cache.pop(token_hash, None)


async def _lookup(db: AsyncSession, token_hash: str) -> AuthTokenORM | None:
    now = time.monotonic()
    if token_hash in _cache:
        record, exp = _cache[token_hash]
        if now < exp:
            return record

    result = await db.execute(
        select(AuthTokenORM).where(AuthTokenORM.token_hash == token_hash)
    )
    record = result.scalar_one_or_none()

    if record is not None:
        if record.is_revoked:
            record = None
        elif record.expires_at is not None:
            # SQLite returns naive datetimes; treat as UTC for comparison
            exp = record.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                record = None

    _cache[token_hash] = (record, now + CACHE_TTL)
    return record


async def get_optional_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthTokenORM | None:
    if credentials is None:
        return None
    token_hash = hash_token(credentials.credentials)
    return await _lookup(db, token_hash)


async def require_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthTokenORM:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    token_hash = hash_token(credentials.credentials)
    record = await _lookup(db, token_hash)
    if record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return record


def require_scope(scope: str):
    """Dependency factory: ensures the token has the given scope (or 'full')."""

    async def _check(token: AuthTokenORM = Depends(require_token)) -> AuthTokenORM:
        if "full" in token.scopes or scope in token.scopes:
            return token
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Token lacks required scope: {scope}",
        )

    return _check


async def update_last_used(db: AsyncSession, token: AuthTokenORM) -> None:
    token.last_used_at = datetime.now(timezone.utc)
    await db.commit()
