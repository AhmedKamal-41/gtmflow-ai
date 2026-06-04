from fastapi import APIRouter

from app.api.ai import router as ai_router
from app.api.batches import router as batches_router
from app.api.demo import router as demo_router
from app.api.health import router as health_router
from app.api.leads import router as leads_router
from app.api.metrics import router as metrics_router
from app.api.outreach_review import router as outreach_review_router
from app.api.push import router as push_router
from app.api.scoring import router as scoring_router

router = APIRouter()
router.include_router(health_router)
router.include_router(batches_router)
router.include_router(leads_router)
router.include_router(scoring_router)
router.include_router(ai_router)
router.include_router(push_router)
router.include_router(outreach_review_router)
router.include_router(metrics_router)
router.include_router(demo_router)
