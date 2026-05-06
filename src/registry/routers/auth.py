"""
Token lifecycle — §9 G-C01.

POST   /v1/auth/tokens        — issue (open, for bootstrapping)
GET    /v1/auth/tokens        — list metadata (requires valid token)
DELETE /v1/auth/tokens/:id    — revoke (requires valid token)

Rotation (zero-downtime):
  1. POST → receive new_token
  2. Update all services to use new_token (15-min grace window)
  3. DELETE old_token_id → old token evicted from cache within 30 s
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from registry.db.database import get_db
from registry.middleware.auth import evict_token, get_optional_token, require_token
from registry.models.auth import (
    AuthTokenORM,
    TokenCreateRequest,
    TokenCreateResponse,
    TokenMetadata,
    VALID_SCOPES,
    hash_token,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])

_ENV = os.getenv("SYNAPSE_ENV", "dev")


def _issue_token() -> str:
    """Generate a token in syn_{env}_{uuid4_no_hyphens} format."""
    uid = uuid.uuid4().hex  # 32 hex chars, no hyphens
    return f"syn_{_ENV}_{uid}"


@router.post("/tokens", response_model=TokenCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_token(
    body: TokenCreateRequest,
    db: AsyncSession = Depends(get_db),
    caller: AuthTokenORM | None = Depends(get_optional_token),
) -> TokenCreateResponse:
    # Validate requested scopes
    unknown = set(body.scopes) - VALID_SCOPES
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown scopes: {sorted(unknown)}",
        )

    # dev tokens never expire per §9 G-C01; test tokens expire in 24 h
    expires_at: datetime | None = None
    if body.expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
    elif _ENV == "test":
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    raw_token = _issue_token()
    token_hash = hash_token(raw_token)

    record = AuthTokenORM(
        token_id=str(uuid.uuid4()),
        token_hash=token_hash,
        scopes=body.scopes,
        description=body.description,
        expires_at=expires_at,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    expires_unix: int | None = (
        int(record.expires_at.timestamp()) if record.expires_at else None
    )
    return TokenCreateResponse(
        token=raw_token,
        token_id=record.token_id,
        expires_at=expires_unix,
    )


@router.get("/tokens", response_model=list[TokenMetadata])
async def list_tokens(
    db: AsyncSession = Depends(get_db),
    _caller: AuthTokenORM = Depends(require_token),
) -> list[TokenMetadata]:
    result = await db.execute(select(AuthTokenORM).order_by(AuthTokenORM.created_at))
    rows = result.scalars().all()
    return [TokenMetadata.model_validate(row) for row in rows]


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: str,
    db: AsyncSession = Depends(get_db),
    caller: AuthTokenORM = Depends(require_token),
) -> None:
    result = await db.execute(
        select(AuthTokenORM).where(AuthTokenORM.token_id == token_id)
    )
    record = result.scalar_one_or_none()

    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")

    if record.is_revoked:
        # Idempotent — already revoked
        return

    # Allow self-revocation or admin scope
    is_self = caller.token_id == token_id
    is_admin = "full" in caller.scopes or "registry:admin" in caller.scopes
    if not is_self and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot revoke another account's token without registry:admin scope",
        )

    record.is_revoked = True
    record.revoked_at = datetime.now(timezone.utc)
    await db.commit()

    # Evict from validation cache immediately
    evict_token(record.token_hash)
