"""Load sample data for local development: python -m app.scripts.seed"""
from decimal import Decimal

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Equipment, EquipmentInventory, Offer, Package, PackageItem, SiteSetting, TransportProvider

EQUIPMENT = [
    ("Bouncing Castle (Large)", "inflatables", "6000", 4, "Fits up to 15 kids."),
    ("Bouncing Castle (Small)", "inflatables", "3500", 3, "Great for toddlers."),
    ("Slide", "inflatables", "4000", 2, "Inflatable slide with safety net."),
    ("Trampoline", "outdoor", "2500", 4, "Round trampoline with enclosure."),
    ("Face Painting Kit", "activities", "1500", 3, "Paints and brushes for a party host."),
    ("Tent (10x10)", "outdoor", "3000", 6, "Shade for guests."),
]

SETTINGS = {
    "business_name": "PartyPlay", "whatsapp": "", "instagram": "", "facebook": "",
    "tiktok": "", "email": "", "phone": "", "hold_minutes": "120",
}


def slugify(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main() -> None:
    with SessionLocal() as db:
        if db.scalar(select(Equipment.id).limit(1)):
            print("Database already has equipment; skipping seed.")
            return
        by_name = {}
        for name, cat, price, qty, desc in EQUIPMENT:
            e = Equipment(name=name, slug=slugify(name), category=cat, price_per_day=Decimal(price),
                          description=desc, inventory=EquipmentInventory(total_quantity=qty))
            db.add(e)
            by_name[name] = e
        db.flush()

        kids = Package(name="Kids Birthday Bash", age_category="kids", price=Decimal("9000"),
                       description="Castle, slide and a tent for the grown-ups.")
        for n, q in [("Bouncing Castle (Large)", 1), ("Slide", 1), ("Tent (10x10)", 1)]:
            kids.items.append(PackageItem(equipment_id=by_name[n].id, quantity=q))
        db.add(kids)

        db.add(Offer(title="First booking discount", description="10% off your first booking.",
                     discount_type="percent", discount_value=Decimal("10")))
        db.add(TransportProvider(name="Sample Driver", phone="+254700000000", area="Nairobi", type="driver"))
        for k, v in SETTINGS.items():
            db.add(SiteSetting(key=k, value=v))
        db.commit()
        print("Seeded equipment, a package, an offer, a transport contact and settings.")


if __name__ == "__main__":
    main()
