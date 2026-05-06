"""
Calibration signal endpoint — §4.2 POST /v1/calibration/signal.

Rate limit: 1000 req/min per token (enforced by RateLimitMiddleware).
Signals are validated then buffered via the C5-compatible CalibrationBuffer.
Returns 202 Accepted immediately; processing is asynchronous.
"""

from fastapi import APIRouter, Depends, status

from registry.middleware.auth import require_scope
from registry.models.auth import AuthTokenORM
from registry.models.signal import CalibrationSignal
from registry.services.calibration_svc import calibration_buffer

router = APIRouter(prefix="/v1/calibration", tags=["calibration"])


@router.post("/signal", status_code=status.HTTP_202_ACCEPTED)
async def submit_signal(
    body: CalibrationSignal,
    _token: AuthTokenORM = Depends(require_scope("calibration:write")),
) -> dict:
    await calibration_buffer.enqueue(body)
    return {"status": "accepted"}
