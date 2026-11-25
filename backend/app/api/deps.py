from fastapi import Cookie, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError
from app.core.security import COOKIE_NAME, decode_access_token
from app.models import Admin


def get_current_admin(
    db: Session = Depends(get_db),
    token: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> Admin:
    admin_id = decode_access_token(token) if token else None
    admin = db.get(Admin, admin_id) if admin_id else None
    if admin is None or not admin.is_active:
        raise AppError(401, "unauthorized", "Please log in.")
    return admin
