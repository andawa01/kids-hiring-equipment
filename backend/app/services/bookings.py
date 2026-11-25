"""Booking lifecycle: create, approve, reject, cancel, expire.

Every function owns its transaction: it commits on success and rolls back before
raising, so inventory locks are never held longer than necessary.
"""

import secrets
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.errors import AppError, BookingConflict, NotFound
from app.models import (
    Booking,
    BookingItem,
    BookingPackage,
    BookingStatus,
    BookingStatusHistory,
    Customer,
    Equipment,
    Package,
    SiteSetting,
)
from app.schemas.booking import BookingCreate
from app.services import availability, pricing

S = BookingStatus

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    S.PENDING: {S.APPROVED, S.REJECTED, S.CANCELLED, S.EXPIRED},
    S.APPROVED: {S.CANCELLED, S.COMPLETED},
    S.REJECTED: set(),
    S.CANCELLED: set(),
    S.EXPIRED: set(),
    S.COMPLETED: set(),
}

_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def _now() -> datetime:
    return datetime.now(UTC)


def get_hold_minutes(db: Session) -> int:
    value = db.scalar(select(SiteSetting.value).where(SiteSetting.key == "hold_minutes"))
    try:
        return int(value) if value else get_settings().hold_minutes
    except ValueError:
        return get_settings().hold_minutes


def _new_reference(db: Session) -> str:
    while True:
        ref = "PP-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(6))
        if not db.scalar(select(Booking.id).where(Booking.reference == ref)):
            return ref


def _add_history(db: Session, booking: Booking, from_status: str | None, to_status: str, admin_id=None, note=None):
    db.add(
        BookingStatusHistory(
            booking=booking, from_status=from_status, to_status=to_status,
            changed_by_admin_id=admin_id, note=note,
        )
    )


def _fail(db: Session, error: AppError):
    db.rollback()
    raise error


def _requested_quantities(booking: Booking) -> dict[UUID, int]:
    req: dict[UUID, int] = defaultdict(int)
    for item in booking.items:
        req[item.equipment_id] += item.quantity
    return dict(req)


def create_booking(db: Session, data: BookingCreate, *, now: datetime | None = None) -> Booking:
    settings = get_settings()
    now = now or _now()
    today = now.astimezone(ZoneInfo(settings.timezone)).date()

    # --- basic date rules -------------------------------------------------
    if data.start_date < today:
        raise AppError(422, "invalid_dates", "Start date cannot be in the past.")
    if pricing.rental_days(data.start_date, data.end_date) > settings.max_booking_days:
        raise AppError(422, "invalid_dates", f"Bookings are limited to {settings.max_booking_days} days.")

    # --- resolve equipment and packages ----------------------------------
    equipment_ids = {i.equipment_id for i in data.items}
    package_ids = {p.package_id for p in data.packages}

    equipment = {
        e.id: e
        for e in db.scalars(select(Equipment).where(Equipment.id.in_(equipment_ids), Equipment.is_active.is_(True)))
    }
    missing = equipment_ids - equipment.keys()
    if missing:
        raise AppError(422, "unknown_equipment", f"Equipment not found or inactive: {sorted(str(m) for m in missing)}")

    packages = {
        p.id: p
        for p in db.scalars(
            select(Package)
            .options(selectinload(Package.items))
            .where(Package.id.in_(package_ids), Package.is_active.is_(True))
        )
    }
    missing = package_ids - packages.keys()
    if missing:
        raise AppError(422, "unknown_package", f"Package not found or inactive: {sorted(str(m) for m in missing)}")

    # --- aggregate requested quantities (direct items + expanded packages) --
    requested: dict[UUID, int] = defaultdict(int)
    for line in data.items:
        requested[line.equipment_id] += line.quantity
    for pline in data.packages:
        for pitem in packages[pline.package_id].items:
            requested[pitem.equipment_id] += pitem.quantity * pline.quantity

    # --- availability, under row locks ------------------------------------
    shortfalls = availability.check_availability(db, dict(requested), data.start_date, data.end_date, now=now)
    if shortfalls:
        _fail(db, BookingConflict([s.as_dict() for s in shortfalls]))

    # --- customer (reuse by phone) ----------------------------------------
    customer = db.scalar(select(Customer).where(Customer.phone == data.customer.phone))
    if customer:
        customer.name = data.customer.name
        customer.email = data.customer.email or customer.email
    else:
        customer = Customer(name=data.customer.name, phone=data.customer.phone, email=data.customer.email)
        db.add(customer)

    # --- build booking with price snapshots -------------------------------
    days = pricing.rental_days(data.start_date, data.end_date)
    booking = Booking(
        reference=_new_reference(db),
        customer=customer,
        start_date=data.start_date,
        end_date=data.end_date,
        status=S.PENDING.value,
        delivery_address=data.delivery_address,
        notes=data.notes,
        expires_at=now + timedelta(minutes=get_hold_minutes(db)),
    )
    equipment_lines: list[tuple[Decimal, int]] = []
    for line in data.items:
        price = equipment[line.equipment_id].price_per_day
        booking.items.append(
            BookingItem(equipment_id=line.equipment_id, quantity=line.quantity, unit_price_snapshot=price)
        )
        equipment_lines.append((price, line.quantity))

    package_lines: list[tuple[Decimal, int]] = []
    for pline in data.packages:
        pkg = packages[pline.package_id]
        booking.packages.append(
            BookingPackage(
                package_id=pkg.id, quantity=pline.quantity,
                name_snapshot=pkg.name, unit_price_snapshot=pkg.price,
            )
        )
        package_lines.append((pkg.price, pline.quantity))
        for pitem in pkg.items:
            booking.items.append(
                BookingItem(
                    equipment_id=pitem.equipment_id,
                    quantity=pitem.quantity * pline.quantity,
                    unit_price_snapshot=Decimal(0),
                    package_id=pkg.id,
                )
            )

    booking.total_amount = pricing.calculate_total(days, equipment_lines, package_lines)
    _add_history(db, booking, None, S.PENDING.value, note="Request submitted")
    db.add(booking)
    db.commit()
    return booking


def _load_for_update(db: Session, booking_id: UUID) -> Booking:
    booking = db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())
    if booking is None:
        db.rollback()
        raise NotFound("Booking not found")
    return booking


def _transition(db: Session, booking: Booking, to: S, admin_id: UUID | None, note: str | None):
    current = booking.status
    if to not in ALLOWED_TRANSITIONS[S(current)]:
        _fail(db, AppError(409, "invalid_status", f"Cannot change a {current} booking to {to.value}."))
    booking.status = to.value
    if to != S.PENDING:
        booking.expires_at = None
    _add_history(db, booking, current, to.value, admin_id, note)


def approve_booking(db: Session, booking_id: UUID, *, admin_id: UUID, note: str | None = None,
                    now: datetime | None = None) -> Booking:
    """Re-checks availability (excluding this booking's own hold) before approving."""
    now = now or _now()
    booking = _load_for_update(db, booking_id)
    if booking.status != S.PENDING:
        _fail(db, AppError(409, "invalid_status", f"Cannot approve a {booking.status} booking."))

    shortfalls = availability.check_availability(
        db, _requested_quantities(booking), booking.start_date, booking.end_date,
        now=now, exclude_booking_id=booking.id,
    )
    if shortfalls:
        _fail(db, BookingConflict([s.as_dict() for s in shortfalls]))

    _transition(db, booking, S.APPROVED, admin_id, note)
    db.commit()
    return booking


def reject_booking(db: Session, booking_id: UUID, *, admin_id: UUID, note: str | None = None) -> Booking:
    booking = _load_for_update(db, booking_id)
    _transition(db, booking, S.REJECTED, admin_id, note)
    db.commit()
    return booking


def cancel_booking(db: Session, booking_id: UUID, *, admin_id: UUID | None = None, note: str | None = None) -> Booking:
    booking = _load_for_update(db, booking_id)
    _transition(db, booking, S.CANCELLED, admin_id, note)
    db.commit()
    return booking


def expire_holds(db: Session, *, now: datetime | None = None) -> int:
    """Mark pending bookings whose hold has lapsed as expired. Returns how many.

    Correctness does not depend on this job: availability queries already ignore
    lapsed holds. This keeps the admin's list and history tidy.
    """
    now = now or _now()
    ids = list(
        db.scalars(
            select(Booking.id)
            .where(Booking.status == S.PENDING.value, Booking.expires_at <= now)
            .with_for_update(skip_locked=True)
        )
    )
    if not ids:
        db.rollback()
        return 0
    db.execute(update(Booking).where(Booking.id.in_(ids)).values(status=S.EXPIRED.value, expires_at=None))
    for booking_id in ids:
        db.add(BookingStatusHistory(booking_id=booking_id, from_status=S.PENDING.value,
                                    to_status=S.EXPIRED.value, note="Hold expired"))
    db.commit()
    return len(ids)
