"""Run every few minutes (cron or scheduler): python -m app.tasks.expire_holds"""
from app.core.db import SessionLocal
from app.services.bookings import expire_holds

if __name__ == "__main__":
    with SessionLocal() as db:
        print(f"Expired {expire_holds(db)} pending bookings.")
