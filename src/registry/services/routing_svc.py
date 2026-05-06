"""
Routing Engine — §4.3 + §9 G-C04.

Composite score = domain_perf * 0.55 + capacity * 0.30 + latency_fit * 0.15

Zero-candidate result always includes relaxation_suggestions ordered by
models_unlocked descending (G-C04).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from registry.models.manifest import ManifestORM


@dataclass
class ScoreBreakdown:
    domain_perf: float
    capacity: float
    latency_fit: float
    cost_fit: float


@dataclass
class CandidateResult:
    model_id: str
    composite_score: float
    score_breakdown: ScoreBreakdown
    p50_latency_ms: int
    capacity_pct: float
    compliance_satisfied: bool


@dataclass
class FilteredOut:
    model_id: str
    reason: str
    detail: str = ""


@dataclass
class RelaxationSuggestion:
    constraint: str
    current_value: Any
    models_unlocked: list[str] = field(default_factory=list)
    suggested_value: Any = None
    remove_tag: str | None = None


@dataclass
class RouteResult:
    candidates: list[CandidateResult]
    filtered_out: list[FilteredOut]
    scoring_timestamp_unix: int
    relaxation_suggestions: list[RelaxationSuggestion] | None = None


class RouteScorer:
    def score(
        self,
        models: list[ManifestORM],
        task_type: str,
        domain: str,
        latency_budget_ms: int,
        compliance_tags: list[str],
        cost_ceiling: float | None = None,
        quality_floor: float | None = None,
        exclude_models: list[str] | None = None,
        limit: int = 5,
    ) -> RouteResult:
        now = int(time.time())
        exclude = set(exclude_models or [])
        candidates: list[CandidateResult] = []
        filtered_out: list[FilteredOut] = []

        for model in models:
            if model.model_id in exclude or model.is_deprecated:
                continue

            if task_type not in (model.task_types or []):
                filtered_out.append(FilteredOut(
                    model_id=model.model_id,
                    reason="task_type_mismatch",
                    detail=f"model task_types {model.task_types} do not include '{task_type}'",
                ))
                continue

            if domain not in (model.domains or []):
                filtered_out.append(FilteredOut(
                    model_id=model.model_id,
                    reason="domain_mismatch",
                    detail=f"model domains {model.domains} do not include '{domain}'",
                ))
                continue

            missing_tags = [t for t in compliance_tags if t not in (model.compliance_tags or [])]
            if missing_tags:
                filtered_out.append(FilteredOut(
                    model_id=model.model_id,
                    reason="compliance_mismatch",
                    detail=f"model compliance_tags {model.compliance_tags} do not include required tag {missing_tags}",
                ))
                continue

            perf = model.perf_profile or {}
            p50 = perf.get("p50_latency_ms", 0)
            p95 = perf.get("p95_latency_ms", 0)
            cost_per_1k = perf.get("cost_per_1k_tokens")
            max_throughput = perf.get("max_throughput_rps") or 100

            if latency_budget_ms > 0 and p95 > latency_budget_ms:
                filtered_out.append(FilteredOut(
                    model_id=model.model_id,
                    reason="latency_budget_exceeded",
                    detail=f"model p95_latency_ms {p95} exceeds budget {latency_budget_ms}ms",
                ))
                continue

            if cost_ceiling is not None and cost_per_1k is not None and cost_per_1k > cost_ceiling:
                filtered_out.append(FilteredOut(
                    model_id=model.model_id,
                    reason="cost_ceiling_exceeded",
                    detail=f"model cost_per_1k_tokens {cost_per_1k} exceeds ceiling {cost_ceiling}",
                ))
                continue

            latency_fit = 1.0
            if latency_budget_ms > 0 and p95 > 0:
                latency_fit = min(1.0, latency_budget_ms / p95)

            cost_fit = 1.0
            if cost_ceiling is not None and cost_per_1k is not None and cost_per_1k > 0:
                cost_fit = min(1.0, cost_ceiling / cost_per_1k)

            # domain_perf defaults to 0.8; updated by Calibration Layer when available
            domain_perf = 0.8
            capacity = min(1.0, max_throughput / 200.0)
            composite = round(domain_perf * 0.55 + capacity * 0.30 + latency_fit * 0.15, 4)

            candidates.append(CandidateResult(
                model_id=model.model_id,
                composite_score=composite,
                score_breakdown=ScoreBreakdown(
                    domain_perf=domain_perf,
                    capacity=capacity,
                    latency_fit=latency_fit,
                    cost_fit=cost_fit,
                ),
                p50_latency_ms=p50,
                capacity_pct=capacity,
                compliance_satisfied=True,
            ))

        candidates.sort(key=lambda c: c.composite_score, reverse=True)
        candidates = candidates[:limit]

        relaxation_suggestions: list[RelaxationSuggestion] | None = None
        if not candidates:
            relaxation_suggestions = self._relaxation(
                models=models,
                task_type=task_type,
                domain=domain,
                latency_budget_ms=latency_budget_ms,
                compliance_tags=compliance_tags,
                cost_ceiling=cost_ceiling,
                exclude=exclude,
            )

        return RouteResult(
            candidates=candidates,
            filtered_out=filtered_out,
            scoring_timestamp_unix=now,
            relaxation_suggestions=relaxation_suggestions,
        )

    # ------------------------------------------------------------------
    # G-C04: minimum constraint change that unlocks ≥1 candidate
    # ------------------------------------------------------------------

    def _relaxation(
        self,
        models: list[ManifestORM],
        task_type: str,
        domain: str,
        latency_budget_ms: int,
        compliance_tags: list[str],
        cost_ceiling: float | None,
        exclude: set[str],
    ) -> list[RelaxationSuggestion]:
        suggestions: list[RelaxationSuggestion] = []

        if latency_budget_ms > 0:
            blocked_by_latency: list[tuple[str, int]] = []
            for model in models:
                if model.model_id in exclude or model.is_deprecated:
                    continue
                if task_type not in (model.task_types or []):
                    continue
                if domain not in (model.domains or []):
                    continue
                if any(t not in (model.compliance_tags or []) for t in compliance_tags):
                    continue
                p95 = (model.perf_profile or {}).get("p95_latency_ms", 0)
                if p95 > latency_budget_ms:
                    blocked_by_latency.append((model.model_id, p95))

            if blocked_by_latency:
                min_p95 = min(p95 for _, p95 in blocked_by_latency)
                unlocked = [mid for mid, p95 in blocked_by_latency if p95 <= min_p95]
                suggestions.append(RelaxationSuggestion(
                    constraint="latency_budget_ms",
                    current_value=latency_budget_ms,
                    suggested_value=min_p95,
                    models_unlocked=unlocked,
                ))

        for tag in compliance_tags:
            relaxed = [t for t in compliance_tags if t != tag]
            unlocked = []
            for model in models:
                if model.model_id in exclude or model.is_deprecated:
                    continue
                if task_type not in (model.task_types or []):
                    continue
                if domain not in (model.domains or []):
                    continue
                if any(t not in (model.compliance_tags or []) for t in relaxed):
                    continue
                p95 = (model.perf_profile or {}).get("p95_latency_ms", 0)
                if latency_budget_ms > 0 and p95 > latency_budget_ms:
                    continue
                unlocked.append(model.model_id)
            if unlocked:
                suggestions.append(RelaxationSuggestion(
                    constraint="compliance_tags",
                    current_value=compliance_tags,
                    remove_tag=tag,
                    models_unlocked=unlocked,
                ))

        suggestions.sort(key=lambda s: len(s.models_unlocked), reverse=True)
        return suggestions
