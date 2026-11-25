import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.catalog import Equipment


class BookingStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    COMPLETED = "completed"


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20), index=True)
    email: Mapped[str | None] = mapped_column(String(255))


class Booking(Base, TimestampMixin):
    """end_date is INCLUSIVE: a booking for 10-12 Nov occupies the 10th, 11th and 12th."""

    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_booking_dates"),
        Index("ix_bookings_status_dates", "status", "start_date", "end_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reference: Mapped[str] = mapped_column(String(16), unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default=BookingStatus.PENDING.value)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    delivery_address: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship()
    items: Mapped[list["BookingItem"]] = relationship(back_populates="booking", cascade="all, delete-orphan")
    packages: Mapped[list["BookingPackage"]] = relationship(back_populates="booking", cascade="all, delete-orphan")
    history: Mapped[list["BookingStatusHistory"]] = relationship(
        back_populates="booking", cascade="all, delete-orphan", order_by="BookingStatusHistory.created_at"
    )
    payments: Mapped[list["Payment"]] = relationship(back_populates="booking", cascade="all, delete-orphan")


class BookingItem(Base):
    """One equipment line. Lines created by expanding a package carry package_id and price 0;
    the package's flat price is snapshotted on BookingPackage."""

    __tablename__ = "booking_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_booking_item_qty_pos"),
        Index("ix_booking_items_equipment", "equipment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    equipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    package_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("packages.id"))

    booking: Mapped[Booking] = relationship(back_populates="items")
    equipment: Mapped[Equipment] = relationship()


class BookingPackage(Base):
    __tablename__ = "booking_packages"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_booking_package_qty_pos"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    package_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("packages.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    name_snapshot: Mapped[str] = mapped_column(String(200))
    unit_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    booking: Mapped[Booking] = relationship(back_populates="packages")


class BookingStatusHistory(Base):
    __tablename__ = "booking_status_history"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    changed_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admins.id"))
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    booking: Mapped[Booking] = relationship(back_populates="history")


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_payment_amount_pos"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    method: Mapped[str] = mapped_column(String(20), default="mpesa")  # mpesa | cash | other
    reference: Mapped[str | None] = mapped_column(String(80))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    booking: Mapped[Booking] = relationship(back_populates="payments")
