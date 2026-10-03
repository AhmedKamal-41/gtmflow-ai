from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.ai.client import AIConfigError
from app.core.config import settings
from app.core.database import get_session
from app.schemas.assistant import AssistantRequest, AssistantResult
from app.services.lead_assistant import AssistantError, run_assistant

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


@router.get("/status")
def assistant_status(request: Request) -> dict:
    mock = settings.use_mock_ai or settings.agent_provider == "mock"
    configured = mock or (settings.agent_provider == "openai" and bool(settings.openai_api_key))
    role = request.state.auth.role
    return {"mode": "mock" if mock else "openai", "configured": configured,
            "can_run": configured and (role == "operator" or (role == "guest" and settings.guest_safe)),
            "max_leads": 5, "max_steps": 6}


@router.post("/plan", response_model=AssistantResult)
def plan(payload: AssistantRequest, session: Session = Depends(get_session)) -> AssistantResult:
    try:
        return run_assistant(session, payload)
    except AssistantError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None
    except AIConfigError:
        raise HTTPException(status_code=503, detail="The assistant provider is not configured.") from None
