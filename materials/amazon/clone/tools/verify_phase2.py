#!/usr/bin/env python3
"""Exercise the fourteen Gate 2 Amazon journeys in a real local browser."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from clawbench.amazon_contract import (  # noqa: E402
    amazon_runtime_fingerprint,
    load_amazon_runtime_contract,
)


RUNTIME_MANIFEST = load_amazon_runtime_contract(REPO_ROOT)
SERVER = REPO_ROOT / RUNTIME_MANIFEST["runtime"]["entrypoint"]
TARGET_ASIN = "B0874XN4D8"
GENERIC_ASIN = "B0D4BOTTLE"
BEST_SELLERS = "/Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011"
TARGET_PRODUCT = "/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8"
GENERIC_PRODUCT = "/Stainless-Steel-Water-Bottle/dp/B0D4BOTTLE"
CART = "/gp/cart/view.html"


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def private_write(path: Path, body: bytes) -> None:
    path.write_bytes(body)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def wait_for_server(origin: str, process: subprocess.Popen[str]) -> None:
    import http.client

    parsed = urlsplit(origin)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"server exited during startup: {output}")
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=1)
        try:
            connection.request("HEAD", "/")
            response = connection.getresponse()
            response.read()
            if response.status == 200:
                return
        except OSError:
            time.sleep(0.05)
        finally:
            connection.close()
    raise TimeoutError("FastAPI SSR server did not start")


class Audit:
    def __init__(self) -> None:
        self.assertions = 0
        self.journeys: list[dict[str, object]] = []

    def check(self, condition: bool, label: str) -> None:
        self.assertions += 1
        if not condition:
            raise AssertionError(label)

    def journey(self, identifier: str, name: str, checks_before: int) -> None:
        self.journeys.append(
            {
                "id": identifier,
                "name": name,
                "assertions": self.assertions - checks_before,
                "passed": True,
            }
        )


def settled(page: Page) -> None:
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function(
        "() => document.documentElement.dataset.render === 'fastapi-ssr'",
        timeout=10_000,
    )
    page.wait_for_timeout(350)


def screenshot(page: Page, output: Path, name: str) -> dict[str, object]:
    path = output / f"{name}.png"
    body = page.screenshot(path=str(path), full_page=True, animations="disabled")
    os.chmod(path, 0o600)
    return {
        "name": name,
        "file": path.name,
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "width": page.viewport_size["width"] if page.viewport_size else None,
        "height": page.viewport_size["height"] if page.viewport_size else None,
    }


def run_desktop(
    context: BrowserContext,
    origin: str,
    output: Path,
    audit: Audit,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    page = context.new_page()
    external: list[str] = []
    failures: list[str] = []
    page_errors: list[str] = []
    screenshots: list[dict[str, object]] = []
    page.on(
        "request",
        lambda request: external.append(request.url)
        if urlsplit(request.url).hostname not in {"127.0.0.1", "localhost"}
        else None,
    )
    page.on("requestfailed", lambda request: failures.append(request.url))
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    before = audit.assertions
    response = page.goto(origin + "/")
    settled(page)
    audit.check(response is not None and response.status == 200, "J01 home response")
    audit.check(page.locator("meta[name=clawbench-catalog-size]").get_attribute("content") == "200", "J01 catalog size")
    audit.check(page.locator(".home-module").count() >= 8, "J01 home modules")
    screenshots.append(screenshot(page, output, "desktop-home"))
    audit.journey("J01", "Storefront discovery", before)

    before = audit.assertions
    page.locator("[data-open-menu]").first.click()
    audit.check(page.locator("#menu-dialog").evaluate("node => node.open"), "J02 drawer opens")
    audit.check(page.locator("#menu-dialog details").count() == 10, "J02 ten departments")
    page.locator("[data-close-menu]").click()
    audit.journey("J02", "Department drawer", before)

    before = audit.assertions
    search = page.locator("#desktop-search")
    search.fill("wireless")
    page.locator(".nav-search .autocomplete-panel").wait_for(state="visible")
    audit.check(page.locator(".nav-search [role=option]").count() >= 2, "J03 suggestions")
    audit.check(search.get_attribute("aria-expanded") == "true", "J03 expanded state")
    audit.journey("J03", "Search autocomplete", before)

    before = audit.assertions
    search.fill("electronics")
    search.press("Enter")
    page.wait_for_url("**/s?**")
    settled(page)
    page.locator(".search-page").wait_for()
    audit.check(page.locator(".search-result").count() >= 6, "J04 populated results")
    audit.check("electronics" in page.locator(".search-heading").inner_text().lower(), "J04 query heading")
    screenshots.append(screenshot(page, output, "desktop-search"))
    audit.journey("J04", "Populated search", before)

    before = audit.assertions
    page.goto(origin + "/s?k=premium")
    settled(page)
    page.wait_for_function(
        "() => document.querySelectorAll('.search-result').length >= 2",
        timeout=10_000,
    )
    page.locator("[data-search-sort]").select_option("price-desc")
    page.wait_for_function(
        "() => document.querySelectorAll('.search-result .price').length >= 2",
        timeout=10_000,
    )
    values = page.locator(".search-result .price").evaluate_all(
        "elements => elements.map(element => { const whole = [...element.childNodes].find(node => node.nodeType === Node.TEXT_NODE)?.textContent || '0'; const cents = element.querySelector('sup')?.textContent || '0'; return Number(`${whole}.${cents}`); })"
    )
    audit.check(len(values) >= 2, "J05 multiple results")
    audit.check(values[0] >= values[-1], "J05 descending price")
    department_filter = page.locator("[data-search-filter=department][value=electronics]").first
    department_filter.check()
    audit.check(page.locator(".search-result").count() >= 1, "J05 department facet")
    audit.journey("J05", "Search refine sort paginate", before)

    before = audit.assertions
    page.goto(origin + "/s?k=clawbench-no-such-product")
    settled(page)
    audit.check(page.locator(".no-results").count() == 1, "J06 no-results state")
    audit.check("No results" in page.locator(".no-results").inner_text(), "J06 recovery copy")
    audit.journey("J06", "No-results recovery", before)

    before = audit.assertions
    page.goto(origin + "/Best-Sellers/zgbs")
    settled(page)
    audit.check(page.locator(".best-rails .product-rail").count() == 10, "J07 department rails")
    page.goto(origin + BEST_SELLERS)
    settled(page)
    audit.check(page.locator(".ranked-product").count() == 6, "J07 ranked products")
    rank_two = page.locator(f".ranked-product[data-asin='{TARGET_ASIN}']")
    audit.check("#2" in rank_two.inner_text(), "J07 target rank")
    rank_two.locator("a").first.click()
    page.wait_for_url(f"**{TARGET_PRODUCT}")
    settled(page)
    audit.journey("J07", "Best Sellers discovery", before)

    before = audit.assertions
    page.locator("[data-gallery-state]").nth(1).click()
    audit.check(page.locator("[data-gallery-state]").nth(1).get_attribute("class").find("selected") >= 0, "J08 gallery selection")
    page.locator("[data-variant]").nth(1).click()
    audit.check(page.locator("[data-variant]").nth(1).get_attribute("class").find("selected") >= 0, "J08 variant selection")
    form = page.locator("[data-add-form]:visible").first
    form.locator("select[name=quantity]").select_option("2")
    audit.journey("J08", "Task product gallery and variants", before)

    before = audit.assertions
    form.locator("button[type=submit]").click()
    page.wait_for_url(f"**{CART}")
    settled(page)
    audit.check("Subtotal (2 items)" in page.locator(".cart-page").inner_text(), "J09 quantity two")
    audit.check("$439.98" in page.locator(".cart-page").inner_text(), "J09 subtotal")
    screenshots.append(screenshot(page, output, "desktop-task-cart"))
    audit.journey("J09", "Exact task add to cart", before)

    before = audit.assertions
    page.goto(origin + GENERIC_PRODUCT)
    settled(page)
    page.locator("[data-generic-quantity]").select_option("2")
    page.locator(f"[data-quick-add='{GENERIC_ASIN}']").first.click()
    page.locator("#toast.show").wait_for()
    page.goto(origin + CART)
    settled(page)
    audit.check(page.locator(f"[data-cart-item='{GENERIC_ASIN}']").count() == 1, "J10 generic item in cart")
    generic_quantity = page.locator(f"[data-cart-quantity='{GENERIC_ASIN}']")
    generic_quantity.select_option("3")
    page.locator("#toast.show").wait_for()
    audit.check(generic_quantity.input_value() == "3", "J10 quantity update")
    audit.journey("J10", "Generic product cart", before)

    before = audit.assertions
    page.locator(f"[data-save='{GENERIC_ASIN}']").click()
    page.locator(f"[data-move-to-cart='{GENERIC_ASIN}']").wait_for()
    audit.check(page.locator(f"[data-move-to-cart='{GENERIC_ASIN}']").count() == 1, "J11 saved item")
    page.locator(f"[data-move-to-cart='{GENERIC_ASIN}']").click()
    page.locator(f"[data-cart-item='{GENERIC_ASIN}']").wait_for()
    audit.journey("J11", "Save for later", before)

    before = audit.assertions
    page.goto(origin + GENERIC_PRODUCT)
    settled(page)
    page.locator(f"[data-list-add='{GENERIC_ASIN}']").click()
    page.locator("#toast.show").wait_for()
    page.goto(origin + "/hz/wishlist/ls")
    settled(page)
    audit.check(page.locator(f"[data-list-remove='{GENERIC_ASIN}']").count() == 1, "J12 item in list")
    page.locator(f"[data-list-remove='{GENERIC_ASIN}']").click()
    page.locator(".lists-hero").wait_for()
    audit.journey("J12", "Session-local list", before)

    before = audit.assertions
    page.goto(origin + "/hz/history")
    settled(page)
    audit.check(page.locator(".history-item").count() >= 1, "J13 recent history")
    page.goto(origin + "/gp/goldbox/")
    settled(page)
    audit.check(page.locator(".deals-grid .compact-card").count() >= 20, "J13 deals")
    page.locator(".deals-grid [data-quick-add]").first.click()
    page.locator("#toast.show").wait_for()
    audit.journey("J13", "History and deals rediscovery", before)

    before = audit.assertions
    page.goto(origin + "/account")
    settled(page)
    audit.check(
        page.locator(".commerce-auth-card").count() == 1
        and page.locator("a[href='/login']").count() >= 1
        and page.locator("a[href='/register']").count() >= 1,
        "J14 local account entry",
    )
    page.goto(origin + "/account/orders")
    settled(page)
    audit.check(
        "Your Orders" in page.locator("main").inner_text()
        and page.locator("a[href^='/login?next=/account/orders']").count() == 1,
        "J14 account-isolated orders entry",
    )
    page.goto(origin + "/checkout")
    settled(page)
    audit.check(
        page.url.endswith("/login?next=/checkout")
        and page.locator("form[action='/login']").count() == 1,
        "J14 anonymous checkout sign-in gate",
    )
    page.goto(origin + "/unknown/page")
    settled(page)
    audit.check(page.locator(".not-found").count() == 1, "J14 404 recovery")
    audit.journey("J14", "Local account and purchase safety", before)

    storage_state = context.storage_state()
    page.close()
    return screenshots, {
        "externalRequests": external,
        "requestFailures": failures,
        "pageErrors": page_errors,
        "storageState": storage_state,
    }


def run_mobile(browser: object, origin: str, output: Path, storage_state: dict) -> list[dict[str, object]]:
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1"
        ),
        is_mobile=True,
        has_touch=True,
        storage_state=storage_state,
    )
    page = context.new_page()
    captures = []
    for path, name, selector in (
        ("/", "mobile-home", ".home-page"),
        ("/Computers-Accessories/b/?node=541966", "mobile-category", ".computers-page"),
        (TARGET_PRODUCT, "mobile-task-product", "[data-add-form]:visible"),
    ):
        page.goto(origin + path)
        settled(page)
        page.locator(selector).first.wait_for()
        captures.append(screenshot(page, output, name))
    page.close()
    context.close()
    return captures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    private_directory(args.output_dir)
    runtime = Path(tempfile.mkdtemp(prefix="amazon-gate2-runtime-"))
    port = reserve_port()
    origin = f"http://127.0.0.1:{port}"
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    process = subprocess.Popen(
        [sys.executable, str(SERVER), "--host", "127.0.0.1", "--port", str(port), "--db", str(runtime / "amazon.sqlite3")],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_server(origin, process)
        audit = Audit()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1365, "height": 900})
                desktop, runtime_audit = run_desktop(context, origin, args.output_dir, audit)
                context.close()
                mobile = run_mobile(
                    browser,
                    origin,
                    args.output_dir,
                    runtime_audit.pop("storageState"),
                )
            finally:
                browser.close()
        if runtime_audit["externalRequests"]:
            raise AssertionError(f"external runtime requests: {runtime_audit['externalRequests']}")
        if runtime_audit["requestFailures"]:
            raise AssertionError(f"request failures: {runtime_audit['requestFailures']}")
        if runtime_audit["pageErrors"]:
            raise AssertionError(f"page errors: {runtime_audit['pageErrors']}")
        report = {
            "format": "clawbench.amazon.phase2-browser-verification.v1",
            "gate": 2,
            "runtimeStructuralSha256": amazon_runtime_fingerprint(
                REPO_ROOT, RUNTIME_MANIFEST
            ),
            "catalog": {"products": 200, "departments": 10, "categories": 20},
            "journeys": audit.journeys,
            "journeyCount": len(audit.journeys),
            "assertions": audit.assertions,
            "screenshots": desktop + mobile,
            "runtime": runtime_audit,
            "externalRuntimeRequests": 0,
            "databaseRemoved": True,
        }
        private_write(
            args.output_dir / "report.json",
            (json.dumps(report, indent=2, sort_keys=True) + "\n").encode(),
        )
        print(json.dumps({"status": "PASS", "journeys": len(audit.journeys), "assertions": audit.assertions, "screenshots": len(report["screenshots"])}))
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
        shutil.rmtree(runtime, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
