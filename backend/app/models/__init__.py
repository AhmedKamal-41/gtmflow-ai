from app.models.ai_output import AIOutput
from app.models.ai_output_review import AIOutputReview
from app.models.auth import User, UserSession
from app.models.background_job import BackgroundJob, BackgroundJobItem
from app.models.annotation import (
    AnnotationCandidate,
    CompanySplitAssignment,
    SplitManifest,
    TrainingAnnotation,
)
from app.models.company_identity import CompanyIdentity
from app.models.import_run import ImportRun
from app.models.integration_push import IntegrationPush
from app.models.lead import Lead
from app.models.lead_batch import LeadBatch
from app.models.lead_fit_score import LeadFitScore
from app.models.lead_score import LeadScore
from app.models.source_snapshot import SourceSnapshot
from app.models.seller_profile import SellerProfile, SellerProfileActivation
from app.models.workflow_event import WorkflowEvent

__all__ = [
    "AIOutput",
    "AIOutputReview",
    "AnnotationCandidate",
    "BackgroundJob",
    "BackgroundJobItem",
    "CompanySplitAssignment",
    "CompanyIdentity",
    "ImportRun",
    "IntegrationPush",
    "Lead",
    "LeadBatch",
    "LeadFitScore",
    "LeadScore",
    "SourceSnapshot",
    "SellerProfile",
    "SellerProfileActivation",
    "SplitManifest",
    "TrainingAnnotation",
    "User",
    "UserSession",
    "WorkflowEvent",
]

# Phase 12: stamp the current actor on every WorkflowEvent in EVERY process
# that uses the models (API, background worker, CLIs), not only the API.
from app.core.actor import install_event_hook as _install_event_hook  # noqa: E402

_install_event_hook()

