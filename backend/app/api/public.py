from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.errors import AppError, NotFound
from app.models import Booking, Equipment, Offer, Package, PackageItem, SiteSetting, TransportProvider
from app.schemas.booking import BookingCreate, BookingOut, booking_to_out, normalize_ke_phone
from app.schemas.catalog import (
    DayAvailability,
    EquipmentOut,
    OfferOut,
    PackageItemOut,
    PackageOut,
    TransportOut,
)
from app.services import availability, bookings

router = APIRouter()

PUBLIC_SETTING_KEYS = {
    "whatsapp", "instagram", "facebook", "tiktok", "email", "phone", "business_name", "address",
}


@router.get("/equipment", response_model=list[EquipmentOut])
def list_equipment(category: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Equipment).where(Equipment.is_active.is_(True)).order_by(Equipment.name)
    if category:
        stmt = stmt.where(Equipment.category == category)
    return db.scalars(stmt).all()


@router.get("/equipment/{slug}", response_model=EquipmentOut)
def get_equipment(slug: str, db: Session = Depends(get_db)):
    item = db.scalar(select(Equipment).where(Equipment.slug == slug, Equipment.is_active.is_(True)))
    if not item:
        raise NotFound("Equipment not found")
    return item


@router.get("/equipment/{equipment_id}/availability", response_model=list[DayAvailability])
def equipment_availability(
    equipment_id: UUID,
    from_: date = Query(alias="from"),
    to: date = Query(),
    db: Session = Depends(get_db),
):
    if to < from_:
        raise AppError(422, "invalid_dates", "'to' must be on or after 'from'.")
    if (to - from_).days > 120:
        raise AppError(422, "invalid_dates", "Range too large (max 120 days).")
    if not db.get(Equipment, equipment_id):
        raise NotFound("Equipment not found")
    remaining = availability.remaining_by_day(db, equipment_id, from_, to, now=datetime.now(UTC))
    return [DayAvailability(date=d, available=n) for d, n in remaining.items()]


@router.get("/packages", response_model=list[PackageOut])
def list_packages(age_category: str | None = None, db: Session = Depends(get_db)):
    stmt = (
        select(Package)
        .options(selectinload(Package.items).selectinload(PackageItem.equipment))
        .where(Package.is_active.is_(True))
        .order_by(Package.age_category, Package.name)
    )
    if age_category:
        stmt = stmt.where(Package.age_category == age_category)
    return [
        PackageOut(
            id=p.id, name=p.name, description=p.description, age_category=p.age_category, price=p.price,
            items=[PackageItemOut(equipment_id=i.equipment_id, name=i.equipment.name, quantity=i.quantity)
                   for i in p.items],
        )
        for p in db.scalars(stmt)
    ]


@router.get("/offers", response_model=list[OfferOut])
def list_offers(db: Session = Depends(get_db)):
    today = date.today()
    stmt = select(Offer).where(
        Offer.is_active.is_(True),
        (Offer.starts_on.is_(None)) | (Offer.starts_on <= today),
        (Offer.ends_on.is_(None)) | (Offer.ends_on >= today),
    )
    return db.scalars(stmt).all()


@router.get("/transport", response_model=list[TransportOut])
def list_transport(db: Session = Depends(get_db)):
    return db.scalars(select(TransportProvider).where(TransportProvider.is_active.is_(True))).all()


@router.get("/settings/public")
def public_settings(db: Session = Depends(get_db)):
    rows = db.scalars(select(SiteSetting).where(SiteSetting.key.in_(PUBLIC_SETTING_KEYS))).all()
    return {r.key: r.value for r in rows}


@router.post("/bookings", response_model=BookingOut, status_code=201)
def create_booking(data: BookingCreate, db: Session = Depends(get_db)):
    booking = bookings.create_booking(db, data)
    return booking_to_out(booking)


@router.get("/bookings/{reference}", response_model=BookingOut)
def get_booking(reference: str, phone: str = Query(), db: Session = Depends(get_db)):
    """Customers look up a booking with its reference AND their phone number."""
    try:
        phone = normalize_ke_phone(phone)
    except ValueError:
        raise NotFound("Booking not found") from None
    booking = db.scalar(select(Booking).where(Booking.reference == reference.upper()))
    if not booking or booking.customer.phone != phone:
        raise NotFound("Booking not found")
    return booking_to_out(booking)
