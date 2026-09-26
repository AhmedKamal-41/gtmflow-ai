"""Job types. Each handler reuses the existing service for one lead, so the
background path enforces exactly the rules the synchronous endpoints do:
grounding and output validation, provenance, runtime quality flags, blocked
lead statuses, incomplete imports and the exact-draft approval gate. No
handler approves, rejects or edits a draft.

`process` returns (item_status, outcome) and may raise:
  * TransientItemError -- retried, up to the job's max_item_attempts;
  * JobFatalError -- the job stops as failed (e.g. no AI configuration).
Any other exception is treated as transient (bounded the same way).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.ai.client import AIConfigError, AIProviderError
from app.models import AIOutput, Lead, LeadBatch, LeadScore, WorkflowEvent
from app.models.ai_output import PURPOSE_OPERATIONAL
from app.models.background_job import (
    ITEM_BLOCKED,
    ITEM_FAILED,
    ITEM_SKIPPED,
    ITEM_SUCCEEDED,
    BackgroundJob,
)
from app.models.lead_batch import INCOMPLETE_BATCH_STATUSES
from app.scoring.lead_scoring import DISQUALIFIED_STATUSES


class TransientItemError(Exception):
    """Worth retrying (provider/network hiccup). Message is a sanitized code."""


class JobFatalError(Exception):
    """Stops the whole job; message is safe to store and show."""


def _bool_params(allowed: dict[str, bool]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def validate(params: dict[str, Any]) -> dict[str, Any]:
        from app.jobs.queue import JobError

        unknown = sorted(set(params) - set(allowed))
        if unknown:
            raise JobError(400, f"Unknown parameter(s): {', '.join(unknown)}.")
        out = dict(allowed)
        for key, value in params.items():
            if not isinstance(value, bool):
                raise JobError(400, f"Parameter '{key}' must be true or false.")
            out[key] = value
        return out
    return validate


def _all_batch_leads(session: Session, job: BackgroundJob) -> list[UUID]:
    return list(session.scalars(select(Lead.id).where(Lead.batch_id == job.batch_id).order_by(Lead.id)))


@dataclass(frozen=True)
class Handler:
    validate_params: Callable[[dict[str, Any]], dict[str, Any]]
    select_leads: Callable[[Session, BackgroundJob], list[UUID]]
    process: Callable[[Session, BackgroundJob, Lead], tuple[str, dict[str, Any]]]
    finalize: Callable[[Session, BackgroundJob], dict[str, Any]] | None = None


# ------------------------------------------------------------ scoring

def _fit_score(session: Session, job: BackgroundJob, lead: Lead) -> tuple[str, dict[str, Any]]:
    from app.services.fit_scoring import score_leads

    [(_, response, error)] = score_leads(session, [lead], persist=True, skip_unchanged=True)
    if error is not None:
        return ITEM_FAILED, {"reason": f"scoring_error:{type(error).__name__}"}
    if response is None:
        return ITEM_SKIPPED, {"reason": "unchanged_inputs"}
    return ITEM_SUCCEEDED, {"fit_score": response.fit_score, "band": response.band}


def _legacy_score(session: Session, job: BackgroundJob, lead: Lead) -> tuple[str, dict[str, Any]]:
    from app.services.legacy_scoring import apply_legacy_score

    result = apply_legacy_score(session, lead)
    return ITEM_SUCCEEDED, {"total_score": result["total_score"], "priority": result["priority"]}


def _legacy_finalize(session: Session, job: BackgroundJob) -> dict[str, Any]:
    """Same batch-level effects as POST /api/batches/{id}/score."""
    batch = session.get(LeadBatch, job.batch_id)
    rows = session.execute(
        select(LeadScore.priority, LeadScore.total_score).join(Lead, Lead.id == LeadScore.lead_id)
        .where(Lead.batch_id == job.batch_id)
    ).all()
    bands = {"Hot": 0, "Warm": 0, "Cold": 0}
    for priority, _ in rows:
        bands[priority if priority in bands else "Cold"] += 1
    average = round(sum(t for _, t in rows) / len(rows), 1) if rows else 0.0
    if rows and batch.status not in INCOMPLETE_BATCH_STATUSES:
        batch.status = "scored"
    summary = {"scored_leads": len(rows), "hot": bands["Hot"], "warm": bands["Warm"],
               "cold": bands["Cold"], "average_score": average}
    session.add(WorkflowEvent(batch_id=job.batch_id, event_type="batch_scored",
                              event_data={**summary, "job_id": str(job.id)}))
    return summary


# ------------------------------------------------------------ generation

def _has_output(session: Session, lead_id: UUID, output_type: str) -> bool:
    return bool(session.scalar(select(exists().where(
        AIOutput.lead_id == lead_id, AIOutput.output_type == output_type,
        AIOutput.purpose == PURPOSE_OPERATIONAL,
    ))))


def _generate(output_type: str):
    def process(session: Session, job: BackgroundJob, lead: Lead) -> tuple[str, dict[str, Any]]:
        from app.ai import quality_checks
        from app.services.ai_generation import (
            GenerationOutputInvalid,
            SellerProfileRequired,
            generate_outreach_for_lead,
            generate_summary_for_lead,
            record_generation_rejected,
        )

        if output_type == "outreach_email" and lead.status in DISQUALIFIED_STATUSES:
            return ITEM_BLOCKED, {"reason": f"lead_status:{lead.status}"}
        if job.params.get("skip_existing", True) and _has_output(session, lead.id, output_type):
            return ITEM_SKIPPED, {"reason": "already_has_output"}
        generate = generate_summary_for_lead if output_type == "company_summary" else generate_outreach_for_lead
        try:
            output = generate(session, lead)
        except SellerProfileRequired as error:
            raise JobFatalError(str(error)) from None
        except AIConfigError as error:
            raise JobFatalError(str(error)) from None  # our own message; never contains a key
        except AIProviderError as error:
            raise TransientItemError(str(error)) from None  # already sanitized
        except GenerationOutputInvalid as error:
            # Not retried: the same input would be sent again (the LoRA
            # decodes greedily). Recorded exactly like the endpoint does.
            session.rollback()
            record_generation_rejected(session, lead.id, output_type, error.reason_codes, error.usage, error.details)
            return ITEM_FAILED, {"reason": "output_invalid", "reason_codes": error.reason_codes}
        flags = quality_checks.flag_codes(
            quality_checks.check_output(output_type, output.content, output.input_snapshot))
        return ITEM_SUCCEEDED, {"ai_output_id": str(output.id), "model_used": output.model_used,
                                "quality_flags": flags}
    return process


# ------------------------------------------------------------ Slack push

def _hot_leads(session: Session, job: BackgroundJob) -> list[UUID]:
    batch = session.get(LeadBatch, job.batch_id)
    if batch.status in INCOMPLETE_BATCH_STATUSES:
        raise JobFatalError(f"Batch is only partially imported (status='{batch.status}'); excluded from routing.")
    return list(session.scalars(
        select(Lead.id).join(LeadScore, LeadScore.lead_id == Lead.id)
        .where(Lead.batch_id == job.batch_id, LeadScore.priority == "Hot").order_by(Lead.id)
    ))


def _push(session: Session, job: BackgroundJob, lead: Lead) -> tuple[str, dict[str, Any]]:
    from app.services.integration_push import (
        BlockedLeadError,
        DeliveryNotApprovedError,
        IncompleteImportError,
        lead_has_successful_slack_push,
        push_lead_to_slack,
    )

    if lead.score is None or lead.score.priority != "Hot":
        return ITEM_SKIPPED, {"reason": "no_longer_hot"}  # rescored since the job was queued
    if not job.params.get("force", False) and lead_has_successful_slack_push(session, lead.id):
        return ITEM_SKIPPED, {"reason": "already_pushed"}
    try:
        push = push_lead_to_slack(session, lead)
    except BlockedLeadError as error:
        return ITEM_BLOCKED, {"reason": f"lead_status:{error.lead_status}"}
    except IncompleteImportError as error:
        return ITEM_BLOCKED, {"reason": f"batch_incomplete:{error.batch_status}"}
    except DeliveryNotApprovedError as error:
        return ITEM_BLOCKED, {"reason": "not_approved", "blockers": error.blockers}
    session.flush()
    if push.status in ("success", "mock_success"):
        return ITEM_SUCCEEDED, {"push_id": str(push.id), "push_status": push.status}
    # Never retried automatically: a retry could deliver the same lead twice.
    return ITEM_FAILED, {"push_id": str(push.id), "reason": "delivery_failed"}


HANDLERS: dict[str, Handler] = {
    "fit_score": Handler(_bool_params({}), _all_batch_leads, _fit_score),
    "legacy_score": Handler(_bool_params({}), _all_batch_leads, _legacy_score, _legacy_finalize),
    "generate_summary": Handler(_bool_params({"skip_existing": True}), _all_batch_leads, _generate("company_summary")),
    "generate_outreach": Handler(_bool_params({"skip_existing": True}), _all_batch_leads, _generate("outreach_email")),
    "push_hot": Handler(_bool_params({"force": False}), _hot_leads, _push),
}
