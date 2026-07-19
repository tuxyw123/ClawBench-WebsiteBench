#!/usr/bin/env python3
"""Build the deterministic 200-product authored Amazon clone catalog."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "static" / "site-catalog.json"
TASK_PRODUCT_COUNT = 6
TOTAL_PRODUCT_COUNT = 200
GENERIC_PRODUCT_COUNT = TOTAL_PRODUCT_COUNT - TASK_PRODUCT_COUNT

DEPARTMENTS: tuple[tuple[str, str, tuple[str, str]], ...] = (
    ("Electronics", "electronics", ("Headphones", "Computers & Accessories")),
    ("Home & Kitchen", "home", ("Kitchen & Dining", "Bedding & Bath")),
    ("Fashion", "fashion", ("Women's Fashion", "Men's Fashion")),
    ("Beauty & Personal Care", "beauty", ("Skin Care", "Hair Care")),
    ("Toys & Games", "toys", ("Building Toys", "Games & Puzzles")),
    ("Books", "books", ("Literature & Fiction", "Crafts, Hobbies & Home")),
    ("Sports & Outdoors", "sports", ("Outdoor Recreation", "Fitness")),
    ("Pet Supplies", "pets", ("Dog Supplies", "Cat Supplies")),
    ("Grocery & Gourmet Food", "grocery", ("Snacks", "Coffee & Tea")),
    ("Office Products", "office", ("Office Electronics", "Writing Supplies")),
)

CATEGORY_PRODUCTS: dict[str, tuple[str, ...]] = {
    "Headphones": ("wireless earbuds", "over-ear headphones", "sports earbuds"),
    "Computers & Accessories": ("USB-C hub", "wireless mouse", "laptop stand"),
    "Kitchen & Dining": ("insulated tumbler", "air fryer", "glass food container"),
    "Bedding & Bath": ("knit throw blanket", "cotton sheet set", "bath towel set"),
    "Women's Fashion": ("running shoes", "crossbody bag", "lightweight cardigan"),
    "Men's Fashion": ("casual sneakers", "travel backpack", "everyday polo shirt"),
    "Skin Care": ("face serum", "daily moisturizer", "gentle cleanser"),
    "Hair Care": ("ionic hair dryer", "repair shampoo", "detangling brush"),
    "Building Toys": ("building block set", "magnetic tiles", "model kit"),
    "Games & Puzzles": ("family board game", "strategy card game", "jigsaw puzzle"),
    "Literature & Fiction": ("hardcover novel", "short story collection", "book club novel"),
    "Crafts, Hobbies & Home": ("creative project guide", "garden handbook", "recipe journal"),
    "Outdoor Recreation": ("day hiking pack", "camping lantern", "trail water flask"),
    "Fitness": ("exercise mat", "resistance band set", "adjustable dumbbell"),
    "Dog Supplies": ("dog walking harness", "durable chew toy", "pet travel bowl"),
    "Cat Supplies": ("cat activity tower", "interactive cat toy", "covered litter mat"),
    "Snacks": ("roasted snack mix", "fruit bar variety pack", "sea salt crackers"),
    "Coffee & Tea": ("medium roast coffee", "herbal tea collection", "cold brew blend"),
    "Office Electronics": ("label maker", "desktop calculator", "document scanner"),
    "Writing Supplies": ("gel pen set", "hardcover notebook", "desk organizer"),
}

BRANDS = (
    "Northstar",
    "Juniper & Co.",
    "Brightwell",
    "Cedar Lane",
    "Atlas Point",
    "Harbor Field",
    "Mosaic Works",
    "Silver Pine",
    "Daymark",
    "Kindred House",
)

ADJECTIVES = (
    "Everyday",
    "Compact",
    "Premium",
    "Lightweight",
    "Essential",
    "Modern",
    "Classic",
    "Comfort",
    "Versatile",
    "Travel-Ready",
)

SEED_NORMALIZATION = {
    "Home": "Home & Kitchen",
    "Beauty": "Beauty & Personal Care",
    "Toys": "Toys & Games",
    "Sports": "Sports & Outdoors",
}

CATEGORY_NORMALIZATION = {
    "Bedding": "Bedding & Bath",
    "Lighting": "Bedding & Bath",
    "Women's Shoes": "Women's Fashion",
    "Luggage": "Women's Fashion",
}


def slugify(value: str) -> str:
    return "-".join("".join(character if character.isalnum() else " " for character in value).split())


def load_seed_catalog(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    products = payload.get("products")
    if not isinstance(products, list) or len(products) < 12:
        raise ValueError("seed catalog must contain the original twelve authored products")
    return payload


def normalize_seed(product: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(product)
    normalized["department"] = SEED_NORMALIZATION.get(
        str(product["department"]), str(product["department"])
    )
    normalized["category"] = CATEGORY_NORMALIZATION.get(
        str(product["category"]), str(product["category"])
    )
    return normalized


def authored_product(index: int, department: str, category: str) -> dict[str, Any]:
    noun_options = CATEGORY_PRODUCTS[category]
    noun = noun_options[index % len(noun_options)]
    adjective = ADJECTIVES[index % len(ADJECTIVES)]
    brand = BRANDS[(index * 3) % len(BRANDS)]
    short_title = f"{adjective} {noun}"
    title = f"{brand} {short_title}, {100 + index} Series, {('Blue', 'Black', 'Natural', 'White')[index % 4]}"
    price = round(8.49 + ((index * 137) % 22100) / 100, 2)
    old_price = round(price * (1.12 + (index % 5) * 0.04), 2)
    rating = round(4.0 + (index % 9) / 10, 1)
    reviews = 317 + ((index * 1297) % 88000)
    asin = f"CB26{index:06d}"
    return {
        "asin": asin,
        "slug": slugify(title)[:72],
        "title": title,
        "short_title": short_title,
        "brand": brand,
        "department": department,
        "category": category,
        "sprite_index": index % 12,
        "price": price,
        "old_price": old_price,
        "rating": rating,
        "reviews": reviews,
        "bought": f"{1 + index % 9}K+ bought in past month",
        "prime": index % 5 != 0,
        "deal": f"{round((1 - price / old_price) * 100)}% off" if index % 3 == 0 else "",
        "availability": "In Stock",
        "variants": {
            "Color": ["Blue", "Black", "Natural", "White"],
            "Style": ["Standard", "Plus"],
        },
        "specs": {
            "Brand": brand,
            "Model": f"{100 + index} Series",
            "Category": category,
            "Warranty": "1-year limited warranty",
        },
        "bullets": [
            f"Designed for reliable everyday use in {category.lower()}.",
            "Simple setup and durable materials make it easy to use.",
            "Compact packaging includes clear care and use instructions.",
        ],
    }


def build_catalog(seed: dict[str, Any]) -> dict[str, Any]:
    products = [normalize_seed(dict(product)) for product in seed["products"][:12]]
    department_cycle = [
        (department, category)
        for department, _slug, categories in DEPARTMENTS
        for category in categories
    ]
    for index in range(1, GENERIC_PRODUCT_COUNT - len(products) + 1):
        department, category = department_cycle[(index - 1) % len(department_cycle)]
        products.append(authored_product(index, department, category))

    if len(products) != GENERIC_PRODUCT_COUNT:
        raise AssertionError(f"catalog has {len(products)} generic products")
    if len({product["asin"] for product in products}) != len(products):
        raise AssertionError("catalog ASINs must be unique")

    by_department: dict[str, list[str]] = defaultdict(list)
    by_category: dict[str, list[str]] = defaultdict(list)
    for product in products:
        by_department[product["department"]].append(product["asin"])
        by_category[product["category"]].append(product["asin"])

    departments = [
        {
            "name": department,
            "slug": slug,
            "href": f"/s?k={slug}&i={slug}",
            "children": list(categories),
            "product_count": len(by_department[department]),
        }
        for department, slug, categories in DEPARTMENTS
    ]
    home_modules = [
        {
            "title": f"Explore {department}",
            "asins": by_department[department][:4],
            "href": f"/s?k={slug}&i={slug}",
        }
        for department, slug, _categories in DEPARTMENTS[:8]
    ]
    best_seller_rails = [
        {
            "title": f"Best Sellers in {department}",
            "asins": by_department[department][:5],
        }
        for department, _slug, _categories in DEPARTMENTS
    ]
    trending = [CATEGORY_PRODUCTS[category][0] for category in CATEGORY_PRODUCTS]
    trending[0:6] = [
        "wireless earbuds",
        "summer home refresh",
        "water bottle",
        "running shoes women",
        "skin care",
        "building blocks",
    ]
    return {
        "schemaVersion": 2,
        "catalogScope": {
            "totalProductsIncludingTaskRegression": TOTAL_PRODUCT_COUNT,
            "genericProducts": GENERIC_PRODUCT_COUNT,
            "taskRegressionProducts": TASK_PRODUCT_COUNT,
            "departments": len(DEPARTMENTS),
            "categories": sum(len(item[2]) for item in DEPARTMENTS),
            "assetPolicy": "independently-authored-local-sprites",
        },
        "departments": departments,
        "trendingSearches": trending,
        "homeModules": home_modules,
        "bestSellerRails": best_seller_rails,
        "products": products,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    catalog = build_catalog(load_seed_catalog(args.seed))
    args.output.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "genericProducts": len(catalog["products"]),
                "totalProducts": len(catalog["products"]) + TASK_PRODUCT_COUNT,
                "departments": len(catalog["departments"]),
                "categories": sum(len(item["children"]) for item in catalog["departments"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
