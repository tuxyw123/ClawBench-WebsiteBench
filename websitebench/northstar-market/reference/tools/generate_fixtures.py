"""Generate all deterministic Northstar catalog and account fixtures."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any


CATEGORIES = (
    ("outdoor", "Outdoor & Trail", "Dependable gear for days beyond the pavement.", "Lantern"),
    ("kitchen", "Kitchen & Table", "Thoughtful tools for everyday cooking and gathering.", "Kettle"),
    ("workspace", "Workspace", "Quietly capable essentials for focused work.", "Desk"),
    ("home", "Home Comfort", "Soft textures and practical details for relaxed rooms.", "Home"),
    ("travel", "Travel", "Compact companions for organized journeys.", "Compass"),
    ("wellness", "Wellness", "Simple equipment for movement, rest, and recovery.", "Balance"),
    ("audio", "Audio", "Personal listening made clear, comfortable, and portable.", "Wave"),
    ("everyday", "Everyday Carry", "Useful objects designed to earn a daily place.", "Pocket"),
)

PRODUCT_PARTS = {
    "outdoor": (
        ("Ridgeline", "Daypack"), ("Drift", "Camp Mug"), ("Solace", "Hammock"),
        ("Summit", "Trail Light"), ("Pine", "Picnic Blanket"), ("Cairn", "Trekking Set"),
    ),
    "kitchen": (
        ("Ember", "Pour-Over Set"), ("Field", "Prep Board"), ("Arc", "Chef Knife"),
        ("Morrow", "Tea Kettle"), ("Vale", "Storage Trio"), ("Gather", "Serving Bowl"),
    ),
    "workspace": (
        ("Focus", "Task Lamp"), ("Draft", "Notebook Set"), ("Calm", "Desk Mat"),
        ("Studio", "Laptop Stand"), ("Tidy", "Cable Kit"), ("Axis", "Pen Cup"),
    ),
    "home": (
        ("Cloud", "Knit Throw"), ("Hearth", "Scent Warmer"), ("Dawn", "Linen Pillow"),
        ("Still", "Bedside Tray"), ("Grove", "Planter Pair"), ("Halo", "Table Light"),
    ),
    "travel": (
        ("Waypoint", "Weekender"), ("Roam", "Packing Cubes"), ("Transit", "Tech Pouch"),
        ("Atlas", "Bottle"), ("Nomad", "Sleep Mask"), ("Gate", "Passport Folio"),
    ),
    "wellness": (
        ("Align", "Yoga Mat"), ("Recover", "Massage Set"), ("Flow", "Resistance Bands"),
        ("Breathe", "Meditation Cushion"), ("Core", "Balance Board"), ("Rest", "Eye Pillow"),
    ),
    "audio": (
        ("Pulse", "Wireless Speaker"), ("Quiet", "Over-Ear Headphones"), ("Echo", "Mini Radio"),
        ("Tempo", "Earbuds"), ("Chord", "Desktop Speaker"), ("Signal", "Travel Case"),
    ),
    "everyday": (
        ("Foundry", "Key Clip"), ("Slate", "Card Wallet"), ("Beacon", "Pocket Torch"),
        ("Ledger", "Utility Pouch"), ("Mark", "Roller Pen"), ("Pivot", "Multi Tool"),
    ),
}

BRANDS = ("Aster & Field", "Common North", "Morrow Works", "Rill Supply", "Tandem House")
PALETTES = (
    ("#E7EFEA", "#1F6B55"),
    ("#F4EBDD", "#B45A32"),
    ("#E6EAF3", "#364E8A"),
    ("#F2E7EC", "#8A3F63"),
    ("#E9EEE2", "#657536"),
    ("#EEE8F4", "#69508E"),
)


def slugify(value: str) -> str:
    return "-".join("".join(character.lower() if character.isalnum() else " " for character in value).split())


def build_fixture(seed: int, kind: str) -> dict[str, Any]:
    rng = random.Random(seed)
    categories = []
    for index, (slug, name, description, symbol) in enumerate(CATEGORIES):
        background, accent = PALETTES[(index + seed) % len(PALETTES)]
        categories.append(
            {
                "id": f"cat_{slug}",
                "slug": slug,
                "name": name,
                "description": description,
                "image": {
                    "kind": "generated-svg",
                    "key": f"category-{slug}-{seed}",
                    "background": background,
                    "accent": accent,
                },
            }
        )
    products = []
    product_index = 0
    for category_slug, *_ in CATEGORIES:
        for local_index, (adjective, noun) in enumerate(PRODUCT_PARTS[category_slug]):
            product_index += 1
            identity = f"{seed:x}{product_index:02x}"
            title = f"{adjective} {noun}"
            background, accent = PALETTES[(product_index + seed) % len(PALETTES)]
            base_price = 1299 + rng.randrange(0, 13_000, 100)
            compare_at = base_price + rng.randrange(800, 3200, 100) if product_index % 4 == 0 else None
            inventory = rng.randint(3, 28)
            products.append(
                {
                    "id": f"prod_{identity}",
                    "sku": f"NS-{seed % 10000:04d}-{product_index:03d}",
                    "slug": f"{slugify(title)}-{identity}",
                    "title": title,
                    "brand": BRANDS[(local_index + product_index + seed) % len(BRANDS)],
                    "description": (
                        f"The {title} pairs considered materials with an easy, durable design. "
                        f"Made for {category_slug} routines, it packs useful detail into a calm profile."
                    ),
                    "category_id": f"cat_{category_slug}",
                    "tags": [category_slug, adjective.casefold(), noun.casefold(), "northstar"],
                    "price_cents": base_price,
                    "compare_at_cents": compare_at,
                    "inventory": inventory,
                    "rating_basis_points": rng.randint(360, 499),
                    "review_count": rng.randint(8, 860),
                    "featured_rank": product_index,
                    "image": {
                        "kind": "generated-svg",
                        "key": f"product-{identity}",
                        "background": background,
                        "accent": accent,
                    },
                }
            )
    stock_one = None
    if kind == "concurrency":
        products[0]["inventory"] = 1
        stock_one = products[0]["id"]
    accounts = []
    names = ("Ava Chen", "Mateo Rivera", "Priya Shah", "Noah Williams")
    for index, name in enumerate(names, start=1):
        accounts.append(
            {
                "id": f"user_seed_{seed}_{index}",
                "email": f"shopper{index}.{seed}@example.test",
                "password": f"Northstar{seed}Test{index}",
                "verified": True,
                "full_name": name,
            }
        )
    return {
        "schema_version": "websitebench.fixture.v1",
        "fixture_id": f"northstar-{seed}",
        "seed": seed,
        "now": "2026-01-15T12:00:00Z",
        "catalog": {"categories": categories, "products": products},
        "accounts": accounts,
        "scenario": {
            "kind": kind,
            "content_salt": f"northstar-content-{seed}",
            "stock_one_product_id": stock_one,
        },
    }


def generate(output_root: Path) -> None:
    public_root = output_root / "public" / "fixtures"
    hidden_root = output_root / "judge" / "fixtures"
    public_root.mkdir(parents=True, exist_ok=True)
    hidden_root.mkdir(parents=True, exist_ok=True)
    seed_kinds = {
        1101: "exploration",
        1102: "smoke",
        9101: "functional",
        9102: "functional",
        9103: "functional",
        9104: "functional",
        9105: "functional",
        9199: "concurrency",
    }
    for seed, kind in seed_kinds.items():
        root = public_root if seed < 9000 else hidden_root
        path = root / f"{seed}.json"
        path.write_text(json.dumps(build_fixture(seed, kind), indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_root", type=Path)
    args = parser.parse_args()
    generate(args.output_root)


if __name__ == "__main__":
    main()

