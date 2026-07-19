"""FastAPI SSR edge for the deterministic Amazon clone.

The existing strict request engine remains an internal loopback-only domain
service. FastAPI owns the public socket, server-side rendering, static assets,
and security headers while forwarding state mutations to that engine.
"""

from __future__ import annotations

import html
import http.client
import json
import math
import re
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates


SECURITY_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-src 'none'; object-src 'none'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}


@dataclass(frozen=True)
class BridgeResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def header_values(self, name: str) -> list[str]:
        folded = name.casefold()
        return [value for key, value in self.headers if key.casefold() == folded]

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class LegacyBridge:
    """Own the strict domain engine on an unexposed ephemeral loopback port."""

    def __init__(self, legacy: Any, db_path: Path) -> None:
        self.server = legacy.AmazonThreadingServer(("127.0.0.1", 0), db_path)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="amazon-domain-engine",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(
        self,
        method: str,
        target: str,
        *,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> BridgeResponse:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        try:
            connection.request(method, target, body=body, headers=headers or {})
            raw = connection.getresponse()
            response = BridgeResponse(raw.status, tuple(raw.getheaders()), raw.read())
        finally:
            connection.close()
        return response


def e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return f"${number:,.2f}"


def product_href(product: dict[str, Any], target_asin: str, product_path: str) -> str:
    if product.get("asin") == target_asin:
        return product_path
    slug = product.get("slug") or re.sub(
        r"[^A-Za-z0-9]+", "-", str(product.get("short_title") or product.get("title"))
    ).strip("-")[:72]
    return f"/{quote(str(slug))}/dp/{quote(str(product.get('asin', '')))}"


class SSRRenderer:
    def __init__(self, legacy: Any, catalog: dict[str, Any]) -> None:
        self.legacy = legacy
        self.catalog = catalog
        self.task_products = [dict(product, source="ssd") for product in legacy.PRODUCTS]
        self.generic_products = [
            dict(product, source="marketplace") for product in catalog["products"]
        ]
        self.products = self.generic_products + self.task_products
        self.index = {product["asin"]: product for product in self.products}

    def href(self, product: dict[str, Any]) -> str:
        return product_href(
            product, self.legacy.TARGET_ASIN, self.legacy.PRODUCT_PATH
        )

    def image(self, product: dict[str, Any], class_name: str = "") -> str:
        source = product.get("source", "marketplace")
        maximum = 5 if source == "ssd" else 11
        index = max(0, min(maximum, int(product.get("sprite_index", 0))))
        base = "sprite-image" if source == "ssd" else "marketplace-image"
        prefix = "sprite" if source == "ssd" else "marketplace"
        label = e(product.get("short_title") or product.get("title"))
        return (
            f"<div class='{base} {prefix}-{index} {e(class_name)}' role='img' "
            f"aria-label='{label}'></div>"
        )

    def rating(self, product: dict[str, Any]) -> str:
        rating = float(product.get("rating", 0))
        reviews = int(product.get("reviews", 0))
        return (
            f"<div class='rating-line' aria-label='{rating:.1f} out of 5 stars, "
            f"{reviews:,} ratings'><span class='rating-value'>{rating:.1f}</span>"
            f"<span class='stars' aria-hidden='true'>★★★★★</span>"
            f"<a class='review-count' href='{self.href(product)}#reviews'>({reviews:,})</a></div>"
        )

    def price(self, product: dict[str, Any], class_name: str = "price") -> str:
        dollars, cents = f"{float(product.get('price', 0)):.2f}".split(".")
        return (
            f"<span class='{e(class_name)}'><span class='currency-symbol'>$</span>"
            f"<span class='price-whole'>{dollars}</span><span class='price-fraction'>{cents}</span></span>"
        )

    def card(
        self,
        product: dict[str, Any],
        *,
        rank: int = 0,
        quick_add: bool = False,
    ) -> str:
        rank_markup = f"<span class='rank-ribbon'>#{rank}</span>" if rank else ""
        quick_markup = (
            f"<button class='quick-add icon-button' type='button' data-quick-add='{e(product['asin'])}' "
            f"aria-label='Add {e(product.get('short_title'))} to cart'>+</button>"
            if quick_add
            else ""
        )
        old_price = (
            f"<span class='old-price'>{money(product.get('old_price'))}</span>"
            if product.get("old_price")
            else ""
        )
        prime = "<span class='prime-mark'>prime</span>" if product.get("prime", True) else ""
        return (
            f"<article class='compact-card' data-asin='{e(product['asin'])}'>{rank_markup}"
            f"<a class='compact-image-link' href='{self.href(product)}'>{self.image(product, 'compact-image')}</a>"
            f"{quick_markup}<a class='compact-title' href='{self.href(product)}'>"
            f"{e(product.get('short_title') or product.get('title'))}</a>{self.rating(product)}"
            f"<div class='compact-price'>{self.price(product)} {old_price}</div>{prime}</article>"
        )

    def rail(
        self, title: str, products: list[dict[str, Any]], *, ranked: bool = False
    ) -> str:
        cards = "".join(
            self.card(product, rank=index if ranked else 0)
            for index, product in enumerate(products, 1)
        )
        return (
            f"<section class='product-rail'><div class='section-heading'><h2>{e(title)}</h2>"
            f"<a href='/s?k={quote(title)}'>See more</a></div><div class='rail-scroller'>{cards}</div></section>"
        )

    def header(
        self,
        bootstrap: dict[str, Any],
        query: dict[str, list[str]],
    ) -> str:
        session = bootstrap.get("session", {})
        delivery = e(session.get("delivery_label", "New York 10001"))
        count = int(bootstrap.get("cart", {}).get("total_quantity", 0))
        search_query = e(query.get("k", [""])[0])
        selected = query.get("i", ["all"])[0]
        options = ["<option value='all'>All</option>"]
        for department in self.catalog["departments"]:
            slug = department["slug"]
            selection = " selected" if slug == selected else ""
            options.append(
                f"<option value='{e(slug)}'{selection}>{e(department['name'])}</option>"
            )
        search = (
            "<form class='nav-search' action='/s' method='get' role='search' data-search-form>"
            f"<select name='i' aria-label='Choose a department'>{''.join(options)}</select>"
            f"<input name='k' value='{search_query}' type='search' maxlength='160' required "
            "placeholder='Search Amazon' autocomplete='off' aria-expanded='false'>"
            "<button type='submit' aria-label='Search'>⌕</button>"
            "<div class='autocomplete-panel' role='listbox'></div></form>"
        )
        mobile_search = (
            "<form class='mobile-search' action='/s' method='get' role='search' data-search-form>"
            f"<input name='k' value='{search_query}' type='search' maxlength='160' required "
            "placeholder='Search Amazon' autocomplete='off' aria-expanded='false'>"
            f"<input type='hidden' name='i' value='{e(selected)}'>"
            "<button type='submit' aria-label='Search'>⌕</button>"
            "<div class='autocomplete-panel' role='listbox'></div></form>"
        )
        nav_links = (
            "<a href='/Best-Sellers/zgbs'>Best Sellers</a>"
            "<a href='/gp/goldbox/'>Today's Deals</a>"
            "<a href='/s?k=new+releases'>New Releases</a>"
            "<a href='/s?k=books&i=books'>Books</a>"
            "<a href='/s?k=grocery&i=grocery'>Groceries</a>"
            "<a href='/hz/wishlist/ls'>Gift Cards</a><a href='/s?k=fashion&i=fashion'>Fashion</a>"
        )
        return f"""
          <div class='desktop-nav'><div class='nav-belt'>
            <a class='amazon-logo nav-box' href='/' aria-label='Amazon home'>amazon</a>
            <a class='nav-box nav-location' href='/local-boundary?kind=delivery' data-preference='delivery'>
              <span>⌖</span><span><span class='nav-line-1'>Delivering to {delivery}</span><span class='nav-line-2'>Update location</span></span>
            </a>{search}
            <a class='nav-box nav-language' href='/local-boundary?kind=language' data-preference='language'>🇺🇸 EN</a>
            <div class='account-wrap'><a class='nav-box account-trigger' href='/account'><span><span class='nav-line-1'>Hello, sign in</span><span class='nav-line-2'>Account &amp; Lists ▾</span></span></a></div>
            <a class='nav-box' href='/account/orders'><span><span class='nav-line-1'>Returns</span><span class='nav-line-2'>&amp; Orders</span></span></a>
            <a class='nav-box nav-cart' href='/gp/cart/view.html' aria-label='Cart with {count} items'><span aria-hidden='true'>🛒</span><span class='cart-count'>{count}</span><span>Cart</span></a>
          </div><nav class='nav-main' aria-label='Primary navigation'><button class='all-menu' type='button' data-open-menu>☰ All</button>{nav_links}</nav></div>
          <div class='mobile-nav'><div class='mobile-top'><button class='icon-button' type='button' data-open-menu aria-label='Open menu'>☰</button>
            <a class='amazon-logo' href='/'>amazon</a><a class='mobile-signin' href='/account'>Sign in ›</a>
            <a class='mobile-cart-link' href='/gp/cart/view.html' aria-label='Cart with {count} items'>🛒<span class='cart-count'>{count}</span></a>
          </div>{mobile_search}<a class='mobile-location' href='/local-boundary?kind=delivery' data-preference='delivery'>⌖ Delivering to {delivery} - Update location</a></div>
        """

    def drawer(self) -> str:
        departments = []
        for department in self.catalog["departments"]:
            children = "".join(
                f"<a href='/s?k={quote(child)}&i={e(department['slug'])}'>{e(child)}</a>"
                for child in department["children"]
            )
            departments.append(
                f"<details><summary><a href='{e(department['href'])}'>{e(department['name'])}</a></summary>"
                f"<div class='drawer-children'>{children}</div></details>"
            )
        return (
            "<div class='menu-heading'><h2 id='menu-title'>Hello, sign in</h2>"
            "<button class='icon-button menu-close' type='button' data-close-menu aria-label='Close menu'>×</button></div>"
            "<nav class='drawer-nav' aria-label='All departments'><section><h3>Trending</h3>"
            "<a href='/Best-Sellers/zgbs'>Best Sellers</a><a href='/gp/goldbox/'>Today's Deals</a></section>"
            f"<section><h3>Shop by Department</h3>{''.join(departments)}</section></nav>"
        )

    def footer(self) -> str:
        return """
          <button class='back-to-top' type='button' data-back-to-top>Back to top</button>
          <div class='footer-links'>
            <section class='footer-column'><h2>Get to Know Us</h2><a href='/account?view=about'>About Amazon</a><a href='/account?view=accessibility'>Accessibility</a></section>
            <section class='footer-column'><h2>Make Money with Us</h2><a href='/account?view=sell'>Sell on Amazon</a><a href='/account?view=affiliate'>Become an Affiliate</a></section>
            <section class='footer-column'><h2>Amazon Payment Products</h2><a href='/checkout/payment'>Amazon Visa</a><a href='/hz/wishlist/ls'>Gift Cards</a></section>
            <section class='footer-column'><h2>Let Us Help You</h2><a href='/account'>Your Account</a><a href='/account/orders'>Your Orders</a><a href='/account?view=returns'>Returns &amp; Replacements</a></section>
          </div><div class='footer-base'><span>English</span><span>United States</span></div>
        """

    def home(self, bootstrap: dict[str, Any]) -> tuple[str, str]:
        modules = []
        for module in self.catalog["homeModules"]:
            products = [self.index[asin] for asin in module["asins"] if asin in self.index]
            tiles = "".join(
                f"<a href='{self.href(product)}'><span>{self.image(product, 'home-module-image')}</span>"
                f"<small>{e(product['category'])}</small></a>"
                for product in products
            )
            modules.append(
                f"<section class='home-module'><h2>{e(module['title'])}</h2>"
                f"<div class='home-module-grid'>{tiles}</div><a class='module-link' href='{e(module['href'])}'>Explore more</a></section>"
            )
        electronics = [product for product in self.products if "Electronic" in product.get("department", "")][:16]
        home = [product for product in self.generic_products if product.get("department") == "Home & Kitchen"][:16]
        content = f"""
          <section class='home-page'><div class='market-hero'><div class='market-hero-copy'>
            <h1>Everyday finds for every room</h1><p>Explore 200 deterministic products across ten departments.</p>
            <a href='/Best-Sellers/zgbs'>Shop Best Sellers</a></div>
            <div class='market-hero-products'>{''.join(self.image(product, 'hero-product-image') for product in self.generic_products[:4])}</div></div>
            <div class='home-site-content'><div class='home-module-row'>{''.join(modules[:4])}</div>
            {self.rail('Popular in electronics', electronics)}{self.rail('Home refresh favorites', home)}
            <div class='home-module-row secondary-modules'>{''.join(modules[4:8])}</div>
            {self.rail('Frequently repurchased essentials', self.generic_products[40:56])}</div></section>
        """
        return "Amazon.com. Spend less. Smile more.", content

    def best_sellers_root(self) -> tuple[str, str]:
        departments = "".join(
            f"<a href='{e(item['href'])}'>{e(item['name'])}</a>"
            for item in self.catalog["departments"]
        )
        rails = "".join(
            self.rail(
                rail["title"],
                [self.index[asin] for asin in rail["asins"] if asin in self.index],
                ranked=True,
            )
            for rail in self.catalog["bestSellerRails"]
        )
        return (
            "Amazon Best Sellers",
            f"<nav class='local-tabs'><a class='active' href='/Best-Sellers/zgbs'>Best Sellers</a><a href='/s?k=new+releases'>New Releases</a></nav>"
            f"<section class='root-best-page'><header><h1>Amazon Best Sellers</h1><p>Our most popular products based on sales. Updated frequently.</p></header>"
            f"<div class='root-best-layout'><aside class='all-departments'><h2>Any Department</h2>{departments}</aside>"
            f"<div class='best-rails'>{rails}</div></div></section>",
        )

    def task_best_sellers(self) -> tuple[str, str]:
        products = []
        for product in self.task_products:
            products.append(
                f"<article class='ranked-product' data-asin='{e(product['asin'])}'><span class='rank-ribbon'>#{product['rank']}</span>"
                f"<a class='ranked-image-link' href='{self.href(product)}'>{self.image(product, 'ranked-image')}</a>"
                f"<div class='ranked-meta'><a class='ranked-title' href='{self.href(product)}'>{e(product['title'])}</a>"
                f"{self.rating(product)}<p class='ranked-bought'>{e(product['bought'])}</p>"
                f"<div class='price-line'>{self.price(product)}<span class='old-price'>{money(product['old_price'])}</span></div></div></article>"
            )
        return (
            "Amazon Best Sellers: Best External Solid State Drives",
            "<section class='best-page ssd-best-page'><div class='best-hero'><h1>Amazon Best Sellers</h1>"
            "<p>Our most popular products based on sales. Updated frequently.</p></div>"
            "<div class='best-layout'><aside class='category-sidebar'><ul><li>Any Department</li>"
            "<li>Computers &amp; Accessories</li><li>Data Storage</li><li class='current'>External Solid State Drives</li></ul></aside>"
            f"<section class='ranked-section'><h2>Best Sellers in External Solid State Drives</h2><div class='ranked-grid'>{''.join(products)}</div>"
            "</section></div></section>",
        )

    def product_page(
        self, product: dict[str, Any], bootstrap: dict[str, Any], *, task: bool
    ) -> tuple[str, str]:
        bullets = "".join(f"<li>{e(item)}</li>" for item in product.get("bullets", []))
        session = bootstrap.get("session", {})
        variants = product.get("variants", {})
        if task:
            variants = {
                "Capacity": ["1 TB", "2 TB", "4 TB"],
                "Color": ["Titan Gray", "Blue", "Red"],
            }
        variant_blocks = []
        for label, values in variants.items():
            buttons = []
            for index, value in enumerate(values):
                selected = " selected" if index == 0 else ""
                buttons.append(
                    f"<button class='variant-option{selected}' type='button' "
                    f"data-variant='{e(value)}'>{e(value)}</button>"
                )
            variant_blocks.append(
                f"<div class='variant-block'><p>{e(label)}: "
                f"<strong>{e(values[0])}</strong></p>"
                f"<div class='variant-options'>{''.join(buttons)}</div></div>"
            )
        variant_markup = "".join(variant_blocks)
        action = "/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance"
        if task:
            purchase = f"""
              <form class='purchase-form desktop-purchase' method='post' action='{action}' data-add-form>
                <input type='hidden' name='ASIN' value='{e(product['asin'])}'><label>Quantity:
                <select name='quantity'><option value='1'>1</option><option value='2'>2</option><option value='3'>3</option></select></label>
                <button class='amazon-button amazon-button-primary' type='submit' name='submit.add-to-cart'>Add to cart</button>
              </form>
            """
        else:
            purchase = (
                f"<label>Quantity: <select data-generic-quantity><option>1</option><option>2</option><option>3</option></select></label>"
                f"<button class='amazon-button amazon-button-primary' type='button' data-quick-add='{e(product['asin'])}'>Add to cart</button>"
            )
        specs = "".join(
            f"<dt>{e(key)}</dt><dd>{e(value)}</dd>"
            for key, value in product.get("specs", {}).items()
        )
        content = f"""
          <article class='generic-pdp{' product-page' if task else ''}' data-ssr-product='{e(product['asin'])}'>
            <nav class='breadcrumbs'>{e(product.get('department', 'Computers'))} › {e(product.get('category', 'Data Storage'))} › {e(product.get('brand', 'Samsung'))}</nav>
            <div class='generic-pdp-layout'><section class='generic-gallery'><div class='generic-thumbnails'>
              <button class='thumbnail selected' type='button' data-gallery-state='main'>{self.image(product)}</button>
              <button class='thumbnail' type='button' data-gallery-state='detail'>Detail</button></div>
              <div class='generic-main-wrap'>{self.image(product, 'generic-main-image gallery-main')}</div></section>
              <section class='generic-summary'><a class='brand-link' href='/s?k={quote(str(product.get('brand', 'Samsung')))}'>Visit the {e(product.get('brand', 'Samsung'))} Store</a>
                <h1>{e(product['title'])}</h1>{self.rating(product)}<span class='choice-badge'>Amazon's <em>Choice</em></span><p>{e(product.get('bought', ''))}</p></section>
              <section class='generic-details'><div class='product-price-block'>{self.price(product, 'product-price')}<p>List Price: <del>{money(product.get('old_price'))}</del></p></div>{variant_markup}</section>
              <section class='generic-information'><dl class='fact-table'>{specs}</dl><section class='about-item'><h2>About this item</h2><ul>{bullets}</ul></section></section>
              <aside class='buy-box generic-buy-box'><div class='buy-price'>{self.price(product, 'buy-price-value')}</div>
                <div class='delivery-copy'>FREE delivery to {e(session.get('delivery_label', 'New York 10001'))}</div><strong class='stock'>{e(product.get('availability', 'In Stock'))}</strong>
                {purchase}<button class='amazon-button amazon-button-orange' type='button' data-boundary='buy-now'>Buy Now</button>
                <button class='amazon-button' type='button' data-list-add='{e(product['asin'])}'>Add to List</button></aside>
            </div>{self.rail('Customers who viewed this item also viewed', self.related(product))}<section id='reviews' class='reviews-section'><h2>Customer reviews</h2>{self.rating(product)}</section>
          </article>
        """
        return f"{product['title']} - Amazon.com", content

    def related(self, product: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            candidate
            for candidate in self.products
            if candidate["asin"] != product["asin"]
            and (
                candidate.get("department") == product.get("department")
                or candidate.get("category") == product.get("category")
            )
        ][:10]

    def search(self, query: dict[str, list[str]]) -> tuple[str, str]:
        raw = " ".join(query.get("k", [""])[0].split())[:160]
        terms = raw.casefold().split()
        department = query.get("i", ["all"])[0].casefold()
        products = self.products
        if terms:
            products = [
                product
                for product in products
                if all(
                    term
                    in " ".join(
                        str(product.get(key, ""))
                        for key in ("title", "short_title", "brand", "department", "category")
                    ).casefold()
                    for term in terms
                )
            ]
        else:
            products = []
        if department != "all":
            products = [
                product
                for product in products
                if department in str(product.get("department", "")).casefold()
                or department in str(product.get("category", "")).casefold()
            ]
        sort = query.get("s", ["featured"])[0]
        if sort == "price-asc-rank":
            products.sort(key=lambda product: float(product.get("price", 0)))
        elif sort == "price-desc-rank":
            products.sort(key=lambda product: -float(product.get("price", 0)))
        elif sort == "review-rank":
            products.sort(key=lambda product: (-float(product.get("rating", 0)), -int(product.get("reviews", 0))))
        else:
            products.sort(key=lambda product: -int(product.get("reviews", 0)))
        page_size = 16
        try:
            page = max(1, int(query.get("page", ["1"])[0]))
        except ValueError:
            page = 1
        pages = max(1, math.ceil(len(products) / page_size))
        page = min(page, pages)
        visible = products[(page - 1) * page_size : page * page_size]
        results = "".join(
            f"<article class='search-result' data-asin='{e(product['asin'])}'><a href='{self.href(product)}'>{self.image(product, 'search-result-image')}</a>"
            f"<div class='search-result-copy'><h2><a href='{self.href(product)}'>{e(product['title'])}</a></h2>{self.rating(product)}"
            f"<p class='ranked-bought'>{e(product.get('bought', ''))}</p>{self.price(product)}<span class='prime-mark'>prime</span>"
            f"<p>FREE delivery to New York 10001</p><button class='amazon-button amazon-button-primary search-quick-add' type='button' data-quick-add='{e(product['asin'])}'>Add to cart</button></div></article>"
            for product in visible
        )
        if not raw:
            results = "<div class='no-results'><h2>Enter a search term</h2><p>Use the search box to find products.</p></div>"
        elif not visible:
            results = f"<div class='no-results'><h2>No results for “{e(raw)}”</h2><p>Try checking your spelling or use more general terms.</p><a href='/Best-Sellers/zgbs'>Browse Best Sellers</a></div>"
        pagination = ""
        if pages > 1:
            links = []
            start, end = max(1, page - 2), min(pages, page + 2)
            for number in range(start, end + 1):
                params = {"k": raw, "i": department, "page": str(number)}
                current = " aria-current='page'" if number == page else ""
                links.append(
                    f"<a href='/s?{urlencode(params)}'{current}>{number}</a>"
                )
            pagination = f"<nav class='pagination' aria-label='Search results pages'>{''.join(links)}</nav>"
        filters = (
            f"<a href='/s?k={quote(raw)}&i=computers'>Computers &amp; Accessories</a>"
            + "".join(
            f"<a href='/s?k={quote(raw)}&i={e(item['slug'])}'>{e(item['name'])}</a>"
            for item in self.catalog["departments"]
            )
        )
        return (
            f"Amazon.com : {raw}" if raw else "Amazon.com Search",
            f"<section class='search-page' data-ssr-results='{len(products)}'><header class='search-heading'><div><span>{len(products)} results for</span><h1>“{e(raw)}”</h1></div>"
            f"<label>Sort by: <select data-search-sort><option>Featured</option><option>Price: Low to High</option><option>Avg. Customer Review</option></select></label></header>"
            f"<div class='search-layout'><aside class='search-filters'><h2>Department</h2>{filters}<h2>Customer Reviews</h2><span class='stars'>★★★★☆</span> &amp; Up<h2>Price</h2><a href='#'>Under $25</a><a href='#'>$25 to $50</a><a href='#'>$100 &amp; above</a></aside>"
            f"<section class='search-results-column'>{results}{pagination}</section></div></section>",
        )

    def category(self) -> tuple[str, str]:
        products = [
            product
            for product in self.products
            if product.get("department") == "Electronics"
            or product.get("category") == "Computers & Accessories"
        ][:32]
        return (
            "Computers, Tablets, & Accessories - Amazon.com",
            "<nav class='store-subnav'><a href='/Computers-Accessories/b/'>Computers</a><a href='/s?k=laptops&i=electronics'>Laptops</a><a href='/s?k=monitors&i=electronics'>Monitors</a><a href='/gp/goldbox/'>Deals</a></nav>"
            f"<section class='computers-page'><div class='computers-content'><h1>Computers, Tablets, &amp; Accessories</h1>"
            f"{self.rail('Shop top categories', products[:12])}{self.rail('Top picks for your setup', products[12:28])}</div></section>",
        )

    def deals(self) -> tuple[str, str]:
        products = [product for product in self.generic_products if product.get("deal")]
        cards = "".join(self.card(product, quick_add=True) for product in products[:48])
        departments = "".join(
            f"<a href='{e(item['href'])}'>{e(item['name'])}</a>"
            for item in self.catalog["departments"]
        )
        return (
            "Today's Deals - Amazon.com",
            "<nav class='deals-subnav'><a href='/gp/goldbox/'>Today's Deals</a><a href='/gp/goldbox/?view=coupons'>Coupons</a><a href='/gp/goldbox/?view=outlet'>Outlet</a></nav>"
            f"<section class='deals-page'><div class='deal-chips'><a href='/gp/goldbox/'>Lightning deals</a><a href='/gp/goldbox/?category=Home'>Home</a><a href='/gp/goldbox/?category=Fashion'>Fashion</a></div>"
            f"<div class='deals-layout'><aside class='deals-filters'><h2>Department</h2>{departments}</aside>"
            f"<section class='deals-grid'>{cards}</section></div></section>",
        )

    def cart(self, bootstrap: dict[str, Any]) -> tuple[str, str]:
        cart = bootstrap.get("cart", {})
        items = cart.get("items", [])
        saved = bootstrap.get("saved_for_later", [])
        if not items:
            main = (
                "<div class='empty-cart-content'><img class='empty-cart-image' src='/static/assets/empty-cart.png' alt='Empty shopping cart'>"
                "<div class='empty-cart-copy'><h1>Your Amazon Cart is empty</h1><a href='/gp/goldbox/'>Shop today's deals</a>"
                "<div class='empty-actions'><a class='amazon-button amazon-button-primary' href='/account'>Sign in to your account</a>"
                "<a class='amazon-button' href='/account?mode=register'>Sign up now</a></div></div></div>"
            )
        else:
            rows = []
            for item in items:
                product = dict(item.get("product") or self.index.get(item["asin"], {}))
                product.setdefault("source", "ssd" if product.get("asin") in self.legacy.TASK_PRODUCT_INDEX else "marketplace")
                quantity = int(item["quantity"])
                rows.append(
                    f"<article class='cart-item'><a href='{self.href(product)}'>{self.image(product, 'cart-item-image')}</a>"
                    f"<div><a class='cart-item-title' href='{self.href(product)}'>{e(product.get('title'))}</a><p class='cart-stock'>In Stock</p>"
                    f"<select class='cart-quantity' data-cart-quantity='{e(item['asin'])}'><option selected>Qty: {quantity}</option></select>"
                    f"<button class='text-action' type='button' data-remove='{e(item['asin'])}'>Delete</button>"
                    f"<button class='text-action' type='button' data-save='{e(item['asin'])}'>Save for later</button></div>"
                    f"<strong>{money(item.get('subtotal'))}</strong></article>"
                )
            main = f"<div class='cart-header'><h1>Shopping Cart</h1><span>Price</span></div>{''.join(rows)}"
        saved_markup = "".join(
            f"<article class='saved-item'><strong>{e(item['asin'])}</strong><button class='amazon-button' type='button' data-move-to-cart='{e(item['asin'])}'>Move to Cart</button></article>"
            for item in saved
        )
        quantity = int(cart.get("total_quantity", 0))
        subtotal = money(cart.get("subtotal", 0))
        summary = (
            f"<aside class='cart-summary'><p>Subtotal ({quantity} items): <strong>{subtotal}</strong></p>"
            "<a class='amazon-button amazon-button-primary' href='/checkout'>Proceed to checkout</a></aside>"
            if items
            else ""
        )
        return (
            "Amazon.com Shopping Cart",
            f"<section class='cart-page'><div class='cart-layout{' empty-cart-layout' if not items else ''}'><section class='cart-main'>{main}</section>"
            f"{summary}"
            f"</div><section class='saved-section'><h2>Saved for later</h2>{saved_markup}</section></section>",
        )

    def list_or_history(
        self, bootstrap: dict[str, Any], *, history: bool
    ) -> tuple[str, str]:
        entries = bootstrap.get("recent_views" if history else "wishlist", [])
        products = []
        for entry in entries:
            product = self.index.get(entry.get("asin")) or entry.get("product")
            if product:
                product = dict(product)
                product.setdefault("source", "marketplace")
                products.append(product)
        title = "Your Browsing History" if history else "Your Lists"
        if products:
            content = self.rail(title, products)
        else:
            content = (
                f"<div class='history-empty'><h1>{title}</h1><h2>Your {'browsing history' if history else 'shopping list'} is empty.</h2>"
                "<p>Products you view or save will appear here.</p><a class='amazon-button amazon-button-primary' href='/Best-Sellers/zgbs'>Explore Best Sellers</a></div>"
            )
        page_class = "history-page" if history else "lists-page"
        return f"{title} - Amazon.com", f"<section class='{page_class}'>{content}</section>"

    def account_page(self) -> tuple[str, str]:
        cards = (
            ("package-search", "Your Orders", "Track, return, cancel an order, download invoice or buy again", "/account/orders"),
            ("shield-check", "Login &amp; security", "Edit login, name, and mobile number", "/local-boundary?kind=account"),
            ("badge-check", "Prime", "Manage your membership, view benefits, and payment settings", "/local-boundary?kind=service"),
            ("house", "Your Addresses", "Edit, remove or set default address", "/local-boundary?kind=delivery"),
            ("briefcase-business", "Your business account", "Sign up to save with business-exclusive pricing and delivery options", "/local-boundary?kind=service"),
            ("gift", "Gift cards", "View balance or redeem a card, and purchase a new Gift Card", "/local-boundary?kind=service"),
            ("wallet-cards", "Your Payments", "View all transactions, manage payment methods and settings", "/local-boundary?kind=payment"),
            ("users-round", "Your Amazon Family", "Manage profiles, sharing, and permissions in one place", "/local-boundary?kind=service"),
            ("tablet-smartphone", "Digital Services and Device Support", "Troubleshoot device issues, manage or cancel digital subscriptions", "/local-boundary?kind=service"),
            ("list-checks", "Your Lists", "View, modify, and share your lists, or create new ones", "/hz/wishlist/ls"),
            ("headset", "Customer Service", "Browse self service options, help articles or contact us", "/account?view=help"),
            ("mail", "Your Messages", "View or respond to messages from Amazon, Sellers and Buyers", "/local-boundary?kind=service"),
        )
        card_markup = "".join(
            "<a class='account-card' href='{}'{}><span class='account-card-icon' aria-hidden='true'><i data-lucide='{}'></i></span>"
            "<span><strong>{}</strong><small>{}</small></span></a>".format(
                href,
                " data-boundary='account'" if href.endswith("kind=account") else "",
                icon,
                title,
                copy,
            )
            for icon, title, copy, href in cards
        )
        links = (
            "<div class='account-link-grid'>"
            "<section><h2>Ordering and shopping preferences</h2><a href='/local-boundary?kind=delivery'>Your Addresses</a><a href='/local-boundary?kind=payment'>Your Payments</a><a href='/hz/wishlist/ls'>Your Lists</a><a href='/hz/history'>Your browsing history</a></section>"
            "<section><h2>Digital content and devices</h2><a href='/local-boundary?kind=service'>Manage digital content</a><a href='/local-boundary?kind=service'>Digital delivery settings</a><a href='/local-boundary?kind=service'>Apps and devices</a></section>"
            "<section><h2>Memberships and subscriptions</h2><a href='/local-boundary?kind=service'>Prime membership</a><a href='/local-boundary?kind=service'>Subscriptions</a><a href='/local-boundary?kind=service'>Membership settings</a></section></div>"
        )
        return "Your Account", f"<section class='account-page'><h1>Your Account</h1><div class='account-card-grid'>{card_markup}</div>{links}</section>"

    def safe_page(self) -> tuple[str, str]:
        title = "Your Orders"
        copy = "Sign in would be required to view order history. No order or account data is collected here."
        return (
            f"{title} - Amazon.com",
            f"<section class='safe-page'><a class='amazon-logo safe-logo' href='/'>amazon</a><div class='safe-panel'>"
            f"<h1>{title}</h1><p>{copy}</p><button class='amazon-button amazon-button-primary' type='button' data-boundary='orders'>Continue</button>"
            "<a href='/'>Return to shopping</a></div></section>",
        )

    def not_found(self) -> tuple[str, str]:
        return (
            "Page Not Found - Amazon.com",
            "<section class='not-found'><div class='not-found-mark'>404</div><div><h1>Sorry, we couldn't find that page.</h1>"
            "<p>Try searching or go back to the <a href='/'>Amazon home page</a>.</p></div></section>",
        )

    def route(
        self,
        path: str,
        query: dict[str, list[str]],
        bootstrap: dict[str, Any],
        status: int,
    ) -> tuple[str, str]:
        if status == 404:
            return self.not_found()
        if path == "/":
            return self.home(bootstrap)
        if path.rstrip("/") == "/Best-Sellers/zgbs":
            return self.best_sellers_root()
        if path == self.legacy.BEST_SELLERS_PATH:
            return self.task_best_sellers()
        if path in {self.legacy.PRODUCT_PATH, self.legacy.MOBILE_PRODUCT_PATH}:
            return self.product_page(
                self.index[self.legacy.TARGET_ASIN], bootstrap, task=True
            )
        match = self.legacy.GENERIC_PDP_RE.fullmatch(path)
        if match and match.group(1) in self.index:
            return self.product_page(self.index[match.group(1)], bootstrap, task=False)
        if path == self.legacy.CART_PATH:
            return self.cart(bootstrap)
        if path == "/s":
            return self.search(query)
        if path.rstrip("/") == "/gp/goldbox":
            return self.deals()
        if path in self.legacy.COMPUTERS_CATEGORY_PATHS:
            return self.category()
        if path.startswith("/hz/wishlist"):
            return self.list_or_history(bootstrap, history=False)
        if path == "/hz/history":
            return self.list_or_history(bootstrap, history=True)
        if path == "/account/orders":
            return self.safe_page()
        if path == "/account":
            return self.account_page()
        if path.startswith(("/checkout", "/buy-now", "/local-boundary")):
            if path.startswith("/checkout"):
                return self.cart(bootstrap)
            return self.safe_page()
        return self.not_found()


def _cookie_pair(response: BridgeResponse) -> str | None:
    values = response.header_values("set-cookie")
    return values[-1].split(";", 1)[0] if values else None


def _forward_headers(request: Request, bridge: LegacyBridge) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name in ("cookie", "content-type", "accept"):
        value = request.headers.get(name)
        if value:
            headers[name.title()] = value
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            request_port = request.url.port or 80
            same_origin = (
                parsed.scheme == "http"
                and parsed.hostname == request.url.hostname
                and (parsed.port or 80) == request_port
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            same_origin = False
        headers["Origin"] = (
            f"http://127.0.0.1:{bridge.port}" if same_origin else origin
        )
    return headers


def _response_from_bridge(result: BridgeResponse) -> Response:
    content_type = result.header_values("content-type")
    response = Response(
        content=result.body,
        status_code=result.status,
        media_type=None,
    )
    if content_type:
        response.headers["Content-Type"] = content_type[-1]
    for name in ("set-cookie", "location", "allow", "retry-after"):
        for value in result.header_values(name):
            response.headers.append(name, value)
    return response


def create_app(db_path: Path, legacy: Any) -> FastAPI:
    root = Path(__file__).resolve().parent
    static_root = root / "static"
    templates = Jinja2Templates(directory=root / "templates")
    catalog = legacy.SITE_CATALOG
    renderer = SSRRenderer(legacy, catalog)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Iterator[None]:
        legacy.init_db(db_path)
        bridge = LegacyBridge(legacy, db_path)
        bridge.start()
        app.state.bridge = bridge
        try:
            yield
        finally:
            await run_in_threadpool(bridge.close)

    app = FastAPI(
        title="Amazon Local Replica",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=static_root), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        response.headers["X-ClawBench-Render"] = "fastapi-ssr"
        return response

    @app.api_route(
        "/{path:path}",
        methods=["GET", "HEAD", "POST", "PATCH", "DELETE", "PUT", "OPTIONS", "TRACE"],
    )
    async def edge(request: Request, path: str) -> Response:
        bridge: LegacyBridge = request.app.state.bridge
        query_bytes = request.scope.get("query_string", b"")
        query_text = query_bytes.decode("ascii", errors="strict")
        pathname = "/" + path
        target = pathname + (f"?{query_text}" if query_text else "")
        body = await request.body()
        headers = _forward_headers(request, bridge)
        result = await run_in_threadpool(
            bridge.request,
            request.method,
            target,
            body=body,
            headers=headers,
        )
        is_page = request.method in {"GET", "HEAD"} and not pathname.startswith("/api/")
        if not is_page:
            return _response_from_bridge(result)

        cookie = _cookie_pair(result) or request.headers.get("cookie")
        bootstrap_headers = {"Cookie": cookie} if cookie else {}
        bootstrap_result = await run_in_threadpool(
            bridge.request,
            "GET",
            "/api/bootstrap",
            headers=bootstrap_headers,
        )
        bootstrap = bootstrap_result.json() if bootstrap_result.status == 200 else {}
        query = parse_qs(query_text, keep_blank_values=True)
        title, content = renderer.route(pathname, query, bootstrap, result.status)
        response = templates.TemplateResponse(
            request=request,
            name="shell.html",
            context={
                "title": title,
                "path": pathname,
                "header_markup": renderer.header(bootstrap, query),
                "main_markup": content,
                "footer_markup": renderer.footer(),
                "drawer_markup": renderer.drawer(),
                "catalog_count": len(renderer.products),
            },
            status_code=result.status,
        )
        for source in (result, bootstrap_result):
            for value in source.header_values("set-cookie"):
                response.headers.append("set-cookie", value)
        if request.method == "HEAD":
            response.body = b""
            response.headers["content-length"] = "0"
        return response

    return app
