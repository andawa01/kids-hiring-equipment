import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.booking import normalize_ke_phone
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, D

client = TestClient(app)


def booking_body(equipment_id, qty=1, start=D(0), end=D(1), phone="0712 345 678"):
    return {
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "items": [{"equipment_id": str(equipment_id), "quantity": qty}],
        "customer": {"name": "Jane Wanjiru", "phone": phone},
        "delivery_address": "Kilimani, Nairobi",
    }


def login(c: TestClient):
    r = c.post("/api/v1/admin/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200


@pytest.mark.parametrize("raw", ["0712345678", "712345678", "254712345678", "+254 712-345-678"])
def test_phone_normalisation(raw):
    assert normalize_ke_phone(raw) == "+254712345678"


def test_bad_phone_rejected():
    with pytest.raises(ValueError):
        normalize_ke_phone("12345")


def test_catalogue_and_availability(make_equipment):
    castle = make_equipment("Bouncing Castle", qty=3)
    assert [e["name"] for e in client.get("/api/v1/equipment").json()] == ["Bouncing Castle"]
    assert client.get(f"/api/v1/equipment/{castle.slug}").status_code == 200
    r = client.get(f"/api/v1/equipment/{castle.id}/availability", params={"from": D(0), "to": D(2)})
    assert [d["available"] for d in r.json()] == [3, 3, 3]


def test_booking_flow_over_http(make_equipment):
    castle = make_equipment("Bouncing Castle", qty=1, price="5000")

    r = client.post("/api/v1/bookings", json=booking_body(castle.id))
    assert r.status_code == 201
    created = r.json()
    assert created["status"] == "pending" and created["total_amount"] == "10000.00"

    # calendar now shows the stock as held
    av = client.get(f"/api/v1/equipment/{castle.id}/availability", params={"from": D(0), "to": D(1)}).json()
    assert [d["available"] for d in av] == [0, 0]

    # a second request for the same dates is refused with a structured error
    r2 = client.post("/api/v1/bookings", json=booking_body(castle.id, phone="0722 000 111"))
    assert r2.status_code == 409
    err = r2.json()["error"]
    assert err["code"] == "insufficient_availability"
    assert err["details"][0]["available"] == 0

    # customer lookup needs reference + matching phone
    ref = created["reference"]
    assert client.get(f"/api/v1/bookings/{ref}", params={"phone": "0712345678"}).status_code == 200
    assert client.get(f"/api/v1/bookings/{ref}", params={"phone": "0799999999"}).status_code == 404


def test_validation_error_shape(make_equipment):
    castle = make_equipment()
    body = booking_body(castle.id, start=D(3), end=D(1))
    r = client.post("/api/v1/bookings", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_admin_routes_require_login():
    assert client.get("/api/v1/admin/bookings").status_code == 401
    assert client.post("/api/v1/admin/bookings/1/approve").status_code == 401


def test_wrong_password_rejected():
    r = client.post("/api/v1/admin/auth/login", json={"email": ADMIN_EMAIL, "password": "nope"})
    assert r.status_code == 401


def test_admin_approves_and_manages_equipment(make_equipment):
    castle = make_equipment("Bouncing Castle", qty=1, price="5000")
    public = TestClient(app)
    ref = public.post("/api/v1/bookings", json=booking_body(castle.id)).json()["reference"]

    admin = TestClient(app)
    login(admin)
    pending = admin.get("/api/v1/admin/bookings", params={"status": "pending"}).json()
    assert [b["reference"] for b in pending] == [ref]
    assert pending[0]["customer_phone"] == "+254712345678"

    r = admin.post(f"/api/v1/admin/bookings/{pending[0]['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert admin.post(f"/api/v1/admin/bookings/{pending[0]['id']}/reject").status_code == 409

    # price edit does not touch the existing booking's total
    assert admin.put(f"/api/v1/admin/equipment/{castle.id}", json={"price_per_day": "7000"}).status_code == 200
    assert public.get(f"/api/v1/bookings/{ref}", params={"phone": "0712345678"}).json()["total_amount"] == "10000.00"

    # new equipment and stock changes
    r = admin.post("/api/v1/admin/equipment", json={"name": "Trampoline", "price_per_day": "2500", "total_quantity": 4})
    assert r.status_code == 201 and r.json()["slug"] == "trampoline"
    inventory = admin.put(f"/api/v1/admin/inventory/{r.json()['id']}", json={"total_quantity": 6})
    assert inventory.json()["total_quantity"] == 6


def test_ids_are_random_uuids_generated_by_the_backend(make_equipment):
    castle = make_equipment("Bouncing Castle", qty=5)
    assert isinstance(castle.id, uuid.UUID) and castle.id.version == 4

    admin = TestClient(app)
    login(admin)
    ids = []
    for qty in (1, 1):
        r = client.post("/api/v1/bookings", json=booking_body(castle.id, qty=qty, phone=f"07120000{qty}{len(ids)}"))
        assert r.status_code == 201
    for b in admin.get("/api/v1/admin/bookings").json():
        parsed = uuid.UUID(b["id"])
        assert parsed.version == 4
        ids.append(parsed)
    assert len(set(ids)) == 2

    # integer ids are no longer accepted in paths
    assert admin.post("/api/v1/admin/bookings/1/approve").status_code == 422
    # an unknown (but valid) UUID is a clean 404
    assert admin.post(f"/api/v1/admin/bookings/{uuid.uuid4()}/approve").status_code == 404
