"""Usage: python -m app.scripts.create_admin you@example.com"""
import getpass
import sys

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import Admin


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python -m app.scripts.create_admin <email>")
    email = sys.argv[1].lower()
    password = getpass.getpass("Password (min 10 chars): ")
    if len(password) < 10:
        sys.exit("Password too short.")
    with SessionLocal() as db:
        if db.scalar(select(Admin).where(Admin.email == email)):
            sys.exit("An admin with that email already exists.")
        db.add(Admin(email=email, password_hash=hash_password(password)))
        db.commit()
    print(f"Admin {email} created.")


if __name__ == "__main__":
    main()
