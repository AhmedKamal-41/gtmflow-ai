"""Orchestrates lead -> grounded context -> AI client -> validation ->
AIOutput persistence.

Order of operations for one request:
1. Resolve the seller revision ONCE (the active one, or an explicit
   override such as the demo's built-in profile) and copy its immutable
   content. Activating another revision mid-request cannot change what this
   output used or records.
2. Build the grounded context (app/ai/grounding.py) from current lead
   facts, provenance, the current fit score and current restrictions.
3. Call the client, then validate the result against the versioned schema
   and the context's fact/capability/claim ids. Invalid output is never
   saved as an AIOutput.
4. Add the output and its audit event to the session; the caller commits.

Generation never changes lead status, batch status, reviews or pushes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.ai import quality_checks
from app.ai.client import AIClient, get_ai_client
from app.ai.grounding import (
    AIOutputValidationError,
    build_grounded_context,
    describe_details,
    validate_outreach,
    validate_summary,
)
from app.ai.json_parser import AIJSONParseError, AIOutputTruncated
from app.ai.prompts import PROMPT_VERSION
from app.models import AIOutput, Lead, WorkflowEvent
from app.models.ai_output import ORIGIN_GENERATED, PURPOSE_OPERATIONAL
from app.scoring import fit
from app.services import fit_queries
from app.services.seller_profiles import (
    GTMFLOW_DEMO_PROFILE,
    active_seller_profile,
    profile_content_hash,
)

# PROMPT_VERSION lives with the prompts (app/ai/prompts.py). Historical rows
# keep "v1" for both fields.
OUTPUT_SCHEMA_VERSION = "v2"

SELLER_SOURCE_ACTIVE = "active_revision"
SELLER_SOURCE_BUILTIN_DEMO = "builtin_demo"


class SellerProfileRequired(RuntimeError):
    """Outreach needs an explicitly activated seller revision."""


class GenerationOutputInvalid(RuntimeError):
    def __init__(
        self,
        reason_codes: list[str],
        usage: dict[str, Any] | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        self.reason_codes = reason_codes
        # Provider-reported usage of the rejected (but still billed) call.
        self.usage = usage
        # Exactly what failed (field, offending value, allowed ids); see
        # AIOutputValidationError. The rejected reply itself is not kept.
        self.details = details or [{"code": code} for code in reason_codes]
        super().__init__(
            "The AI output failed validation ("
            + ", ".join(reason_codes)
            + "): "
            + describe_details(self.details)
            + ". No output was saved."
        )


@dataclass(frozen=True)
class SellerContext:
    """An immutable copy of the seller revision used for one request."""

    source: str
    profile_id: UUID | None
    version: int | None
    content_hash: str
    content: dict[str, Any]

    @property
    def kind(self) -> str:
        return self.content["profile_kind"]

    def as_context(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "profile_id": str(self.profile_id) if self.profile_id else None,
            "version": self.version,
            "content_hash": self.content_hash,
            "content": self.content,
        }


def resolve_active_seller(session: Session) -> SellerContext | None:
    row = active_seller_profile(session)
    if row is None:
        return None
    return SellerContext(
        source=SELLER_SOURCE_ACTIVE,
        profile_id=row.id,
        version=row.version,
        content_hash=row.content_hash,
        content=json.loads(json.dumps(row.profile)),
    )


def builtin_demo_seller() -> SellerContext:
    """The labeled GTMFlow demonstration profile, used by the standalone
    mock demo without storing or activating anything."""
    content = GTMFLOW_DEMO_PROFILE.model_dump(mode="json")
    return SellerContext(
        source=SELLER_SOURCE_BUILTIN_DEMO,
        profile_id=None,
        version=None,
        content_hash=profile_content_hash(content),
        content=content,
    )


def _hash_input(ctx: dict[str, Any]) -> str:
    """Stable hash of the exact context handed to the AI client.

    Used to prove/refute "was this the same input" later without storing a
    second copy of the snapshot -- sort_keys makes it independent of dict
    ordering, default=str covers any UUID/datetime values that sneak in.
    """
    canonical = json.dumps(ctx, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _provenance(lead: Lead) -> dict[str, Any]:
    def _id(value: Any) -> str | None:
        return str(value) if value is not None else None

    return {
        "lead_id": str(lead.id),
        "batch_id": str(lead.batch_id),
        "batch_source": lead.batch.source if lead.batch is not None else None,
        "record_source_field": lead.source,
        "source_snapshot_id": _id(lead.source_snapshot_id),
        "import_run_id": _id(lead.import_run_id),
        "source_record_id": lead.source_record_id,
        "company_identity_id": _id(lead.company_identity_id),
    }


def seller_context_for_output(
    output: AIOutput, active: SellerContext | None
) -> SellerContext | None:
    """The seller context that would reproduce `output`'s input *today*, or
    None when that seller is no longer the one in force: an active-revision
    draft needs that exact revision to still be active; a built-in demo
    draft needs the built-in profile's content to be unchanged."""
    if output.seller_profile_content_hash is None:
        return None
    if output.seller_profile_id is None:
        demo = builtin_demo_seller()
        return demo if demo.content_hash == output.seller_profile_content_hash else None
    if active is not None and active.profile_id == output.seller_profile_id:
        return active
    return None


def current_input_hash(
    lead: Lead, task: str, seller: SellerContext | None, fit_row: Any
) -> str:
    """Hash of the context generation would build right now -- compared with
    a draft's recorded `input_hash` to detect changed company or seller
    inputs. `fit_row` is the lead's current fit score (or None), prefetched
    so a page of leads needs one fit query, not one per lead."""
    return _hash_input(_context_from(lead, task, seller, fit_row))


def _build_context(
    session: Session, lead: Lead, task: str, seller: SellerContext | None
) -> dict[str, Any]:
    return _context_from(lead, task, seller, fit_queries.latest_fit_score(session, lead.id))


def _context_from(
    lead: Lead, task: str, seller: SellerContext | None, score: Any
) -> dict[str, Any]:
    fit_block = None
    if score is not None:
        fit_block = {
            "fit_score": score.fit_score,
            "band": score.band,
            "evidence_coverage_pct": score.evidence_coverage_pct,
            "profile_id": score.profile_id,
            "profile_version": score.profile_version,
        }
    eligibility = fit.compute_eligibility(lead)
    return build_grounded_context(
        task=task,  # type: ignore[arg-type]
        lead=lead,
        provenance=_provenance(lead),
        seller=seller.as_context() if seller is not None else None,
        fit=fit_block,
        restrictions={"excluded": eligibility.excluded, "reasons": eligibility.reasons},
    )


def _run(
    client: AIClient, output_type: str, ctx: dict[str, Any]
) -> dict[str, Any]:
    try:
        if output_type == "company_summary":
            return validate_summary(client.generate_company_summary(ctx), ctx)
        return validate_outreach(client.generate_outreach(ctx), ctx)
    except AIJSONParseError as e:
        # The parser's message is a JSON position/type error, not reply text.
        code = "truncated_output" if isinstance(e, AIOutputTruncated) else "invalid_json"
        raise GenerationOutputInvalid(
            [code], details=[{"code": code, "message": str(e)[:200]}]
        ) from None
    except AIOutputValidationError as e:
        raise GenerationOutputInvalid(e.reason_codes, details=e.details) from None


def _generate(
    session: Session,
    lead: Lead,
    output_type: str,
    event_type: str,
    seller: SellerContext | None,
    purpose: str = PURPOSE_OPERATIONAL,
    client: AIClient | None = None,
) -> AIOutput:
    client = client if client is not None else get_ai_client()
    ctx = _build_context(session, lead, output_type, seller)
    try:
        content = _run(client, output_type, ctx)
    except GenerationOutputInvalid as error:
        error.usage = client.last_usage
        raise
    input_hash = _hash_input(ctx)

    output = AIOutput(
        lead_id=lead.id,
        output_type=output_type,
        content=content,
        model_used=client.name,
        prompt_version=PROMPT_VERSION,
        origin=ORIGIN_GENERATED,
        input_snapshot=ctx,
        input_hash=input_hash,
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        model_revision=client.model_revision,
        adapter_revision=client.adapter_revision,
        seller_profile_id=seller.profile_id if seller else None,
        seller_profile_version=seller.version if seller else None,
        seller_profile_content_hash=seller.content_hash if seller else None,
        seller_profile_kind=seller.kind if seller else None,
        purpose=purpose,
    )
    session.add(output)
    session.flush()
    flags = quality_checks.check_output(output_type, content, ctx)
    session.add(
        WorkflowEvent(
            lead_id=lead.id,
            event_type=event_type,
            event_data={
                "output_type": output_type,
                "ai_output_id": str(output.id),
                "purpose": purpose,
                "model_used": client.name,
                "model_revision": client.model_revision,
                "adapter_revision": client.adapter_revision,
                "quality_checks_version": quality_checks.CHECKS_VERSION,
                "quality_flags": quality_checks.flag_codes(flags),
                "prompt_version": PROMPT_VERSION,
                "output_schema_version": OUTPUT_SCHEMA_VERSION,
                "input_hash": input_hash,
                "seller_profile_source": seller.source if seller else None,
                "seller_profile_id": str(seller.profile_id) if seller and seller.profile_id else None,
                "seller_profile_version": seller.version if seller else None,
                "seller_profile_content_hash": seller.content_hash if seller else None,
                "confidence": content.get("confidence"),
                "usage": client.last_usage,
            },
        )
    )
    return output


def generate_summary_for_lead(
    session: Session, lead: Lead, *, seller: SellerContext | None = None
) -> AIOutput:
    """A summary describes the lead record; it works without a seller
    profile and records the active one when there is one. `seller`
    overrides the active revision (the standalone demo only)."""
    resolved = seller if seller is not None else resolve_active_seller(session)
    return _generate(session, lead, "company_summary", "ai_summary_generated", resolved)


def generate_outreach_for_lead(
    session: Session, lead: Lead, *, seller: SellerContext | None = None
) -> AIOutput:
    """Outreach needs a seller: an explicitly activated revision, or the
    demo's explicit override. Without one nothing is generated."""
    resolved = seller if seller is not None else resolve_active_seller(session)
    if resolved is None:
        raise SellerProfileRequired(
            "No seller profile revision is active. Save and explicitly "
            "activate a reviewed revision on the Seller page before "
            "generating outreach."
        )
    return _generate(session, lead, "outreach_email", "outreach_generated", resolved)


def record_generation_rejected(
    session: Session,
    lead_id: UUID,
    output_type: str,
    reason_codes: list[str],
    usage: dict[str, Any] | None = None,
    details: list[dict[str, Any]] | None = None,
) -> None:
    """Audit a rejected generation without saving any of its content."""
    session.add(
        WorkflowEvent(
            lead_id=lead_id,
            event_type="ai_generation_rejected",
            event_data={
                "output_type": output_type,
                "reason_codes": reason_codes,
                "details": details,
                "usage": usage,
                "prompt_version": PROMPT_VERSION,
                "output_schema_version": OUTPUT_SCHEMA_VERSION,
            },
        )
    )
