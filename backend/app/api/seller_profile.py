from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import SellerProfile
from app.schemas.pagination import Page
from app.schemas.seller_profile import SellerProfileCreate, SellerProfileRead
from app.services.pagination import paginate, pagination_params
from app.services.seller_profiles import (
    ProfileVersionConflict,
    latest_seller_profile,
    save_seller_profile,
)

router = APIRouter(prefix="/api/seller-profile", tags=["seller-profile"])


@router.get("", response_model=SellerProfileRead)
def get_seller_profile(session: Session = Depends(get_session)) -> SellerProfile:
    row = latest_seller_profile(session)
    if row is None:
        raise HTTPException(status_code=404, detail="No seller profile has been saved yet.")
    return row


@router.post("", response_model=SellerProfileRead, status_code=201)
def post_seller_profile(
    payload: SellerProfileCreate,
    response: Response,
    session: Session = Depends(get_session),
) -> SellerProfile:
    try:
        row, created = save_seller_profile(session, payload)
    except ProfileVersionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not created:
        response.status_code = 200
    return row


@router.get("/versions", response_model=Page[SellerProfileRead])
def list_seller_profile_versions(
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> Page[SellerProfileRead]:
    limit, offset = pagination
    return paginate(
        session,
        select(SellerProfile).order_by(SellerProfile.version.desc()),
        limit=limit,
        offset=offset,
        schema=SellerProfileRead,
    )
