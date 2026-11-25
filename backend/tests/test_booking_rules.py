"""The Phase 3 test list from BUILD_PLAN.md."""
import threading
from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.db import SessionLocal
from app.core.errors import AppError, BookingConflict
from app.models import Booking, BookingStatus
from app.services import bookings
from tests.conftest import ADMIN_ID, D

ADMIN = ADMIN_ID


def book(db, payload, start, end, items=(), packages=(), now=None, **kw):
    return bookings.create_booking(db, payload(start, end, items, packages, **kw), now=now)


def test_exact_fit_succeeds(db, make_equipment, payload):
    castle = make_equipment(qty=5)
    b = book(db, payload, D(0), D(2), [(castle, 5)])
    assert b.status == "pending"


def test_over_request_fails_with_details(db, make_equipment, payload):
    castle = make_equipment(qty=5)
    with pytest.raises(BookingConflict) as exc:
        book(db, payload, D(0), D(2), [(castle, 6)])
    detail = exc.value.details[0]
    assert (detail["requested"], detail["available"]) == (6, 5)


def test_two_overlapping_requests_second_fails(db, make_equipment, payload):
    """The README example: 5 castles, 3 then 4 for the same dates."""
    castle = make_equipment(qty=5)
    book(db, payload, D(0), D(2), [(castle, 3)])
    with pytest.raises(BookingConflict) as exc:
        book(db, payload, D(0), D(2), [(castle, 4)])
    assert exc.value.details[0]["available"] == 2


def test_back_to_back_ranges_both_succeed(db, make_equipment, payload):
    castle = make_equipment(qty=1)
    book(db, payload, D(0), D(2), [(castle, 1)])
    book(db, payload, D(3), D(5), [(castle, 1)])  # starts the day after the first ends


def test_same_day_boundary_is_blocked_because_end_date_is_inclusive(db, make_equipment, payload):
    castle = make_equipment(qty=1)
    book(db, payload, D(0), D(2), [(castle, 1)])
    with pytest.raises(BookingConflict):
        book(db, payload, D(2), D(4), [(castle, 1)])


def test_availability_uses_peak_not_sum(db, make_equipment, payload, now):
    """Two bookings overlap the request but not each other, so only the peak (3) counts."""
    castle = make_equipment(qty=5)
    a = book(db, payload, D(0), D(1), [(castle, 3)])
    b = book(db, payload, D(2), D(3), [(castle, 3)])
    for bk in (a, b):
        bookings.approve_booking(db, bk.id, admin_id=ADMIN)

    book(db, payload, D(0), D(3), [(castle, 2)])  # 5 - peak 3 = 2 free: OK
    with pytest.raises(BookingConflict):
        book(db, payload, D(0), D(3), [(castle, 3)])  # only 0 free after the line above


def test_expired_pending_hold_stops_blocking(db, make_equipment, payload, now):
    castle = make_equipment(qty=1)
    book(db, payload, D(0), D(1), [(castle, 1)], now=now)
    later = now + timedelta(hours=3)  # default hold is 2 hours
    book(db, payload, D(0), D(1), [(castle, 1)], now=later)  # succeeds without any cleanup job


def test_rejected_and_cancelled_release_stock(db, make_equipment, payload):
    castle = make_equipment(qty=1)
    a = book(db, payload, D(0), D(1), [(castle, 1)])
    bookings.reject_booking(db, a.id, admin_id=ADMIN)
    b = book(db, payload, D(0), D(1), [(castle, 1)])
    bookings.approve_booking(db, b.id, admin_id=ADMIN)
    bookings.cancel_booking(db, b.id, admin_id=ADMIN)
    book(db, payload, D(0), D(1), [(castle, 1)])


def test_package_expands_to_equipment_quantities(db, make_equipment, make_package, payload):
    castle = make_equipment("Bouncing Castle", qty=2)
    slide = make_equipment("Slide", qty=4)
    party = make_package("Kids Party", "12000", {castle: 1, slide: 2})

    book(db, payload, D(0), D(0), packages=[(party, 2)])  # needs 2 castles + 4 slides
    with pytest.raises(BookingConflict) as exc:
        book(db, payload, D(0), D(0), [(castle, 1)])  # all castles are taken by the package
    assert exc.value.details[0]["name"] == "Bouncing Castle"


def test_package_slides_shortfall_is_reported(db, make_equipment, make_package, payload):
    castle = make_equipment("Bouncing Castle", qty=5)
    slide = make_equipment("Slide", qty=3)
    party = make_package("Kids Party", "12000", {castle: 1, slide: 2})
    with pytest.raises(BookingConflict) as exc:
        book(db, payload, D(0), D(0), packages=[(party, 2)])  # needs 4 slides, only 3
    assert [s["name"] for s in exc.value.details] == ["Slide"]


def test_prices_are_snapshotted_and_totalled(db, make_equipment, make_package, payload):
    castle = make_equipment("Bouncing Castle", qty=5, price="5000")
    slide = make_equipment("Slide", qty=5, price="2000")
    party = make_package("Kids Party", "3000", {slide: 1})

    # 3 days: castle 5000*2*3 = 30000, package 3000*1 = 3000
    b = book(db, payload, D(0), D(2), [(castle, 2)], [(party, 1)])
    assert b.total_amount == Decimal("33000")

    castle.price_per_day = Decimal("9999")
    party.price = Decimal("1")
    db.commit()
    db.refresh(b)
    assert b.total_amount == Decimal("33000")
    assert {i.unit_price_snapshot for i in b.items if i.package_id is None} == {Decimal("5000")}
    assert b.packages[0].unit_price_snapshot == Decimal("3000")


def test_approval_rechecks_availability(db, make_equipment, payload, now):
    """A request whose hold lapsed can't be approved if someone else took the stock."""
    castle = make_equipment(qty=1)
    first = book(db, payload, D(0), D(1), [(castle, 1)], now=now)
    later = now + timedelta(hours=3)
    second = book(db, payload, D(0), D(1), [(castle, 1)], now=later)
    bookings.approve_booking(db, second.id, admin_id=ADMIN, now=later)

    with pytest.raises(BookingConflict):
        bookings.approve_booking(db, first.id, admin_id=ADMIN, now=later)
    db.expire_all()
    assert db.get(Booking, first.id).status == BookingStatus.PENDING.value


def test_invalid_status_transitions(db, make_equipment, payload):
    castle = make_equipment(qty=2)
    b = book(db, payload, D(0), D(1), [(castle, 1)])
    bookings.reject_booking(db, b.id, admin_id=ADMIN)
    with pytest.raises(AppError) as exc:
        bookings.approve_booking(db, b.id, admin_id=ADMIN)
    assert exc.value.code == "invalid_status"


def test_status_history_is_recorded(db, make_equipment, payload):
    castle = make_equipment(qty=2)
    b = book(db, payload, D(0), D(1), [(castle, 1)])
    bookings.approve_booking(db, b.id, admin_id=ADMIN)
    db.refresh(b)
    assert [(h.from_status, h.to_status) for h in b.history] == [(None, "pending"), ("pending", "approved")]


def test_expire_holds_marks_lapsed_requests(db, make_equipment, payload, now):
    castle = make_equipment(qty=2)
    b = book(db, payload, D(0), D(1), [(castle, 1)], now=now)
    assert bookings.expire_holds(db, now=now) == 0
    assert bookings.expire_holds(db, now=now + timedelta(hours=3)) == 1
    db.refresh(b)
    assert b.status == "expired"


@pytest.mark.parametrize("start,end,code", [
    (D(-60), D(-58), "invalid_dates"),   # in the past
    (D(0), D(100), "invalid_dates"),     # longer than max_booking_days
])
def test_date_rules(db, make_equipment, payload, start, end, code):
    castle = make_equipment(qty=1)
    with pytest.raises(AppError) as exc:
        book(db, payload, start, end, [(castle, 1)])
    assert exc.value.code == code


def test_concurrent_requests_for_last_unit_only_one_wins(make_equipment, payload):
    castle = make_equipment(qty=1)
    attempts = 8
    barrier = threading.Barrier(attempts)
    results: list[str] = []
    lock = threading.Lock()

    def worker(i: int):
        data = payload(D(0), D(1), [(castle, 1)])
        with SessionLocal() as session:
            barrier.wait()
            try:
                bookings.create_booking(session, data)
                outcome = "ok"
            except BookingConflict:
                outcome = "conflict"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(attempts)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert results.count("ok") == 1
    assert results.count("conflict") == attempts - 1


def test_concurrent_requests_never_exceed_stock(make_equipment, payload):
    """6 castles, 10 customers each wanting 2: exactly 3 may succeed."""
    castle = make_equipment(qty=6)
    attempts = 10
    barrier = threading.Barrier(attempts)
    ok = []
    lock = threading.Lock()

    def worker():
        data = payload(D(0), D(1), [(castle, 2)])
        with SessionLocal() as session:
            barrier.wait()
            try:
                bookings.create_booking(session, data)
                with lock:
                    ok.append(1)
            except BookingConflict:
                pass

    threads = [threading.Thread(target=worker) for _ in range(attempts)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(ok) == 3
