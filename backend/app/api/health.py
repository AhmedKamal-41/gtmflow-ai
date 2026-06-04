from fastapi import APIRouter

router = APIRouter(tags=["health"])

HEALTH_PAYLOAD: dict[str, str] = {
    "status": "ok",
    "service": "gtmflow-ai-backend",
    "version": "0.1.0",
}


@router.get("/api/health")
def api_health() -> dict[str, str]:
    return HEALTH_PAYLOAD


@router.get("/health")
def health_alias() -> dict[str, str]:
    return HEALTH_PAYLOAD
