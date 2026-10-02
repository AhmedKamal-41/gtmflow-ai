from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import SellerProfile, SellerProfileActivation
from app.schemas.pagination import Page
from app.schemas.seller_profile import (
    SellerProfileActivate,
    SellerProfileActivationRead,
    SellerProfileContent,
    SellerProfileCreate,
    SellerProfileDeactivate,
    SellerProfileRead,
    SellerProfileStatus,
)
from app.services.pagination import paginate, pagination_params
from app.services.seller_profiles import (
    GTMFLOW_DEMO_PROFILE,
    ActivationConflict,
    DemoAcknowledgementRequired,
    ProfileVersionConflict,
    SellerProfileNotFound,
    activate_seller_profile,
    current_activation,
    deactivate_seller_profile,
    latest_seller_profile,
    save_seller_profile,
    seller_profile_status,
)

router = APIRouter(prefix="/api/seller-profile", tags=["seller-profile"])


def _read(row: SellerProfile, active_id) -> SellerProfileRead:
    item = SellerProfileRead.model_validate(row)
    item.status = "active" if row.id == active_id else "draft"
    return item


def _active_id(session: Session):
    activation = current_activation(session)
    return activation.seller_profile_id if activation is not None else None


@router.get("", response_model=SellerProfileRead)
def get_seller_profile(session: Session = Depends(get_session)) -> SellerProfileRead:
    row = latest_seller_profile(session)
    if row is None:
        raise HTTPException(status_code=404, detail="No seller profile has been saved yet.")
    return _read(row, _active_id(session))


@router.post("", response_model=SellerProfileRead, status_code=201)
def post_seller_profile(
    payload: SellerProfileCreate,
    response: Response,
    session: Session = Depends(get_session),
) -> SellerProfileRead:
    try:
        row, created = save_seller_profile(session, payload)
    except ProfileVersionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not created:
        response.status_code = 200
    return _read(row, _active_id(session))


@router.get("/versions", response_model=Page[SellerProfileRead])
def list_seller_profile_versions(
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> Page[SellerProfileRead]:
    limit, offset = pagination
    page = paginate(
        session,
        select(SellerProfile).order_by(SellerProfile.version.desc()),
        limit=limit,
        offset=offset,
        schema=SellerProfileRead,
    )
    active_id = _active_id(session)
    for item in page.items:
        item.status = "active" if item.id == active_id else "draft"
    return page


@router.get("/status", response_model=SellerProfileStatus)
def get_seller_profile_status(session: Session = Depends(get_session)) -> SellerProfileStatus:
    status = seller_profile_status(session)
    active = status["active_profile"]
    return SellerProfileStatus(
        state=status["state"],
        latest_version=status["latest_version"],
        activation_sequence=status["activation_sequence"],
        last_activation=(
            SellerProfileActivationRead.model_validate(status["last_activation"])
            if status["last_activation"] is not None
            else None
        ),
        active_profile=_read(active, active.id) if active is not None else None,
        latest_is_active=status["latest_is_active"],
    )


@router.get("/demonstration-template", response_model=SellerProfileContent)
def get_demonstration_template() -> SellerProfileContent:
    """The labeled GTMFlow demonstration profile, for loading into the
    editor. Returning it saves and activates nothing."""
    return GTMFLOW_DEMO_PROFILE


@router.post("/activate", response_model=SellerProfileActivationRead, status_code=201)
def post_activate_seller_profile(
    payload: SellerProfileActivate,
    response: Response,
    session: Session = Depends(get_session),
) -> SellerProfileActivation:
    try:
        row, created = activate_seller_profile(session, payload)
    except SellerProfileNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DemoAcknowledgementRequired as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ActivationConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not created:
        response.status_code = 200
    return row


@router.post("/deactivate", response_model=SellerProfileActivationRead, status_code=201)
def post_deactivate_seller_profile(
    payload: SellerProfileDeactivate,
    response: Response,
    session: Session = Depends(get_session),
) -> SellerProfileActivation:
    try:
        row, created = deactivate_seller_profile(session, payload)
    except ActivationConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not created:
        response.status_code = 200
    return row
