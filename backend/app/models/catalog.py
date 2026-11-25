import enum
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class AgeCategory(enum.StrEnum):
    KIDS = "kids"
    TEENS = "teens"
    YOUTHS = "youths"
    ADULTS = "adults"
    ELDERLY = "elderly"


class Equipment(Base, TimestampMixin):
    __tablename__ = "equipment"
    __table_args__ = (CheckConstraint("price_per_day >= 0", name="ck_equipment_price_nonneg"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(220), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(80))
    price_per_day: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    inventory: Mapped["EquipmentInventory"] = relationship(
        back_populates="equipment", uselist=False, cascade="all, delete-orphan"
    )


class EquipmentInventory(Base, TimestampMixin):
    __tablename__ = "equipment_inventory"
    __table_args__ = (CheckConstraint("total_quantity >= 0", name="ck_inventory_qty_nonneg"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    equipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment.id", ondelete="CASCADE"), unique=True)
    total_quantity: Mapped[int] = mapped_column(Integer, default=0)

    equipment: Mapped[Equipment] = relationship(back_populates="inventory")


class Package(Base, TimestampMixin):
    """A bundle of equipment sold at one flat price per booking."""

    __tablename__ = "packages"
    __table_args__ = (CheckConstraint("price >= 0", name="ck_package_price_nonneg"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    age_category: Mapped[str] = mapped_column(String(20))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    items: Mapped[list["PackageItem"]] = relationship(back_populates="package", cascade="all, delete-orphan")


class PackageItem(Base):
    __tablename__ = "package_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_package_item_qty_pos"),
        UniqueConstraint("package_id", "equipment_id", name="uq_package_equipment"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    package_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("packages.id", ondelete="CASCADE"))
    equipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    package: Mapped[Package] = relationship(back_populates="items")
    equipment: Mapped[Equipment] = relationship()


class Offer(Base, TimestampMixin):
    __tablename__ = "offers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    discount_type: Mapped[str] = mapped_column(String(10), default="percent")  # percent | fixed
    discount_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class TransportProvider(Base, TimestampMixin):
    __tablename__ = "transport_providers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20))
    area: Mapped[str | None] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(20), default="driver")  # driver | boda
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SiteSetting(Base, TimestampMixin):
    __tablename__ = "site_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    value: Mapped[str] = mapped_column(Text, default="")
