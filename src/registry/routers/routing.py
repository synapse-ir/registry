"""
Routing query endpoint — §4.3 GET /v1/route.

Returns a ranked list of eligible models with composite scores.
When no models satisfy constraints, returns HTTP 422 with G-C04
zero-candidate structure including relaxation_suggestions.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from registry.db.database import get_db
from registry.middleware.auth import require_token
from registry.models.auth import AuthTokenORM
from registry.models.manifest import ManifestORM
from registry.services.heartbeat_svc import heartbeat_service
from registry.services.routing_svc import RelaxationSuggestion, RouteScorer

router = APIRouter(prefix="/v1", tags=["routing"])

_scorer = RouteScorer()


class RouteRequest(BaseModel):
    task_type: str
    domain: str
    latency_budget_ms: int
    compliance_tags: list[str]
    cost_ceiling: float | None = None
    quality_floor: float | None = None
    exclude_models: list[str] | None = None
    limit: int = 5


@router.get("/route")
async def route(
    body: RouteRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    _token: AuthTokenORM = Depends(require_token),
) -> Any:
    result_rows = await db.execute(
        select(ManifestORM).where(ManifestORM.is_deprecated == False)  # noqa: E712
    )
    models = list(result_rows.scalars().all())

    result = _scorer.score(
        models=models,
        task_type=body.task_type,
        domain=body.domain,
        latency_budget_ms=body.latency_budget_ms,
        compliance_tags=body.compliance_tags,
        cost_ceiling=body.cost_ceiling,
        quality_floor=body.quality_floor,
        exclude_models=body.exclude_models,
        limit=body.limit,
        heartbeat_service=heartbeat_service,
    )

    filtered_out_full = [
        {"model_id": f.model_id, "reason": f.reason, "detail": f.detail}
        for f in result.filtered_out
    ]

    if not result.candidates:
        return JSONResponse(
            status_code=422,
            content={
                "error": "ROUTE_NO_CANDIDATES",
                "message": "No registered models satisfy all routing constraints",
                "request_id": str(uuid.uuid4()),
                "timestamp_unix": result.scoring_timestamp_unix,
                "candidates": [],
                "filtered_out": filtered_out_full,
                "relaxation_suggestions": _serialise_suggestions(result.relaxation_suggestions or []),
                "scoring_timestamp_unix": result.scoring_timestamp_unix,
            },
        )

    return {
        "candidates": [
            {
                "model_id": c.model_id,
                "composite_score": c.composite_score,
                "score_breakdown": {
                    "domain_perf": c.score_breakdown.domain_perf,
                    "capacity": c.score_breakdown.capacity,
                    "latency_fit": c.score_breakdown.latency_fit,
                    "cost_fit": c.score_breakdown.cost_fit,
                },
                "p50_latency_ms": c.p50_latency_ms,
                "capacity_pct": c.capacity_pct,
                "compliance_satisfied": c.compliance_satisfied,
            }
            for c in result.candidates
        ],
        "filtered_out": [
            {"model_id": f.model_id, "reason": f.reason}
            for f in result.filtered_out
        ],
        "scoring_timestamp_unix": result.scoring_timestamp_unix,
    }


def _serialise_suggestions(suggestions: list[RelaxationSuggestion]) -> list[dict]:
    out = []
    for s in suggestions:
        entry: dict[str, Any] = {
            "constraint": s.constraint,
            "current_value": s.current_value,
            "models_unlocked": s.models_unlocked,
        }
        if s.suggested_value is not None:
            entry["suggested_value"] = s.suggested_value
        if s.remove_tag is not None:
            entry["remove_tag"] = s.remove_tag
        out.append(entry)
    return out
