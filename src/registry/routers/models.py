"""
Model registry CRUD — §4.2.

GET    /v1/models                        public
GET    /v1/models/:model_id              public
POST   /v1/models                        registry:write
PUT    /v1/models/:model_id              registry:write (own) or registry:admin
DELETE /v1/models/:model_id              registry:admin  (soft-delete only)
GET    /v1/models/:model_id/adapters/:lang  public
GET    /v1/models/:model_id/heartbeat    public (proxy)
"""

import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from registry.db.database import get_db
from registry.middleware.auth import require_scope, require_token
from registry.models.auth import AuthTokenORM
from registry.models.manifest import ManifestCreate, ManifestORM, ManifestResponse, ManifestUpdate, hash_token

router = APIRouter(prefix="/v1/models", tags=["models"])


@router.get("", response_model=list[ManifestResponse])
async def list_models(
    domain: str | None = Query(None),
    task_type: str | None = Query(None),
    compliance_tag: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[ManifestResponse]:
    stmt = select(ManifestORM).where(ManifestORM.is_deprecated == False)  # noqa: E712
    result = await db.execute(stmt)
    rows = result.scalars().all()

    out = []
    for row in rows:
        if domain and domain not in (row.domains or []):
            continue
        if task_type and task_type not in (row.task_types or []):
            continue
        if compliance_tag and compliance_tag not in (row.compliance_tags or []):
            continue
        out.append(ManifestResponse.model_validate(row))
    return out


@router.get("/{model_id}", response_model=ManifestResponse)
async def get_model(model_id: str, db: AsyncSession = Depends(get_db)) -> ManifestResponse:
    row = await _get_or_404(db, model_id)
    return ManifestResponse.model_validate(row)


@router.post("", response_model=ManifestResponse, status_code=status.HTTP_201_CREATED)
async def register_model(
    body: ManifestCreate,
    db: AsyncSession = Depends(get_db),
    token: AuthTokenORM = Depends(require_scope("registry:write")),
) -> ManifestResponse:
    existing = await db.execute(select(ManifestORM).where(ManifestORM.model_id == body.model_id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model_id already registered")

    row = ManifestORM(
        id=str(uuid.uuid4()),
        owner_hash=token.token_hash,
        is_deprecated=body.deprecated,
        **_manifest_fields(body),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return ManifestResponse.model_validate(row)


@router.put("/{model_id}", response_model=ManifestResponse)
async def update_model(
    model_id: str,
    body: ManifestUpdate,
    db: AsyncSession = Depends(get_db),
    token: AuthTokenORM = Depends(require_scope("registry:write")),
) -> ManifestResponse:
    row = await _get_or_404(db, model_id)

    is_admin = "full" in token.scopes or "registry:admin" in token.scopes
    if row.owner_hash != token.token_hash and not is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot update another owner's model")

    for field, value in body.model_dump(exclude_none=True).items():
        if field == "adapters":
            setattr(row, field, value.model_dump() if hasattr(value, "model_dump") else value)
        elif field == "perf_profile":
            setattr(row, field, value.model_dump() if hasattr(value, "model_dump") else value)
        elif field == "deprecated":
            row.is_deprecated = value
        else:
            setattr(row, field, value)

    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return ManifestResponse.model_validate(row)


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deprecate_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    _token: AuthTokenORM = Depends(require_scope("registry:admin")),
) -> None:
    row = await _get_or_404(db, model_id)
    row.is_deprecated = True
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()


@router.get("/{model_id}/adapters/{lang}")
async def get_adapter(model_id: str, lang: str, db: AsyncSession = Depends(get_db)) -> dict:
    row = await _get_or_404(db, model_id)
    adapters: dict = row.adapters or {}
    if lang not in adapters:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No {lang} adapter registered")
    return adapters[lang]


@router.get("/{model_id}/heartbeat")
async def proxy_heartbeat(model_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    row = await _get_or_404(db, model_id)
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(row.heartbeat_endpoint)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Heartbeat unreachable")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_or_404(db: AsyncSession, model_id: str) -> ManifestORM:
    result = await db.execute(select(ManifestORM).where(ManifestORM.model_id == model_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    return row


def _manifest_fields(body: ManifestCreate) -> dict:
    return {
        "manifest_version": body.manifest_version,
        "model_id": body.model_id,
        "display_name": body.display_name,
        "model_version": body.model_version,
        "description": body.description,
        "task_types": body.task_types,
        "domains": body.domains,
        "input_modalities": body.input_modalities,
        "output_modalities": body.output_modalities,
        "perf_profile": body.perf_profile.model_dump(),
        "compliance_tags": body.compliance_tags,
        "data_residency": body.data_residency,
        "adapters": body.adapters.model_dump(exclude_none=True),
        "heartbeat_endpoint": body.heartbeat_endpoint,
        "contact_email": body.contact_email,
        "license": body.license,
        "successor_model_id": body.successor_model_id,
    }
