"""CalibrationSignal schema — submitted by the Calibration Layer."""

from pydantic import BaseModel, Field


class CalibrationSignal(BaseModel):
    model_id: str
    task_type: str
    domain: str
    quality_score: float = Field(..., ge=0.0, le=1.0)
    latency_ms: int = Field(..., ge=0)
    cost_usd: float | None = Field(None, ge=0.0)
    token_count: int | None = Field(None, ge=0)
    session_id: str | None = None
    idempotency_key: str | None = None
    observed_at: int | None = None   # unix timestamp; server fills in if absent
