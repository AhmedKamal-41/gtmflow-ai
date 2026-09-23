from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import SellerProfile, WorkflowEvent
from app.schemas.seller_profile import SellerProfileCreate


class ProfileVersionConflict(ValueError):
    pass


def latest_seller_profile(session: Session) -> SellerProfile | None:
    return session.scalars(
        select(SellerProfile).order_by(SellerProfile.version.desc()).limit(1)
    ).first()


def save_seller_profile(
    session: Session, request: SellerProfileCreate
) -> tuple[SellerProfile, bool]:
    profile = request.profile.model_dump(mode="json")
    canonical = json.dumps(profile, sort_keys=True, separators=(",", ":"))
    content_hash = hashlib.sha256(canonical.encode()).hexdigest()
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
        editor_label="local-demo-unauthenticated",
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
