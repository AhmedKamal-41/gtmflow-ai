from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import Lead
from app.schemas.lead import LeadRead
from app.schemas.pagination import Page
from app.scoring import fit as fit_module
from app.services.fit_queries import latest_fit_subquery
from app.services.pagination import pagination_params, paginate

router = APIRouter(prefix="/api/leads", tags=["leads"])

SORT_FIT_DESC = "fit_score_desc"
SORT_FIT_ASC = "fit_score_asc"
SORT_CREATED_DESC = "created_at_desc"
VALID_SORTS = (SORT_CREATED_DESC, SORT_FIT_DESC, SORT_FIT_ASC)
VALID_BANDS = (
    fit_module.BAND_STRONG,
    fit_module.BAND_PARTIAL,
    fit_module.BAND_WEAK,
    fit_module.BAND_INSUFFICIENT_EVIDENCE,
)


@router.get("", response_model=Page[LeadRead])
def list_leads(
    batch_id: UUID | None = None,
    fit_band: str | None = Query(
        None,
        description=(
            "Filter to leads whose latest v2 fit score has this band "
            "(strong_match/partial_match/weak_match/insufficient_evidence). "
            "Excludes leads never fit-scored."
        ),
    ),
    min_fit_score: int | None = Query(
        None, ge=0, le=100,
        description="Filter to leads whose latest v2 fit score is >= this value. Excludes leads never fit-scored.",
    ),
    sort: str = Query(SORT_CREATED_DESC, description=f"One of {VALID_SORTS}"),
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> Page[LeadRead]:
    if sort not in VALID_SORTS:
        raise HTTPException(status_code=400, detail=f"sort must be one of {VALID_SORTS}")
    if fit_band is not None and fit_band not in VALID_BANDS:
        raise HTTPException(status_code=400, detail=f"fit_band must be one of {VALID_BANDS}")

    limit, offset = pagination
    needs_fit_join = fit_band is not None or min_fit_score is not None or sort in (
        SORT_FIT_DESC, SORT_FIT_ASC,
    )
    filtering_by_fit = fit_band is not None or min_fit_score is not None

    if needs_fit_join:
        # Latest applicable fit row per lead -- the same definition every
        # fit endpoint uses (app/services/fit_queries.py).
        latest_fit = latest_fit_subquery()
        join_on = Lead.id == latest_fit.c.lead_id
        # Filtering by fit requires an INNER join (an unscored lead has no
        # fit_score/band to compare against, so it's correctly excluded).
        # Sorting alone must still show unscored leads (Part E: "support
        # unscored leads") -- an OUTER join keeps them, ordered last in
        # both directions (see the order_by below).
        stmt = (
            select(Lead).join(latest_fit, join_on)
            if filtering_by_fit
            else select(Lead).outerjoin(latest_fit, join_on)
        )
        if batch_id is not None:
            stmt = stmt.where(Lead.batch_id == batch_id)
        if fit_band is not None:
            stmt = stmt.where(latest_fit.c.band == fit_band)
        if min_fit_score is not None:
            stmt = stmt.where(latest_fit.c.fit_score >= min_fit_score)

        # Unscored leads (NULL score) sort LAST in both directions, spelled
        # out as an explicit "is null" key rather than relying on NULLS
        # LAST support/defaults, which differ between Postgres (NULLs
        # sort as largest) and SQLite (as smallest). Ties within a score
        # use the list's usual created_at/id order, so pages are stable.
        unscored_last = latest_fit.c.fit_score.is_(None).asc()
        if sort == SORT_FIT_DESC:
            stmt = stmt.order_by(
                unscored_last, latest_fit.c.fit_score.desc(),
                Lead.created_at.desc(), Lead.id.desc(),
            )
        elif sort == SORT_FIT_ASC:
            stmt = stmt.order_by(
                unscored_last, latest_fit.c.fit_score.asc(),
                Lead.created_at.desc(), Lead.id.desc(),
            )
        else:
            stmt = stmt.order_by(Lead.created_at.desc(), Lead.id.desc())
    else:
        stmt = select(Lead).order_by(Lead.created_at.desc(), Lead.id.desc())
        if batch_id is not None:
            stmt = stmt.where(Lead.batch_id == batch_id)

    return paginate(session, stmt, limit=limit, offset=offset, schema=LeadRead)


@router.get("/{lead_id}", response_model=LeadRead)
def get_lead(lead_id: UUID, session: Session = Depends(get_session)) -> Lead:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead
