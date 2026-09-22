from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import Lead, LeadFitScore
from app.schemas.lead import LeadRead
from app.schemas.pagination import Page
from app.scoring import fit as fit_module
from app.services.pagination import pagination_params, paginate

router = APIRouter(prefix="/api/leads", tags=["leads"])

SORT_FIT_DESC = "fit_score_desc"
SORT_FIT_ASC = "fit_score_asc"
SORT_CREATED_DESC = "created_at_desc"
VALID_SORTS = (SORT_CREATED_DESC, SORT_FIT_DESC, SORT_FIT_ASC)


def _latest_fit_subquery():
    """Latest v2 fit score per lead, current scorer/profile/config version
    only -- a window function over one indexed scan (see migration 0006's
    composite index), not a correlated subquery per lead (Part E:
    server-side sort/filter before pagination, no N+1).

    Ties on `created_at` (possible: two rows from the same bounded batch
    run can share a timestamp at whatever resolution the DB stores) are
    broken by `id` -- the same "timestamp desc, id desc" tie-breaker
    convention `paginate()` requires of every ordered list query in this
    codebase, so "latest" is deterministic and reproducible across
    identical repeated queries, not just usually-correct.
    """
    ranked = (
        select(
            LeadFitScore.lead_id.label("lead_id"),
            LeadFitScore.fit_score.label("fit_score"),
            LeadFitScore.band.label("band"),
            func.row_number()
            .over(
                partition_by=LeadFitScore.lead_id,
                order_by=(LeadFitScore.created_at.desc(), LeadFitScore.id.desc()),
            )
            .label("rn"),
        ).where(
            LeadFitScore.scorer_version == fit_module.SCORER_VERSION,
            LeadFitScore.profile_id == fit_module.PROFILE_ID,
            LeadFitScore.profile_version == fit_module.PROFILE_VERSION,
        )
    ).subquery()
    return select(ranked).where(ranked.c.rn == 1).subquery()


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

    limit, offset = pagination
    needs_fit_join = fit_band is not None or min_fit_score is not None or sort in (
        SORT_FIT_DESC, SORT_FIT_ASC,
    )
    filtering_by_fit = fit_band is not None or min_fit_score is not None

    if needs_fit_join:
        latest_fit = _latest_fit_subquery()
        join_on = Lead.id == latest_fit.c.lead_id
        # Filtering by fit requires an INNER join (an unscored lead has no
        # fit_score/band to compare against, so it's correctly excluded).
        # Sorting alone must still show unscored leads (Part E: "support
        # unscored leads") -- an OUTER join keeps them, always ordered last
        # (nullslast()) regardless of sort direction, so "ascending" and
        # "descending" both mean "among the SCORED leads," with unscored
        # ones consistently at the end rather than jumping to the front
        # for ascending.
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

        if sort == SORT_FIT_DESC:
            stmt = stmt.order_by(latest_fit.c.fit_score.desc().nullslast(), Lead.id.desc())
        elif sort == SORT_FIT_ASC:
            stmt = stmt.order_by(latest_fit.c.fit_score.asc().nullslast(), Lead.id.desc())
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
