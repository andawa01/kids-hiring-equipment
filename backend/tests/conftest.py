import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Load backend/.env so TEST_DATABASE_URL is picked up regardless of the CWD.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# Must be set before the app is imported so the engine points at the test database.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://partyplay:partyplay@localhost:5434/partyplay_test"
)

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal, engine
from app.core.security import hash_password
from app.models import Admin, Base, Equipment, EquipmentInventory, Package, PackageItem
from app.schemas.booking import BookingCreate

BASE_DATE = datetime.now(ZoneInfo("Africa/Nairobi")).date() + timedelta(days=30)


def D(n: int) -> date:
    """Day n of the test calendar (always in the future, so date validation passes)."""
    return BASE_DATE + timedelta(days=n)


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean():
    yield
    with engine.begin() as conn:
        names = ", ".join(t.name for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


ADMIN_ID = uuid.uuid4()
ADMIN_EMAIL = "admin@test.dev"
ADMIN_PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _admin(_clean):
    """A real admin so status-history foreign keys are satisfied."""
    with SessionLocal() as session:
        session.add(Admin(id=ADMIN_ID, email=ADMIN_EMAIL, password_hash=hash_password(ADMIN_PASSWORD)))
        session.commit()


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def make_equipment(db):
    def _make(name="Bouncing Castle", qty=5, price="5000"):
        e = Equipment(name=name, slug=name.lower().replace(" ", "-"), price_per_day=Decimal(price),
                      inventory=EquipmentInventory(total_quantity=qty))
        db.add(e)
        db.commit()
        return e
    return _make


@pytest.fixture
def make_package(db):
    def _make(name, price, contents: dict, category="kids"):
        p = Package(name=name, age_category=category, price=Decimal(price))
        for equipment, qty in contents.items():
            p.items.append(PackageItem(equipment_id=equipment.id, quantity=qty))
        db.add(p)
        db.commit()
        return p
    return _make


@pytest.fixture
def payload():
    """Build a BookingCreate. items = [(equipment, qty)], packages = [(package, qty)]."""
    counter = iter(range(1, 10_000))

    def _build(start, end, items=(), packages=(), phone=None):
        n = next(counter)
        return BookingCreate(
            start_date=start, end_date=end,
            items=[{"equipment_id": e.id, "quantity": q} for e, q in items],
            packages=[{"package_id": p.id, "quantity": q} for p, q in packages],
            customer={"name": f"Customer {n}", "phone": phone or f"0712{n:06d}"},
        )
    return _build


@pytest.fixture
def now():
    return datetime.now(UTC)
