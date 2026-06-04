from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.schemas.metrics import MetricsDashboard
from app.services.metrics import compute_dashboard

router = APIRouter(tags=["metrics"])


@router.get("/api/metrics/dashboard", response_model=MetricsDashboard)
def get_metrics_dashboard(
    session: Session = Depends(get_session),
) -> MetricsDashboard:
    return MetricsDashboard(**compute_dashboard(session))
