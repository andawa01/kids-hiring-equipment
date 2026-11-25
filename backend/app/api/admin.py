import re
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, NotFound
from app.core.security import COOKIE_NAME, create_access_token, verify_password
from app.models import Admin, Booking, Equipment, EquipmentInventory
from app.schemas.booking import AdminBookingOut, booking_to_out
from app.schemas.catalog import EquipmentCreate, EquipmentOut, EquipmentUpdate, InventoryUpdate, LoginIn
from app.services import bookings

router = APIRouter(prefix="/admin")


# ---------------- auth ----------------
@router.post("/auth/login")
def login(data: LoginIn, response: Response, db: Session = Depends(get_db)):
    admin = db.scalar(select(Admin).where(Admin.email == data.email.lower()))
    if not admin or not admin.is_active or not verify_password(data.password, admin.password_hash):
        raise AppError(401, "invalid_credentials", "Incorrect email or password.")
    response.set_cookie(
        COOKIE_NAME, create_access_token(admin.id), httponly=True,
        secure=get_settings().cookie_secure, samesite="lax",
        max_age=get_settings().jwt_expire_minutes * 60,
    )
    return {"email": admin.email}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(admin: Admin = Depends(get_current_admin)):
    return {"id": admin.id, "email": admin.email}


# ---------------- bookings ----------------
@router.get("/bookings", response_model=list[AdminBookingOut])
def list_bookings(status: str | None = None, db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)):
    stmt = select(Booking).order_by(Booking.created_at.desc())
    if status:
        stmt = stmt.where(Booking.status == status)
    return [booking_to_out(b, admin=True) for b in db.scalars(stmt)]


@router.post("/bookings/{booking_id}/approve", response_model=AdminBookingOut)
def approve(booking_id: UUID, db: Session = Depends(get_db), admin: Admin = Depends(get_current_admin)):
    return booking_to_out(bookings.approve_booking(db, booking_id, admin_id=admin.id), admin=True)


@router.post("/bookings/{booking_id}/reject", response_model=AdminBookingOut)
def reject(booking_id: UUID, db: Session = Depends(get_db), admin: Admin = Depends(get_current_admin)):
    return booking_to_out(bookings.reject_booking(db, booking_id, admin_id=admin.id), admin=True)


@router.post("/bookings/{booking_id}/cancel", response_model=AdminBookingOut)
def cancel(booking_id: UUID, db: Session = Depends(get_db), admin: Admin = Depends(get_current_admin)):
    return booking_to_out(bookings.cancel_booking(db, booking_id, admin_id=admin.id), admin=True)


# ---------------- equipment & inventory ----------------
def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


@router.get("/equipment", response_model=list[EquipmentOut])
def admin_list_equipment(db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)):
    return db.scalars(select(Equipment).order_by(Equipment.name)).all()


@router.post("/equipment", response_model=EquipmentOut, status_code=201)
def create_equipment(data: EquipmentCreate, db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)):
    slug = base = _slugify(data.name)
    n = 2
    while db.scalar(select(Equipment.id).where(Equipment.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    item = Equipment(
        name=data.name, slug=slug, description=data.description, image_url=data.image_url,
        category=data.category, price_per_day=data.price_per_day,
        inventory=EquipmentInventory(total_quantity=data.total_quantity),
    )
    db.add(item)
    db.commit()
    return item


@router.put("/equipment/{equipment_id}", response_model=EquipmentOut)
def update_equipment(
    equipment_id: UUID, data: EquipmentUpdate, db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)
):
    item = db.get(Equipment, equipment_id)
    if not item:
        raise NotFound("Equipment not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    db.commit()
    return item


@router.put("/inventory/{equipment_id}")
def update_inventory(
    equipment_id: UUID, data: InventoryUpdate, db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)
):
    inv = db.scalar(
        select(EquipmentInventory).where(EquipmentInventory.equipment_id == equipment_id).with_for_update()
    )
    if not inv:
        raise NotFound("Equipment not found")
    inv.total_quantity = data.total_quantity
    db.commit()
    return {"equipment_id": equipment_id, "total_quantity": inv.total_quantity}
