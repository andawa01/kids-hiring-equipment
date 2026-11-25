from datetime import date
from decimal import Decimal


def rental_days(start: date, end: date) -> int:
    """Inclusive day count: 10 Nov to 12 Nov is 3 days."""
    return (end - start).days + 1


def calculate_total(
    days: int,
    equipment_lines: list[tuple[Decimal, int]],
    package_lines: list[tuple[Decimal, int]],
) -> Decimal:
    """Equipment is charged per day; a package is a flat price per booking.

    Offers/discounts are not applied yet (see TODO in the build plan, Phase 2/4).
    """
    equipment_total = sum((price * qty * days for price, qty in equipment_lines), Decimal(0))
    package_total = sum((price * qty for price, qty in package_lines), Decimal(0))
    return equipment_total + package_total
