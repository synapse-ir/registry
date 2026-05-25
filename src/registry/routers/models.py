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
from registry.middleware.auth import require_scope
from registry.models.auth import AuthTokenORM
from registry.models.manifest import ManifestCreate, ManifestORM, ManifestResponse, ManifestUpdate
from registry.services.heartbeat_svc import heartbeat_service

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

    pypi_hash: str | None = None
    if body.adapters.python:
        pypi_hash = await _verify_pypi_package(
            body.adapters.python.package,
            body.adapters.python.version,
        )

    row = ManifestORM(
        id=str(uuid.uuid4()),
        owner_hash=token.token_hash,
        is_deprecated=body.deprecated,
        pypi_hash=pypi_hash,
        **_manifest_fields(body, pypi_hash=pypi_hash),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    if not row.is_deprecated:
        heartbeat_service.register_model(row.model_id, row.heartbeat_endpoint)
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

    if row.is_deprecated:
        heartbeat_service.unregister_model(row.model_id)
    else:
        heartbeat_service.register_model(row.model_id, row.heartbeat_endpoint)

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
    heartbeat_service.unregister_model(row.model_id)


@router.get("/{model_id}/adapters/{lang}")
async def get_adapter(model_id: str, lang: str, db: AsyncSession = Depends(get_db)) -> dict:
    row = await _get_or_404(db, model_id)
    adapters: dict = row.adapters or {}
    if lang not in adapters:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No {lang} adapter registered")
    return adapters[lang]


@router.get("/{model_id}/heartbeat")
async def proxy_heartbeat(model_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """
    Returns the cached HeartbeatResponse for a model.

    NEVER makes a live request on this path — all data comes from the
    background polling cache (§8 C3). Returns 503 when the cache entry
    is absent or the model is unreachable (≥3 consecutive poll failures).
    """
    await _get_or_404(db, model_id)   # 404 if model unknown

    cached = heartbeat_service.get_cached(model_id)
    routing_st = heartbeat_service.routing_status(model_id)

    if cached is None or routing_st == "unavailable":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "model_id": model_id,
                "status": routing_st,
                "consecutive_failures": cached.meta.consecutive_failures if cached else 0,
                "last_error": cached.meta.last_error if cached else None,
            },
        )

    return {
        "model_id": model_id,
        "status": routing_st,
        "cached_at": cached.meta.fetched_at,
        "is_stale": cached.meta.is_stale,
        "consecutive_failures": cached.meta.consecutive_failures,
        "heartbeat": cached.response,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_or_404(db: AsyncSession, model_id: str) -> ManifestORM:
    """Lookup by model_id string, or by UUID for slash-containing model IDs.

    Model IDs that follow the ``org/name`` convention (e.g. ``openai/gpt-4o-mini``)
    cannot be embedded in a URL path segment without ambiguity.  Callers may
    pass the UUID returned at registration time instead; this helper detects
    the UUID format and performs the appropriate lookup.
    """
    import re
    _UUID_RE = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
    )
    if _UUID_RE.match(model_id):
        result = await db.execute(select(ManifestORM).where(ManifestORM.id == model_id))
    else:
        result = await db.execute(select(ManifestORM).where(ManifestORM.model_id == model_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    return row


async def _verify_pypi_package(package: str, version: str) -> str:
    """Verify a package exists on PyPI and return the SHA-256 hash of its sdist tarball."""
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "REG_ADAPTER_MISSING",
                "message": f"Python adapter '{package}=={version}' not found on PyPI",
            },
        )

    urls = resp.json().get("urls", [])
    # Prefer sdist, fall back to any artifact
    sdist = next((u for u in urls if u.get("packagetype") == "sdist"), None)
    artifact = sdist or (urls[0] if urls else None)
    if artifact:
        sha256 = artifact.get("digests", {}).get("sha256", "")
        if sha256:
            return sha256

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "REG_ADAPTER_MISSING",
            "message": f"Python adapter '{package}=={version}' has no release artifacts on PyPI",
        },
    )


def _manifest_fields(body: ManifestCreate, pypi_hash: str | None = None) -> dict:
    adapters_dict = body.adapters.model_dump(exclude_none=True)
    if pypi_hash and "python" in adapters_dict:
        adapters_dict["python"]["pypi_hash"] = pypi_hash
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
        "adapters": adapters_dict,
        "heartbeat_endpoint": body.heartbeat_endpoint,
        "contact_email": body.contact_email,
        "license": body.license,
        "successor_model_id": body.successor_model_id,
    }
