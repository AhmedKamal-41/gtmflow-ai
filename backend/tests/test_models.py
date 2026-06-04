from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.orm import configure_mappers

from app import models
from app.core.database import Base
from app.models import (
    AIOutput,
    IntegrationPush,
    Lead,
    LeadBatch,
    LeadScore,
    WorkflowEvent,
)
from app.schemas import (
    AIOutputCreate,
    AIOutputRead,
    IntegrationPushCreate,
    IntegrationPushRead,
    LeadBatchCreate,
    LeadBatchRead,
    LeadCreate,
    LeadRead,
    LeadScoreCreate,
    LeadScoreRead,
    WorkflowEventCreate,
    WorkflowEventRead,
)

EXPECTED_TABLES = {
    LeadBatch: "lead_batches",
    Lead: "leads",
    LeadScore: "lead_scores",
    AIOutput: "ai_outputs",
    WorkflowEvent: "workflow_events",
    IntegrationPush: "integration_pushes",
}


def test_all_models_exposed_on_package() -> None:
    for cls in EXPECTED_TABLES:
        assert cls is getattr(models, cls.__name__)


def test_each_model_has_expected_tablename() -> None:
    for cls, table in EXPECTED_TABLES.items():
        assert cls.__tablename__ == table


def test_metadata_contains_all_tables() -> None:
    assert set(EXPECTED_TABLES.values()).issubset(Base.metadata.tables.keys())


def test_relationships_configure_without_error() -> None:
    configure_mappers()
    assert LeadBatch.leads.property.mapper.class_ is Lead
    assert LeadBatch.workflow_events.property.mapper.class_ is WorkflowEvent
    assert Lead.batch.property.mapper.class_ is LeadBatch
    assert Lead.score.property.mapper.class_ is LeadScore
    assert Lead.ai_outputs.property.mapper.class_ is AIOutput
    assert Lead.workflow_events.property.mapper.class_ is WorkflowEvent
    assert Lead.integration_pushes.property.mapper.class_ is IntegrationPush
    assert LeadScore.lead.property.mapper.class_ is Lead
    assert AIOutput.lead.property.mapper.class_ is Lead
    assert WorkflowEvent.lead.property.mapper.class_ is Lead
    assert WorkflowEvent.batch.property.mapper.class_ is LeadBatch
    assert IntegrationPush.lead.property.mapper.class_ is Lead


def test_pydantic_schemas_instantiate() -> None:
    batch = LeadBatchCreate(name="June pipeline test")
    assert batch.source == "csv"

    batch_id = uuid4()
    lead = LeadCreate(batch_id=batch_id, company_name="Cascade Modular Homes")
    assert lead.batch_id == batch_id
    assert lead.company_name == "Cascade Modular Homes"

    lead_id = uuid4()
    score = LeadScoreCreate(
        lead_id=lead_id,
        total_score=82,
        priority="hot",
        score_breakdown={"industry": 20, "title": 30, "size": 32},
        reasoning="VP at modular homes manufacturer in target ICP",
    )
    assert score.total_score == 82
    assert score.priority == "hot"

    ai = AIOutputCreate(
        lead_id=lead_id,
        output_type="summary",
        content={"text": "Mock summary"},
        model_used="mock",
    )
    assert ai.output_type == "summary"

    evt = WorkflowEventCreate(
        event_type="batch_uploaded",
        event_data={"count": 10},
        batch_id=batch_id,
    )
    assert evt.event_type == "batch_uploaded"
    assert evt.lead_id is None

    push = IntegrationPushCreate(
        lead_id=lead_id,
        integration_type="slack",
        payload={"text": "hot lead"},
        status="ok",
    )
    assert push.status == "ok"


def test_pydantic_read_schemas_support_from_attributes() -> None:
    """Read schemas use from_attributes for ORM rows (no DB required)."""
    now = datetime.now(timezone.utc)
    batch_id = uuid4()
    lead_id = uuid4()

    batch_row = SimpleNamespace(
        id=batch_id,
        name="June pipeline",
        source="csv",
        total_leads=1,
        processed_leads=0,
        status="uploaded",
        created_at=now,
        updated_at=now,
    )
    assert LeadBatchRead.model_validate(batch_row).id == batch_id

    lead_row = SimpleNamespace(
        id=lead_id,
        batch_id=batch_id,
        company_name="Cascade Modular Homes",
        website=None,
        industry=None,
        contact_name=None,
        contact_email=None,
        contact_title=None,
        company_size=None,
        location=None,
        source=None,
        status="new",
        cleaned_data={"normalized_email": "ops@example.com"},
        created_at=now,
        updated_at=now,
    )
    assert LeadRead.model_validate(lead_row).company_name == "Cascade Modular Homes"

    score_row = SimpleNamespace(
        id=uuid4(),
        lead_id=lead_id,
        total_score=82,
        priority="hot",
        score_breakdown={"title": 30},
        reasoning="VP title",
        created_at=now,
        updated_at=now,
    )
    assert LeadScoreRead.model_validate(score_row).priority == "hot"

    ai_row = SimpleNamespace(
        id=uuid4(),
        lead_id=lead_id,
        output_type="summary",
        content={"text": "Mock"},
        model_used="mock",
        prompt_version="v0",
        created_at=now,
    )
    assert AIOutputRead.model_validate(ai_row).output_type == "summary"

    event_row = SimpleNamespace(
        id=uuid4(),
        lead_id=None,
        batch_id=batch_id,
        event_type="batch_uploaded",
        event_data={"count": 1},
        created_at=now,
    )
    assert WorkflowEventRead.model_validate(event_row).lead_id is None

    push_row = SimpleNamespace(
        id=uuid4(),
        lead_id=lead_id,
        integration_type="slack",
        payload={"text": "hot"},
        status="ok",
        response_text=None,
        created_at=now,
    )
    assert IntegrationPushRead.model_validate(push_row).status == "ok"
