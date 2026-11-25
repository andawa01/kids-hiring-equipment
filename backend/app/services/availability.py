"""Availability logic.

Rules
-----
* A booking holds stock if it is APPROVED, or PENDING with expires_at in the future.
* Dates are inclusive on both ends.
* Availability for a range is total_quantity minus the PEAK daily usage in that range
  (not the sum of overlapping bookings: two bookings that overlap the request but not
  each other must not both count against it).
* check_availability() takes a row lock on the inventory rows first, so concurrent
  requests for the same equipment are serialised and the second one sees the first
  one's committed booking.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import Booking, BookingItem, BookingStatus, Equipment, EquipmentInventory


@dataclass
class Shortfall:
    equipment_id: UUID
    name: str
    requested: int
    available: int

    def as_dict(self) -> dict:
        return {
            "equipment_id": str(self.equipment_id),
            "name": self.name,
            "requested": self.requested,
            "available": self.available,
        }


def daterange(start: date, end: date):
    """Every date from start to end, both inclusive."""
    for i in range((end - start).days + 1):
        yield start + timedelta(days=i)


def holds_stock_clause(now: datetime):
    return or_(
        Booking.status == BookingStatus.APPROVED.value,
        and_(Booking.status == BookingStatus.PENDING.value, Booking.expires_at > now),
    )


def daily_usage(
    db: Session,
    equipment_id: UUID,
    start: date,
    end: date,
    *,
    now: datetime,
    exclude_booking_id: UUID | None = None,
) -> dict[date, int]:
    """Units of one equipment item held on each day of [start, end]."""
    stmt = (
        select(Booking.start_date, Booking.end_date, BookingItem.quantity)
        .select_from(BookingItem)
        .join(Booking, Booking.id == BookingItem.booking_id)
        .where(
            BookingItem.equipment_id == equipment_id,
            Booking.start_date <= end,
            Booking.end_date >= start,
            holds_stock_clause(now),
        )
    )
    if exclude_booking_id is not None:
        stmt = stmt.where(Booking.id != exclude_booking_id)

    usage = {d: 0 for d in daterange(start, end)}
    for b_start, b_end, qty in db.execute(stmt):
        for d in daterange(max(b_start, start), min(b_end, end)):
            usage[d] += qty
    return usage


def remaining_by_day(db: Session, equipment_id: UUID, start: date, end: date, *, now: datetime) -> dict[date, int]:
    """Read-only view for the calendar. Does not lock."""
    total = db.scalar(
        select(EquipmentInventory.total_quantity).where(EquipmentInventory.equipment_id == equipment_id)
    ) or 0
    usage = daily_usage(db, equipment_id, start, end, now=now)
    return {d: max(total - used, 0) for d, used in usage.items()}


def lock_inventory(db: Session, equipment_ids: list[UUID]) -> dict[UUID, int]:
    """Lock inventory rows (sorted, to avoid deadlocks) and return {equipment_id: total}."""
    rows = db.execute(
        select(EquipmentInventory.equipment_id, EquipmentInventory.total_quantity)
        .where(EquipmentInventory.equipment_id.in_(equipment_ids))
        .order_by(EquipmentInventory.equipment_id)
        .with_for_update()
    ).all()
    return {eid: total for eid, total in rows}


def check_availability(
    db: Session,
    requested: dict[UUID, int],
    start: date,
    end: date,
    *,
    now: datetime,
    exclude_booking_id: UUID | None = None,
) -> list[Shortfall]:
    """Return a Shortfall for each item that cannot be fulfilled (empty list = all good).

    Must be called inside the transaction that will insert/approve the booking.
    """
    if not requested:
        return []
    totals = lock_inventory(db, sorted(requested))
    names = dict(db.execute(select(Equipment.id, Equipment.name).where(Equipment.id.in_(requested))).all())

    shortfalls: list[Shortfall] = []
    for equipment_id in sorted(requested):
        total = totals.get(equipment_id, 0)
        usage = daily_usage(db, equipment_id, start, end, now=now, exclude_booking_id=exclude_booking_id)
        available = max(total - max(usage.values(), default=0), 0)
        if requested[equipment_id] > available:
            shortfalls.append(
                Shortfall(equipment_id, names.get(equipment_id, "Unknown"), requested[equipment_id], available)
            )
    return shortfalls
