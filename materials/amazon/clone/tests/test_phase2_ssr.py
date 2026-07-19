from __future__ import annotations

import importlib.util
import html
import sys
import warnings
from pathlib import Path

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=Warning,
)

from fastapi.testclient import TestClient


CLONE_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = CLONE_ROOT / "server.py"
FASTAPI_PATH = CLONE_ROOT / "fastapi_app.py"
TARGET_ASIN = "B0874XN4D8"
GENERIC_ASIN = "B0D4BOTTLE"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


legacy = load_module("amazon_phase2_legacy", SERVER_PATH)
fastapi_edge = load_module("amazon_phase2_fastapi", FASTAPI_PATH)


def test_fastapi_ssr_surface_matrix(tmp_path: Path) -> None:
    app = fastapi_edge.create_app(tmp_path / "amazon.sqlite3", legacy)
    routes = {
        "/": (200, "Everyday finds for every room"),
        "/s?k=electronics&i=electronics": (200, "results for"),
        "/s?k=clawbench-no-such-product": (200, "No results for"),
        "/s?k=premium&page=2&s=review-rank": (200, "Search results pages"),
        "/Best-Sellers/zgbs": (200, "Amazon Best Sellers"),
        legacy.BEST_SELLERS_PATH: (200, "Best Sellers in External Solid State Drives"),
        legacy.PRODUCT_PATH: (200, "data-ssr-product='B0874XN4D8'"),
        f"/Stainless-Steel-Water-Bottle/dp/{GENERIC_ASIN}": (
            200,
            f"data-ssr-product='{GENERIC_ASIN}'",
        ),
        "/Computers-Accessories/b/?node=541966": (200, "Computers, Tablets"),
        "/gp/goldbox/": (200, "deals-grid"),
        legacy.CART_PATH: (200, "Your Amazon Cart is empty"),
        "/hz/wishlist/ls": (200, "shopping list is empty"),
        "/hz/history": (200, "Your Browsing History"),
        "/account": (200, "Your Account"),
        "/account/orders": (200, "Your Orders"),
        "/unknown/page": (404, "couldn't find that page"),
    }
    with TestClient(app) as client:
        for route, (status, marker) in routes.items():
            response = client.get(route)
            assert response.status_code == status, route
            assert response.headers["x-clawbench-render"] == "fastapi-ssr"
            assert 'data-render="fastapi-ssr"' in response.text
            assert 'data-server-rendered="true"' in response.text
            assert marker in response.text
            assert '<meta name="clawbench-catalog-size" content="200">' in response.text
            assert "New York 10001" in response.text


def test_ssr_shell_exposes_all_departments_and_no_remote_assets(tmp_path: Path) -> None:
    app = fastapi_edge.create_app(tmp_path / "amazon.sqlite3", legacy)
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        for department in legacy.SITE_CATALOG["departments"]:
            assert html.escape(department["name"]) in response.text
        assert response.text.count("<details>") == 10
        assert "https://" not in response.text
        assert "http://" not in response.text
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["permissions-policy"].startswith("camera=()")


def test_empty_cart_has_no_checkout_affordance(tmp_path: Path) -> None:
    app = fastapi_edge.create_app(tmp_path / "amazon.sqlite3", legacy)
    with TestClient(app) as client:
        cart = client.get(legacy.CART_PATH)
        assert cart.status_code == 200
        assert "Your Amazon Cart is empty" in cart.text
        assert "Proceed to checkout" not in cart.text
        assert "class='cart-summary'" not in cart.text
    app_js = (CLONE_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "${empty ? '' : `<aside class='cart-summary'>" in app_js


def test_ssr_task_journey_preserves_exact_terminal_contract(tmp_path: Path) -> None:
    app = fastapi_edge.create_app(tmp_path / "amazon.sqlite3", legacy)
    with TestClient(app, follow_redirects=False) as client:
        assert client.get(legacy.BEST_SELLERS_PATH).status_code == 200
        product = client.get(legacy.PRODUCT_PATH)
        assert product.status_code == 200
        assert "name='ASIN' value='B0874XN4D8'" in product.text
        assert "name='quantity'" in product.text

        added = client.post(
            "/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance",
            data={"ASIN": TARGET_ASIN, "quantity": "2"},
        )
        assert added.status_code == 303
        assert added.headers["location"] == legacy.CART_PATH

        cart = client.get(legacy.CART_PATH)
        assert cart.status_code == 200
        assert "Samsung T7 Portable SSD" in cart.text
        assert "Subtotal (2 items)" in cart.text
        assert "$439.98" in cart.text


def test_fastapi_edge_keeps_api_and_method_validation(tmp_path: Path) -> None:
    app = fastapi_edge.create_app(tmp_path / "amazon.sqlite3", legacy)
    with TestClient(app) as client:
        bootstrap = client.get("/api/bootstrap")
        assert bootstrap.status_code == 200
        assert bootstrap.json()["session"]["delivery_label"] == "New York 10001"
        assert bootstrap.headers["x-clawbench-render"] == "fastapi-ssr"

        wrong_origin = client.post(
            "/api/cart/add",
            json={"asin": GENERIC_ASIN, "quantity": 1},
            headers={"Origin": "https://www.amazon.com"},
        )
        assert wrong_origin.status_code == 403
        assert wrong_origin.json()["outcome"] == "bad_origin"
        assert client.put("/api/cart/add").status_code == 405
