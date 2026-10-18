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
from app.models import AIOutput, Lead, LeadBatch
from app.models.ai_output import PURPOSE_OPERATIONAL
from app.models.background_job import (
    ITEM_BLOCKED,
    ITEM_FAILED,
    ITEM_SKIPPED,
    ITEM_SUCCEEDED,
    BackgroundJob,
)
from app.models.lead_batch import INCOMPLETE_BATCH_STATUSES
from app.models.lead import DISQUALIFIED_STATUSES


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
    from app.scoring.fit import RankingSetupRequired
    from app.services.fit_scoring import score_leads

    try:
        [(_, response, error)] = score_leads(session, [lead], persist=True, skip_unchanged=True)
    except RankingSetupRequired as e:
        raise JobFatalError(str(e)) from None
    if error is not None:
        return ITEM_FAILED, {"reason": f"scoring_error:{type(error).__name__}"}
    if response is None:
        return ITEM_SKIPPED, {"reason": "unchanged_inputs"}
    return ITEM_SUCCEEDED, {"fit_score": response.fit_score, "band": response.band,
                            "priority": response.priority, "profile_version": response.profile_version}


def _ranked_batch_leads(session: Session, job: BackgroundJob) -> list[UUID]:
    from app.scoring.fit import RankingSetupRequired
    from app.services.fit_scoring import require_ranking_profile

    try:
        require_ranking_profile(session)
    except RankingSetupRequired as e:
        raise JobFatalError(str(e)) from None
    return _all_batch_leads(session, job)


def _retired(name: str, replacement: str):
    def select_leads(session: Session, job: BackgroundJob) -> list[UUID]:
        raise JobFatalError(f"The '{name}' job type was retired; use '{replacement}' instead.")
    return select_leads


def _refuse_item(session: Session, job: BackgroundJob, lead: Lead) -> tuple[str, dict[str, Any]]:
    return ITEM_BLOCKED, {"reason": "job_type_retired"}


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


# ------------------------------------------------------------ Assistant selections

def _selection_params(max_leads: int):
    def validate(params: dict[str, Any]) -> dict[str, Any]:
        from app.jobs.queue import JobError

        ids = params.get("lead_ids") if isinstance(params, dict) else None
        try:
            if set(params) != {"lead_ids"} or not isinstance(ids, list):
                raise ValueError
            parsed = [UUID(str(i)) for i in ids]
            if not parsed or len(parsed) > max_leads or len(set(parsed)) != len(parsed):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise JobError(400, f"Choose one to {max_leads} distinct lead IDs; no other options are accepted.") from None
        return {"lead_ids": sorted(str(i) for i in parsed)}
    return validate


_assistant_params = _selection_params(5)
SELECTED_OUTREACH_MAX = 25


def validate_assistant_selection(session: Session, batch_id: UUID, params: dict) -> None:
    from app.jobs.queue import JobError

    batch = session.get(LeadBatch, batch_id)
    if batch.status in INCOMPLETE_BATCH_STATUSES:
        raise JobError(409, "Finish this import before preparing drafts.")
    ids = [UUID(i) for i in params["lead_ids"]]
    found = list(session.scalars(select(Lead.id).where(Lead.batch_id == batch_id, Lead.id.in_(ids))))
    if set(found) != set(ids):
        raise JobError(400, "Every selected lead must belong to this import.")


def _assistant_leads(session: Session, job: BackgroundJob) -> list[UUID]:
    from app.jobs.queue import JobError

    try:
        validate_assistant_selection(session, job.batch_id, job.params)
    except JobError as error:
        raise JobFatalError(error.detail) from None
    return [UUID(i) for i in job.params["lead_ids"]]


def _assistant_outreach(session: Session, job: BackgroundJob, lead: Lead) -> tuple[str, dict[str, Any]]:
    batch = session.get(LeadBatch, job.batch_id)
    if batch.status in INCOMPLETE_BATCH_STATUSES:
        return ITEM_BLOCKED, {"reason": "batch_incomplete"}
    # Reuse grounding, provenance, existing-draft skipping and block rules.
    return _generate("outreach_email")(session, job, lead)


def _selected_outreach(session: Session, job: BackgroundJob, lead: Lead) -> tuple[str, dict[str, Any]]:
    """Drafts for leads a rep picked from the ranked list. Each lead must
    still have a current ranking under the active seller profile -- the
    same revision the draft is generated from -- so a draft is never built
    on a ranking made against other target preferences."""
    from app.services import fit_queries

    if lead.status in DISQUALIFIED_STATUSES:
        return ITEM_BLOCKED, {"reason": f"lead_status:{lead.status}"}
    profile = fit_queries.current_ranking_profile(session)
    if fit_queries.latest_fit_score(session, lead.id, profile) is None:
        return ITEM_BLOCKED, {"reason": "ranking_not_current"}
    return _assistant_outreach(session, job, lead)


# ------------------------------------------------------------ Slack push

def _hot_leads(session: Session, job: BackgroundJob) -> list[UUID]:
    from app.services.integration_push import current_hot_lead_ids

    batch = session.get(LeadBatch, job.batch_id)
    if batch.status in INCOMPLETE_BATCH_STATUSES:
        raise JobFatalError(f"Batch is only partially imported (status='{batch.status}'); excluded from routing.")
    return current_hot_lead_ids(session, job.batch_id)


def _push(session: Session, job: BackgroundJob, lead: Lead) -> tuple[str, dict[str, Any]]:
    from app.services.integration_push import (
        BlockedLeadError,
        DeliveryInProgressError,
        DeliveryNotApprovedError,
        DeliveryOutcomeUnknownError,
        IncompleteImportError,
        deliver_lead_to_slack,
        current_ranking,
        lead_has_successful_slack_push,
    )

    ranking = current_ranking(session, lead)
    if ranking is None or ranking.band != "strong_match":
        return ITEM_SKIPPED, {"reason": "no_longer_hot"}  # re-ranked since the job was queued
    if not job.params.get("force", False) and lead_has_successful_slack_push(session, lead.id):
        return ITEM_SKIPPED, {"reason": "already_pushed"}
    try:
        # The Phase 11 ledger makes this safe across restarts: an item re-run
        # after a crash finds the earlier claim or delivery and sends nothing.
        result = deliver_lead_to_slack(session, lead, redeliver=job.params.get("force", False),
                                       actor=f"job:{job.id}")
    except DeliveryInProgressError as error:
        return ITEM_BLOCKED, {"reason": "delivery_in_progress", "push_id": str(error.push.id) if error.push else None}
    except DeliveryOutcomeUnknownError as error:
        return ITEM_BLOCKED, {"reason": "delivery_outcome_unknown", "push_id": str(error.push.id)}
    except BlockedLeadError as error:
        return ITEM_BLOCKED, {"reason": f"lead_status:{error.lead_status}"}
    except IncompleteImportError as error:
        return ITEM_BLOCKED, {"reason": f"batch_incomplete:{error.batch_status}"}
    except DeliveryNotApprovedError as error:
        return ITEM_BLOCKED, {"reason": "not_approved", "blockers": error.blockers}
    push = result.push
    if result.replay:
        return ITEM_SKIPPED, {"reason": "already_delivered", "push_id": str(push.id)}
    if push.status in ("success", "mock_success"):
        return ITEM_SUCCEEDED, {"push_id": str(push.id), "push_status": push.status}
    if push.status == "unknown":
        return ITEM_BLOCKED, {"reason": "delivery_outcome_unknown", "push_id": str(push.id)}
    # Never retried automatically: failed means not delivered, but a retry is
    # an explicit new request.
    return ITEM_FAILED, {"push_id": str(push.id), "reason": "delivery_failed"}


HANDLERS: dict[str, Handler] = {
    "fit_score": Handler(_bool_params({}), _ranked_batch_leads, _fit_score),
    # Retired: the fixed-term v1 priority score. Refused at enqueue
    # (RETIRED_JOB_TYPES); a job queued before the upgrade fails cleanly.
    "legacy_score": Handler(_bool_params({}), _retired("legacy_score", "fit_score"), _refuse_item),
    "generate_summary": Handler(_bool_params({"skip_existing": True}), _all_batch_leads, _generate("company_summary")),
    "generate_outreach": Handler(_bool_params({"skip_existing": True}), _all_batch_leads, _generate("outreach_email")),
    "push_hot": Handler(_bool_params({"force": False}), _hot_leads, _push),
    "assistant_outreach": Handler(_assistant_params, _assistant_leads, _assistant_outreach),
    "selected_outreach": Handler(_selection_params(SELECTED_OUTREACH_MAX), _assistant_leads, _selected_outreach),
}
RETIRED_JOB_TYPES = frozenset({"legacy_score"})
SELECTION_JOB_TYPES = frozenset({"assistant_outreach", "selected_outreach"})
