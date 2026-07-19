from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


CLONE_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = CLONE_ROOT / "static" / "site-catalog.json"
TASK_ASINS = {
    "B08HN37XC1",
    "B0874XN4D8",
    "B0CHFSWM2P",
    "B0C5JQ68FY",
    "B0BGKXX9TK",
    "B08GV9M64L",
}


def load_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def test_catalog_has_frozen_phase_two_shape() -> None:
    catalog = load_catalog()
    products = catalog["products"]
    departments = catalog["departments"]
    categories = [category for item in departments for category in item["children"]]

    assert catalog["schemaVersion"] == 2
    assert len(products) == 194
    assert len(products) + len(TASK_ASINS) == 200
    assert len(departments) == 10
    assert len(categories) == len(set(categories)) == 20
    assert len({product["asin"] for product in products}) == 194
    assert not TASK_ASINS.intersection(product["asin"] for product in products)
    assert catalog["catalogScope"] == {
        "totalProductsIncludingTaskRegression": 200,
        "genericProducts": 194,
        "taskRegressionProducts": 6,
        "departments": 10,
        "categories": 20,
        "assetPolicy": "independently-authored-local-sprites",
    }


def test_every_department_and_category_is_populated() -> None:
    catalog = load_catalog()
    department_counts = Counter(product["department"] for product in catalog["products"])
    category_counts = Counter(product["category"] for product in catalog["products"])

    for department in catalog["departments"]:
        assert department_counts[department["name"]] == department["product_count"]
        assert department_counts[department["name"]] > 0
        for category in department["children"]:
            assert category_counts[category] > 0


def test_catalog_relations_only_reference_known_products() -> None:
    catalog = load_catalog()
    known = {product["asin"] for product in catalog["products"]} | TASK_ASINS
    for group in (*catalog["homeModules"], *catalog["bestSellerRails"]):
        assert group["asins"]
        assert set(group["asins"]).issubset(known)
