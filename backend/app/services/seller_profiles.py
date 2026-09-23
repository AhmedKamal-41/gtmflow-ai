from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import SellerProfile, SellerProfileActivation, WorkflowEvent
from app.schemas.seller_profile import (
    SellerProfileActivate,
    SellerProfileContent,
    SellerProfileCreate,
    SellerProfileDeactivate,
)

EDITOR_LABEL = "local-demo-unauthenticated"

# An explicitly labeled demonstration of GTMFlow itself. Every statement
# describes what this repository does; it has no proof points because no
# evidence of customer results exists. It is offered as an editor template
# and used by the standalone mock demo -- it is never activated for the
# real cohort automatically.
GTMFLOW_DEMO_PROFILE = SellerProfileContent(
    profile_kind="demo",
    company_name="GTMFlow (demonstration)",
    product_name="GTMFlow demo workflow",
    value_proposition=(
        "GTMFlow is a portfolio demonstration that imports company lists, "
        "scores company fit with a versioned rubric, drafts outreach for a "
        "person to approve, reject or correct, routes approved leads to "
        "Slack, and records each step in an audit trail. This demonstration "
        "profile is not a commercial offer and has no customer results to cite."
    ),
    target_customer=(
        "Demonstration only: teams evaluating a lead-workflow prototype. "
        "No real customer segment has been specified."
    ),
    capabilities=[
        "Imports company lists from CSV files and a public company dataset",
        "Scores company fit with a deterministic, versioned rubric",
        "Drafts outreach grounded in the imported company facts",
        "Lets a person approve, reject or correct each exact draft",
        "Routes approved leads to a Slack channel for internal handoff",
        "Records workflow events for an audit trail",
    ],
    proof_points=[],
    exclusions=[],
)


class ProfileVersionConflict(ValueError):
    pass


class ActivationConflict(ValueError):
    pass


class SellerProfileNotFound(LookupError):
    pass


class DemoAcknowledgementRequired(ValueError):
    pass


def profile_content_hash(profile: dict) -> str:
    canonical = json.dumps(profile, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def latest_seller_profile(session: Session) -> SellerProfile | None:
    return session.scalars(
        select(SellerProfile).order_by(SellerProfile.version.desc()).limit(1)
    ).first()


def save_seller_profile(
    session: Session, request: SellerProfileCreate
) -> tuple[SellerProfile, bool]:
    profile = request.profile.model_dump(mode="json")
    content_hash = profile_content_hash(profile)
    current = latest_seller_profile(session)
    current_version = current.version if current is not None else 0

    if current is not None and current.content_hash == content_hash:
        if request.expected_version in (current_version, current_version - 1):
            return current, False
    if request.expected_version != current_version:
        raise ProfileVersionConflict("The seller profile changed. Load the latest version before saving.")

    row = SellerProfile(
        id=uuid.uuid4(),
        version=current_version + 1,
        profile=profile,
        content_hash=content_hash,
        editor_label=EDITOR_LABEL,
    )
    session.add(row)
    session.add(WorkflowEvent(
        event_type="seller_profile_draft_saved",
        event_data={
            "seller_profile_id": str(row.id),
            "version": row.version,
            "content_hash": content_hash,
            "editor_label": row.editor_label,
        },
    ))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        winner = latest_seller_profile(session)
        if winner is not None and winner.version > current_version:
            if winner.version == current_version + 1 and winner.content_hash == content_hash:
                return winner, False
            raise ProfileVersionConflict(
                "The seller profile changed. Load the latest version before saving."
            ) from None
        raise
    session.refresh(row)
    return row, True


# ------------------------------------------------------------- activation

def current_activation(session: Session) -> SellerProfileActivation | None:
    return session.scalars(
        select(SellerProfileActivation)
        .order_by(SellerProfileActivation.sequence.desc())
        .limit(1)
    ).first()


def active_seller_profile(session: Session) -> SellerProfile | None:
    """The revision generation uses right now, in one query. Revisions are
    immutable, so a caller that reads this once has a stable copy even if
    someone activates another revision afterwards."""
    latest = (
        select(SellerProfileActivation.seller_profile_id)
        .order_by(SellerProfileActivation.sequence.desc())
        .limit(1)
        .scalar_subquery()
    )
    return session.scalars(
        select(SellerProfile).where(SellerProfile.id == latest)
    ).first()


def _record_activation(
    session: Session, row: SellerProfileActivation, event_type: str
) -> None:
    session.add(row)
    session.add(WorkflowEvent(
        event_type=event_type,
        event_data={
            "activation_id": str(row.id),
            "sequence": row.sequence,
            "seller_profile_id": str(row.seller_profile_id) if row.seller_profile_id else None,
            "seller_profile_version": row.seller_profile_version,
            "content_hash": row.content_hash,
            "reviewed_confirmation": row.reviewed_confirmation,
            "demo_acknowledged": row.demo_acknowledged,
            "actor_label": row.actor_label,
        },
    ))


def _commit_activation(
    session: Session, row: SellerProfileActivation, current_sequence: int, same_as
) -> tuple[SellerProfileActivation, bool]:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        winner = current_activation(session)
        if winner is not None and winner.sequence == current_sequence + 1 and same_as(winner):
            return winner, False
        raise ActivationConflict(
            "The active seller profile changed. Reload before activating."
        ) from None
    session.refresh(row)
    return row, True


def activate_seller_profile(
    session: Session, request: SellerProfileActivate
) -> tuple[SellerProfileActivation, bool]:
    profile = session.get(SellerProfile, request.seller_profile_id)
    if profile is None:
        raise SellerProfileNotFound("Seller profile revision not found.")
    if profile.profile.get("profile_kind") == "demo" and not request.acknowledge_demo:
        raise DemoAcknowledgementRequired(
            "This revision is a demonstration profile. Acknowledge that "
            "outreach generated from it is for demonstration only."
        )
    current = current_activation(session)
    current_sequence = current.sequence if current is not None else 0

    def same(row: SellerProfileActivation) -> bool:
        return row.action == "activate" and row.seller_profile_id == profile.id

    # Identical retry of the activation that is already current.
    if current is not None and same(current):
        if request.expected_activation_sequence in (current_sequence, current_sequence - 1):
            return current, False
    if request.expected_activation_sequence != current_sequence:
        raise ActivationConflict(
            "The active seller profile changed. Reload before activating."
        )

    row = SellerProfileActivation(
        id=uuid.uuid4(),
        sequence=current_sequence + 1,
        seller_profile_id=profile.id,
        seller_profile_version=profile.version,
        content_hash=profile.content_hash,
        action="activate",
        reviewed_confirmation=True,
        demo_acknowledged=bool(request.acknowledge_demo),
        actor_label=EDITOR_LABEL,
    )
    _record_activation(session, row, "seller_profile_activated")
    return _commit_activation(session, row, current_sequence, same)


def deactivate_seller_profile(
    session: Session, request: SellerProfileDeactivate
) -> tuple[SellerProfileActivation, bool]:
    current = current_activation(session)
    current_sequence = current.sequence if current is not None else 0

    def same(row: SellerProfileActivation) -> bool:
        return row.action == "deactivate"

    if current is not None and same(current):
        if request.expected_activation_sequence in (current_sequence, current_sequence - 1):
            return current, False
    if request.expected_activation_sequence != current_sequence:
        raise ActivationConflict(
            "The active seller profile changed. Reload before deactivating."
        )

    row = SellerProfileActivation(
        id=uuid.uuid4(),
        sequence=current_sequence + 1,
        seller_profile_id=None,
        seller_profile_version=None,
        content_hash=None,
        action="deactivate",
        reviewed_confirmation=False,
        demo_acknowledged=False,
        actor_label=EDITOR_LABEL,
    )
    _record_activation(session, row, "seller_profile_deactivated")
    return _commit_activation(session, row, current_sequence, same)


def seller_profile_status(session: Session) -> dict:
    latest = latest_seller_profile(session)
    activation = current_activation(session)
    active = None
    if activation is not None and activation.seller_profile_id is not None:
        active = session.get(SellerProfile, activation.seller_profile_id)
    if latest is None:
        state = "missing"
    elif active is None:
        state = "draft_only"
    else:
        state = "active"
    return {
        "state": state,
        "latest_version": latest.version if latest is not None else None,
        "activation_sequence": activation.sequence if activation is not None else 0,
        "last_activation": activation,
        "active_profile": active,
        "latest_is_active": active is not None and latest is not None and active.id == latest.id,
    }
