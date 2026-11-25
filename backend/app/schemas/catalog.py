from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EquipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    slug: str
    description: str
    image_url: str | None
    category: str | None
    price_per_day: Decimal


class PackageItemOut(BaseModel):
    equipment_id: UUID
    name: str
    quantity: int


class PackageOut(BaseModel):
    id: UUID
    name: str
    description: str
    age_category: str
    price: Decimal
    items: list[PackageItemOut]


class OfferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    description: str
    discount_type: str
    discount_value: Decimal
    starts_on: date | None
    ends_on: date | None


class TransportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    phone: str
    area: str | None
    type: str


class DayAvailability(BaseModel):
    date: date
    available: int


# ---- admin inputs ----
class EquipmentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str = ""
    image_url: str | None = None
    category: str | None = None
    price_per_day: Decimal = Field(ge=0)
    total_quantity: int = Field(ge=0, default=1)


class EquipmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = None
    image_url: str | None = None
    category: str | None = None
    price_per_day: Decimal | None = Field(default=None, ge=0)
    is_active: bool | None = None


class InventoryUpdate(BaseModel):
    total_quantity: int = Field(ge=0)


class LoginIn(BaseModel):
    email: str
    password: str
