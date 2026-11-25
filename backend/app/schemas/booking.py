import re
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


def normalize_ke_phone(raw: str) -> str:
    """Accepts 0712345678, 712345678, 254712345678, +254 712 345 678 -> +254712345678."""
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("+"):
        digits = digits[1:]
    if digits.startswith("254"):
        national = digits[3:]
    elif digits.startswith("0"):
        national = digits[1:]
    else:
        national = digits
    if not re.fullmatch(r"[17]\d{8}", national):
        raise ValueError("Enter a valid Kenyan phone number, e.g. 0712 345 678")
    return f"+254{national}"


class CustomerIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str
    email: EmailStr | None = None

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_ke_phone(v)


class BookingItemIn(BaseModel):
    equipment_id: UUID
    quantity: int = Field(ge=1, le=50)


class BookingPackageIn(BaseModel):
    package_id: UUID
    quantity: int = Field(default=1, ge=1, le=10)


class BookingCreate(BaseModel):
    start_date: date
    end_date: date
    items: list[BookingItemIn] = []
    packages: list[BookingPackageIn] = []
    customer: CustomerIn
    delivery_address: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _check(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if not self.items and not self.packages:
            raise ValueError("Select at least one item or package")
        return self


class BookingItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    equipment_id: UUID
    name: str
    quantity: int
    unit_price: Decimal
    from_package: bool


class BookingPackageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    package_id: UUID
    name: str
    quantity: int
    unit_price: Decimal


class BookingOut(BaseModel):
    reference: str
    status: str
    start_date: date
    end_date: date
    total_amount: Decimal
    expires_at: datetime | None
    items: list[BookingItemOut]
    packages: list[BookingPackageOut]


class AdminBookingOut(BookingOut):
    id: UUID
    customer_name: str
    customer_phone: str
    customer_email: str | None
    delivery_address: str | None
    notes: str | None
    created_at: datetime


def booking_to_out(b, *, admin: bool = False) -> BookingOut | AdminBookingOut:
    base = dict(
        reference=b.reference, status=b.status, start_date=b.start_date, end_date=b.end_date,
        total_amount=b.total_amount, expires_at=b.expires_at,
        items=[
            BookingItemOut(
                equipment_id=i.equipment_id, name=i.equipment.name, quantity=i.quantity,
                unit_price=i.unit_price_snapshot, from_package=i.package_id is not None,
            )
            for i in b.items
        ],
        packages=[
            BookingPackageOut(package_id=p.package_id, name=p.name_snapshot, quantity=p.quantity,
                              unit_price=p.unit_price_snapshot)
            for p in b.packages
        ],
    )
    if not admin:
        return BookingOut(**base)
    return AdminBookingOut(
        **base, id=b.id, customer_name=b.customer.name, customer_phone=b.customer.phone,
        customer_email=b.customer.email, delivery_address=b.delivery_address, notes=b.notes,
        created_at=b.created_at,
    )
