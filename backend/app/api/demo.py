from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.schemas.demo import DemoRunResponse
from app.services.demo import run_demo

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.post("/run", response_model=DemoRunResponse, status_code=status.HTTP_201_CREATED)
def post_run_demo(session: Session = Depends(get_session)) -> dict:
    """Seed a sample batch and run the full workflow end-to-end.

    One click: upload → score → AI summary + outreach → approve → push Hot
    leads to Slack (mock by default). Returns a summary of what happened so the
    UI can deep-link straight to the seeded batch and the populated metrics.
    """
    return run_demo(session)
