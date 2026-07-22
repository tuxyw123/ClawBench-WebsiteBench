from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote_plus, urlencode

from payment_methods import DEFAULT_PAYMENT_METHOD, public_payment_methods
from product_options import T7_SOURCE_OPTIONS, option_groups_from_detail
from review_catalog import render_reviews_section
from search_catalog import (
    DEFAULT_SEARCH_DEPARTMENT,
    SOURCE_DEPARTMENTS,
    SOURCE_DEPARTMENT_BY_SLUG,
    SearchPage,
    SearchRequest,
    search_href,
    source_department_for_query,
    source_department_for_rail,
)
from store import (
    BEST_SELLERS_PATH,
    DESKTOP_TERMINAL_PATH,
    MOBILE_TERMINAL_PATH,
    PDP_PATH,
    SUPPORTED_DELIVERY_COUNTRIES,
    TARGET_ASIN,
)


SITE_NAME = "Amazon.com"
CUSTOMER_SERVICE_HREF = "/gp/help/customer/display.html?nodeId=508510"
SHIPPING_POLICIES_HREF = "/gp/help/customer/display.html?nodeId=468520"
RETURNS_REPLACEMENTS_HREF = "/gp/help/customer/display.html?nodeId=201819200"
GIFT_CARDS_HREF = "/gift-cards/b/?ie=UTF8&node=2238192011"
DELIVERY_PREFERENCE_HREF = "/gp/delivery/ajax/address-change.html"
SITE_DIRECTORY_HREF = "/gp/site-directory"
T7_TITLE = (
    "Samsung T7 Portable SSD, 1TB External Solid State Drive, Speeds Up to "
    "1,050MB/s, USB 3.2 Gen 2, Reliable Storage for Gaming, Students, "
    "Professionals, MU-PC1T0T/AM, Gray"
)


def money(minor: int | None, currency: str = "USD") -> str:
    if minor is None:
        return ""
    symbol = "$" if currency == "USD" else f"{currency} "
    return f"{symbol}{minor / 100:,.2f}"


def _quote_data_attributes(product: dict[str, Any]) -> str:
    """Embed server-resolved transaction facts without executable markup.

    JSON lives in quoted data attributes so the HTML parser decodes entities
    before the frontend reads them.  The browser must never derive a quote
    from the visible price or from option-button copy.
    """

    quote_matrix = product.get("option_quote_matrix")
    if not isinstance(quote_matrix, (list, tuple)):
        quote_matrix = []
    default_selection = product.get("default_selected_options")
    if not isinstance(default_selection, dict):
        default_selection = {}
    unavailable_copy = str(
        product.get("option_unavailable_copy")
        or "No verified offer for this selection"
    )
    quote_json = escape(
        json.dumps(quote_matrix, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        quote=True,
    )
    default_json = escape(
        json.dumps(default_selection, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        quote=True,
    )
    return (
        f'data-product-quote-matrix="{quote_json}" '
        f'data-default-selected-options="{default_json}" '
        f'data-option-unavailable-copy="{escape(unavailable_copy, quote=True)}"'
    )


def product_href(product: dict[str, Any]) -> str:
    canonical_path = product.get("canonical_path")
    if isinstance(canonical_path, str) and canonical_path.startswith("/") and not canonical_path.startswith("//"):
        return canonical_path
    slug = product.get("slug")
    if isinstance(slug, str) and slug:
        return f"/{slug}/dp/{product['asin']}"
    return f"/dp/{product['asin']}"


def product_review_href(product: dict[str, Any]) -> str:
    """Link every server-known product to its live local review surface."""

    asin = str(product.get("asin", "")).upper()
    return f"/product-reviews/{escape(asin, quote=True)}"


def amazon_logo() -> str:
    return (
        '<span class="amazon-wordmark" aria-label="Amazon">amazon'
        '<span class="amazon-smile" aria-hidden="true"></span></span>'
    )


def source_department_href(department: dict[str, Any]) -> str:
    return f'/s?i={quote_plus(str(department["slug"]))}'


def source_department_navigation(active_slug: str = "") -> str:
    """Render only real, rail-backed department destinations."""

    links = []
    for department in SOURCE_DEPARTMENTS:
        slug = str(department["slug"])
        current = ' aria-current="page"' if slug == active_slug else ""
        links.append(
            f'<a href="{escape(source_department_href(department), quote=True)}"'
            f'{current}>{escape(str(department["title"]))}</a>'
        )
    return "".join(links)


def shopping_browse_href(label: str, page_category: str = "") -> str:
    """Return a local department or search destination for PDP browse links."""

    query = " ".join(str(label).split()).strip()
    normalized = query.casefold().replace("&", "and")
    for department in SOURCE_DEPARTMENTS:
        aliases = {
            str(department["slug"]).casefold().replace("-", " "),
            str(department["title"]).casefold().replace("&", "and"),
            *(
                str(alias).casefold().replace("&", "and")
                for alias in department.get("aliases", ())
            ),
        }
        if normalized in aliases:
            return source_department_href(department)

    parameters: dict[str, str] = {"k": query or "products"}
    category = " ".join(str(page_category).split()).strip().casefold().replace("&", "and")
    for department in SOURCE_DEPARTMENTS:
        aliases = {
            str(department["title"]).casefold().replace("&", "and"),
            *(str(alias).casefold().replace("&", "and") for alias in department.get("aliases", ())),
        }
        if category in aliases:
            parameters["i"] = str(department["slug"])
            break
    return "/s?" + urlencode(parameters)


def pdp_full_view_controls(image_path: str, title: str) -> tuple[str, str]:
    """Build the keyboard-operable full-image trigger and its local dialog."""

    safe_image = escape(str(image_path), quote=True)
    safe_alt = escape(str(title), quote=True)
    trigger = (
        '<button class="full-view-link" type="button" data-pdp-full-view-open '
        'aria-controls="pdp-full-view-dialog">Click to see full view</button>'
    )
    dialog = f"""
      <dialog id="pdp-full-view-dialog" class="pdp-full-view-dialog" aria-labelledby="pdp-full-view-title">
        <div class="pdp-dialog-header"><h2 id="pdp-full-view-title">Product image</h2><button type="button" data-pdp-full-view-close aria-label="Close full product image">×</button></div>
        <div class="pdp-full-view-stage"><img data-pdp-full-view-image src="{safe_image}" alt="{safe_alt}"></div>
      </dialog>
    """
    return trigger, dialog


def secure_transaction_dialog() -> str:
    """Explain the modeled checkout boundary without implying a live processor."""

    return """
      <dialog id="pdp-secure-transaction-dialog" class="pdp-info-dialog" aria-labelledby="pdp-secure-transaction-title">
        <div class="pdp-dialog-header"><h2 id="pdp-secure-transaction-title">Secure transaction</h2><button type="button" data-pdp-info-close aria-label="Close secure transaction information">×</button></div>
        <div class="pdp-info-dialog-copy"><p>Payment details are selected during checkout. You can review the delivery address, payment method, items, and order total before placing this local test order.</p><a href="/gp/cart/view.html">Review your cart</a></div>
      </dialog>
    """


def all_menu(account_name: str | None = None) -> str:
    """Render Amazon's persistent All navigation as a usable modal drawer.

    The ordinary links remain real server destinations.  JavaScript only changes
    how the menu is revealed, so ``/gp/site-directory`` remains the no-script
    fallback for both All triggers.
    """

    short_name = account_name.strip().split()[0] if account_name and account_name.strip() else ""
    account_href = (
        "/gp/css/homepage.html"
        if short_name
        else "/ap/signin?openid.return_to=%2Fgp%2Fcss%2Fhomepage.html"
    )
    greeting = f"Hello, {escape(short_name)}" if short_name else "Hello, sign in"
    department_links = "".join(
        f'<a class="all-menu-row all-menu-row-arrow" href="{escape(source_department_href(department), quote=True)}">'
        f'{escape(str(department["title"]))}<span aria-hidden="true">›</span></a>'
        for department in SOURCE_DEPARTMENTS
    )
    auth_action = (
        '<form method="post" action="/ap/signout"><button class="all-menu-row" type="submit">Sign Out</button></form>'
        if short_name
        else '<a class="all-menu-row" href="/ap/signin?openid.return_to=%2F">Sign In</a>'
    )
    return f"""
      <div class="nav-drawer-layer" data-all-menu-root aria-hidden="true">
        <button class="nav-drawer-scrim" type="button" tabindex="-1" aria-label="Close All menu" data-all-menu-overlay></button>
        <aside id="nav-all-menu" class="nav-drawer" role="dialog" aria-modal="true" aria-labelledby="nav-all-menu-heading" tabindex="-1" data-all-menu-panel>
          <div class="all-menu-profile">
            <a href="{account_href}"><span class="all-menu-user-icon" aria-hidden="true"></span><strong id="nav-all-menu-heading">{greeting}</strong></a>
            <button class="all-menu-close" type="button" aria-label="Close All menu" data-all-menu-close>×</button>
          </div>
          <div class="all-menu-scroll">
            <section aria-labelledby="all-menu-digital-heading">
              <h2 id="all-menu-digital-heading">Digital Content &amp; Devices</h2>
              <a class="all-menu-row all-menu-row-arrow" href="/Amazon-Video/b/?ie=UTF8&amp;node=2858778011">Prime Video<span aria-hidden="true">›</span></a>
            </section>
            <section aria-labelledby="all-menu-departments-heading">
              <h2 id="all-menu-departments-heading">Shop by Department</h2>
              {department_links}
              <a class="all-menu-row" href="/gp/site-directory">See all</a>
            </section>
            <section aria-labelledby="all-menu-programs-heading">
              <h2 id="all-menu-programs-heading">Programs &amp; Features</h2>
              <a class="all-menu-row" href="/gp/goldbox/">Today's Deals</a>
              <a class="all-menu-row" href="/gift-cards/b/?ie=UTF8&amp;node=2238192011">Gift Cards</a>
              <a class="all-menu-row" href="/b/?_encoding=UTF8&amp;ld=AZUSSOA-sell&amp;node=12766669011">Sell on Amazon</a>
              <a class="all-menu-row" href="/gp/browse.html?node=16115931011">Registry</a>
            </section>
            <section aria-labelledby="all-menu-help-heading">
              <h2 id="all-menu-help-heading">Help &amp; Settings</h2>
              <a class="all-menu-row" href="/gp/css/homepage.html">Your Account</a>
              <a class="all-menu-row" href="/customer-preferences/edit?preferencesReturnUrl=%2F">🌐 English</a>
              <a class="all-menu-row" href="/gp/help/customer/display.html?nodeId=508510">Customer Service</a>
              {auth_action}
            </section>
          </div>
        </aside>
      </div>
    """


def header(
    cart_count: int,
    search_value: str = "",
    account_name: str | None = None,
    active_department: str = "aps",
) -> str:
    value = escape(search_value, quote=True)
    short_name = account_name.strip().split()[0] if account_name and account_name.strip() else ""
    desktop_greeting = f"Hello, {escape(short_name)}" if short_name else "Hello, sign in"
    mobile_greeting = f"{escape(short_name)} ›" if short_name else "Sign in ›"
    account_href = (
        "/gp/css/homepage.html"
        if short_name
        else "/ap/signin?openid.return_to=%2Fgp%2Fcss%2Fhomepage.html"
    )
    account_auth = (
        f"""
        <div class="account-flyout-signed-in">
          <strong>Welcome, {escape(short_name)}</strong>
          <form method="post" action="/ap/signout"><button type="submit">Sign out</button></form>
        </div>
        """
        if short_name
        else """
        <div class="account-flyout-auth">
          <a class="account-flyout-signin" href="/ap/signin?openid.return_to=%2Fgp%2Fcss%2Fhomepage.html">Sign in</a>
          <span>New customer? <a href="/ap/register">Start here.</a></span>
        </div>
        """
    )
    account_markup = f"""
        <div class="nav-account-wrap" data-account-menu>
          <a class="nav-account" href="{account_href}" aria-haspopup="true" aria-controls="nav-flyout-accountList" aria-expanded="false" data-account-menu-trigger>
            <span class="desktop-only"><small>{desktop_greeting}</small><strong>Account &amp; Lists<span class="nav-account-caret" aria-hidden="true"></span></strong></span>
            <span class="mobile-signin">{mobile_greeting}</span><span class="user-icon mobile-signin" aria-hidden="true"></span>
          </a>
          <div id="nav-flyout-accountList" class="account-flyout" data-account-menu-panel aria-label="Account and Lists menu" aria-hidden="true">
            <span class="account-flyout-arrow" aria-hidden="true"></span>
            {account_auth}
            <div class="account-flyout-columns">
              <section aria-labelledby="account-flyout-lists-heading">
                <h2 id="account-flyout-lists-heading">Your Lists</h2>
                <a href="/hz/wishlist/intro">Create a List</a>
                <a href="/hz/wishlist/ls">Find a List or Registry</a>
              </section>
              <section aria-labelledby="account-flyout-account-heading">
                <h2 id="account-flyout-account-heading">Your Account</h2>
                <a href="/gp/css/homepage.html">Account</a>
                <a href="/gp/css/order-history">Orders</a>
                <a href="/s?k=recommended+for+you">Recommendations</a>
                <a href="/gp/help/customer/display.html?nodeId=508510">Customer Service</a>
              </section>
            </div>
          </div>
        </div>
    """
    department_options = "".join(
        f'<option value="{escape(str(department["slug"]), quote=True)}"'
        f'{" selected" if str(department["slug"]) == active_department else ""}>'
        f'{escape(str(department["title"]))}</option>'
        for department in SOURCE_DEPARTMENTS
    )
    all_selected = " selected" if active_department in {"", "aps"} else ""
    department_links = source_department_navigation()
    return f"""
    <header class="site-header desktop-shell">
      <div class="nav-top">
        <a id="nav-hamburger-menu" class="mobile-menu" href="/gp/site-directory" aria-label="Open All Categories Menu" aria-haspopup="dialog" aria-controls="nav-all-menu" aria-expanded="false" data-all-menu-trigger>
          <span class="hamburger-icon" aria-hidden="true"></span>
        </a>
        <a class="nav-logo" href="/ref=nav_logo" aria-label="Amazon">{amazon_logo()}</a>
        <a class="nav-location desktop-only" href="/gp/delivery/ajax/address-change.html" aria-label="Deliver to Singapore">
          <span class="pin-icon" aria-hidden="true"></span>
          <span><small>Deliver to</small><strong>Singapore</strong></span>
        </a>
        <form id="nav-search-bar-form" class="nav-search" method="get" action="/s/ref=nb_sb_noss" role="search" data-search-autocomplete-form data-search-suggestions-endpoint="/search/suggestions">
          <label class="sr-only" for="twotabsearchtextbox">Search Amazon</label>
          <select class="search-department desktop-only" name="i" aria-label="Select the department you want to search in">
            <option value="aps"{all_selected}>All</option>
            {department_options}
          </select>
          <div class="nav-search-input-wrap">
            <input id="twotabsearchtextbox" name="field-keywords" value="{value}" placeholder="Search Amazon" autocomplete="off" role="combobox" aria-autocomplete="list" aria-haspopup="listbox" aria-expanded="false" aria-controls="nav-search-suggestions">
            <div id="nav-search-suggestions" class="nav-search-suggestions" role="listbox" aria-label="Search suggestions" data-search-suggestions hidden></div>
            <span class="sr-only" aria-live="polite" data-search-suggestions-status></span>
          </div>
          <button id="nav-search-submit-button" type="submit" aria-label="Go"><span class="search-icon"></span></button>
        </form>
        <a class="nav-language desktop-only" href="/customer-preferences/edit?preferencesReturnUrl=%2F"><span class="flag-us">🇺🇸</span> EN⌄</a>
        {account_markup}
        <a class="nav-orders desktop-only" href="/gp/css/order-history"><small>Returns</small><strong>&amp; Orders</strong></a>
        <a class="nav-cart" href="/gp/cart/view.html" aria-label="{cart_count} items in cart">
          <span id="nav-cart-count">{cart_count}</span><span class="cart-icon" aria-hidden="true"></span><strong class="desktop-only">Cart</strong>
        </a>
      </div>
      <nav class="nav-secondary" aria-label="Primary">
        <a href="/gp/site-directory" class="all-link" aria-haspopup="dialog" aria-controls="nav-all-menu" aria-expanded="false" data-all-menu-trigger><span class="hamburger-small"></span> All</a>
        {department_links}
        <a href="/gp/goldbox/">Today's Deals</a>
        <a href="/Amazon-Video/b/?ie=UTF8&amp;node=2858778011">Prime Video</a>
        <a href="/gift-cards/b/?ie=UTF8&amp;node=2238192011">Gift Cards</a>
        <a href="/b/?_encoding=UTF8&amp;ld=AZUSSOA-sell&amp;node=12766669011">Sell</a>
        <a href="/gp/help/customer/display.html?nodeId=508510">Customer Service</a>
        <a href="/gp/browse.html?node=16115931011">Registry</a>
      </nav>
      <a class="mobile-location" href="/gp/delivery/ajax/address-change.html"><span class="pin-icon"></span> Delivering to Singapore</a>
      {all_menu(account_name)}
    </header>
    """


def footer(account_name: str | None = None) -> str:
    signin_link = (
        ""
        if account_name
        else '<a class="footer-mobile-signin" href="/ap/signin?openid.return_to=%2F">Already a customer? Sign in</a>'
    )
    return """
    <footer class="site-footer desktop-shell">
      <a class="back-to-top" href="#top"><span class="desktop-copy">Back to top</span><span class="mobile-copy">Top of page</span></a>
      <div class="footer-links">
        <section><h3>Get to Know Us</h3><a href="#">Careers</a><a href="#">Blog</a><a href="#">About Amazon</a><a href="#">Investor Relations</a><a href="#">Amazon Devices</a><a href="#">Amazon Science</a></section>
        <section><h3>Make Money with Us</h3><a href="/b/?_encoding=UTF8&amp;ld=AZUSSOA-sell&amp;node=12766669011">Sell products on Amazon</a><a href="#">Sell on Amazon Business</a><a href="#">Sell apps on Amazon</a><a href="#">Become an Affiliate</a><a href="#">Advertise Your Products</a><a href="#">Self-Publish with Us</a><a href="#">Host an Amazon Hub</a><a href="/b/?_encoding=UTF8&amp;ld=AZUSSOA-sell&amp;node=12766669011">› See More Make Money with Us</a></section>
        <section><h3>Amazon Payment Products</h3><a href="#">Amazon Business Card</a><a href="#">Shop with Points</a><a href="#">Reload Your Balance</a><a href="#">Amazon Currency Converter</a></section>
        <section><h3>Let Us Help You</h3><a href="#">Amazon and COVID-19</a><a href="/gp/css/homepage.html">Your Account</a><a href="/gp/css/order-history">Your Orders</a><a href="{shipping_href}">Shipping Rates &amp; Policies</a><a href="{returns_href}">Returns &amp; Replacements</a><a href="#">Manage Your Content and Devices</a><a href="{customer_service_href}">Help</a></section>
      </div>
      <nav class="footer-mobile-links" aria-label="Amazon mobile footer">
        <a href="/gp/css/order-history">Your Orders</a><a href="#">Amazon Live</a>
        <a href="/gp/browse.html?node=16115931011">Registry &amp; Gift List</a><a href="/gp/css/homepage.html">Your Account</a>
        <a href="/b/?_encoding=UTF8&amp;ld=AZUSSOA-sell&amp;node=12766669011">Sell products on Amazon</a><a href="#">Recalls and Product Safety Alerts</a>
        <a href="{customer_service_href}">Customer Service</a><a href="/">Amazon.com</a>
        <a href="/hz/wishlist/intro">Your Lists</a><a href="{gift_cards_href}">Gift Cards</a>
        <a href="{gift_cards_href}">Find a Gift</a><a href="#">Browsing History</a>
        <a href="{returns_href}">Your Returns</a>
      </nav>
      <div class="footer-locale">{logo}<a class="footer-locale-button" href="/customer-preferences/edit?preferencesReturnUrl=%2F">🌐 English</a><a class="footer-locale-button" href="/customer-preferences/edit?preferencesReturnUrl=%2F">$ USD - U.S. Dollar</a><a class="footer-locale-button" href="/customer-preferences/edit?preferencesReturnUrl=%2F">🇺🇸 United States</a></div>
      <div class="footer-services" aria-label="More on Amazon">
        <a href="#"><strong>Amazon Music</strong><span>Stream millions of songs</span></a>
        <a href="#"><strong>Amazon Ads</strong><span>Reach customers wherever they spend their time</span></a>
        <a href="#"><strong>6pm</strong><span>Score deals on fashion brands</span></a>
        <a href="#"><strong>AbeBooks</strong><span>Books, art &amp; collectibles</span></a>
        <a href="#"><strong>ACX</strong><span>Audiobook Publishing Made Easy</span></a>
        <a href="/b/?_encoding=UTF8&amp;ld=AZUSSOA-sell&amp;node=12766669011"><strong>Sell on Amazon</strong><span>Start a Selling Account</span></a>
        <a href="#"><strong>Veeqo</strong><span>Shipping Software Inventory Management</span></a>
        <a href="#"><strong>Amazon Business</strong><span>Everything For Your Business</span></a>
        <a href="#"><strong>AmazonGlobal</strong><span>Ship Orders Internationally</span></a>
        <a href="#"><strong>Amazon Web Services</strong><span>Scalable Cloud Computing Services</span></a>
        <a href="#"><strong>Audible</strong><span>Listen to Books &amp; Original Audio Performances</span></a>
        <a href="#"><strong>Box Office Mojo</strong><span>Find Movie Box Office Data</span></a>
        <a href="#"><strong>Goodreads</strong><span>Book reviews &amp; recommendations</span></a>
        <a href="#"><strong>IMDb</strong><span>Movies, TV &amp; Celebrities</span></a>
        <a href="#"><strong>IMDbPro</strong><span>Get Info Entertainment Professionals Need</span></a>
        <a href="#"><strong>Kindle Direct Publishing</strong><span>Indie Digital &amp; Print Publishing Made Easy</span></a>
        <a href="#"><strong>Prime Video Direct</strong><span>Video Distribution Made Easy</span></a>
        <a href="#"><strong>Shopbop</strong><span>Designer Fashion Brands</span></a>
        <a href="#"><strong>Woot!</strong><span>Deals and Shenanigans</span></a>
        <a href="#"><strong>Zappos</strong><span>Shoes &amp; Clothing</span></a>
        <a href="#"><strong>Ring</strong><span>Smart Home Security Systems</span></a>
        <span class="footer-service-spacer" aria-hidden="true"></span>
        <a href="#"><strong>eero WiFi</strong><span>Stream 4K Video in Every Room</span></a>
        <a href="#"><strong>Blink</strong><span>Smart Security for Every Home</span></a>
        <a href="#"><strong>Neighbors App</strong><span>Real-Time Crime &amp; Safety Alerts</span></a>
        <a href="#"><strong>PillPack</strong><span>Pharmacy Simplified</span></a>
        <span class="footer-service-spacer" aria-hidden="true"></span>
        <span class="footer-service-spacer" aria-hidden="true"></span>
      </div>
      {signin_link}
      <div class="footer-bottom"><a href="#">Conditions of Use</a><a href="#">Privacy Notice</a><a href="#">Consumer Health Data Privacy Disclosure</a><a href="#">Your Ads Privacy Choices</a><small>© 1996-2026, Amazon.com, Inc. or its affiliates</small></div>
    </footer>
    """.format(
        logo=amazon_logo(),
        signin_link=signin_link,
        customer_service_href=CUSTOMER_SERVICE_HREF.replace("&", "&amp;"),
        shipping_href=SHIPPING_POLICIES_HREF.replace("&", "&amp;"),
        returns_href=RETURNS_REPLACEMENTS_HREF.replace("&", "&amp;"),
        gift_cards_href=GIFT_CARDS_HREF.replace("&", "&amp;"),
    )


def layout(
    title: str,
    body: str,
    cart_count: int,
    *,
    search_value: str = "",
    body_class: str = "",
    extra_head: str = "",
    account_name: str | None = None,
    active_department: str = "aps",
) -> str:
    return f"""<!doctype html>
<html lang="en-US">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
  <title>{escape(title)}</title>
  <link rel="stylesheet" href="/static/styles.css?v=20260722-39">
  {extra_head}
</head>
<body id="top" class="{escape(body_class, quote=True)}">
  <a class="skip-link" href="#main">Skip to main content</a>
  {header(cart_count, search_value, account_name, active_department)}
  {body}
  {footer(account_name)}
  <script src="/static/app.js?v=20260722-32" defer></script>
</body>
</html>"""


HOME_ASSET_ROOT = "/static/assets/source-current/2026-07-21/home"
HOME_RAIL_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "home-rails.json"


def _load_home_rail_fixture() -> dict[str, dict[str, Any]]:
    payload = json.loads(HOME_RAIL_FIXTURE_PATH.read_text(encoding="utf-8"))
    return {rail["key"]: rail for rail in payload["rails"]}


HOME_RAILS_BY_KEY = _load_home_rail_fixture()


def _home_asset(asset_id: str) -> str:
    return f"{HOME_ASSET_ROOT}/{asset_id}.jpg"


def _home_single_card(title: str, asset_id: str, link_text: str, href: str) -> str:
    return f"""
      <article class="home-card home-card-single">
        <h2>{escape(title)}</h2>
        <a class="home-single-image" href="{href}"><img src="{_home_asset(asset_id)}" width="379" height="304" alt="{escape(title, quote=True)}"></a>
        <a class="home-card-link" href="{href}">{escape(link_text)}</a>
      </article>
    """


def _home_quad_card(
    title: str,
    tiles: list[tuple[str, str] | tuple[str, str, str]],
    link_text: str,
    href: str,
) -> str:
    rendered_tiles = ""
    for tile in tiles:
        label, asset_id = tile[:2]
        tile_href = tile[2] if len(tile) == 3 else f"/s?k={quote_plus(label)}"
        rendered_tiles += (
            f'<a class="home-tile" href="{tile_href}"><img src="{_home_asset(asset_id)}" '
            f'width="186" height="116" alt="{escape(label, quote=True)}" loading="lazy">'
            f'<span>{escape(label)}</span></a>'
        )
    return f"""
      <article class="home-card home-card-quad">
        <h2>{escape(title)}</h2>
        <div class="quad-grid">{rendered_tiles}</div>
        <a class="home-card-link" href="{href}">{escape(link_text)}</a>
      </article>
    """


def _home_product_rail(
    title: str,
    products: Iterable[dict[str, str]],
    extra_class: str = "",
    link_text: str = "",
) -> str:
    items = "".join(
        f'<a class="home-rail-item" data-asin="{product["asin"]}" href="{product["href"]}"><img src="{HOME_ASSET_ROOT}/{product["imagePath"]}" height="200" alt="{escape(product["title"], quote=True)}" loading="lazy"></a>'
        for product in products
    )
    action = f'<a class="home-rail-link" href="/s?k=portable+ssd">{escape(link_text)}</a>' if link_text else ""
    return f"""
      <section class="home-rail home-product-rail{extra_class}" aria-label="{escape(title, quote=True)}">
        <div class="home-rail-heading"><h2>{escape(title)}</h2>{action}</div>
        <button class="home-rail-button previous" type="button" aria-label="Previous items in {escape(title, quote=True)}" data-home-rail-previous>‹</button>
        <div class="home-rail-track" data-home-rail-track>{items}</div>
        <button class="home-rail-button next" type="button" aria-label="Next items in {escape(title, quote=True)}" data-home-rail-next>›</button>
      </section>
    """


def _home_frozen_rail(key: str) -> str:
    rail = HOME_RAILS_BY_KEY[key]
    extra_class = " home-related-rail" if key == "related-items" else ""
    return _home_product_rail(rail["title"], rail["items"], extra_class=extra_class)


def home_page(
    products: list[dict[str, Any]], cart_count: int, account_name: str | None = None
) -> str:
    del products
    cards = "".join(
        (
            _home_single_card("Get your game on", "4541d8781270e263", "Shop gaming", "/s?k=gaming"),
            _home_single_card("Must-haves for every student", "25ffc20ec0a7e38e", "Shop Back to School", "/s?k=school+supplies"),
            _home_quad_card("Shop Fashion for less", [("Jeans under $50", "014f2bdb67865e0e"), ("Tops under $25", "31686248cca236ec"), ("Dresses under $30", "b9b5c2175c9e968e"), ("Shoes under $50", "f760d9376ddef80d")], "See all deals", "/gp/goldbox/"),
            _home_quad_card("Must-have school supplies", [("Backpacks", "13b4e86ff2941173"), ("Electronics", "864424c08dd196b8"), ("Stationery", "9752a198d4271456"), ("Fashion", "c6d42e4f6bd30f7d")], "Shop Back to School", "/s?k=school+supplies"),
            _home_quad_card("New home arrivals under $50", [("Kitchen & dining", "4caab0b1d3f3ed6f"), ("Home improvement", "d821da00440eec1c"), ("Décor", "950f9fa40eca21de"), ("Bedding & bath", "cdeb1caaa6497025")], "Shop the latest from Home", "/s?k=home+arrivals"),
            _home_quad_card("Top categories in Kitchen appliances", [("Cooker", "49eee639f994b488"), ("Coffee", "b48141567982327c"), ("Pots and Pans", "9f3b26ba35bc8e01"), ("Kettles", "d1794b7faf501a9d")], "Explore all products in Kitchen", "/s?k=kitchen+products"),
            _home_quad_card("Fashion trends you like", [("Dresses", "f4963edd4e122be7"), ("Knits", "c0514a4ba72c5b37"), ("Jackets", "867dd3e0a203917a"), ("Jewelry", "779a6383af8ffb6e")], "Explore more", "/s?k=trending+fashion"),
            _home_quad_card("Easy updates for elevated spaces", [("Baskets & hampers", "64f21735e1c74948"), ("Hardware", "369381e3dd82f9f8"), ("Accent furniture", "dc8f8bbc2c6b4bab"), ("Wallpaper & paint", "389bf293eaa51bc0")], "Shop home products", "/s?k=home+products"),
            _home_frozen_rail("related-items"),
            _home_frozen_rail("best-sellers-home-kitchen"),
            _home_quad_card("Gear up to get fit", [("Clothing", "6938765e159953cd"), ("Trackers", "c84abc45d3c7831b"), ("Equipment", "4bcaecb91986cc37"), ("Deals", "6d06fd12fb426900")], "Discover more", "/s?k=fitness"),
            _home_quad_card("Have more fun with family", [("Outdoor play", "007d6dcf1fdb975a"), ("Learning toys", "616b49f5ba5b5080"), ("Action figures", "45496c7680c4f68c"), ("Pretend play", "b31a1ce1611d39bb")], "See more", "/s?k=family+toys"),
            _home_quad_card("Wireless Tech", [("Smartphones", "wireless-tech/smartphones", "/s?k=smartphones"), ("Watches", "wireless-tech/watches", "/s?k=smart+watches"), ("Headphones", "wireless-tech/headphones", "/s?k=headphones"), ("Tablets", "wireless-tech/tablets", "/s?k=tablets")], "Discover more", "/s?k=electronics"),
            _home_quad_card("Gaming merchandise", [("Apparel", "bdaf957e8276af10"), ("Hats", "14d4ecc4ed87b683"), ("Action figures", "66789d6ab1fe9905"), ("Mugs", "77748f7f125db3cf")], "See more", "/s?k=gaming+merchandise"),
            _home_frozen_rail("top-sellers-toys"),
            _home_frozen_rail("best-sellers-computers-accessories"),
            _home_quad_card("Level up your gaming", [("PC gaming", "1f00662f2c41fe99"), ("Xbox", "1915b416636972ea"), ("PlayStation", "1044f0abd1b0a9a4"), ("Nintendo", "308a8232dbd7f66b")], "Shop gaming", "/s?k=gaming"),
            _home_quad_card("Deals on top categories", [("Books", "f806a7aed4925070"), ("Fashion", "b2eb0a31612bba2b"), ("Desktops", "59f62bc9e9953c91"), ("Beauty", "1a61cc11afe4a3d9")], "Explore all deals", "/gp/goldbox/"),
            _home_quad_card("Level up your beauty routine", [("Makeup", "5d4f18f300b34f3f"), ("Brushes", "f83db64a2f3cbec8"), ("Sponges", "a753d6bf393e34c4"), ("Mirrors", "3197d03b8e86fb9c")], "See more", "/s?k=beauty"),
            _home_quad_card("Level up your PC here", [("Laptops", "c15c7d78fd314a2d"), ("PCs", "918f344527c4b4de"), ("Hard drives", "12237c942f7639ab"), ("Monitors", "47795f5c030aaad1")], "Shop now", "/s?k=computers"),
            _home_frozen_rail("best-sellers-books"),
            _home_frozen_rail("top-picks-singapore"),
            _home_quad_card("Most-loved watches", [("Women", "a0aa517f7d0f803e"), ("Men", "8020e47a983015ab"), ("Girls", "34a6123442d40dd2"), ("Boys", "0570588e61329a69")], "Discover more", "/s?k=watches"),
            _home_quad_card("Finds for Home", [("Kitchen", "79d09d346509aa4f"), ("Home décor", "a82abedd503127bb"), ("Dining", "834d20af59880114"), ("Smart home", "876c2616cc6fd466")], "See more", "/s?k=home"),
            _home_single_card("Transformers toys & more", "9341c0aa5ab587ad", "Shop now", "/s?k=transformers+toys"),
            _home_quad_card("Discover these beauty products for you", [("Skincare", "1a2952451fe8674c"), ("Makeup", "2b4ed40de0adcc4e"), ("Nails", "7f62214a5481cf69"), ("Fragrance", "5dbaa9ffe04c6573")], "Explore more", "/s?k=beauty+products"),
            _home_frozen_rail("best-sellers-beauty-personal-care"),
        )
    )
    body = f"""
    <main id="main" class="home-main desktop-shell">
      <section class="home-hero" aria-label="Featured offers" data-home-carousel>
        <a class="home-hero-slide is-active" href="/s?k=school+supplies" data-home-slide><img src="{HOME_ASSET_ROOT}/8b6861c8f69edb45.jpg" width="3000" height="1200" alt="Shop Back to School: School essentials at every price"></a>
        <a class="home-hero-slide" href="/s?k=amazon+devices" data-home-slide><img src="{HOME_ASSET_ROOT}/ba0a643ae5c32b0e.jpg" width="3000" height="1200" alt="Featured Amazon offer"></a>
        <a class="home-hero-slide" href="/s?k=featured+deals" data-home-slide><img src="{HOME_ASSET_ROOT}/c9b9ee3fb2232cce.jpg" width="3000" height="1200" alt="Featured Amazon deals"></a>
        <a class="home-hero-slide" href="/s?k=summer+essentials" data-home-slide><img src="{HOME_ASSET_ROOT}/e319297d016a5492.jpg" width="3000" height="1200" alt="Summer essentials"></a>
        <button class="home-carousel-button previous" type="button" aria-label="Previous featured offer" data-home-previous>‹</button>
        <button class="home-carousel-button next" type="button" aria-label="Next featured offer" data-home-next>›</button>
      </section>
      <section class="home-grid">{cards}</section>
    </main>
    """
    return layout("Amazon.com. Spend less. Smile more.", body, cart_count, body_class="home-page", account_name=account_name)


def home_product(product: dict[str, Any]) -> str:
    return f"""<a class="home-product" href="{product_href(product)}"><img src="{product['image_path']}" width="165" height="130" alt="{escape(product['title'], quote=True)}"><span>{escape(product['brand'])}</span></a>"""


def best_product(
    product: dict[str, Any],
    rank: int,
    *,
    compact: bool = False,
    image_path: str | None = None,
) -> str:
    card_image = image_path or product["image_path"]
    return f"""
    <article class="best-product{' compact' if compact else ''}" data-rank="{rank}" data-asin="{product['asin']}">
      <span class="rank-ribbon">#{rank}</span>
      <a class="best-image" href="{product_href(product)}"><img src="{card_image}" width="300" height="200" alt="{escape(product['title'], quote=True)}"></a>
      <a class="best-title" href="{product_href(product)}">{escape(product['title'])}</a>
      <a class="rating" href="{product_review_href(product)}"><span>{product['rating']}</span><span class="stars" aria-label="{product['rating']} out of 5 stars">★★★★★</span> <small>{product['reviews']:,}</small></a>
      <a class="best-price" href="{product_href(product)}">{money(product['price_minor'], product['currency'])}</a>
    </article>
    """


def best_sellers_root(
    products: list[dict[str, Any]], cart_count: int, account_name: str | None = None
) -> str:
    sections = [
        "Best Sellers in Computers & Accessories",
        "Best Sellers in Electronics",
        "Best Sellers in Home & Kitchen",
        "Best Sellers in Books",
        "Best Sellers in Beauty & Personal Care",
        "Best Sellers in Sports & Outdoors",
    ]
    expanded = products + list(reversed(products))
    rails = "".join(
        f"""<section class="best-section"><h2>{escape(section)}</h2><div class="best-rail">{''.join(best_product(p, i + 1, compact=True) for i, p in enumerate(expanded))}</div><a class="section-see-more" href="{BEST_SELLERS_PATH}">See More</a></section>"""
        for section in sections
    )
    body = f"""
    <main id="main" class="best-root desktop-shell">
      <nav class="best-tabs"><a class="active" href="/Best-Sellers/zgbs">Best Sellers</a><a href="#">New Releases</a><a href="#">Movers &amp; Shakers</a></nav>
      <header class="best-intro"><h1>Amazon Best Sellers</h1><p>Our most popular products based on sales. Updated frequently.</p></header>
      <div class="best-layout"><aside class="department-tree"><h2>Any Department</h2><a href="/s?i=books">Books</a><a href="/s?i=computers">Computers &amp; Accessories</a><a href="/s?i=electronics">Electronics</a><a href="/s?i=home-kitchen">Home &amp; Kitchen</a><a href="#">Beauty &amp; Personal Care</a><a href="#">Sports &amp; Outdoors</a><a href="#">Toys &amp; Games</a></aside><div class="best-content">{rails}</div></div>
    </main>
    """
    return layout("Amazon Best Sellers", body, cart_count, body_class="best-page", account_name=account_name)


def external_ssd_best_sellers(
    ranking: list[dict[str, Any]], cart_count: int, account_name: str | None = None
) -> str:
    cards = "".join(
        best_product(
            product,
            int(product["rank"]),
            image_path=f"/static/assets/rank-{int(product['rank']):02d}-current.jpg",
        )
        for product in ranking
    )
    body = f"""
    <main id="main" class="ranking-page desktop-shell">
      <nav class="ranking-mobile-category" aria-label="Current Best Sellers category"><a href="/Best-Sellers/zgbs">Any Department</a><span>›</span><a href="#">Computers &amp; Accessories</a><span>›</span><strong>External Solid State Drives</strong></nav>
      <nav class="ranking-mobile-tabs" aria-label="Best Sellers lists"><a class="active" href="{BEST_SELLERS_PATH}">Best Sellers</a><a href="#">New Releases</a><a href="#">Movers &amp; Shakers</a></nav>
      <section class="ranking-hero"><h1>Amazon Best Sellers</h1><p>Our most popular products based on sales. Updated frequently.</p></section>
      <div class="ranking-layout">
        <aside class="ranking-sidebar"><a href="/Best-Sellers/zgbs">‹ Any Department</a><a href="#">‹ Computers &amp; Accessories</a><a href="#">Data Storage</a><a href="#">Crypto Hardware Wallets</a><a href="#">External Hard Drives</a><strong>External Solid State Drives</strong><a href="#">External Zip Drives</a><a href="#">Floppy &amp; Tape Drives</a><a href="#">Internal Hard Drives</a><a href="#">Internal Solid State Drives</a><a href="#">Network Attached Storage</a><a href="#">Tape Libraries</a><a href="#">USB Flash Drives</a></aside>
        <section class="ranking-content"><h1>Best Sellers in External Solid State Drives</h1><div class="ranking-grid">{cards}</div></section>
      </div>
    </main>
    """
    return layout(
        "Amazon Best Sellers: Best External Solid State Drives",
        body,
        cart_count,
        body_class="ranking-list-page",
        account_name=account_name,
    )


def browse_only_card(product: dict[str, Any], class_name: str) -> str:
    """Render a local homepage-evidence card without implying an offer."""

    asin = escape(str(product["asin"]), quote=True)
    title = escape(str(product["title"]))
    title_attr = escape(str(product["title"]), quote=True)
    href = escape(product_href(product), quote=True)
    image_path = escape(str(product["image_path"]), quote=True)
    placement = _evidence_placement_label(product)
    placement_markup = (
        f'<p class="browse-card-source">Featured in {escape(placement)}</p>'
        if placement
        else ""
    )
    return f"""
    <article class="{escape(class_name, quote=True)}" data-asin="{asin}" data-browse-only="true">
      <a class="browse-card-image" href="{href}"><img src="{image_path}" width="180" height="180" alt="{title_attr}"></a>
      <div class="browse-card-copy"><h3><a href="{href}">{title}</a></h3>{placement_markup}<a class="browse-card-details" href="{href}">See product details</a></div>
    </article>
    """


def search_page(
    products: list[dict[str, Any]],
    cart_count: int,
    query: str,
    account_name: str | None = None,
    *,
    supplemental_products: list[dict[str, Any]] | None = None,
) -> str:
    safe_query = escape(query)
    asset_root = "/static/assets/source-current/2026-07-21/search"
    by_asin = {product["asin"]: product for product in products}
    current_slots = (
        ("B08HN37XC1", dict(image_path=f"{asset_root}/search-sandisk-extreme-1tb.jpg", bought="5K+ bought in past month", delivery="$6.86 delivery Wednesday, July 29", buying_choices="More Buying Choices $256.26 (12+ used & new offers)")),
        ("B08GTYFC37", dict(image_path=f"{asset_root}/search-sandisk-extreme-1tb.jpg", bought="3K+ bought in past month", delivery="$6.72 delivery Monday, July 27", buying_choices="More Buying Choices $180.49 (10+ used & new offers)")),
        ("B0F6NKYDTY", dict(image_path=f"{asset_root}/search-sponsored-1tb.jpg", bought="100+ bought in past month", delivery="$6.21 delivery Monday, July 27", sponsored=True)),
        ("B0BGKXX9TK", dict(image_path=f"{asset_root}/search-ssk-500gb.jpg", bought="1K+ bought in past month", delivery="$6.34 delivery Monday, July 27")),
        ("B0874XN4D8", dict(image_path=f"{asset_root}/search-samsung-t7-1tb.jpg", bought="4K+ bought in past month", delivery="$7.30 delivery Sunday, July 26")),
        ("B0C5JQ68FY", dict(image_path=f"{asset_root}/search-sandisk-portable-1tb.jpg", bought="2K+ bought in past month", delivery="$6.52 delivery Monday, July 27")),
    )
    missing = [asin for asin, _ in current_slots if asin not in by_asin]
    if missing:
        raise ValueError(f"search fixture is missing catalog products: {', '.join(missing)}")
    current_results = [search_result(by_asin[asin], **display) for asin, display in current_slots]
    featured_asins = {asin for asin, _ in current_slots}
    trailing_results = [
        search_result(product)
        for product in products
        if product["asin"] not in featured_asins
    ]
    results = "".join(current_results + trailing_results)
    supplement_products = supplemental_products or []
    supplement = ""
    if supplement_products:
        cards = "".join(
            browse_only_card(product, "browse-supplement-card")
            for product in supplement_products
        )
        supplement = f"""
        <section class="browse-supplement" aria-labelledby="browse-supplement-heading" data-supplement-count="{len(supplement_products)}">
          <header><h2 id="browse-supplement-heading">More from today's homepage snapshot</h2><p>These browse-only products were visible on the captured Amazon homepage. Open a product to see the source-backed details available locally.</p></header>
          <div class="browse-supplement-grid">{cards}</div>
        </section>
        """
    body = f"""
    <main id="main" class="search-main desktop-shell">
      <div class="mobile-filters"><button>☰</button><button>★★★★ &amp; Up</button><button>All Discounts</button><button>Discover</button><button>Storage Capacity</button><button>Hard Drive Type</button></div>
      <div class="result-summary"><span>1-16 of over 6,000 results for <strong>"{safe_query}"</strong></span><select aria-label="Sort by"><option>Sort by: Featured</option><option>Avg. Customer Review</option></select></div>
      <div class="search-layout">
        <aside class="search-refinements"><h3>Popular Shopping Ideas</h3><a href="#">1tb</a><a href="#">2tb</a><a href="#">4tb</a><a href="#">Usb-c</a><h3>Hard Drive Size</h3>{checkboxes(['4 TB & Above','3 TB','2 TB','1 TB','501 to 999 GB','321 to 500 GB','121 to 320 GB','81 to 120 GB','Up to 80 GB'])}<h3>Storage Capacity</h3>{checkboxes(['2 TB & Up','1 to 1.9 TB','960 to 999 GB','480 to 959 GB','240 to 479 GB'])}<h3>Customer Reviews</h3><a class="stars-filter" href="#">★★★★☆ &amp; Up</a><h3>Brands</h3>{checkboxes(['SAMSUNG','SanDisk','Crucial','Lexar'])}</aside>
        <section class="search-results"><h1>Results</h1><p>Check each product page for other buying options.</p>{results}{supplement}</section>
      </div>
    </main>
    """
    return layout(f"Amazon.com : {query}", body, cart_count, search_value=query, body_class="search-page", account_name=account_name)


def _evidence_placement_label(product: dict[str, Any]) -> str:
    labels: list[str] = []
    placements = product.get("placements")
    if isinstance(placements, list):
        for placement in placements:
            if not isinstance(placement, dict):
                continue
            label = placement.get("railTitle")
            if isinstance(label, str) and label and label not in labels:
                labels.append(label)
    return " · ".join(labels[:2])


def compare_add_form(product: dict[str, Any], *, compact: bool = False) -> str:
    asin = str(product.get("asin") or "")
    if not asin or product.get("compare_eligible") is not True:
        return ""
    class_name = "compare-add-form compact" if compact else "compare-add-form"
    label = "Compare" if compact else "Add to compare"
    safe_asin = escape(asin, quote=True)
    selected_options = product.get("default_selected_options")
    option_fields = "".join(
        f'<input type="hidden" name="option.{escape(str(option_label), quote=True)}" '
        f'value="{escape(str(option_value), quote=True)}" '
        f'data-product-option-field="{escape(str(option_label), quote=True)}">'
        for option_label, option_value in (
            selected_options.items()
            if isinstance(selected_options, dict)
            else ()
        )
    )
    return f'<form class="{class_name}" method="post" action="/gp/compare/add"><input type="hidden" name="ASIN" value="{safe_asin}">{option_fields}<button type="submit" data-product-compare>{label}</button></form>'


def evidence_search_result(product: dict[str, Any]) -> str:
    """Render only fields carried by the home or direct-PDP evidence record."""

    title = str(product["title"])
    href = product_href(product)
    image_path = str(product["image_path"])
    placement = _evidence_placement_label(product)
    placement_markup = (
        f'<p class="evidence-source">Featured in {escape(placement)}</p>' if placement else ""
    )
    badge = product.get("badge")
    badge_markup = (
        f'<span class="best-seller-badge">{escape(str(badge))}</span>' if badge else ""
    )
    rating = product.get("rating")
    reviews = product.get("reviews")
    rating_markup = ""
    if isinstance(rating, str) and rating and isinstance(reviews, int) and reviews > 0:
        review_label = f"{reviews:,}" if isinstance(reviews, int) else escape(str(reviews))
        rating_markup = (
            f'<a class="rating" href="{escape(product_review_href(product), quote=True)}">'
            f'{escape(str(rating))} <span class="stars">★★★★★</span> '
            f'<small>({review_label})</small></a>'
        )
    price_minor = product.get("price_minor")
    price_markup = ""
    if isinstance(price_minor, int):
        currency = str(product.get("currency") or "USD")
        price_markup = f'<a class="result-price" href="{href}">{money(price_minor, currency)}</a>'
    has_verified_offer = (
        product.get("evidence_tier") == "pdp-direct"
        and isinstance(price_minor, int)
        and product.get("currency") == "USD"
    )
    evidence_level = "verified-offer" if has_verified_offer else "homepage-browse"
    evidence_label = (
        "Verified product page & offer"
        if has_verified_offer
        else "Homepage browse evidence"
    )
    evidence_badge = (
        f'<span class="evidence-tier-badge {evidence_level}">{evidence_label}</span>'
    )
    detail = product.get("pdp")
    bought_markup = ""
    if isinstance(detail, dict) and isinstance(detail.get("bought"), str):
        bought_markup = f'<p class="bought">{escape(detail["bought"])}</p>'
    compare_markup = compare_add_form(product, compact=True)
    purchase_markup = (
        generic_add_to_cart_form(
            product,
            quantity_id=f'category-quantity-{str(product["asin"]).lower()}',
            selected_options=product.get("default_selected_options"),
        )
        if has_verified_offer
        else ""
    )
    return f"""
    <article class="search-result evidence-search-result {evidence_level}" data-asin="{escape(str(product['asin']), quote=True)}" data-evidence-level="{evidence_level}">
      <a class="result-image" href="{href}"><img src="{escape(image_path, quote=True)}" width="225" height="218" alt="{escape(title, quote=True)}"></a>
      <div class="result-copy">{evidence_badge}{badge_markup}<h2><a href="{href}">{escape(title)}</a></h2>{rating_markup}{bought_markup}{price_markup}{placement_markup}{purchase_markup}<a class="result-cta" href="{href}">See product details</a>{compare_markup}</div>
    </article>
    """


def direct_search_card_result(product: dict[str, Any]) -> str:
    """Render a purchasable card without promoting it to full-PDP evidence."""

    title = str(product["title"])
    href = product_href(product)
    rating = escape(str(product["rating"]))
    reviews_display = escape(str(product["reviews_display"]))
    sponsored = (
        '<span class="sponsored-label">Sponsored</span>'
        if product.get("sponsored")
        else ""
    )
    product_format = product.get("format")
    format_markup = (
        f'<p class="search-card-format">{escape(str(product_format))}</p>'
        if isinstance(product_format, str) and product_format
        else ""
    )
    return f"""
    <article class="search-result direct-search-card-result" data-asin="{escape(str(product['asin']), quote=True)}" data-evidence-level="direct-search-card">
      <a class="result-image" href="{href}"><img src="{escape(str(product['image_path']), quote=True)}" width="225" height="218" alt="{escape(title, quote=True)}"></a>
      <div class="result-copy"><span class="evidence-tier-badge verified-offer">Verified search-card offer</span>{sponsored}<h2><a href="{href}">{escape(title)}</a></h2>{format_markup}<a class="rating" href="{escape(product_review_href(product), quote=True)}">{rating} <span class="stars">★★★★★</span> <small>({reviews_display})</small></a><a class="result-price" href="{href}">{money(int(product['price_minor']), str(product['currency']))}</a><p class="evidence-source">Price and Add to cart were captured on the same current search card.</p>{generic_add_to_cart_form(product, quantity_id=f'search-card-quantity-{str(product["asin"]).lower()}')}{compare_add_form(product, compact=True)}<a class="result-cta" href="{href}">See captured offer details</a></div>
    </article>
    """


def _result_departments(products: list[dict[str, Any]]) -> str:
    labels: list[str] = []
    for product in products:
        placements = product.get("placements")
        if not isinstance(placements, list):
            continue
        for placement in placements:
            if not isinstance(placement, dict):
                continue
            label = placement.get("railTitle")
            if isinstance(label, str) and label and label not in labels:
                labels.append(label)
    return "".join(
        f'<a href="/s?k={quote_plus(label)}">{escape(label)}</a>' for label in labels
    )


def catalog_search_page(
    products: list[dict[str, Any]],
    cart_count: int,
    query: str,
    account_name: str | None = None,
) -> str:
    """Search surface for sparse, homepage-evidenced products."""

    safe_query = escape(query)
    department = source_department_for_query(query)
    department_slug = str(department["slug"]) if department is not None else ""
    result_count = len(products)
    if products:
        if department is not None:
            department_title = escape(str(department["title"]))
            result_summary = (
                f'{result_count} source-backed products in '
                f'<strong>{department_title}</strong>'
            )
        else:
            result_summary = f'1-{result_count} of {result_count} results for <strong>"{safe_query}"</strong>'
        result_markup = "".join(evidence_search_result(product) for product in products)
        if department is not None:
            department_title = escape(str(department["title"]))
            department_nav = source_department_navigation(department_slug)
            aside = (
                '<aside class="search-refinements department-refinements">'
                '<h2>Departments</h2>'
                f'<nav aria-label="Browse source departments">{department_nav}</nav>'
                '</aside>'
            )
            content = f"""
            <section class="search-results department-results" aria-labelledby="department-heading">
              <header class="department-results-header">
                <p class="department-eyebrow">Department</p>
                <h1 id="department-heading">{department_title}</h1>
                <p>{result_count} products from the captured <strong>{escape(str(department['rail_title']))}</strong> source rail.</p>
                <div class="evidence-tier-legend" aria-label="Product evidence levels"><span><i class="verified-offer"></i>Verified offer: shown offer fields and purchase are source-backed</span><span><i class="homepage-browse"></i>Browse evidence: title and image only</span></div>
              </header>
              {result_markup}
            </section>
            """
        else:
            departments = _result_departments(products)
            aside = (
                f'<aside class="search-refinements"><h3>Related departments</h3>{departments}</aside>'
                if departments
                else '<aside class="search-refinements"></aside>'
            )
            content = (
                '<section class="search-results"><h1>Results</h1>'
                '<p>Product details shown here come from the current local source snapshot.</p>'
                f'{result_markup}</section>'
            )
    else:
        result_summary = f'0 results for <strong>"{safe_query}"</strong>'
        aside = '<aside class="search-refinements"></aside>'
        content = f"""
        <section class="search-results search-no-results">
          <h1>No results for “{safe_query}”</h1>
          <p>Try checking your spelling or using fewer words.</p>
          <a class="button secondary" href="/">Return to Amazon home</a>
        </section>
        """
    department_attributes = (
        f' data-department="{escape(department_slug, quote=True)}"'
        f' data-source-rail="{escape(str(department["rail_key"]), quote=True)}"'
        if department is not None
        else ""
    )
    category_class = " department-search-main" if department is not None else ""
    body = f"""
    <main id="main" class="search-main evidence-search-main{category_class} desktop-shell"{department_attributes}>
      <div class="result-summary"><span>{result_summary}</span></div>
      <div class="search-layout">{aside}{content}</div>
    </main>
    """
    return layout(f"Amazon.com : {query}", body, cart_count, search_value=query, body_class="search-page", account_name=account_name)


PORTABLE_SEARCH_DISPLAY: dict[str, dict[str, Any]] = {
    "B08HN37XC1": {
        "image_path": "/static/assets/source-current/2026-07-21/search/search-sandisk-extreme-1tb.jpg",
        "bought": "5K+ bought in past month",
        "delivery": "$6.86 delivery Wednesday, July 29",
        "buying_choices": "More Buying Choices $256.26 (12+ used & new offers)",
    },
    "B08GTYFC37": {
        "image_path": "/static/assets/source-current/2026-07-21/search/search-sandisk-extreme-1tb.jpg",
        "bought": "3K+ bought in past month",
        "delivery": "$6.72 delivery Monday, July 27",
        "buying_choices": "More Buying Choices $180.49 (10+ used & new offers)",
    },
    "B0F6NKYDTY": {
        "image_path": "/static/assets/source-current/2026-07-21/search/search-sponsored-1tb.jpg",
        "bought": "100+ bought in past month",
        "delivery": "$6.21 delivery Monday, July 27",
        "sponsored": True,
    },
    "B0BGKXX9TK": {
        "image_path": "/static/assets/source-current/2026-07-21/search/search-ssk-500gb.jpg",
        "bought": "1K+ bought in past month",
        "delivery": "$6.34 delivery Monday, July 27",
    },
    "B0874XN4D8": {
        "image_path": "/static/assets/source-current/2026-07-21/search/search-samsung-t7-1tb.jpg",
        "bought": "4K+ bought in past month",
        "delivery": "$7.30 delivery Sunday, July 26",
    },
    "B0C5JQ68FY": {
        "image_path": "/static/assets/source-current/2026-07-21/search/search-sandisk-portable-1tb.jpg",
        "bought": "2K+ bought in past month",
        "delivery": "$6.52 delivery Monday, July 27",
    },
    "B0CHFSWM2P": {
        "bought": "10K+ bought in past month",
        "delivery": "$7.61 delivery Monday, July 27",
    },
}
PORTABLE_SEARCH_ASINS = frozenset(
    {
        "B08HN37XC1",
        "B08GTYFC37",
        "B0F6NKYDTY",
        "B0BGKXX9TK",
        "B0874XN4D8",
        "B0C5JQ68FY",
        "B08GV9M64L",
        "B09VLK9W3S",
        "B0CHFSWM2P",
    }
)


def _search_money_parameter(minor: int) -> str:
    whole, cents = divmod(minor, 100)
    return str(whole) if cents == 0 else f"{whole}.{cents:02d}".rstrip("0")


def _search_state_hidden_fields(request: SearchRequest) -> str:
    pairs: list[tuple[str, str]] = []
    if request.query:
        pairs.append(("k", request.query))
    if request.department != DEFAULT_SEARCH_DEPARTMENT:
        pairs.append(("i", request.department))
    pairs.extend(("brand", brand) for brand in request.brands)
    if request.min_price_minor is not None:
        pairs.append(("minPrice", _search_money_parameter(request.min_price_minor)))
    if request.max_price_minor is not None:
        pairs.append(("maxPrice", _search_money_parameter(request.max_price_minor)))
    if request.rating is not None:
        pairs.append(("rating", request.rating))
    if request.availability is not None:
        pairs.append(("availability", request.availability))
    return "".join(
        f'<input type="hidden" name="{escape(name, quote=True)}" value="{escape(value, quote=True)}">'
        for name, value in pairs
    )


def _search_filter_link(
    label: str,
    href: str,
    *,
    selected: bool = False,
    navigation: bool = False,
) -> str:
    class_name = "search-filter-link is-selected" if selected else "search-filter-link"
    state = (
        (' aria-current="page"' if selected else "")
        if navigation
        else f' role="checkbox" aria-checked="{"true" if selected else "false"}"'
    )
    return (
        f'<a class="{class_name}" href="{escape(href, quote=True)}"{state}>'
        f'<span class="search-filter-box" aria-hidden="true"></span>'
        f'<span>{escape(label)}</span></a>'
    )


def _search_department_filters(request: SearchRequest) -> str:
    links = [
        _search_filter_link(
            "All Departments",
            search_href(request, department=DEFAULT_SEARCH_DEPARTMENT, page=1),
            selected=request.department == DEFAULT_SEARCH_DEPARTMENT,
            navigation=True,
        )
    ]
    for department in SOURCE_DEPARTMENTS:
        slug = str(department["slug"])
        links.append(
            _search_filter_link(
                str(department["title"]),
                search_href(request, department=slug, page=1),
                selected=request.department == slug,
                navigation=True,
            )
        )
    return "".join(links)


def _search_refinements(
    request: SearchRequest, available_brands: Iterable[str]
) -> str:
    clear_href = search_href(
        request,
        brands=(),
        min_price_minor=None,
        max_price_minor=None,
        rating=None,
        availability=None,
        sort="relevance",
        page=1,
    )
    brand_values = sorted(
        {brand for brand in available_brands if brand} | set(request.brands),
        key=str.casefold,
    )
    brand_links = []
    active_brand_keys = {brand.casefold() for brand in request.brands}
    for brand in brand_values:
        selected = brand.casefold() in active_brand_keys
        updated = tuple(
            existing
            for existing in request.brands
            if existing.casefold() != brand.casefold()
        )
        if not selected:
            updated = (*updated, brand)
        brand_links.append(
            _search_filter_link(
                brand,
                search_href(request, brands=updated, page=1),
                selected=selected,
            )
        )

    price_ranges = (
        ("Under $25", None, 2499),
        ("$25 to $50", 2500, 4999),
        ("$50 to $100", 5000, 9999),
        ("$100 to $200", 10000, 19999),
        ("$200 & above", 20000, None),
    )
    price_links = "".join(
        _search_filter_link(
            label,
            search_href(
                request,
                min_price_minor=(None if selected else minimum),
                max_price_minor=(None if selected else maximum),
                page=1,
            ),
            selected=selected,
        )
        for label, minimum, maximum in price_ranges
        for selected in (
            request.min_price_minor == minimum
            and request.max_price_minor == maximum,
        )
    )
    rating_selected = request.rating == "4-up"
    availability_selected = request.availability == "in-stock"
    return f"""
      <aside id="search-refinements" class="search-refinements" aria-label="Search filters" data-search-filter-panel>
        <button class="search-filter-close" type="button" aria-label="Close filters" data-search-filter-close>Close</button>
        <a class="search-filter-clear" href="{escape(clear_href, quote=True)}">Clear all filters</a>
        <h3>Department</h3>{_search_department_filters(request)}
        <h3>Customer Reviews</h3>{_search_filter_link("4 Stars & Up", search_href(request, rating=None if rating_selected else "4-up", page=1), selected=rating_selected)}
        <h3>Brands</h3>{''.join(brand_links) if brand_links else '<span class="search-filter-unavailable">No evidenced brands</span>'}
        <h3>Price</h3>{price_links}
        <h3>Availability</h3>{_search_filter_link("In Stock", search_href(request, availability=None if availability_selected else "in-stock", page=1), selected=availability_selected)}
      </aside>
    """


def _active_search_filters(request: SearchRequest) -> str:
    chips: list[str] = []
    for brand in request.brands:
        remaining = tuple(
            value for value in request.brands if value.casefold() != brand.casefold()
        )
        chips.append(
            f'<a href="{escape(search_href(request, brands=remaining, page=1), quote=True)}" aria-label="Remove brand {escape(brand, quote=True)}">{escape(brand)} <span aria-hidden="true">×</span></a>'
        )
    if request.min_price_minor is not None or request.max_price_minor is not None:
        if request.min_price_minor is None:
            label = f"Up to ${_search_money_parameter(request.max_price_minor or 0)}"
        elif request.max_price_minor is None:
            label = f"${_search_money_parameter(request.min_price_minor)} & above"
        else:
            label = (
                f"${_search_money_parameter(request.min_price_minor)}–"
                f"${_search_money_parameter(request.max_price_minor)}"
            )
        chips.append(
            f'<a href="{escape(search_href(request, min_price_minor=None, max_price_minor=None, page=1), quote=True)}" aria-label="Remove price filter">{escape(label)} <span aria-hidden="true">×</span></a>'
        )
    if request.rating is not None:
        chips.append(
            f'<a href="{escape(search_href(request, rating=None, page=1), quote=True)}" aria-label="Remove rating filter">4 Stars &amp; Up <span aria-hidden="true">×</span></a>'
        )
    if request.availability is not None:
        chips.append(
            f'<a href="{escape(search_href(request, availability=None, page=1), quote=True)}" aria-label="Remove availability filter">In Stock <span aria-hidden="true">×</span></a>'
        )
    return (
        f'<nav class="active-search-filters" aria-label="Applied filters">{"".join(chips)}</nav>'
        if chips
        else ""
    )


def _search_sort_form(request: SearchRequest) -> str:
    options = (
        ("relevance", "Featured"),
        ("price-asc", "Price: Low to High"),
        ("price-desc", "Price: High to Low"),
        ("rating-desc", "Avg. Customer Review"),
    )
    option_markup = "".join(
        f'<option value="{value}"{" selected" if value == request.sort else ""}>{escape(label)}</option>'
        for value, label in options
    )
    return f"""
      <form class="search-sort-form" method="get" action="/s">
        {_search_state_hidden_fields(request)}
        <label class="sr-only" for="search-sort">Sort by</label>
        <select id="search-sort" name="sort" aria-label="Sort by" data-search-sort>{option_markup}</select>
        <button type="submit">Go</button>
      </form>
    """


def _search_pagination(page: SearchPage) -> str:
    if page.page_count <= 1:
        return ""
    page_numbers = sorted(
        {1, page.page_count, page.page - 1, page.page, page.page + 1}
        & set(range(1, page.page_count + 1))
    )
    parts = []
    if page.page > 1:
        parts.append(
            f'<a class="search-page-previous" href="{escape(search_href(page.request, page=page.page - 1), quote=True)}">‹ Previous</a>'
        )
    else:
        parts.append('<span class="search-page-previous is-disabled" aria-disabled="true">‹ Previous</span>')
    previous_number = 0
    for number in page_numbers:
        if previous_number and number > previous_number + 1:
            parts.append('<span class="search-page-gap" aria-hidden="true">…</span>')
        if number == page.page:
            parts.append(
                f'<span class="is-current" aria-current="page" data-search-current-page="{number}">{number}</span>'
            )
        else:
            parts.append(
                f'<a href="{escape(search_href(page.request, page=number), quote=True)}" aria-label="Go to page {number}">{number}</a>'
            )
        previous_number = number
    if page.page < page.page_count:
        parts.append(
            f'<a class="search-page-next" href="{escape(search_href(page.request, page=page.page + 1), quote=True)}">Next ›</a>'
        )
    else:
        parts.append('<span class="search-page-next is-disabled" aria-disabled="true">Next ›</span>')
    return f'<nav class="search-pagination" aria-label="Search results pages">{"".join(parts)}</nav>'


def evidence_aware_search_page(
    page: SearchPage,
    cart_count: int,
    account_name: str | None = None,
    *,
    available_brands: Iterable[str] = (),
) -> str:
    """Render one server-refined result page without inventing sparse facts."""

    request = page.request
    safe_query = escape(request.query)
    department = SOURCE_DEPARTMENT_BY_SLUG.get(request.department)
    department_slug = str(department["slug"]) if department is not None else ""
    start = (page.page - 1) * page.page_size + 1 if page.total else 0
    end = start + len(page.items) - 1 if page.items else 0
    if department is not None and not request.query:
        context = f'in <strong>{escape(str(department["title"]))}</strong>'
    else:
        context = f'for <strong>"{safe_query}"</strong>'
    summary = f"{start}-{end} of {page.total} results {context}" if page.total else f"0 results {context}"

    result_markup = []
    for hit in page.items:
        product = dict(hit.product)
        asin = str(product.get("asin") or "")
        if product.get("evidence_class") == "direct-search-card":
            result_markup.append(direct_search_card_result(product))
        elif asin in PORTABLE_SEARCH_ASINS and product.get("evidence_tier") is None:
            result_markup.append(search_result(product, **PORTABLE_SEARCH_DISPLAY.get(asin, {})))
        else:
            result_markup.append(evidence_search_result(product))

    if result_markup:
        heading = escape(str(department["title"])) if department is not None else "Results"
        results = f"""
          <section class="search-results" aria-labelledby="search-results-heading">
            <h1 id="search-results-heading">{heading}</h1>
            <p>Check each product page for source-backed buying options.</p>
            {''.join(result_markup)}
            {_search_pagination(page)}
          </section>
        """
    else:
        has_refinements = bool(
            request.brands
            or request.min_price_minor is not None
            or request.max_price_minor is not None
            or request.rating is not None
            or request.availability is not None
        )
        if department is not None and not request.query:
            empty_heading = f'No results in {escape(str(department["title"]))}'
        else:
            empty_heading = f'No results for “{safe_query}”'
        if has_refinements:
            action = (
                f'<a class="button secondary" href="{escape(search_href(request, brands=(), min_price_minor=None, max_price_minor=None, rating=None, availability=None, sort="relevance", page=1), quote=True)}">Clear filters</a>'
            )
        elif request.department != DEFAULT_SEARCH_DEPARTMENT and request.query:
            action = (
                f'<a class="button secondary" href="{escape(search_href(request, department=DEFAULT_SEARCH_DEPARTMENT, page=1), quote=True)}">Search all departments</a>'
            )
        else:
            action = '<a class="button secondary" href="/">Return to Amazon home</a>'
        results = f"""
          <section class="search-results search-no-results" aria-labelledby="search-results-heading">
            <h1 id="search-results-heading">{empty_heading}</h1>
            <p>Try removing a filter, checking your spelling, or using fewer words.</p>
            {action}
          </section>
        """
    active_filters = _active_search_filters(request)
    body = f"""
    <main id="main" class="search-main evidence-search-main desktop-shell" data-search-total="{page.total}" data-search-page="{page.page}"{f' data-department="{escape(department_slug, quote=True)}" data-source-rail="{escape(str(department["rail_key"]), quote=True)}"' if department is not None else ''}>
      <div class="mobile-filters"><button type="button" aria-controls="search-refinements" aria-expanded="false" data-search-filter-toggle>☰ Filters</button><span>{summary}</span></div>
      <div class="result-summary"><span>{summary}</span>{_search_sort_form(request)}</div>
      {active_filters}
      <div class="search-layout">{_search_refinements(request, available_brands)}{results}</div>
    </main>
    """
    return layout(
        f"Amazon.com : {request.query or (department or {}).get('title', 'Search')}",
        body,
        cart_count,
        search_value=request.query,
        body_class="search-page",
        account_name=account_name,
        active_department=request.department,
    )


def site_directory_page(
    sections: list[dict[str, Any]],
    cart_count: int,
    account_name: str | None = None,
) -> str:
    """Expose the complete homepage catalog in its seven original source rails."""

    total = sum(int(section.get("count", 0)) for section in sections)
    anchors = "".join(
        f'<a href="#rail-{escape(str(section["key"]), quote=True)}">'
        f'<span>{escape(str(section["title"]))}</span>'
        f'<strong>{int(section["count"])}</strong></a>'
        for section in sections
    )
    rail_markup: list[str] = []
    for section in sections:
        raw_key = str(section["key"])
        key = escape(raw_key, quote=True)
        title = escape(str(section["title"]))
        products = list(section.get("products", ()))
        cards = "".join(
            browse_only_card(product, "site-directory-card") for product in products
        )
        department = source_department_for_rail(raw_key)
        department_link = (
            f'<a class="directory-shop-link" href="{escape(source_department_href(department), quote=True)}">'
            f'Shop {escape(str(department["title"]))}</a>'
            if department is not None
            else ""
        )
        rail_markup.append(
            f"""
            <section id="rail-{key}" class="site-directory-section" data-rail-key="{key}" data-product-count="{len(products)}">
              <header><div><h2>{title}</h2><p>{len(products)} products from the captured source rail</p></div><div class="site-directory-actions">{department_link}<a href="#directory-departments">Back to departments</a></div></header>
              <div class="site-directory-grid">{cards}</div>
            </section>
            """
        )
    body = f"""
    <main id="main" class="site-directory-main desktop-shell" data-total-products="{total}">
      <header class="site-directory-hero"><h1>Amazon site directory</h1><p>Browse all {total} unique products captured from today's homepage, organized by their original source rails.</p></header>
      <nav id="directory-departments" class="site-directory-nav" aria-label="Source departments">{anchors}</nav>
      {"".join(rail_markup)}
    </main>
    """
    return layout(
        "Amazon.com - Site directory",
        body,
        cart_count,
        body_class="site-directory-page",
        account_name=account_name,
    )


def checkboxes(labels: Iterable[str]) -> str:
    return "".join(f'<label class="filter-check"><input type="checkbox"> {escape(label)}</label>' for label in labels)


def search_result(
    product: dict[str, Any],
    *,
    image_path: str | None = None,
    bought: str = "",
    delivery: str = "",
    badge: str | None = None,
    sponsored: bool = False,
    buying_choices: str = "",
) -> str:
    result_title = product["title"]
    result_image = image_path or product["image_path"]
    result_badge = product.get("badge") if badge is None else badge
    heading_label = '<span class="sponsored-label">Sponsored</span>' if sponsored else ""
    choices = f'<p class="buying-choices">{escape(buying_choices)}</p>' if buying_choices else ""
    bought_markup = f'<p class="bought">{escape(bought)}</p>' if bought else ""
    delivery_markup = (
        f'<p>{escape(delivery)}<br>Ships to Singapore</p>' if delivery else ""
    )
    compare_markup = compare_add_form(product, compact=True)
    purchase_markup = generic_add_to_cart_form(
        product,
        quantity_id=f'search-quantity-{str(product["asin"]).lower()}',
        selected_options=product.get("default_selected_options"),
    )
    return f"""
    <article class="search-result" data-asin="{product['asin']}">
      <a class="result-image" href="{product_href(product)}"><img src="{result_image}" width="225" height="218" alt="{escape(result_title, quote=True)}"></a>
      <div class="result-copy">{f'<span class="best-seller-badge">{escape(result_badge)}</span>' if result_badge else ''}{heading_label}<h2><a href="{product_href(product)}">{escape(result_title)}</a></h2><a class="rating" href="{product_review_href(product)}">{product['rating']} <span class="stars">★★★★★</span> <small>({product['reviews']:,})</small></a>{bought_markup}<a class="result-price" href="{product_href(product)}">{money(product['price_minor'], product['currency'])}</a>{delivery_markup}{choices}{purchase_markup}{compare_markup}</div>
    </article>
    """


def generic_add_to_cart_form(
    product: dict[str, Any],
    *,
    quantity_max: int = 30,
    quantity_id: str | None = None,
    button_class: str = "",
    selected_options: dict[str, str] | None = None,
) -> str:
    """Render the ordinary commerce form used outside the strict T7 journey."""

    asin = escape(str(product["asin"]), quote=True)
    field_id = quantity_id or f"quantity-{asin.lower()}"
    limit = max(1, min(int(quantity_max), 30))
    options = "".join(
        f'<option value="{quantity}">Quantity: {quantity}</option>'
        for quantity in range(1, limit + 1)
    )
    extra_button_class = f" {button_class}" if button_class else ""
    option_fields = "".join(
        f'<input type="hidden" name="option.{escape(str(label), quote=True)}" '
        f'value="{escape(str(value), quote=True)}" '
        f'data-product-option-field="{escape(str(label), quote=True)}">'
        for label, value in (selected_options or {}).items()
    )
    return f"""
            <form class="generic-cart-form" method="post" action="/gp/cart/add.html">
              <input type="hidden" name="ASIN" value="{asin}">
              {option_fields}
              <label class="sr-only" for="{field_id}">Quantity</label>
              <select id="{field_id}" class="generic-quantity" name="quantity" aria-label="Quantity">{options}</select>
              <button class="generic-add-to-cart{extra_button_class}" type="submit" data-product-add-to-cart>Add to cart</button>
            </form>
    """


def wishlist_add_form(
    product: dict[str, Any],
    selected_options: dict[str, str] | None = None,
    *,
    css_class: str = "add-to-list-form",
) -> str:
    """Render the PDP entry point while keeping Wishlist views decoupled.

    The lazy import avoids a module cycle: ``wishlist_views`` reuses the global
    Amazon layout, money, and option-markup helpers from this module.
    """

    from wishlist_views import wishlist_entry_form

    defaults = product.get("default_selected_options")
    selection = (
        {str(label): str(value) for label, value in defaults.items()}
        if selected_options is None and isinstance(defaults, dict)
        else dict(selected_options or {})
    )
    return wishlist_entry_form(
        product,
        selection,
        css_class=css_class,
    )


def _deals_filter_url(
    filters: dict[str, Any], **overrides: Any
) -> str:
    state: dict[str, Any] = {
        "theme": filters.get("theme") or "",
        "department": filters.get("department") or "",
        "brand": list(filters.get("brands") or ()),
        "rating": filters.get("rating") or "",
        "minPrice": filters.get("min_price_text") or "",
        "maxPrice": filters.get("max_price_text") or "",
        "minDiscount": filters.get("min_discount_text") or "",
        "maxDiscount": filters.get("max_discount_text") or "",
        "dealType": filters.get("deal_type") or "",
    }
    state.update(overrides)
    pairs: list[tuple[str, str]] = []
    for key in (
        "theme",
        "department",
        "rating",
        "minPrice",
        "maxPrice",
        "minDiscount",
        "maxDiscount",
        "dealType",
    ):
        value = state.get(key)
        if isinstance(value, str) and value:
            pairs.append((key, value))
    raw_brands = state.get("brand")
    if isinstance(raw_brands, (list, tuple)):
        pairs.extend(
            ("brand", str(brand)) for brand in raw_brands if str(brand)
        )
    query = urlencode(pairs, doseq=True)
    return "/gp/goldbox/" + (f"?{query}" if query else "")


def _deals_quick_add_form(product: dict[str, Any]) -> str:
    asin = escape(str(product["asin"]), quote=True)
    label = escape(f"Add {product['title']} to cart", quote=True)
    return f"""
    <form class="deals-quick-add" method="post" action="/gp/cart/add.html">
      <input type="hidden" name="ASIN" value="{asin}">
      <input type="hidden" name="quantity" value="1">
      <button type="submit" aria-label="{label}" title="Add to cart"><span aria-hidden="true">+</span></button>
    </form>
    """


def verified_offer_card(
    product: dict[str, Any], filters: dict[str, Any] | None = None
) -> str:
    """Render one strict offer and only the deal claims retained in evidence."""

    active_filters = filters or {}
    asin = escape(str(product["asin"]), quote=True)
    title = escape(str(product["title"]))
    href = escape(product_href(product), quote=True)
    review_href = escape(product_review_href(product), quote=True)
    image_path = escape(str(product["image_path"]), quote=True)
    price = money(int(product["price_minor"]), str(product["currency"]))
    brand = str(product.get("brand") or "").strip()
    brand_markup = (
        f'<a class="deals-brand" href="{escape(_deals_filter_url(active_filters, brand=[brand]), quote=True)}">{escape(brand)}</a>'
        if brand
        else ""
    )
    rating = str(product.get("rating") or "").strip()
    reviews = product.get("reviews")
    rating_markup = ""
    if rating and isinstance(reviews, int) and reviews > 0:
        rating_markup = (
            f'<a class="deals-rating" href="{review_href}" aria-label="{escape(rating, quote=True)} out of 5 stars, {reviews:,} reviews">'
            f'<span>{escape(rating)}</span><i aria-hidden="true">★★★★★</i><small>{reviews:,}</small></a>'
        )
    discount = product.get("discount_percent")
    list_price = product.get("list_price_minor")
    discount_markup = ""
    reference_markup = ""
    if (
        isinstance(list_price, int)
        and not isinstance(list_price, bool)
        and list_price > int(product["price_minor"])
    ):
        reference_label = escape(
            str(product.get("reference_price_label") or "List price")
        )
        reference_markup = (
            f'<p class="deals-reference-price">{reference_label}: <del>{money(list_price, str(product["currency"]))}</del></p>'
        )
    if (
        isinstance(discount, int)
        and discount > 0
    ):
        limited_label = (
            '<strong class="deals-limited-label">Limited time deal</strong>'
            if product.get("limited_time_deal")
            else ""
        )
        discount_markup = (
            f'<div class="deals-badge-row"><span class="deals-discount">-{discount}%</span>{limited_label}</div>'
        )
    image_alt = escape(
        str(product.get("image_alt") or product["title"]), quote=True
    )
    return f"""
    <article class="deals-card verified-offer-card" data-asin="{asin}" data-currency="USD" data-department="{escape(str(product.get('department_slug') or ''), quote=True)}" data-brand="{escape(brand, quote=True)}" data-price-minor="{int(product['price_minor'])}">
      <div class="deals-image-well"><a class="deals-product-image" href="{href}"><img src="{image_path}" width="240" height="240" alt="{image_alt}" loading="lazy"></a>{_deals_quick_add_form(product)}</div>
      <div class="deals-card-copy">{discount_markup}<a class="deals-price" href="{href}">{price}</a>{reference_markup}{brand_markup}<h2><a href="{href}">{title}</a></h2>{rating_markup}</div>
    </article>
    """


def deals_page(
    deals_view: dict[str, Any],
    cart_count: int,
    account_name: str | None = None,
) -> str:
    """Render evidence-backed Deals with server-owned, copyable GET filters."""

    products = deals_view.get("products")
    products = products if isinstance(products, list) else []
    filters = deals_view.get("filters")
    filters = filters if isinstance(filters, dict) else {}
    result_count = int(deals_view.get("result_count") or 0)
    all_count = int(deals_view.get("all_count") or 0)
    active_count = int(deals_view.get("active_filter_count") or 0)
    cards = "".join(verified_offer_card(product, filters) for product in products)
    theme_pills = [
        f'<a class="deals-theme-pill{(" active" if not filters.get("theme") else "")}" href="{escape(_deals_filter_url(filters, theme=""), quote=True)}"{(" aria-current=\"page\"" if not filters.get("theme") else "")}>All deals</a>'
    ]
    for raw_theme in deals_view.get("theme_chips", ()):
        if not isinstance(raw_theme, (list, tuple)) or len(raw_theme) != 2:
            continue
        slug, label = str(raw_theme[0]), str(raw_theme[1])
        active = filters.get("theme") == slug
        theme_pills.append(
            f'<a class="deals-theme-pill{(" active" if active else "")}" href="{escape(_deals_filter_url(filters, theme=slug), quote=True)}"{(" aria-current=\"page\"" if active else "")}>{escape(label)}</a>'
        )

    department_rows = [
        '<label><input type="radio" name="department" value=""'
        + (' checked' if not filters.get("department") else "")
        + f'><span>All</span><small>{all_count}</small></label>'
    ]
    for department in deals_view.get("departments", ()):
        if not isinstance(department, dict):
            continue
        slug = str(department.get("slug") or "")
        department_rows.append(
            f'<label><input type="radio" name="department" value="{escape(slug, quote=True)}"'
            f'{(" checked" if filters.get("department") == slug else "")}><span>{escape(str(department.get("label") or slug))}</span><small>{int(department.get("count") or 0)}</small></label>'
        )
    selected_brands = set(filters.get("brands") or ())
    brand_rows = []
    for brand in deals_view.get("brands", ()):
        if not isinstance(brand, dict):
            continue
        label = str(brand.get("label") or "")
        brand_rows.append(
            f'<label><input type="checkbox" name="brand" value="{escape(label, quote=True)}"'
            f'{(" checked" if label in selected_brands else "")}><span>{escape(label)}</span><small>{int(brand.get("count") or 0)}</small></label>'
        )
    range_error = str(filters.get("range_error") or "")
    error_markup = (
        f'<p class="deals-filter-error" role="alert">{escape(range_error)}</p>'
        if range_error
        else ""
    )
    no_results = ""
    if not cards:
        no_results = f"""
        <section class="deals-empty" aria-live="polite"><h2>No deals match these filters</h2><p>Clear the filters or choose a different evidence-backed range.</p><a class="button primary" href="/gp/goldbox/">Clear all filters</a></section>
        """
    theme_hidden = (
        f'<input type="hidden" name="theme" value="{escape(str(filters.get("theme")), quote=True)}">'
        if filters.get("theme")
        else ""
    )
    body = f"""
    <main id="main" class="deals-main desktop-shell" data-deals-page data-offer-count="{all_count}" data-deals-result-count="{result_count}">
      <nav class="deals-title-bar" aria-label="Today's Deals"><a aria-current="page" href="/gp/goldbox/">Today's Deals</a><a href="/gp/goldbox/?theme=lightning-deals">Lightning Deals</a><a href="/gp/goldbox/?maxPrice=50">Deals under $50</a><a href="/gp/goldbox/?theme=amazon-brands">Amazon Brands</a></nav>
      <h1 class="sr-only">Today's Deals</h1>
      <nav class="deals-theme-strip" aria-label="Deal themes">{''.join(theme_pills)}</nav>
      <div class="deals-content">
        <aside class="deals-filters" aria-label="Filter deals">
          {f'<p class="sr-only">{active_count} filters active</p>' if active_count else ''}
          {error_markup}
          <form method="get" action="/gp/goldbox/" data-deals-filter-form>
            {theme_hidden}
            <fieldset><legend>Department</legend><div class="deals-filter-options">{''.join(department_rows)}</div></fieldset>
            <fieldset><legend>Brands</legend><div class="deals-filter-options deals-brand-options">{''.join(brand_rows)}</div></fieldset>
            <fieldset><legend>Customer Reviews</legend><div class="deals-filter-options"><label><input type="radio" name="rating" value=""{(' checked' if not filters.get('rating') else '')}><span>All</span></label><label><input type="radio" name="rating" value="4-up"{(' checked' if filters.get('rating') == '4-up' else '')}><span><i class="deals-filter-stars" aria-hidden="true">★★★★</i> &amp; up</span></label></div></fieldset>
            <fieldset><legend>Price</legend><div class="deals-range-fields"><label><span>Minimum</span><input type="number" name="minPrice" min="0" max="{escape(str(filters.get('price_limit_text') or '158'), quote=True)}" step="0.01" value="{escape(str(filters.get('min_price_text') or ''), quote=True)}" placeholder="$0"></label><label><span>Maximum</span><input type="number" name="maxPrice" min="0" max="{escape(str(filters.get('price_limit_text') or '158'), quote=True)}" step="0.01" value="{escape(str(filters.get('max_price_text') or ''), quote=True)}" placeholder="${escape(str(filters.get('price_limit_text') or '158'), quote=True)}"></label></div></fieldset>
            <fieldset><legend>Discount</legend><div class="deals-range-fields"><label><span>Minimum</span><input type="number" name="minDiscount" min="0" max="75" step="1" value="{escape(str(filters.get('min_discount_text') or ''), quote=True)}" placeholder="0%"></label><label><span>Maximum</span><input type="number" name="maxDiscount" min="0" max="75" step="1" value="{escape(str(filters.get('max_discount_text') or ''), quote=True)}" placeholder="75%"></label></div></fieldset>
            <fieldset><legend>Deal type</legend><div class="deals-filter-options"><label><input type="checkbox" name="dealType" value="limited-time"{(' checked' if filters.get('deal_type') == 'limited-time' else '')}><span>Limited-time deal</span></label></div></fieldset>
            <button class="deals-apply-filter" type="submit">Apply filters</button>
            <a class="deals-clear-filters" href="/gp/goldbox/">Clear all filters</a>
          </form>
        </aside>
        <section class="deals-results" aria-labelledby="deals-results-heading"><h2 id="deals-results-heading" class="sr-only">Today's Deals results</h2><p class="sr-only" aria-live="polite">Showing {result_count} of {all_count} source-backed offers</p><div class="deals-grid" aria-label="Today's Deals results">{cards}</div>{no_results}</section>
      </div>
    </main>
    """
    return layout(
        "Amazon.com - Today's Deals",
        body,
        cart_count,
        body_class="deals-page verified-offers-page",
        account_name=account_name,
    )


def deal_card_product_page(
    product: dict[str, Any],
    cart_count: int,
    account_name: str | None = None,
    reviews_html: str = "",
) -> str:
    """Render only the offer facts retained from a current Deals card."""

    title = escape(str(product["title"]))
    image_alt = escape(
        str(product.get("image_alt") or product["title"]), quote=True
    )
    image_path = escape(str(product["image_path"]), quote=True)
    currency = str(product.get("currency") or "USD")
    price = money(int(product["price_minor"]), currency)
    brand = str(product.get("brand") or "").strip()
    full_view_trigger, full_view_dialog = pdp_full_view_controls(
        image_path, str(product["title"])
    )
    brand_markup = (
        f'<a class="deal-card-pdp-brand" href="{escape(shopping_browse_href(brand), quote=True)}">'
        f'{escape(brand)}</a>'
        if brand
        else ""
    )
    reference_markup = ""
    list_price = product.get("list_price_minor")
    if (
        isinstance(list_price, int)
        and not isinstance(list_price, bool)
        and list_price > int(product["price_minor"])
    ):
        reference_label = escape(
            str(product.get("reference_price_label") or "List price")
        )
        reference_markup = (
            f'<p class="deal-card-pdp-reference">{reference_label}: '
            f'<del>{money(list_price, currency)}</del></p>'
        )
    discount_markup = ""
    discount = product.get("discount_percent")
    if isinstance(discount, int) and discount > 0:
        limited = (
            '<strong class="deals-limited-label">Limited time deal</strong>'
            if product.get("limited_time_deal")
            else ""
        )
        discount_markup = (
            f'<div class="deals-badge-row"><span class="deals-discount">-{discount}%</span>{limited}</div>'
        )
    asin = escape(str(product["asin"]), quote=True)
    quote_data_attributes = _quote_data_attributes(product)
    body = f"""
    <main id="main" class="pdp-main deal-card-pdp-main desktop-shell" data-asin="{asin}" data-pdp-variant="direct-deals-card" data-evidence-level="direct-deals-card" {quote_data_attributes}>
      <nav class="breadcrumb"><a href="/gp/goldbox/">Today's Deals</a><span>›</span><span>Current deal</span></nav>
      <section class="deal-card-pdp-grid">
        <div class="deal-card-pdp-image"><img src="{image_path}" width="520" height="520" alt="{image_alt}">{full_view_trigger}</div>
        <div class="deal-card-pdp-facts">
          {brand_markup}
          <h1 id="productTitle">{title}</h1>
          <a class="pdp-review-entry" href="{product_review_href(product)}">Customer reviews</a>
          <hr>
          {discount_markup}
          <p class="deal-card-pdp-price" data-product-price data-price-minor="{int(product['price_minor'])}" data-currency="{escape(currency, quote=True)}">{price}</p>
          {reference_markup}
          <p class="deal-card-evidence-note">This page is limited to facts retained from the current Today's Deals card. Rating, reviews, delivery, inventory, and product options were not captured.</p>
        </div>
        <aside class="deal-card-pdp-buybox">
          <section class="buybox">
            <p class="buybox-price" data-product-price data-price-minor="{int(product['price_minor'])}" data-currency="{escape(currency, quote=True)}">{price}</p>
            {generic_add_to_cart_form(product, quantity_max=30, quantity_id=f'deal-quantity-{str(product["asin"]).lower()}')}
            <form class="deal-card-buy-now-form" method="post" action="/gp/buy/now">
              <input type="hidden" name="ASIN" value="{asin}">
              <input type="hidden" name="quantity" value="1">
              <button class="buy-now" type="submit">Buy Now</button>
            </form>
            {wishlist_add_form(product, {})}
          </section>
        </aside>
      </section>
      {full_view_dialog}
    </main>
    """
    if reviews_html:
        body = body.replace("\n    </main>", f"\n      {reviews_html}\n    </main>", 1)
    return layout(
        f"Amazon.com: {product['title']}",
        body,
        cart_count,
        body_class="pdp-page deal-card-pdp-page",
        account_name=account_name,
    )


def search_card_product_page(
    product: dict[str, Any],
    cart_count: int,
    account_name: str | None = None,
    reviews_html: str = "",
) -> str:
    """Render the narrow detail shell authorized by one captured search card."""

    title = escape(str(product["title"]))
    asin = escape(str(product["asin"]), quote=True)
    image_path = escape(str(product["image_path"]), quote=True)
    currency = str(product.get("currency") or "USD")
    price = money(int(product["price_minor"]), currency)
    full_view_trigger, full_view_dialog = pdp_full_view_controls(
        image_path, str(product["title"])
    )
    rating = escape(str(product.get("rating") or ""))
    reviews_display = escape(str(product.get("reviews_display") or ""))
    sponsored = (
        '<span class="sponsored-label">Sponsored</span>'
        if product.get("sponsored")
        else ""
    )
    product_format = product.get("format")
    format_markup = (
        f'<p class="search-card-format"><strong>Format:</strong> {escape(str(product_format))}</p>'
        if isinstance(product_format, str) and product_format
        else ""
    )
    department_slug = next(iter(product.get("department_slugs") or ()), "")
    department_labels = {
        "books": "Books",
        "home-kitchen": "Home & Kitchen",
        "toys-games": "Toys & Games",
        "computers": "Computers & Accessories",
        "beauty-personal-care": "Beauty & Personal Care",
    }
    department_label = department_labels.get(str(department_slug), "All Departments")
    quote_data_attributes = _quote_data_attributes(product)
    body = f"""
    <main id="main" class="pdp-main deal-card-pdp-main search-card-pdp-main desktop-shell" data-asin="{asin}" data-pdp-variant="direct-search-card" data-evidence-level="direct-search-card" {quote_data_attributes}>
      <nav class="breadcrumb"><a href="/s?k=best+sellers">Best sellers search</a><span>›</span><a href="/s?i={escape(str(department_slug), quote=True)}">{escape(department_label)}</a><span>›</span><span>Captured offer</span></nav>
      <section class="deal-card-pdp-grid">
        <div class="deal-card-pdp-image"><img src="{image_path}" width="520" height="520" alt="{escape(str(product['title']), quote=True)}">{full_view_trigger}</div>
        <div class="deal-card-pdp-facts">
          <span class="evidence-tier-badge verified-offer">Verified search-card offer</span>{sponsored}
          <h1 id="productTitle">{title}</h1>
          {format_markup}
          <a class="pdp-review-entry" href="{escape(product_review_href(product), quote=True)}">{rating} out of 5 stars ({reviews_display})</a>
          <hr>
          <p class="deal-card-pdp-price" data-product-price data-price-minor="{int(product['price_minor'])}" data-currency="{escape(currency, quote=True)}">{price}</p>
          <p class="deal-card-evidence-note">This is a card-evidence detail shell, not a complete product page capture. The title, displayed format, aggregate rating copy, image, USD price, sponsored status, and Add to cart eligibility are retained only when present on the source search card. No seller, inventory count, delivery date, list price, product options, or full review set is inferred.</p>
        </div>
        <aside class="deal-card-pdp-buybox">
          <section class="buybox">
            <p class="buybox-price" data-product-price data-price-minor="{int(product['price_minor'])}" data-currency="{escape(currency, quote=True)}">{price}</p>
            {generic_add_to_cart_form(product, quantity_max=30, quantity_id=f'search-card-detail-quantity-{str(product["asin"]).lower()}')}
            <form class="deal-card-buy-now-form" method="post" action="/gp/buy/now">
              <input type="hidden" name="ASIN" value="{asin}">
              <input type="hidden" name="quantity" value="1">
              <button class="buy-now" type="submit">Buy Now</button>
            </form>
            {compare_add_form(product)}
            {wishlist_add_form(product, {})}
          </section>
        </aside>
      </section>
      {full_view_dialog}
    </main>
    """
    if reviews_html:
        body = body.replace("\n    </main>", f"\n      {reviews_html}\n    </main>", 1)
    return layout(
        f"Amazon.com: {product['title']}",
        body,
        cart_count,
        body_class="pdp-page search-card-pdp-page",
        account_name=account_name,
    )


def generic_product_page(
    product: dict[str, Any],
    cart_count: int,
    account_name: str | None = None,
    reviews_html: str = "",
) -> str:
    """Render a product-specific evidence page without borrowing T7-only content."""
    title = escape(product["title"])
    brand = escape(product["brand"])
    capacity = escape(product["capacity"])
    color = escape(product["color"])
    image_path = escape(product["image_path"], quote=True)
    full_view_trigger, full_view_dialog = pdp_full_view_controls(
        str(product["image_path"]), str(product["title"])
    )
    brand_href = escape(shopping_browse_href(str(product["brand"]), "Electronics"), quote=True)
    breadcrumb_links = "<span>›</span>".join(
        f'<a href="{escape(shopping_browse_href(label, "Electronics"), quote=True)}">{escape(label)}</a>'
        for label in (
            "Electronics",
            "Computers & Accessories",
            "Data Storage",
            "External Solid State Drives",
        )
    )
    price_whole, price_cents = divmod(product["price_minor"], 100)
    currency_symbol = "$" if product["currency"] == "USD" else escape(product["currency"])
    quote_data_attributes = _quote_data_attributes(product)
    badge = (
        f'<span class="choice-badge">{escape(product["badge"])}</span>'
        if product.get("badge")
        else ""
    )
    list_price = (
        f'<p class="list-price">List Price: <s>{money(product["list_price_minor"], product["currency"])}</s></p>'
        if product.get("list_price_minor") is not None
        else ""
    )
    body = f"""
    <main id="main" class="pdp-main generic-pdp-main desktop-shell" data-asin="{product['asin']}" data-pdp-variant="catalog-evidence" {quote_data_attributes}>
      <nav class="breadcrumb">{breadcrumb_links}</nav>
      <section class="pdp-grid generic-pdp-grid">
        <div class="generic-product-gallery" data-source-scope="single-current-product-image">
          <div class="generic-thumbnail-list"><button type="button" aria-current="true" aria-label="View product image 1"><img src="{image_path}" width="40" height="40" alt=""></button></div>
          <div class="generic-main-image"><button class="pdp-share" type="button" aria-label="Share this product">⇧</button><img id="pdp-main-image" src="{image_path}" width="409" height="373" alt="{escape(product['title'], quote=True)}"><span class="mobile-gallery-index">1 / 1</span>{full_view_trigger}</div>
        </div>
        <div class="pdp-facts generic-pdp-facts">
          <a class="generic-brand" href="{brand_href}">Visit the {brand} Store</a>
          <h1 id="productTitle">{title}</h1>
          <div class="pdp-rating"><span>{product['rating']}</span> <span class="stars">★★★★★</span> <a href="{escape(product_review_href(product), quote=True)}">({product['reviews']:,})</a></div>
          {badge}<hr>
          <div class="pdp-price"><strong data-product-price data-price-minor="{product['price_minor']}" data-currency="{escape(product['currency'], quote=True)}"><span class="price-symbol">{currency_symbol}</span>{price_whole:,}<sup>{price_cents:02d}</sup></strong></div>
          {list_price}
          <a href="{RETURNS_REPLACEMENTS_HREF}">FREE International Returns⌄</a>
          <p>Ships to Singapore. Delivery and import-charge details are shown at checkout.</p>
          <p class="color-label">Color: <strong>{color}</strong></p>
          <div class="generic-option-row"><button type="button" aria-pressed="true">{color}</button></div>
          <h3 class="capacity-heading">Digital Storage Capacity: <strong>{capacity}</strong></h3>
          <div class="capacity-options"><button class="capacity selected" type="button">{capacity}</button></div>
          <section class="pdp-specs" aria-label="Product overview"><dl><dt>Digital Storage Capacity</dt><dd>{capacity}</dd><dt>Brand</dt><dd>{brand}</dd><dt>Installation Type</dt><dd>External Drive</dd><dt>Color</dt><dd>{color}</dd></dl></section>
          <section class="about"><h2>About this item</h2><ul><li>Portable solid-state storage for compatible computers and mobile devices.</li><li>Compact external design helps keep files available at home, at work and on the go.</li></ul></section>
        </div>
        <aside class="pdp-buy-column">
          <section class="buybox generic-buybox">
            <div class="buybox-price" data-product-price data-price-minor="{product['price_minor']}" data-currency="{escape(product['currency'], quote=True)}"><span class="price-symbol">{currency_symbol}</span><span>{price_whole:,}</span><sup>{price_cents:02d}</sup></div>
            <p>Ships to Singapore</p>
            <a class="buybox-details" href="{SHIPPING_POLICIES_HREF}">Details⌄</a>
            <p class="buybox-tax"><span class="info-icon" aria-hidden="true">i</span><span>Sales taxes may apply at checkout</span></p>
            <a class="deliver-link" href="{DELIVERY_PREFERENCE_HREF}"><span class="pin-icon"></span> Deliver to Singapore</a>
            {generic_add_to_cart_form(product)}
            {compare_add_form(product)}
            <button class="buy-now" type="button" data-product-buy-now data-quote-can-enable="true">Buy Now</button>
            <dl><dt>Product</dt><dd>{brand} {capacity}</dd><dt>Returns</dt><dd><a href="{RETURNS_REPLACEMENTS_HREF}">Return policy</a></dd><dt>Payment</dt><dd><button class="pdp-inline-link" type="button" data-pdp-info-open aria-controls="pdp-secure-transaction-dialog">Secure transaction</button></dd></dl>
            <a class="buybox-more" href="{CUSTOMER_SERVICE_HREF}">See more purchase help</a><hr class="buybox-rule">
            {wishlist_add_form(product)}
          </section>
        </aside>
      </section>
      {full_view_dialog}
      {secure_transaction_dialog()}
      <section class="pdp-below"><h2>Product information</h2><p>{brand} portable storage · {capacity} · {color}</p></section>
    </main>
    """
    if reviews_html:
        body = body.replace("\n    </main>", f"\n      {reviews_html}\n    </main>", 1)
    return layout(
        f"Amazon.com: {product['title']} : Electronics",
        body,
        cart_count,
        body_class="pdp-page generic-pdp-page",
        account_name=account_name,
    )


def home_card_product_page(
    product: dict[str, Any],
    cart_count: int,
    account_name: str | None = None,
    reviews_html: str = "",
) -> str:
    """Render only facts frozen on the homepage while richer PDP evidence is pending."""

    title = escape(product["title"])
    title_attr = escape(product["title"], quote=True)
    image_path = escape(product["image_path"], quote=True)
    full_view_trigger, full_view_dialog = pdp_full_view_controls(
        str(product["image_path"]), str(product["title"])
    )
    asin = escape(product["asin"], quote=True)
    title_status = escape(product.get("title_status", "home-observed"), quote=True)
    body = f"""
    <main id="main" class="pdp-main home-card-pdp-main desktop-shell" data-asin="{asin}" data-pdp-variant="home-card-evidence" data-evidence-level="home-card-only" data-title-status="{title_status}">
      <a class="international-promo" href="{SITE_DIRECTORY_HREF}" aria-label="Shop top categories that ship internationally"><img src="/static/assets/source-current/2026-07-21/pdp-t7/international-promo.png" width="649" height="45" alt="Shop top categories that ship internationally"></a>
      <section class="pdp-grid home-card-pdp-grid">
        <div class="pdp-gallery home-card-pdp-gallery" data-gallery-images="{image_path}" tabindex="0" aria-label="Product image gallery">
          <div class="thumbnail-list"><button class="pdp-thumbnail" type="button" data-gallery-src="{image_path}" aria-label="View product image 1" aria-current="true"><img src="{image_path}" width="40" height="40" alt=""></button></div>
          <div class="pdp-main-image"><button class="pdp-share" type="button" aria-label="Share this product">⇧</button><img id="pdp-main-image" src="{image_path}" width="409" height="373" alt="{title_attr}"><span class="mobile-gallery-index" aria-live="polite">1 / 1</span>{full_view_trigger}</div>
        </div>
        <div class="pdp-facts home-card-pdp-facts">
          <h1 id="productTitle">{title}</h1>
          <a class="pdp-review-entry" href="{product_review_href(product)}">Customer reviews</a>
          <hr>
          <p class="home-card-evidence-note">Product offer details are not available in this local marketplace snapshot.</p>
        </div>
        <aside class="pdp-buy-column">
          <section class="buybox home-card-buybox">
            <h2>Shopping options</h2>
            <p>Offer and delivery information is not available for this item yet.</p>
            <a class="deliver-link" href="{DELIVERY_PREFERENCE_HREF}"><span class="pin-icon"></span> Deliver to Singapore</a>
            <button class="generic-add-to-cart" type="button" disabled>Add to cart</button>
            <button class="buy-now" type="button" disabled>Buy Now</button>
            <hr class="buybox-rule">
            {wishlist_add_form(product)}
          </section>
        </aside>
      </section>
      {full_view_dialog}
      <section class="pdp-below"><h2>Product information</h2><p>ASIN: {asin}</p></section>
    </main>
    """
    if reviews_html:
        body = body.replace("\n    </main>", f"\n      {reviews_html}\n    </main>", 1)
    return layout(
        f"Amazon.com: {product['title']}",
        body,
        cart_count,
        body_class="pdp-page home-card-pdp-page",
        account_name=account_name,
    )


def evidence_product_page(
    product: dict[str, Any],
    cart_count: int,
    account_name: str | None = None,
    reviews_html: str = "",
) -> str:
    """Render a source-backed PDP from reusable fixture detail data."""
    detail = product["pdp"]
    quote_data_attributes = _quote_data_attributes(product)
    title = escape(product["title"])
    brand = escape(product["brand"])
    brand_attr = escape(product["brand"], quote=True)
    price_whole, price_cents = divmod(product["price_minor"], 100)
    currency_symbol = "$" if product["currency"] == "USD" else escape(product["currency"])
    gallery_paths = [escape(path, quote=True) for path in detail["gallery"]]
    full_view_trigger, full_view_dialog = pdp_full_view_controls(
        str(detail["gallery"][0]), str(product["title"])
    )
    thumbnail_paths = [
        escape(path, quote=True)
        for path in detail.get("thumbnail_paths", detail["gallery"])
    ]
    if len(thumbnail_paths) != len(gallery_paths):
        thumbnail_paths = gallery_paths
    more_count = int(detail.get("gallery_more_count", 0))
    visible_thumbnail_count = max(
        1,
        min(int(detail.get("visible_thumbnail_count", len(gallery_paths))), len(gallery_paths)),
    )
    gallery_buttons: list[str] = []
    for index, (gallery_path, thumbnail_path) in enumerate(
        zip(
            gallery_paths[:visible_thumbnail_count],
            thumbnail_paths[:visible_thumbnail_count],
        ),
        1,
    ):
        is_more = more_count and index == visible_thumbnail_count
        classes = "pdp-thumbnail gallery-more" if is_more else "pdp-thumbnail"
        label = (
            f"View {more_count} more product images"
            if is_more
            else f"View product image {index}"
        )
        overlay = f"<span>{more_count}+</span>" if is_more else ""
        current = ' aria-current="true"' if index == 1 else ""
        gallery_buttons.append(
            f'<button class="{classes}" type="button" data-gallery-src="{gallery_path}" '
            f'aria-label="{label}"{current}><img src="{thumbnail_path}" width="40" '
            f'height="40" alt="">{overlay}</button>'
        )
    gallery_images = escape("|".join(gallery_paths), quote=True)
    video_count = int(detail.get("video_count", 0))
    video_thumbnail = escape(detail.get("video_thumbnail", gallery_paths[0]), quote=True)
    brand_logo = escape(detail.get("brand_logo", ""), quote=True)
    specs = "".join(
        f"<dt>{escape(str(label))}</dt><dd>{escape(str(value))}</dd>"
        for label, value in detail.get("specs", [])
    )
    about_items = "".join(
        f"<li>{escape(str(item))}</li>" for item in detail.get("about", [])
    )
    specs_block = (
        f'<section class="pdp-specs" aria-label="Product overview"><dl>{specs}</dl></section>'
        if specs
        else ""
    )
    if about_items and detail.get("inline_about"):
        about_block = f'<ul class="pdp-feature-bullets">{about_items}</ul>'
    elif about_items:
        about_block = f'<section class="about"><h2>About this item</h2><ul>{about_items}</ul></section>'
    else:
        about_block = ""
    primary_option_label = str(detail.get("primary_option_label", "Digital Storage Capacity"))
    primary_option_value = str(detail.get("primary_option_value", product["capacity"]))
    selected_options = {
        str(group["label"]): str(group["default"])
        for group in option_groups_from_detail(detail, product)
    }
    embedded_defaults = product.get("default_selected_options")
    if isinstance(embedded_defaults, dict):
        selected_options = {
            str(label): str(value) for label, value in embedded_defaults.items()
        }
    primary_options = detail.get(
        "primary_options",
        detail.get("capacity_options", [primary_option_value]),
    )
    capacity_buttons = "".join(
        f'<button class="capacity{" selected" if str(value) == primary_option_value else ""}" '
        f'type="button" aria-pressed="{"true" if str(value) == primary_option_value else "false"}" '
        f'data-product-option data-option-label="{escape(primary_option_label, quote=True)}" '
        f'data-option-value="{escape(str(value), quote=True)}">'
        f"{escape(str(value))}</button>"
        for value in primary_options
    )
    secondary_option_label = str(detail.get("secondary_option_label", "Color"))
    secondary_option_value = str(detail.get("secondary_option_value", product["color"]))
    color_card_parts: list[str] = []
    for option in detail.get("color_options", []):
        selected = option["name"] == product["color"]
        offer_text = option.get("offer_copy") or money(
            option.get("price_minor"), product["currency"]
        )
        classes = "swatch-card selected" if selected else "swatch-card"
        color_card_parts.append(
            f'<button class="{classes}" type="button" aria-pressed="{str(selected).lower()}" '
            f'data-product-option data-option-label="{escape(secondary_option_label, quote=True)}" '
            f'data-option-value="{escape(str(option["name"]), quote=True)}" '
            f'data-option-image="{escape(str(option["image"]), quote=True)}" '
            f'aria-label="{escape(str(option["name"]), quote=True)}, '
            f'{escape(str(offer_text), quote=True)}"><img src="{escape(str(option["image"]), quote=True)}" '
            f'width="56" height="56" alt="{escape(str(option["name"]), quote=True)}">'
            f'<span>{escape(str(offer_text))}</span></button>'
        )
    color_cards = "".join(color_card_parts)
    secondary_option_block = (
        f'<p class="color-label">{escape(secondary_option_label)}: '
        f'<strong data-selected-option-label="{escape(secondary_option_label, quote=True)}">{escape(secondary_option_value)}</strong></p>'
        + (f'<div class="swatches" role="group" aria-label="{escape(secondary_option_label, quote=True)} options">{color_cards}</div>' if color_cards else "")
        if secondary_option_value
        else ""
    )
    primary_option_block = (
        f'<h3 class="capacity-heading">{escape(primary_option_label)}: '
        f'<strong data-selected-option-label="{escape(primary_option_label, quote=True)}">{escape(primary_option_value)}</strong></h3>'
        + (f'<div class="capacity-options" role="group" aria-label="{escape(primary_option_label, quote=True)} options">{capacity_buttons}</div>' if capacity_buttons else "")
        if primary_option_value
        else ""
    )
    choice_groups_markup = ""
    for group in detail.get("choice_groups", []):
        if not isinstance(group, dict):
            continue
        group_label = str(group.get("label", ""))
        group_value = str(group.get("value", ""))
        group_options = group.get("options", [])
        if not group_label or not group_value or not isinstance(group_options, list):
            continue
        if group.get("mode") == "select":
            select_options = "".join(
                f'<option value="{escape(str(option), quote=True)}"'
                f'{" selected" if str(option) == group_value else ""}>'
                f'{escape(str(option))}</option>'
                for option in group_options
                if isinstance(option, str) and option
            )
            if select_options:
                choice_groups_markup += (
                    f'<section class="pdp-option-group pdp-option-select-group"><h3>{escape(group_label)}: '
                    f'<strong data-selected-option-label="{escape(group_label, quote=True)}">{escape(group_value)}</strong></h3>'
                    f'<select class="pdp-choice-select" data-product-option-select '
                    f'data-option-label="{escape(group_label, quote=True)}" '
                    f'aria-label="{escape(group_label, quote=True)}">{select_options}</select></section>'
                )
            continue
        group_buttons: list[str] = []
        if group.get("mode") == "swatch":
            for option in group_options:
                if not isinstance(option, dict) or not option.get("name"):
                    continue
                option_name = str(option["name"])
                selected = option_name == group_value
                offer_copy = str(option.get("offer_copy", ""))
                image_path = str(option.get("image", ""))
                image_markup = (
                    f'<img src="{escape(image_path, quote=True)}" width="64" height="64" '
                    f'alt="{escape(option_name, quote=True)}">'
                    if image_path
                    else ""
                )
                group_buttons.append(
                    f'<button class="pdp-choice-swatch{" selected" if selected else ""}" '
                    f'type="button" aria-pressed="{str(selected).lower()}" '
                    f'data-product-option data-option-label="{escape(group_label, quote=True)}" '
                    f'data-option-value="{escape(option_name, quote=True)}" '
                    f'data-option-image="{escape(image_path, quote=True)}" '
                    f'aria-label="{escape(option_name, quote=True)}{", " + escape(offer_copy, quote=True) if offer_copy else ""}">'
                    f'{image_markup}<span>{escape(option_name)}</span>'
                    f'{f"<small>{escape(offer_copy)}</small>" if offer_copy else ""}</button>'
                )
            options_class = "pdp-choice-options pdp-choice-swatches"
        else:
            for option in group_options:
                option_name = str(option)
                selected = option_name == group_value
                group_buttons.append(
                    f'<button class="pdp-choice-option{" selected" if selected else ""}" '
                    f'type="button" aria-pressed="{str(selected).lower()}" '
                    f'data-product-option data-option-label="{escape(group_label, quote=True)}" '
                    f'data-option-value="{escape(option_name, quote=True)}">'
                    f'{escape(option_name)}</button>'
                )
            options_class = "pdp-choice-options"
        if group_buttons:
            choice_groups_markup += (
                f'<section class="pdp-option-group"><h3>{escape(group_label)}: '
                f'<strong data-selected-option-label="{escape(group_label, quote=True)}">{escape(group_value)}</strong></h3><div class="{options_class}" '
                f'role="group" aria-label="{escape(group_label, quote=True)} options">'
                f'{"".join(group_buttons)}</div></section>'
            )
    option_blocks = choice_groups_markup or (secondary_option_block + primary_option_block)
    retention_note = (
        f'<p class="pdp-retention-note">{escape(str(detail["retention_note"]))}</p>'
        if detail.get("retention_note")
        else ""
    )
    video_slot = (
        '<div class="video-thumbnail-slot"><button class="pdp-thumbnail video-thumb" '
        'type="button" data-video-trigger aria-controls="pdp-video-dialog" '
        f'aria-label="View product videos"><img src="{video_thumbnail}" width="40" '
        'height="40" alt=""><span class="video-play" aria-hidden="true">▶</span>'
        f'</button><small>{video_count} VIDEOS</small></div>'
        if video_count
        else ""
    )
    badge_class = "best-seller-badge" if detail.get("badge_style") == "best-seller" else "choice-badge"
    badge = (
        f'<span class="{badge_class}">{escape(product["badge"])}</span>'
        if product.get("badge")
        else ""
    )
    deal_badge = (
        f'<span class="deal-badge">{escape(str(detail["deal_badge"]))}</span>'
        if detail.get("deal_badge")
        else ""
    )
    discount = (
        f'<span>-{int(detail["discount_percent"])}%</span> '
        if detail.get("discount_percent")
        else ""
    )
    unit_price = (
        f'<span class="pdp-unit-price">{escape(str(detail["unit_price_copy"]))}</span>'
        if detail.get("unit_price_copy")
        else ""
    )
    list_price = (
        f'<p class="list-price">List Price: <s>{money(product["list_price_minor"], product["currency"])}</s></p>'
        if product.get("list_price_minor") is not None
        else ""
    )
    breadcrumb_labels = detail.get(
        "breadcrumb",
        ["Electronics", "Computers & Accessories", "Data Storage", "External Solid State Drives"],
    )
    breadcrumb_links = "<span>›</span>".join(
        f'<a href="{escape(shopping_browse_href(str(label), str(detail.get("page_category", "Electronics"))), quote=True)}">{escape(str(label))}</a>'
        for label in breadcrumb_labels
    )
    raw_byline = detail.get("byline")
    if raw_byline is None:
        raw_byline = f"Visit the {product['brand']} Store"
    byline = escape(str(raw_byline))
    compact_brand_logo = bool(detail.get("compact_brand_logo"))
    brand_logo_class = "samsung-brand compact-brand-logo" if compact_brand_logo else "samsung-brand"
    brand_logo_width = 36 if compact_brand_logo else 158
    brand_logo_height = 36 if compact_brand_logo else 18
    brand_href = escape(
        shopping_browse_href(str(product["brand"]), str(detail.get("page_category", "Electronics"))),
        quote=True,
    )
    brand_logo_markup = (
        f'<a class="{brand_logo_class}" href="{brand_href}" aria-label="{brand_attr}">'
        f'<img src="{brand_logo}" width="{brand_logo_width}" height="{brand_logo_height}" alt="{brand_attr}"></a>'
        if brand_logo
        else ""
    )
    byline_markup = (
        f'<div class="pdp-byline"><a href="{brand_href}">{byline}</a></div>' if byline else ""
    )
    source_rating = product.get("rating")
    source_reviews = product.get("reviews")
    if isinstance(source_rating, str) and source_rating and isinstance(source_reviews, int) and source_reviews > 0:
        rating_markup = (
            f'<div class="pdp-rating"><span>{source_rating}</span> '
            f'<span class="stars">★★★★★</span> '
            f'<a href="{escape(product_review_href(product), quote=True)}">({source_reviews:,})</a></div>'
        )
    else:
        # A direct PDP may establish that a product currently has no reviews
        # without establishing a rating.  Do not manufacture stars or an
        # aggregate from the local-review system in that case.
        source_review_copy = detail.get("source_review_copy")
        rating_markup = (
            f'<p class="pdp-no-source-reviews">{escape(str(source_review_copy))} '
            f'<a href="{escape(product_review_href(product), quote=True)}">Customer reviews</a></p>'
            if isinstance(source_review_copy, str) and source_review_copy
            else (
                f'<a class="pdp-review-entry" href="{escape(product_review_href(product), quote=True)}">'
                "Customer reviews</a>"
            )
        )
    bought_markup = (
        f'<p class="pdp-bought">{escape(str(detail["bought"]))}</p>'
        if detail.get("bought")
        else ""
    )
    if detail.get("title_first"):
        identity_header = (
            f'<h1 id="productTitle">{title}</h1>{byline_markup}{rating_markup}'
            f'{badge}{bought_markup}<hr>'
        )
    else:
        identity_header = (
            f'{brand_logo_markup}{byline_markup}<h1 id="productTitle">{title}</h1>'
            f'{rating_markup}{badge}{bought_markup}<hr>'
        )
    fulfillment_rows: list[str] = []
    for key, label in (("shipper", "Ships from"), ("seller", "Sold by")):
        if detail.get(key):
            fulfillment_rows.append(
                f"<dt>{label}</dt><dd>{escape(str(detail[key]))}</dd>"
            )
    for key, label in (
        ("returns", "Returns"),
        ("support", "Support"),
        ("payment", "Payment"),
        ("gift_options", "Gift options"),
    ):
        if detail.get(key):
            copy = escape(str(detail[key]))
            if key == "returns":
                action = f'<a href="{RETURNS_REPLACEMENTS_HREF}">{copy}</a>'
            elif key == "support":
                action = f'<a href="{CUSTOMER_SERVICE_HREF}&amp;help_keywords=product+support">{copy}</a>'
            elif key == "payment":
                action = (
                    '<button class="pdp-inline-link" type="button" data-pdp-info-open '
                    f'aria-controls="pdp-secure-transaction-dialog">{copy}</button>'
                )
            else:
                action = f'<a href="{GIFT_CARDS_HREF}">{copy}</a>'
            fulfillment_rows.append(f"<dt>{label}</dt><dd>{action}</dd>")
    seller_certification = (
        f'<p class="seller-certification"><strong>Seller Certifications:</strong> '
        f'{escape(str(detail["seller_certification"]))}</p>'
        if detail.get("seller_certification")
        else ""
    )
    fulfillment = (
        f"<dl>{''.join(fulfillment_rows)}</dl>{seller_certification}"
        if fulfillment_rows or seller_certification
        else ""
    )
    page_category = str(detail.get("page_category", "Electronics"))
    product_summary = escape(
        str(
            detail.get(
                "product_summary",
                f"{product['brand']} portable storage · {product['capacity']} · {product['color']}",
            )
        )
    )
    video_description = escape(
        str(detail.get("video_description", f"{product['brand']} portable SSD overview"))
    )
    category_subnav_markup = ""
    category_subnav = detail.get("category_subnav", [])
    if category_subnav:
        category_subnav_links = "".join(
            (
                f'<strong>{escape(str(label))}</strong>'
                if index == 0
                else f'<a href="{escape(shopping_browse_href(str(label), page_category), quote=True)}">{escape(str(label))}</a>'
            )
            for index, label in enumerate(category_subnav)
        )
        category_subnav_markup = (
            f'<nav class="pdp-category-subnav" aria-label="{escape(page_category, quote=True)}">'
            f"{category_subnav_links}</nav>"
        )
    top_promo_image = escape(
        str(
            detail.get(
                "top_promo_image",
                "/static/assets/source-current/2026-07-21/pdp-t7/international-promo.png",
            )
        ),
        quote=True,
    )
    top_promo_alt = escape(
        str(detail.get("top_promo_alt", "Shop top categories that ship internationally")),
        quote=True,
    )
    top_promo_markup = (
        f'<a class="international-promo" href="{SITE_DIRECTORY_HREF}" aria-label="{top_promo_alt}">'
        f'<img src="{top_promo_image}" width="650" height="45" alt="{top_promo_alt}"></a>'
    )
    other_sellers_note = ""
    if detail.get("show_other_sellers_note") is True:
        other_sellers_note = (
            '<p>Available at a lower price from other sellers that may not offer '
            "free Prime shipping.</p>"
        )
    fastest_delivery = (
        f'<p class="fastest-delivery">{escape(str(detail["fastest_delivery_copy"]))}</p>'
        if detail.get("fastest_delivery_copy")
        else ""
    )
    raw_other_sellers_copy = detail.get("other_sellers_copy")
    other_sellers_copy = (
        escape(str(raw_other_sellers_copy))
        if isinstance(raw_other_sellers_copy, str) and raw_other_sellers_copy.strip()
        else ""
    )
    other_sellers_box = (
        '<section class="other-sellers"><h2>Other sellers on Amazon</h2>'
        f'<p>{other_sellers_copy}</p><small>Individual seller offers were not retained in this local snapshot.</small></section>'
        if detail.get("show_other_sellers_box") is True and other_sellers_copy
        else ""
    )
    important_information = detail.get("important_information", {})
    important_information_markup = ""
    if important_information:
        warning = escape(str(important_information.get("warning", "")))
        safety = escape(str(important_information.get("safety_information", "")))
        warning_markup = f'<p class="product-warning"><strong>WARNING:</strong> {warning}</p>' if warning else ""
        safety_markup = f'<h3>Safety Information</h3><p>{safety}</p>' if safety else ""
        important_information_markup = (
            f'<section class="pdp-important"><h2>Important information</h2>'
            f"{warning_markup}{safety_markup}</section>"
        )
    gallery_layout_class = ""
    if detail.get("wide_gallery"):
        gallery_layout_class = " wide-gallery-pdp-main"
    elif detail.get("large_gallery"):
        gallery_layout_class = " large-gallery-pdp-main"
    elif detail.get("classic_gallery"):
        gallery_layout_class = " classic-gallery-pdp-main"
    international_returns = (
        f'<a href="{RETURNS_REPLACEMENTS_HREF}">FREE International Returns⌄</a>'
        if detail.get("show_international_returns", True)
        else ""
    )
    quantity_max = max(1, min(int(detail.get("quantity_max", 3)), 30))
    compare_form = compare_add_form(product)
    body = f"""
    <main id="main" class="pdp-main detailed-pdp-main desktop-shell{gallery_layout_class}" data-asin="{product['asin']}" data-pdp-variant="verified-detail" data-evidence-level="{escape(detail['evidence_level'], quote=True)}" data-page-category="{escape(page_category, quote=True)}" {quote_data_attributes}>
      {category_subnav_markup}
      {top_promo_markup}
      <nav class="breadcrumb">{breadcrumb_links}</nav>
      <section class="pdp-grid detailed-pdp-grid">
        <div class="pdp-gallery detailed-pdp-gallery" data-gallery-images="{gallery_images}" tabindex="0" aria-label="Product image gallery">
          <div class="thumbnail-list">{"".join(gallery_buttons)}{video_slot}</div>
          <div class="pdp-main-image"><button class="pdp-share" type="button" aria-label="Share this product">⇧</button><img id="pdp-main-image" src="{gallery_paths[0]}" width="409" height="373" alt="{escape(product['title'], quote=True)}"><button class="mobile-gallery-control mobile-gallery-prev" type="button" aria-label="Previous product image">‹</button><button class="mobile-gallery-control mobile-gallery-next" type="button" aria-label="Next product image">›</button><span class="mobile-gallery-index" aria-live="polite">1 / {len(gallery_paths)}</span>{full_view_trigger}</div>
        </div>
        <div class="pdp-facts detailed-pdp-facts">
          {identity_header}
          {deal_badge}
          <div class="pdp-price">{discount}<strong data-product-price data-price-minor="{product['price_minor']}" data-currency="{escape(product['currency'], quote=True)}"><span class="price-symbol">{currency_symbol}</span>{price_whole:,}<sup>{price_cents:02d}</sup></strong>{unit_price}</div>
          {list_price}
          {international_returns}
          <p>{escape(detail.get('shipping_copy', 'Ships to Singapore'))} <a href="{SHIPPING_POLICIES_HREF}">Details⌄</a></p>
          <p class="pdp-tax-notice"><span class="info-icon" aria-hidden="true">i</span><span>Sales taxes may apply at checkout</span></p>
          {other_sellers_note}
          {option_blocks}
          <p class="pdp-option-quote-status" data-product-quote-status role="status" aria-live="polite" hidden></p>
          {retention_note}
          {specs_block}
          {about_block}
        </div>
        <aside class="pdp-buy-column">
          <section class="buybox detailed-buybox">
            <div class="buybox-price detailed-subtotal" data-product-price data-price-minor="{product['price_minor']}" data-currency="{escape(product['currency'], quote=True)}"><span class="price-symbol">{currency_symbol}</span><span>{price_whole:,}</span><sup>{price_cents:02d}</sup></div>
            <p class="buybox-import">{escape(detail.get('shipping_copy', 'Ships to Singapore'))}</p>
            <a class="buybox-details" href="{SHIPPING_POLICIES_HREF}">Details⌄</a>
            <p class="buybox-tax"><span class="info-icon" aria-hidden="true">i</span><span>Sales taxes may apply at checkout</span></p>
            <p>{escape(detail.get('delivery_copy', 'Delivery date shown at checkout'))}</p>
            {fastest_delivery}
            <a class="deliver-link" href="{DELIVERY_PREFERENCE_HREF}"><span class="pin-icon"></span> Deliver to Singapore</a>
            <p class="in-stock" data-product-availability>{escape(detail.get('availability', ''))}</p>
            {generic_add_to_cart_form(product, quantity_max=quantity_max, quantity_id="detail-quantity", button_class="detailed-add-to-cart", selected_options=selected_options)}
            {compare_form}
            <button class="buy-now" type="button" data-product-buy-now data-quote-can-enable="true">Buy Now</button>
            {fulfillment}
            <a class="buybox-more" href="{CUSTOMER_SERVICE_HREF}">See more purchase help</a><hr class="buybox-rule">
            {wishlist_add_form(product, selected_options)}
          </section>
          {other_sellers_box}
        </aside>
      </section>
      {full_view_dialog}
      {secure_transaction_dialog()}
      <dialog id="pdp-video-dialog" class="pdp-video-dialog" aria-labelledby="pdp-video-title"><div class="pdp-video-dialog-header"><h2 id="pdp-video-title">Videos for this product</h2><button type="button" data-video-close aria-label="Close product videos">×</button></div><div class="pdp-video-stage"><img src="{gallery_paths[0]}" width="320" height="320" alt="{brand_attr} product video still"><button type="button" aria-label="Play product video">▶</button></div><p>{video_description}</p></dialog>
      <section class="pdp-below"><h2>Product information</h2><p>{product_summary}</p></section>
      {important_information_markup}
    </main>
    """
    if reviews_html:
        body = body.replace("\n    </main>", f"\n      {reviews_html}\n    </main>", 1)
    return layout(
        f"Amazon.com: {product['title']} : {page_category}",
        body,
        cart_count,
        body_class="pdp-page detailed-pdp-page",
        account_name=account_name,
    )


def product_page(
    product: dict[str, Any],
    cart_count: int,
    flow_ready: bool,
    account_name: str | None = None,
    *,
    local_reviews: Iterable[dict[str, Any]] | None = None,
    review_star: int | None = None,
    review_sort: str = "recent",
    review_base_path: str | None = None,
    review_form_error: str | None = None,
) -> str:
    reviews_html = (
        render_reviews_section(
            product["asin"],
            local_reviews,
            star=review_star,
            sort=review_sort,
            account_name=account_name,
            base_path=review_base_path,
            form_error=review_form_error,
            product_label=str(product["title"]),
        )
        if local_reviews is not None
        else ""
    )
    if product.get("evidence_tier") == "home-card-only":
        return home_card_product_page(product, cart_count, account_name, reviews_html)
    if product.get("evidence_class") == "direct-deals-card":
        return deal_card_product_page(
            product, cart_count, account_name, reviews_html
        )
    if product.get("evidence_class") == "direct-search-card":
        return search_card_product_page(
            product, cart_count, account_name, reviews_html
        )
    if product["asin"] != TARGET_ASIN:
        if product.get("pdp"):
            return evidence_product_page(product, cart_count, account_name, reviews_html)
        return generic_product_page(product, cart_count, account_name, reviews_html)

    title = escape(product["title"])
    price = money(product["price_minor"], product["currency"])
    list_price = money(product["list_price_minor"], product["currency"])
    price_whole, price_cents = divmod(product["price_minor"], 100)
    currency_symbol = "$" if product["currency"] == "USD" else escape(product["currency"])
    quote_data_attributes = _quote_data_attributes(product)
    pdp_asset_root = "/static/assets/source-current/2026-07-21/pdp-t7"
    gallery_paths = [f"{pdp_asset_root}/gallery-{index:02d}.jpg" for index in range(1, 11)]
    full_view_trigger, full_view_dialog = pdp_full_view_controls(
        gallery_paths[0], str(product["title"])
    )
    samsung_href = escape(shopping_browse_href("Samsung", "Electronics"), quote=True)
    breadcrumb_links = "<span>›</span>".join(
        f'<a href="{escape(shopping_browse_href(label, "Electronics"), quote=True)}">{escape(label)}</a>'
        for label in (
            "Electronics",
            "Computers & Accessories",
            "Data Storage",
            "External Solid State Drives",
        )
    )
    gallery_buttons = "".join(
        '<button class="pdp-thumbnail" type="button" data-gallery-src="{path}" aria-label="View product image {index}"{current}><img src="{path}" width="40" height="40" alt=""></button>'.format(
            path=path,
            index=index,
            current=' aria-current="true"' if index == 1 else "",
        )
        for index, path in enumerate(gallery_paths[:5], 1)
    )
    gallery_buttons += (
        '<button class="pdp-thumbnail gallery-more" type="button" '
        f'data-gallery-src="{gallery_paths[5]}" aria-label="View three more product images">'
        f'<img src="{gallery_paths[5]}" width="40" height="40" alt=""><span>3+</span></button>'
    )
    gallery_images = escape("|".join(gallery_paths[:8]), quote=True)
    t7_selected_options = {
        str(group["label"]): str(group["default"])
        for group in T7_SOURCE_OPTIONS
    }
    embedded_defaults = product.get("default_selected_options")
    if isinstance(embedded_defaults, dict):
        t7_selected_options = {
            str(label): str(value) for label, value in embedded_defaults.items()
        }
    if flow_ready:
        cart_form = f'<form id="addToCart" method="post" action="{DESKTOP_TERMINAL_PATH}" data-desktop-action="{DESKTOP_TERMINAL_PATH}" data-mobile-action="{MOBILE_TERMINAL_PATH}" data-generic-action="/gp/cart/add.html"><input type="hidden" name="ASIN" value="{product["asin"]}"><label for="quantity">Quantity:</label><select id="quantity" name="quantity"><option value="1">Quantity: 1</option><option value="2">Quantity: 2</option><option value="3">Quantity: 3</option></select><button id="add-to-cart-button" type="submit" data-product-add-to-cart>Add to cart</button></form>'
    else:
        cart_form = generic_add_to_cart_form(
            product,
            quantity_max=3,
            quantity_id="t7-quantity",
            selected_options=t7_selected_options,
        )
    body = f"""
    <main id="main" class="pdp-main desktop-shell" data-asin="{product['asin']}" data-pdp-variant="t7-source-detail" {quote_data_attributes}>
      <a class="international-promo" href="{SITE_DIRECTORY_HREF}" aria-label="Shop top categories that ship internationally"><img src="{pdp_asset_root}/international-promo.png" width="649" height="45" alt="Shop top categories that ship internationally"></a>
      <nav class="breadcrumb">{breadcrumb_links}</nav>
      <section class="pdp-grid">
        <div class="pdp-gallery" data-gallery-images="{gallery_images}" tabindex="0" aria-label="Product image gallery">
          <div class="thumbnail-list">{gallery_buttons}<div class="video-thumbnail-slot"><button class="pdp-thumbnail video-thumb" type="button" data-video-trigger aria-controls="pdp-video-dialog" aria-label="View product videos"><img src="{pdp_asset_root}/video-thumb.jpg" width="40" height="40" alt=""><span class="video-play" aria-hidden="true">▶</span></button><small>9 VIDEOS</small></div></div>
          <div class="pdp-main-image"><button class="pdp-share" type="button" aria-label="Share this product">⇧</button><img id="pdp-main-image" src="{gallery_paths[0]}" width="409" height="373" alt="{title}"><button class="mobile-gallery-control mobile-gallery-prev" type="button" aria-label="Previous product image">‹</button><button class="mobile-gallery-control mobile-gallery-next" type="button" aria-label="Next product image">›</button><span class="mobile-gallery-index" aria-live="polite">1 / 8</span>{full_view_trigger}</div>
        </div>
        <div class="pdp-facts">
          <a class="samsung-brand" href="{samsung_href}" aria-label="Samsung"><img src="{pdp_asset_root}/samsung-logo.jpg" width="158" height="18" alt="SAMSUNG"></a>
          <a href="{samsung_href}">Visit the Samsung Store</a>
          <h1 id="productTitle">{title}</h1>
          <div class="pdp-rating"><span>{product['rating']}</span> <span class="stars">★★★★★</span> <a href="{escape(product_review_href(product), quote=True)}">({product['reviews']:,})</a></div>
          <span class="choice-badge">Amazon's Choice</span><p>4K+ bought in past month</p><hr>
          <div class="pdp-price"><span>-20%</span> <strong data-product-price data-price-minor="{product['price_minor']}" data-currency="{escape(product['currency'], quote=True)}"><span class="price-symbol">{currency_symbol}</span>{price_whole:,}<sup>{price_cents:02d}</sup></strong></div>
          <p class="list-price">List Price: <s>{list_price}</s></p>
          <a href="{RETURNS_REPLACEMENTS_HREF}">FREE International Returns⌄</a>
          <p>No Import Charges &amp; $7.30 Shipping to Singapore <a href="{SHIPPING_POLICIES_HREF}">Details⌄</a></p>
          <p class="pdp-tax-notice"><span class="info-icon" aria-hidden="true">i</span><span>Sales taxes may apply at checkout</span></p>
          <p class="color-label">Color: <strong class="color-name" data-selected-option-label="Color">Titan Gray</strong></p>
          <div class="swatches" role="group" aria-label="Color options">
            <button class="swatch-card selected" type="button" aria-pressed="true" data-product-option data-option-label="Color" data-option-value="Titan Gray" data-option-image="{gallery_paths[0]}" aria-label="Titan Gray, $219.99"><img src="{gallery_paths[0]}" width="56" height="56" alt="Titan Gray"><span>{price}</span><s>{list_price}</s></button>
            <button class="swatch-card" type="button" aria-pressed="false" data-product-option data-option-label="Color" data-option-value="Blue" data-option-image="{gallery_paths[8]}" aria-label="Blue, $267.89"><img src="{gallery_paths[8]}" width="56" height="56" alt="Blue"><span>$267.89</span></button>
            <button class="swatch-card no-featured-offer" type="button">See 1 options with no featured offers</button>
          </div>
          <h3 class="capacity-heading">Memory Storage Capacity: <strong data-selected-option-label="Memory Storage Capacity">1 TB</strong></h3>
          <div class="capacity-options" role="group" aria-label="Memory storage capacity"><button class="capacity selected" type="button" aria-pressed="true" data-product-option data-option-label="Memory Storage Capacity" data-option-value="1 TB">1 TB</button><button class="capacity" type="button" aria-pressed="false" data-product-option data-option-label="Memory Storage Capacity" data-option-value="2 TB">2 TB</button><button class="capacity" type="button" aria-pressed="false" data-product-option data-option-label="Memory Storage Capacity" data-option-value="2.1 TB">2.1 TB</button><button class="capacity" type="button" aria-pressed="false" data-product-option data-option-label="Memory Storage Capacity" data-option-value="4.0 TB">4.0 TB</button></div>
          <p class="pdp-option-quote-status" data-product-quote-status role="status" aria-live="polite" hidden></p>
          <section class="pdp-specs" aria-label="Product overview"><dl><dt>Digital Storage Capacity</dt><dd>1 TB</dd><dt>Hard Disk Interface</dt><dd>USB 3.0</dd><dt>Connectivity Technology</dt><dd>USB</dd><dt>Brand</dt><dd>Samsung</dd><dt>Special Feature</dt><dd>256-Bit AES Hardware Encryption, Shock Resistant, Thermal Control</dd><dt>Hard Disk Description</dt><dd>Solid State Hard Drive</dd><dt>Compatible Devices</dt><dd>Gaming Console, Mac, PC, Smartphone, Tablet</dd><dt>Installation Type</dt><dd>External Hard Drive</dd><dt>Color</dt><dd>Titan Gray</dd><dt>Hard Disk Size</dt><dd>1 TB</dd></dl></section>
          <section class="about"><h2>About this item</h2><ul><li>MADE FOR THE MAKERS: Create, explore and store with fast speeds and durable features.</li><li>SHARE IDEAS IN A FLASH: PCIe NVMe technology brings read and write speeds up to 1,050/1,000 MB/s.</li><li>ALWAYS MAKE THE SAVE: Compact design with capacities for working files, game data and backups.</li><li>ADAPTS TO EVERY NEED: Compatible with PCs, mobile phones, gaming consoles and tablets.</li><li>HI RESOLUTION VIDEO RECORDING: Record 4K video directly to the portable SSD.</li><li>ALL FOR THE SHOT: A sturdy aluminum unibody offers shock resistance and fall protection.</li></ul></section>
        </div>
        <aside class="pdp-buy-column">
          <section id="buybox" class="buybox">
            <div class="buybox-price" data-product-price data-price-minor="{product['price_minor']}" data-currency="{escape(product['currency'], quote=True)}"><span class="price-symbol">{currency_symbol}</span><span>{price_whole:,}</span><sup>{price_cents:02d}</sup></div>
            <p class="buybox-import">No Import Charges &amp; $7.30 Shipping to Singapore</p>
            <a class="buybox-details" href="{SHIPPING_POLICIES_HREF}">Details⌄</a>
            <p class="buybox-tax"><span class="info-icon" aria-hidden="true">i</span><span>Sales taxes may apply at checkout</span></p>
            <p>$7.30 delivery <strong>Sunday, July 26</strong></p>
            <p>Or fastest delivery <strong>Friday, July 24</strong></p>
            <a class="deliver-link" href="{DELIVERY_PREFERENCE_HREF}"><span class="pin-icon"></span> Deliver to Singapore</a>
            <p class="in-stock" data-product-availability>In Stock</p>
            {cart_form}
            {compare_add_form(product)}
            <button class="buy-now" type="button" data-product-buy-now data-quote-can-enable="true">Buy Now</button>
            <dl><dt>Shipper / Seller</dt><dd>Amazon.com</dd><dt>Returns</dt><dd><a href="{RETURNS_REPLACEMENTS_HREF}">FREE 30-day refund/replacement</a></dd><dt>Payment</dt><dd><button class="pdp-inline-link" type="button" data-pdp-info-open aria-controls="pdp-secure-transaction-dialog">Secure transaction</button></dd></dl>
            <a class="buybox-more" href="{CUSTOMER_SERVICE_HREF}">See more purchase help</a><hr class="buybox-rule">
            {wishlist_add_form(product, t7_selected_options)}
          </section>
          <section class="other-sellers"><h2>Other sellers on Amazon</h2><p>Individual seller offers were not retained in this local snapshot, so no seller count, price, or shipping quote is shown.</p></section>
        </aside>
      </section>
      {full_view_dialog}
      {secure_transaction_dialog()}
      <dialog id="pdp-video-dialog" class="pdp-video-dialog" aria-labelledby="pdp-video-title"><div class="pdp-video-dialog-header"><h2 id="pdp-video-title">Videos for this product</h2><button type="button" data-video-close aria-label="Close product videos">×</button></div><div class="pdp-video-stage"><img src="{pdp_asset_root}/video-thumb.jpg" width="320" height="320" alt="Samsung T7 product video still"><button type="button" aria-label="Play product video">▶</button></div><p>Samsung T7 Portable SSD overview</p></dialog>
      <section class="pdp-below"><h2>Products related to this item</h2><p>Customers who viewed this item also viewed portable storage from Samsung and SanDisk.</p></section>
    </main>
    """
    if reviews_html:
        body = body.replace("\n    </main>", f"\n      {reviews_html}\n    </main>", 1)
    return layout(f"Amazon.com: {product['title']} : Electronics", body, cart_count, body_class="pdp-page", account_name=account_name)


def product_reviews_page(
    product: dict[str, Any],
    reviews_html: str,
    cart_count: int,
    account_name: str | None = None,
) -> str:
    """Render a live review destination for every product review link."""

    href = escape(product_href(product), quote=True)
    image_path = escape(str(product.get("image_path", "")), quote=True)
    title = escape(str(product["title"]))
    if reviews_html:
        content = reviews_html
    else:
        content = (
            '<section class="reviews-evidence-limited">'
            '<h2>Customer reviews</h2>'
            '<p>No complete source review aggregate was captured for this item, '
            'so this local clone does not invent review cards or ratings.</p>'
            f'<a href="{href}">Return to the product page</a></section>'
        )
    body = f"""
    <main id="main" class="product-reviews-main desktop-shell" data-asin="{escape(str(product['asin']), quote=True)}">
      <nav class="review-product-summary" aria-label="Reviewed product">
        <a href="{href}"><img src="{image_path}" width="96" height="96" alt=""></a>
        <div><span>Customer reviews for</span><h1><a href="{href}">{title}</a></h1><a href="{href}">See full product details</a></div>
      </nav>
      {content}
    </main>
    """
    return layout(
        f"Amazon.com: Customer reviews: {product['title']}",
        body,
        cart_count,
        body_class="product-reviews-page",
        account_name=account_name,
    )


def cart_page(
    lines: list[dict[str, Any]],
    cart_count: int,
    account_name: str | None = None,
    saved_lines: list[dict[str, Any]] | None = None,
) -> str:
    saved = list(saved_lines or [])
    has_active = bool(lines)
    has_saved = bool(saved)
    has_cart_content = has_active or has_saved

    saved_markup = ""
    if has_saved:
        saved_count = sum(max(1, int(line.get("quantity", 1))) for line in saved)
        saved_label = "item" if saved_count == 1 else "items"
        saved_items = "".join(cart_line(line, saved=True) for line in saved)
        saved_markup = f"""
        <section id="sc-saved-cart" class="saved-cart-panel" aria-labelledby="saved-cart-heading">
          <h2 id="saved-cart-heading">Saved for later ({saved_count} {saved_label})</h2>
          {saved_items}
        </section>
        """

    if has_active:
        items = "".join(cart_line(line) for line in lines)
        subtotal = sum(int(line["price_minor"]) * int(line["quantity"]) for line in lines)
        currency = str(lines[0].get("currency", "USD"))
        active_content = f"""
        <section id="sc-active-cart" class="active-cart-panel"><h1>Shopping Cart</h1>{items}<p class="cart-subtotal">Subtotal ({cart_count} items): <strong>{money(subtotal, currency)}</strong></p></section>
        <aside class="checkout-panel populated"><p>Part of your order qualifies for FREE Shipping.</p><p>Subtotal ({cart_count} items): <strong>{money(subtotal, currency)}</strong></p><form method="post" action="/gp/buy/spc/handlers/display.html"><button type="submit">Proceed to checkout</button></form></aside>
        """
    else:
        empty_actions = (
            '<div class="cart-auth-buttons"><a class="button primary" href="/">Continue shopping</a></div>'
            if account_name
            else '<div class="cart-auth-buttons"><a class="button primary" href="/ap/signin">Sign in to your account</a><a class="button secondary" href="/ap/register">Sign up now</a></div>'
        )
        active_content = f"""
        <section id="sc-active-cart" class="empty-cart-panel"><div class="cart-illustration" role="img" aria-label="A kettle, cup, carton, and smart display"><span class="box-lid"></span><span class="cart-box" aria-hidden="true"></span></div><div class="empty-cart-copy"><h1>Your Amazon Cart is empty</h1><a href="/gp/goldbox/">Shop today's deals</a>{empty_actions}</div></section>
        """

    if has_cart_content:
        content = active_content + saved_markup
        recommendations = ""
    else:
        content = active_content + """
        <aside class="recently-viewed-panel" aria-labelledby="recently-viewed-heading"><h2 id="recently-viewed-heading">Your recently viewed items</h2><div class="recently-viewed-product"><a href="/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8"><img src="/static/assets/cart-recent-t7-current.jpg" width="100" height="100" alt="Samsung T7 Portable SSD"></a><div><a class="recently-viewed-title" href="/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8">Samsung T7 Portable SSD, 1TB External Solid...</a><div class="recently-viewed-rating"><span class="stars">★★★★★</span> <a href="/product-reviews/B0874XN4D8">38,085</a></div><div class="recently-viewed-price"><span>-20%</span> <strong>$219<sup>99</sup></strong></div><small>List: <s>$274.99</s></small><p>$7.30 shipping</p><a class="recently-viewed-add" href="/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8">Add to cart</a></div></div></aside>
        <div class="cart-spacer" aria-hidden="true"></div><div class="mobile-cart-divider" aria-hidden="true"></div><a class="mobile-continue button primary" href="/">Continue shopping</a>
        """
        recommendations = cart_recommendations()

    main_state = "populated-cart-main" if has_cart_content else "empty-cart-main"
    canvas_state = "populated" if has_cart_content else "empty"
    if has_saved and not has_active:
        canvas_state += " saved-only"
    body = f"""
    <main id="main" class="cart-main desktop-shell {main_state}">
      <div class="cart-canvas {canvas_state}"><div id="sc-retail-cart-container" class="cart-retail-container">{content}<section class="cart-disclaimer"><p>The price and availability of items at Amazon.com are subject to change. The Cart is a temporary place to store a list of your items and reflects each item's most recent price. <a href="{CUSTOMER_SERVICE_HREF}">Learn more</a><br>Do you have a gift card or promotional code? We'll ask you to enter your claim code when it's time to pay.</p></section></div></div>
      {recommendations}
    </main>
    """
    return layout("Amazon.com Shopping Cart", body, cart_count, body_class="cart-page", account_name=account_name)


def cart_recommendations() -> str:
    products = (
        (
            "B0CHFSWM2P",
            "/static/assets/source-current/2026-07-21/pdp-t9/main.jpg",
            "Samsung T9 Portable SSD 1TB, USB 3.2 Gen 2x2 External Solid State Drive",
            "4.6", "2,894", "$239.99",
        ),
        (
            "B08GTYFC37",
            "/static/assets/source-current/2026-07-21/pdp-search/b08gtyfc37/main.jpg",
            "SANDISK 1TB Extreme Portable SSD (Old Model), USB-C, USB 3.2 Gen 2",
            "4.6", "91,208", "$189.99",
        ),
        (
            "B0BGKXX9TK",
            "/static/assets/source-current/2026-07-21/pdp-search/b0bgkxx9tk/main.jpg",
            "SSK Portable SSD 500GB External Solid State Drive, USB-C USB 3.2 Gen 2",
            "4.5", "3,522", "$89.99",
        ),
        (
            "B0C5JQ68FY",
            "/static/assets/source-current/2026-07-21/pdp-search/b0c5jq68fy/main.jpg",
            "SANDISK 1TB Portable SSD, USB-C, USB 3.2 Gen 2, Updated Firmware",
            "4.6", "13,477", "$139.99",
        ),
        (
            "B08GV9M64L",
            "/static/assets/rank-05-current.jpg",
            "SANDISK 1TB Extreme PRO Portable SSD, USB-C, USB 3.2 Gen 2x2",
            "4.5", "16,940", "$209.99",
        ),
        (
            "B09VLK9W3S",
            "/static/assets/rank-06-current.jpg",
            "Samsung T7 Shield 1TB Rugged Portable External Solid State Drive",
            "4.7", "16,377", "$272.54",
        ),
    )
    cards = "".join(
        f"""
        <article class="cart-recommendation-card" data-asin="{asin}"><a href="/dp/{asin}"><img src="{image}" width="165" height="165" alt="{escape(title, quote=True)}"></a><a class="cart-recommendation-title" href="/dp/{asin}">{escape(title)}</a><div><span class="stars" aria-label="{rating} out of 5 stars">★★★★★</span> <a href="/product-reviews/{asin}" aria-label="{reviews} reviews">{reviews}</a></div><strong>{price}</strong><p><a href="/dp/{asin}">See product details</a></p><small>Delivery and shipping shown at checkout</small></article>
        """
        for asin, image, title, rating, reviews, price in products
    )
    return f"""
    <section class="cart-recommendations" aria-labelledby="cart-recommendations-heading" data-cart-recommendations>
      <h2 id="cart-recommendations-heading">Customers Who Bought Items in Your Recent History Also Bought</h2><span class="cart-recommendations-page" data-cart-recommendations-page aria-live="polite">Page 1 of 1</span>
      <button class="cart-recommendations-prev" type="button" data-cart-recommendations-prev aria-controls="cart-recommendation-viewport" aria-label="Previous recommendations">‹</button><div id="cart-recommendation-viewport" class="cart-recommendation-viewport" data-cart-recommendations-viewport tabindex="0" aria-label="Product recommendations; use left and right arrow keys to browse"><div class="cart-recommendation-grid">{cards}</div></div><button class="cart-recommendations-next" type="button" data-cart-recommendations-next aria-controls="cart-recommendation-viewport" aria-label="Next recommendations">›</button>
    </section>
    """


def selected_options_markup(
    payload: dict[str, Any], *, css_class: str = "selected-product-options"
) -> str:
    raw_options = payload.get("selected_options")
    if not isinstance(raw_options, dict) or not raw_options:
        return ""
    pairs = "".join(
        f'<div><dt>{escape(str(label))}:</dt><dd>{escape(str(value))}</dd></div>'
        for label, value in raw_options.items()
        if isinstance(label, str) and isinstance(value, str)
    )
    return f'<dl class="{escape(css_class, quote=True)}" aria-label="Selected product options">{pairs}</dl>' if pairs else ""


def cart_line(line: dict[str, Any], *, saved: bool = False) -> str:
    asin = escape(str(line["asin"]), quote=True)
    raw_line_id = str(line["line_id"])
    line_id = escape(raw_line_id, quote=True)
    title = escape(str(line["title"]))
    title_attr = escape(str(line["title"]), quote=True)
    image_path = escape(str(line["image_path"]), quote=True)
    href = escape(product_href(line), quote=True)
    quantity = max(1, min(int(line.get("quantity", 1)), 30))
    currency = str(line.get("currency", "USD"))

    if saved:
        controls = f"""
          <span class="saved-quantity">Quantity: {quantity}</span>
          <form class="cart-line-form" method="post" action="/gp/cart/move-to-cart.html"><input type="hidden" name="lineID" value="{line_id}"><button class="cart-action-button" type="submit">Move to Cart</button></form>
          <form class="cart-line-form" method="post" action="/gp/cart/delete.html"><input type="hidden" name="lineID" value="{line_id}"><button class="cart-action-button" type="submit">Delete</button></form>
        """
    else:
        quantity_options = "".join(
            f'<option value="{value}"{" selected" if value == quantity else ""}>Quantity: {value}</option>'
            for value in range(1, 31)
        )
        quantity_id = escape(f"cart-quantity-{raw_line_id}", quote=True)
        controls = f"""
          <form class="cart-line-form cart-quantity-form" method="post" action="/gp/cart/update.html"><input type="hidden" name="lineID" value="{line_id}"><label for="{quantity_id}">Qty:</label><select id="{quantity_id}" name="quantity" aria-label="Quantity for {title_attr}">{quantity_options}</select><button class="cart-action-button" type="submit">Update</button></form>
          <form class="cart-line-form" method="post" action="/gp/cart/delete.html"><input type="hidden" name="lineID" value="{line_id}"><button class="cart-action-button" type="submit">Delete</button></form>
          <form class="cart-line-form" method="post" action="/gp/cart/save-for-later.html"><input type="hidden" name="lineID" value="{line_id}"><button class="cart-action-button" type="submit">Save for later</button></form>
        """

    state_class = " saved-cart-line" if saved else ""
    stock_copy = "Saved for later" if saved else "In Stock"
    options_markup = selected_options_markup(line, css_class="cart-line-options")
    return f"""
    <article class="cart-line{state_class}" data-asin="{asin}" data-line-id="{line_id}">
      <a class="cart-line-image" href="{href}"><img src="{image_path}" width="180" height="180" alt="{title_attr}"></a>
      <div class="cart-line-details"><h2><a href="{href}">{title}</a></h2>{options_markup}<p class="in-stock">{stock_copy}</p><p>FREE International Returns</p><div class="cart-line-actions">{controls}</div></div>
      <strong>{money(int(line["price_minor"]), currency)}</strong>
    </article>
    """


def signin_page(cart_count: int, destination: str = "Your Account") -> str:
    body = f"""
    <main id="main" class="signin-main"><div class="signin-logo">{amazon_logo()}</div><section class="signin-card"><h1>Sign in or create account</h1><label for="email">Enter mobile number or email</label><input id="email" type="email"><button>Continue</button><p>By continuing, you agree to Amazon's <a href="#">Conditions of Use</a> and <a href="#">Privacy Notice</a>.</p><details><summary>Need help?</summary><a href="#">Forgot your password?</a></details></section><div class="business-link"><a href="#">Buying for work? Create a free business account</a><small>Destination: {escape(destination)}</small></div></main>
    """
    return layout("Amazon Sign-In", body, cart_count, body_class="signin-page")


def boundary_page(
    title: str,
    copy: str,
    cart_count: int,
    account_name: str | None = None,
) -> str:
    body = f"""
    <main id="main" class="boundary-main desktop-shell"><section><h1>{escape(title)}</h1><p>{escape(copy)}</p><a class="button primary" href="/ap/signin">Sign in</a><a class="button secondary" href="/">Continue shopping</a></section></main>
    """
    return layout(f"Amazon.com - {title}", body, cart_count, body_class="boundary-page", account_name=account_name)


def customer_service_page(
    cart_count: int,
    account_name: str | None = None,
    *,
    help_query: str = "",
) -> str:
    """Render a navigable support hub instead of a generic boundary card."""

    account_href = (
        "/gp/css/homepage.html"
        if account_name
        else "/ap/signin?openid.return_to=%2Fgp%2Fcss%2Fhomepage.html"
    )
    topics = (
        (
            "📦",
            "Your Orders",
            "Track a package, inspect a placed order, or review its delivery status.",
            "/gp/css/order-history",
            "order package tracking purchase",
        ),
        (
            "↩",
            "Returns & refunds",
            "Find return and replacement guidance, then open the relevant order.",
            RETURNS_REPLACEMENTS_HREF,
            "return refund replacement exchange",
        ),
        (
            "🚚",
            "Shipping & delivery",
            "Compare delivery choices and learn where shipping charges are shown.",
            SHIPPING_POLICIES_HREF,
            "shipping delivery rate charge address singapore",
        ),
        (
            "👤",
            "Manage your account",
            "Sign in, review account access, and update the checkout address you use.",
            account_href,
            "account sign in login password address",
        ),
        (
            "🎁",
            "Gift Cards & gifts",
            "Open Gift Cards or browse gift ideas from the marketplace departments.",
            GIFT_CARDS_HREF,
            "gift card balance redeem present",
        ),
        (
            "🛒",
            "Cart & checkout",
            "Review your cart before selecting an address, delivery, and payment method.",
            "/gp/cart/view.html",
            "cart checkout payment buy",
        ),
    )

    def topic_card(topic: tuple[str, str, str, str, str], css_class: str) -> str:
        icon, title, copy, href, _ = topic
        return f"""
          <a class="{css_class}" href="{escape(href, quote=True)}">
            <span class="help-topic-icon" aria-hidden="true">{icon}</span>
            <span><strong>{escape(title)}</strong><small>{escape(copy)}</small></span>
          </a>
        """

    normalized_query = " ".join(help_query.split())[:160]
    search_results = ""
    if normalized_query:
        terms = tuple(term.lower() for term in normalized_query.split() if term)
        matches = tuple(
            topic
            for topic in topics
            if any(
                term in f"{topic[1]} {topic[2]} {topic[4]}".lower()
                for term in terms
            )
        )
        if matches:
            result_cards = "".join(topic_card(topic, "help-search-result") for topic in matches)
            search_results = f"""
              <section class="help-search-results" aria-live="polite">
                <h2>Help results for “{escape(normalized_query)}”</h2>
                <div>{result_cards}</div>
              </section>
            """
        else:
            search_results = f"""
              <section class="help-search-results help-search-empty" aria-live="polite">
                <h2>No exact help result for “{escape(normalized_query)}”</h2>
                <p>Try a shorter search, or choose one of the help topics below.</p>
              </section>
            """

    topic_cards = "".join(topic_card(topic, "help-topic-card") for topic in topics)
    greeting = f"Hello, {escape(account_name.split()[0])}." if account_name else "Hello."
    body = f"""
    <main id="main" class="help-main desktop-shell" data-help-page="customer-service">
      <section class="help-hero">
        <div><p>{greeting}</p><h1>What can we help you with?</h1></div>
        <form class="help-search" method="get" action="/gp/help/customer/display.html" role="search">
          <input type="hidden" name="nodeId" value="508510">
          <label class="sr-only" for="help-search-input">Search Customer Service</label>
          <span aria-hidden="true">⌕</span>
          <input id="help-search-input" name="help_keywords" value="{escape(normalized_query, quote=True)}" placeholder="Search Customer Service" autocomplete="off">
          <button type="submit">Search</button>
        </form>
      </section>
      {search_results}
      <section class="help-topics" aria-labelledby="help-topics-heading">
        <h2 id="help-topics-heading">Some things you can do here</h2>
        <div class="help-topic-grid">{topic_cards}</div>
      </section>
      <section class="help-contact-panel">
        <div><h2>Need help with an order?</h2><p>Open Your Orders for order-specific delivery and payment details.</p></div>
        <a class="button primary" href="/gp/css/order-history">Go to Your Orders</a>
      </section>
    </main>
    """
    return layout(
        "Amazon Customer Service",
        body,
        cart_count,
        body_class="help-page customer-service-page",
        account_name=account_name,
    )


def shipping_policies_page(
    cart_count: int,
    account_name: str | None = None,
) -> str:
    """Describe the delivery choices that the checkout backend actually supports."""

    body = f"""
    <main id="main" class="help-article-main desktop-shell" data-help-page="shipping-policies">
      <nav class="help-breadcrumb" aria-label="Breadcrumb"><a href="{CUSTOMER_SERVICE_HREF}">Customer Service</a><span>›</span><strong>Shipping Rates &amp; Policies</strong></nav>
      <div class="help-article-layout">
        <aside class="help-article-nav" aria-label="Shipping help">
          <h2>Shipping &amp; delivery</h2>
          <a aria-current="page" href="{SHIPPING_POLICIES_HREF}">Shipping rates &amp; policies</a>
          <a href="/gp/delivery/ajax/address-change.html">Delivery location</a>
          <a href="/gp/css/order-history">Track your orders</a>
          <a href="{RETURNS_REPLACEMENTS_HREF}">Returns &amp; replacements</a>
        </aside>
        <article class="help-article">
          <h1>Shipping Rates &amp; Policies</h1>
          <p class="help-article-lead">Shipping availability and charges are shown before an order is placed. Product pages show offer-specific delivery information when it is available.</p>
          <section><h2>Delivery choices at checkout</h2><div class="shipping-choice-grid"><div><strong>Standard delivery</strong><span>FREE</span><p>Included in the supported checkout flow.</p></div><div><strong>Expedited delivery</strong><span>$12.99</span><p>A faster delivery choice available during checkout.</p></div></div></section>
          <section><h2>Delivering to Singapore</h2><p>The storefront currently shows offers for delivery to Singapore. Change the delivery location from the header, then select the full shipping address while checking out.</p><a href="/gp/delivery/ajax/address-change.html">Review delivery location</a></section>
          <section><h2>Where to find an order's delivery status</h2><p>After placing an order, its selected delivery method, shipment status, carrier, and tracking reference appear in Your Orders.</p><a href="/gp/css/order-history">Open Your Orders</a></section>
          <div class="help-article-actions"><a class="button primary" href="/gp/cart/view.html">View your cart</a><a class="button secondary" href="{CUSTOMER_SERVICE_HREF}">Back to Customer Service</a></div>
        </article>
      </div>
    </main>
    """
    return layout(
        "Amazon.com Help: Shipping Rates & Policies",
        body,
        cart_count,
        body_class="help-page help-article-page shipping-policies-page",
        account_name=account_name,
    )


def returns_replacements_page(
    cart_count: int,
    account_name: str | None = None,
) -> str:
    """Provide a useful path from return help to the shopper's real orders."""

    order_action = "View Your Orders" if account_name else "Sign in to view orders"
    body = f"""
    <main id="main" class="help-article-main desktop-shell" data-help-page="returns-replacements">
      <nav class="help-breadcrumb" aria-label="Breadcrumb"><a href="{CUSTOMER_SERVICE_HREF}">Customer Service</a><span>›</span><strong>Returns &amp; Replacements</strong></nav>
      <div class="help-article-layout">
        <aside class="help-article-nav" aria-label="Returns help">
          <h2>Returns &amp; refunds</h2>
          <a aria-current="page" href="{RETURNS_REPLACEMENTS_HREF}">Returns &amp; replacements</a>
          <a href="/gp/css/order-history">Your Orders</a>
          <a href="{SHIPPING_POLICIES_HREF}">Shipping policies</a>
          <a href="{CUSTOMER_SERVICE_HREF}">Customer Service</a>
        </aside>
        <article class="help-article">
          <h1>Returns &amp; Replacements</h1>
          <p class="help-article-lead">Start with Your Orders so the available action stays tied to the correct order and delivery state. This clone provides a local return-and-refund simulation; it does not create a real label, shipment, or money movement.</p>
          <ol class="return-steps">
            <li><span>1</span><div><strong>Open Your Orders</strong><p>Sign in and locate the order that contains the item.</p></div></li>
            <li><span>2</span><div><strong>Wait for simulated delivery</strong><p>The Return items action appears on eligible order details after the local shipment reaches Delivered.</p></div></li>
            <li><span>3</span><div><strong>Submit and track the return</strong><p>Choose a reason, submit the request, and follow its Requested, Received, and Refunded states from the order.</p></div></li>
          </ol>
          <section><h2>Refund timing in this clone</h2><p>A completed simulated refund record appears only after the local return advances through Received to Refunded. It is a state model only and never contacts a bank or card network.</p></section>
          <div class="help-article-actions"><a class="button primary" href="/gp/css/order-history">{order_action}</a><a class="button secondary" href="{CUSTOMER_SERVICE_HREF}&amp;help_keywords=return">Search return help</a></div>
        </article>
      </div>
    </main>
    """
    return layout(
        "Amazon.com Help: Returns & Replacements",
        body,
        cart_count,
        body_class="help-page help-article-page returns-page",
        account_name=account_name,
    )


def gift_cards_page(
    cart_count: int,
    account_name: str | None = None,
) -> str:
    """Render a branded Gift Cards destination with only usable local actions."""

    account_href = (
        "/gp/css/homepage.html"
        if account_name
        else "/ap/signin?openid.return_to=%2Fgift-cards%2Fb%2F"
    )
    account_action = "Go to your account" if account_name else "Sign in"
    departments = (
        ("Books", "/s?k=books", "For readers of every kind"),
        ("Home & Kitchen", "/s?k=home+kitchen", "Useful picks for the home"),
        ("Toys & Games", "/s?k=toys", "Gifts for play and discovery"),
        ("Beauty", "/s?k=beauty+personal+care", "Beauty and personal care picks"),
    )
    department_cards = "".join(
        f'<a class="gift-department-card" href="{href}"><strong>{escape(title)}</strong><span>{escape(copy)}</span><small>Shop now ›</small></a>'
        for title, href, copy in departments
    )
    body = f"""
    <main id="main" class="gift-cards-main desktop-shell" data-navigation-page="gift-cards">
      <section class="gift-cards-hero">
        <div><p>Amazon Gift Cards</p><h1>Give them more ways to find the right gift</h1><span>Browse current marketplace departments and deals, or sign in for account help.</span><div class="gift-hero-actions"><a class="button primary" href="/gp/goldbox/">Shop today's deals</a><a class="button secondary" href="{account_href}">{account_action}</a></div></div>
        <div class="gift-card-art" aria-hidden="true"><span>amazon</span><strong>Gift Card</strong><i></i></div>
      </section>
      <section class="gift-card-actions" aria-label="Gift Card actions">
        <a href="{account_href}"><span aria-hidden="true">👤</span><strong>{account_action}</strong><small>Open account tools</small></a>
        <a href="{CUSTOMER_SERVICE_HREF}&amp;help_keywords=gift"><span aria-hidden="true">?</span><strong>Gift Card help</strong><small>Search Customer Service</small></a>
        <a href="/gp/goldbox/"><span aria-hidden="true">%</span><strong>Today's Deals</strong><small>Browse verified offers</small></a>
      </section>
      <section class="gift-departments" aria-labelledby="gift-departments-heading"><h2 id="gift-departments-heading">Shop gifts by department</h2><div>{department_cards}</div></section>
      <section class="gift-help-banner"><div><h2>Questions about Gift Cards?</h2><p>Customer Service connects gift questions with account, order, and shopping help.</p></div><a class="button secondary" href="{CUSTOMER_SERVICE_HREF}&amp;help_keywords=gift">Visit Customer Service</a></section>
    </main>
    """
    return layout(
        "Amazon.com Gift Cards",
        body,
        cart_count,
        body_class="gift-cards-page",
        account_name=account_name,
    )


def navigation_landing_page(
    title: str,
    copy: str,
    links: Iterable[tuple[str, str]],
    cart_count: int,
    account_name: str | None = None,
) -> str:
    """Render a shallow but usable destination for secondary Amazon navigation."""

    actions = "".join(
        f'<a class="button secondary" href="{escape(href, quote=True)}">{escape(label)}</a>'
        for label, href in links
    )
    body = f"""
    <main id="main" class="boundary-main navigation-landing-main desktop-shell">
      <section>
        <h1>{escape(title)}</h1>
        <p>{escape(copy)}</p>
        <div class="account-actions">{actions}</div>
      </section>
    </main>
    """
    return layout(
        f"Amazon.com - {title}",
        body,
        cart_count,
        body_class="boundary-page navigation-landing-page",
        account_name=account_name,
    )


def account_page(account: dict[str, Any], cart_count: int) -> str:
    display_name = str(account.get("display_name", "Customer"))
    body = f"""
    <main id="main" class="boundary-main account-main desktop-shell">
      <section>
        <h1>Hello, {escape(display_name)}</h1>
        <p>You are signed in to your local Amazon account.</p>
        <div class="account-tool-grid">
          <a class="account-tool-card" href="/gp/css/order-history"><strong>Your Orders</strong><span>Track and review your orders</span></a>
          <a class="account-tool-card" href="/a/addresses"><strong>Your Addresses</strong><span>Add, edit, remove, and set your default address</span></a>
          <a class="account-tool-card" href="/hz/wishlist/ls"><strong>Your Lists</strong><span>Create Lists and save products for later</span></a>
        </div>
        <div class="account-actions">
          <a class="button secondary" href="/">Continue shopping</a>
          <form method="post" action="/ap/signout"><button class="button primary" type="submit">Sign out</button></form>
        </div>
      </section>
    </main>
    """
    return layout(
        "Amazon.com - Your Account",
        body,
        cart_count,
        body_class="boundary-page account-page",
        account_name=display_name,
    )


def _address_country_options(address: dict[str, Any]) -> str:
    country_code = str(address.get("country_code") or "SG").upper()
    options = "".join(
        f'<option value="{code}"{" selected" if code == country_code else ""}>{label}</option>'
        for code, label in SUPPORTED_DELIVERY_COUNTRIES
    )
    if country_code not in {code for code, _ in SUPPORTED_DELIVERY_COUNTRIES}:
        options = (
            f'<option value="{escape(country_code, quote=True)}" selected>'
            f'{escape(country_code)}</option>' + options
        )
    return options


def _address_input_fields(
    address: dict[str, Any], account_name: str | None, *, id_prefix: str
) -> str:
    full_name = str(address.get("full_name") or account_name or "")
    prefix = escape(id_prefix, quote=True)
    return f"""
      <label class="checkout-field checkout-field-wide" for="{prefix}-full-name">Full name<input id="{prefix}-full-name" name="fullName" value="{escape(full_name, quote=True)}" autocomplete="name" maxlength="128" required></label>
      <label class="checkout-field checkout-field-wide" for="{prefix}-line-1">Address line 1<input id="{prefix}-line-1" name="addressLine1" value="{escape(str(address.get('address_line1') or ''), quote=True)}" autocomplete="address-line1" maxlength="200" required></label>
      <label class="checkout-field checkout-field-wide" for="{prefix}-line-2">Address line 2 <span>(optional)</span><input id="{prefix}-line-2" name="addressLine2" value="{escape(str(address.get('address_line2') or ''), quote=True)}" autocomplete="address-line2" maxlength="200"></label>
      <label class="checkout-field" for="{prefix}-city">City<input id="{prefix}-city" name="city" value="{escape(str(address.get('city') or ''), quote=True)}" autocomplete="address-level2" maxlength="100" required></label>
      <label class="checkout-field" for="{prefix}-state">State / Province / Region<input id="{prefix}-state" name="state" value="{escape(str(address.get('state_region') or ''), quote=True)}" autocomplete="address-level1" maxlength="100" required></label>
      <label class="checkout-field" for="{prefix}-postal">Postal code<input id="{prefix}-postal" name="postalCode" value="{escape(str(address.get('postal_code') or ''), quote=True)}" autocomplete="postal-code" maxlength="32" required></label>
      <label class="checkout-field" for="{prefix}-country">Country / Region<select id="{prefix}-country" name="countryCode" autocomplete="country" required>{_address_country_options(address)}</select></label>
      <label class="checkout-field checkout-field-wide" for="{prefix}-phone">Phone number <span>(optional)</span><input id="{prefix}-phone" name="phoneNumber" type="tel" value="{escape(str(address.get('phone') or ''), quote=True)}" autocomplete="tel" maxlength="32"></label>
    """


def address_book_page(
    addresses: list[dict[str, Any]],
    cart_count: int,
    account_name: str,
    *,
    status: str | None = None,
    error: str | None = None,
) -> str:
    status_messages = {
        "added": "Address added.",
        "updated": "Address updated.",
        "deleted": "Address removed from your address book.",
        "default": "Default address updated.",
    }
    notice = (
        f'<div class="address-book-notice" role="status">{escape(status_messages[status])}</div>'
        if status in status_messages
        else ""
    )
    error_markup = (
        f'<div class="address-book-error" role="alert">{escape(error)}</div>'
        if error
        else ""
    )
    cards: list[str] = [
        '<a class="address-add-card" href="/a/addresses/add"><span aria-hidden="true">+</span><strong>Add address</strong></a>'
    ]
    for address in addresses:
        address_id = int(address["address_id"])
        revision = int(address["revision"])
        default_badge = (
            '<div class="address-default-badge">Default</div>'
            if address.get("is_default")
            else ""
        )
        set_default = (
            ""
            if address.get("is_default")
            else f"""
              <form method="post" action="/a/addresses/set-default">
                <input type="hidden" name="addressId" value="{address_id}">
                <input type="hidden" name="addressRevision" value="{revision}">
                <button type="submit">Set as Default</button>
              </form>
            """
        )
        cards.append(
            f"""
            <article class="address-book-card" data-address-id="{address_id}">
              {default_badge}
              {_address_markup(address)}
              <div class="address-card-actions">
                <a href="/a/addresses/edit?addressID={address_id}">Edit</a>
                {set_default}
                <form method="post" action="/a/addresses/delete">
                  <input type="hidden" name="addressId" value="{address_id}">
                  <input type="hidden" name="addressRevision" value="{revision}">
                  <button type="submit">Remove</button>
                </form>
              </div>
            </article>
            """
        )
    body = f"""
    <main id="main" class="address-book-main desktop-shell">
      <nav class="account-breadcrumb" aria-label="Breadcrumb"><a href="/gp/css/homepage.html">Your Account</a><span>›</span><span>Your Addresses</span></nav>
      <header class="address-book-heading"><h1>Your Addresses</h1><p>Choose the default used first during checkout.</p></header>
      {notice}{error_markup}
      <section class="address-book-grid" aria-label="Saved addresses">{"".join(cards)}</section>
    </main>
    """
    return layout(
        "Amazon.com - Your Addresses",
        body,
        cart_count,
        body_class="address-book-page",
        account_name=account_name,
    )


def address_form_page(
    address: dict[str, Any] | None,
    cart_count: int,
    account_name: str,
) -> str:
    current = address if isinstance(address, dict) else {}
    editing = bool(current)
    title = "Edit address" if editing else "Add a new address"
    action = "/a/addresses/update" if editing else "/a/addresses/create"
    hidden = ""
    if editing:
        hidden = (
            f'<input type="hidden" name="addressId" value="{int(current["address_id"])}">'
            f'<input type="hidden" name="addressRevision" value="{int(current["revision"])}">'
        )
    default_control = (
        '<p class="address-default-current checkout-field-wide">'
        '<strong>Default address</strong><span>To change it, set another saved address as default.</span></p>'
        if editing and current.get("is_default")
        else '<label class="address-default-choice checkout-field-wide"><input type="checkbox" name="makeDefault" value="1"> Make this my default address</label>'
    )
    body = f"""
    <main id="main" class="address-form-main desktop-shell">
      <nav class="account-breadcrumb" aria-label="Breadcrumb"><a href="/gp/css/homepage.html">Your Account</a><span>›</span><a href="/a/addresses">Your Addresses</a><span>›</span><span>{escape(title)}</span></nav>
      <section class="address-form-card">
        <h1>{escape(title)}</h1>
        <form class="checkout-form checkout-address-form" method="post" action="{action}">
          {hidden}
          {_address_input_fields(current, account_name, id_prefix="address-book")}
          {default_control}
          <button class="checkout-primary-button" type="submit">{escape("Save changes" if editing else "Add address")}</button>
        </form>
      </section>
    </main>
    """
    return layout(
        f"Amazon.com - {title}",
        body,
        cart_count,
        body_class="address-form-page",
        account_name=account_name,
    )


def _minor_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _checkout_items(checkout: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = checkout.get("items", [])
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _checkout_address(checkout: dict[str, Any]) -> dict[str, Any]:
    address = checkout.get("address")
    return address if isinstance(address, dict) else {}


def _checkout_layout(
    title: str,
    body: str,
    cart_count: int,
    account_name: str | None,
    *,
    body_class: str,
) -> str:
    short_name = account_name.strip().split()[0] if account_name and account_name.strip() else ""
    greeting = f"Hello, {escape(short_name)}" if short_name else "Secure checkout"
    item_label = "item" if cart_count == 1 else "items"
    return f"""<!doctype html>
<html lang="en-US">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
  <title>{escape(title)}</title>
  <link rel="stylesheet" href="/static/styles.css?v=20260722-39">
</head>
<body class="checkout-page {escape(body_class, quote=True)}">
  <a class="skip-link" href="#main">Skip to main content</a>
  <header class="checkout-header">
    <a class="checkout-logo" href="/" aria-label="Amazon home">{amazon_logo()}</a>
    <h1>Checkout <a href="/gp/cart/view.html">({cart_count} {item_label})</a></h1>
    <div class="checkout-secure"><span aria-hidden="true">&#128274;</span><span>{greeting}<small>Local secure checkout</small></span></div>
  </header>
  {body}
  <footer class="checkout-footer"><nav aria-label="Checkout policies"><a href="#">Conditions of Use</a><a href="#">Privacy Notice</a><a href="#">Help</a></nav><p>Local Amazon clone · No real purchases or payments</p></footer>
</body>
</html>"""


def _checkout_steps(current: str) -> str:
    steps = (
        ("address", "Shipping address"),
        ("delivery", "Delivery options"),
        ("payment", "Payment"),
        ("review", "Review items"),
    )
    positions = {name: index for index, (name, _) in enumerate(steps)}
    current_position = positions.get(current, len(steps))
    items = []
    for index, (name, label) in enumerate(steps):
        state = "current" if index == current_position else "complete" if index < current_position else "upcoming"
        marker = "✓" if state == "complete" else str(index + 1)
        current_attribute = ' aria-current="step"' if state == "current" else ""
        items.append(
            f'<li class="{state}"{current_attribute}><span>{marker}</span>{escape(label)}</li>'
        )
    return f'<nav class="checkout-progress" aria-label="Checkout progress"><ol>{"".join(items)}</ol></nav>'


def _checkout_item_rows(checkout: dict[str, Any]) -> str:
    rows: list[str] = []
    currency = str(checkout.get("currency") or "USD")
    for item in _checkout_items(checkout):
        asin = escape(str(item.get("asin", "")), quote=True)
        title = escape(str(item.get("title", "Item")))
        title_attr = escape(str(item.get("title", "Item")), quote=True)
        image_path = escape(str(item.get("image_path", "")), quote=True)
        quantity = max(1, _minor_value(item.get("quantity"), 1))
        item_currency = str(item.get("currency") or currency)
        unit_price = _minor_value(item.get("price_minor"))
        line_total = _minor_value(item.get("line_total_minor"), unit_price * quantity)
        href = f"/dp/{asin}" if asin else "/"
        options_markup = selected_options_markup(
            item, css_class="checkout-item-options"
        )
        image = (
            f'<a href="{href}"><img src="{image_path}" width="64" height="64" alt="{title_attr}"></a>'
            if image_path
            else '<span class="checkout-item-placeholder" aria-hidden="true"></span>'
        )
        rows.append(
            f'<article class="checkout-item">{image}<div><a href="{href}">{title}</a>{options_markup}<small>Quantity: {quantity}</small></div><strong>{money(line_total, item_currency)}</strong></article>'
        )
    return "".join(rows) or '<p class="checkout-empty-copy">No items are attached to this checkout.</p>'


def _checkout_summary(checkout: dict[str, Any]) -> str:
    currency = str(checkout.get("currency") or "USD")
    items = _checkout_items(checkout)
    computed_subtotal = sum(
        _minor_value(item.get("line_total_minor"), _minor_value(item.get("price_minor")) * max(1, _minor_value(item.get("quantity"), 1)))
        for item in items
    )
    subtotal = _minor_value(checkout.get("items_subtotal_minor"), computed_subtotal)
    shipping = _minor_value(checkout.get("shipping_minor"))
    total = _minor_value(checkout.get("total_minor"), subtotal + shipping)
    shipping_copy = "FREE" if shipping == 0 else money(shipping, currency)
    return f"""
    <aside class="checkout-summary" aria-labelledby="checkout-summary-heading">
      <h2 id="checkout-summary-heading">Order Summary</h2>
      <div class="checkout-summary-items">{_checkout_item_rows(checkout)}</div>
      <dl><dt>Items:</dt><dd>{money(subtotal, currency)}</dd><dt>Shipping &amp; handling:</dt><dd>{shipping_copy}</dd><dt class="checkout-total-label">Order total:</dt><dd class="checkout-total-value">{money(total, currency)}</dd></dl>
    </aside>
    """


def _address_markup(address: dict[str, Any]) -> str:
    fields = (
        address.get("full_name"),
        address.get("address_line1"),
        address.get("address_line2"),
        " ".join(
            part
            for part in (
                str(address.get("city") or ""),
                str(address.get("state_region") or ""),
                str(address.get("postal_code") or ""),
            )
            if part
        ),
        address.get("country_code"),
        address.get("phone"),
    )
    lines = [escape(str(value)) for value in fields if value]
    if not lines:
        return '<p class="checkout-empty-copy">No shipping address selected.</p>'
    return f'<address>{"<br>".join(lines)}</address>'


def _delivery_label(value: Any) -> str:
    return "Expedited delivery" if str(value).lower() == "expedited" else "Standard delivery"


def _payment_markup(payment: Any) -> str:
    if not isinstance(payment, dict):
        return '<p class="checkout-empty-copy">No payment method selected.</p>'
    status = escape(str(payment.get("status") or "Selected"))
    label = escape(str(payment.get("method_label") or "Sandbox payment method"))
    return f'<p><strong>{label}</strong><br><span>Local simulation · {status}</span></p>'


def _simulation_notice(payload: dict[str, Any]) -> str:
    notice = str(
        payload.get("simulation_notice")
        or "This is a local simulation. No real order, payment, shipment, or email is sent outside this workspace."
    )
    return f'<div class="checkout-simulation" role="note"><strong>Local simulation</strong><p>{escape(notice)}</p></div>'


def _checkout_reconciliation_notice(payload: dict[str, Any]) -> str:
    notice = str(payload.get("notice") or "")
    if notice == "cart-changed":
        return (
            '<div class="checkout-payment-warning" role="alert">'
            '<strong>Your cart changed</strong>'
            '<p>Items, quantities, options, or prices changed after the previous '
            'payment approval. Review the updated order total and select a sandbox '
            'payment method again.</p></div>'
        )
    if notice == "payment-declined":
        return (
            '<div class="checkout-payment-warning" role="alert">'
            '<strong>The sandbox issuer declined this payment</strong>'
            '<p>No money was moved and no real account was contacted. Choose a '
            'different sandbox method below and retry.</p></div>'
        )
    if notice == "unsupported-delivery-country":
        return (
            '<div class="checkout-payment-warning" role="alert">'
            '<strong>Choose a supported delivery country</strong>'
            '<p>The previously selected address cannot be used for this local '
            'checkout. Choose or add one of the countries listed below.</p></div>'
        )
    return ""


def checkout_address_page(
    checkout: dict[str, Any], cart_count: int, account_name: str | None
) -> str:
    selected_address = _checkout_address(checkout)
    raw_saved = checkout.get("saved_addresses", [])
    saved_addresses = (
        [item for item in raw_saved if isinstance(item, dict)]
        if isinstance(raw_saved, list)
        else []
    )
    selected_id = _minor_value(selected_address.get("address_id"), 0)
    if not selected_id:
        default_address = next(
            (item for item in saved_addresses if item.get("is_default")),
            saved_addresses[0] if saved_addresses else None,
        )
        selected_id = (
            _minor_value(default_address.get("address_id"), 0)
            if default_address
            else 0
        )
    saved_markup = ""
    if saved_addresses:
        choices: list[str] = []
        for address in saved_addresses:
            address_id = int(address["address_id"])
            revision = int(address["revision"])
            checked = " checked" if address_id == selected_id else ""
            default_badge = (
                '<span class="checkout-address-default">Default</span>'
                if address.get("is_default")
                else ""
            )
            choices.append(
                f"""
                <label class="checkout-address-choice">
                  <input type="radio" name="addressSelection" value="{address_id}:{revision}"{checked}>
                  <span>{default_badge}{_address_markup(address)}</span>
                </label>
                """
            )
        saved_markup = f"""
          <section class="checkout-saved-addresses" aria-labelledby="saved-addresses-heading">
            <div class="checkout-section-heading"><h2 id="saved-addresses-heading">Your addresses</h2><a href="/a/addresses">Manage addresses</a></div>
            <form class="checkout-saved-address-form" method="post" action="/gp/buy/addressselect/handlers/display.html">
              {"".join(choices)}
              <button class="checkout-primary-button" type="submit">Use this address</button>
            </form>
          </section>
          <div class="checkout-address-divider"><span>or add a new address</span></div>
        """
    body = f"""
    <main id="main" class="checkout-main">
      {_checkout_steps("address")}
      <div class="checkout-grid">
        <section class="checkout-card checkout-primary-card">
          <h1>Choose a shipping address</h1>
          {_checkout_reconciliation_notice(checkout)}
          {saved_markup}
          <h2 class="checkout-new-address-heading">Add a new address</h2>
          <form class="checkout-form checkout-address-form" method="post" action="/gp/buy/addressselect/handlers/display.html">
            {_address_input_fields({}, account_name, id_prefix="checkout-new-address")}
            <label class="address-default-choice checkout-field-wide"><input type="checkbox" name="makeDefault" value="1"> Make this my default address</label>
            <button class="checkout-primary-button" type="submit">Use this address</button>
          </form>
        </section>
        {_checkout_summary(checkout)}
      </div>
    </main>
    """
    return _checkout_layout("Amazon Checkout - Shipping address", body, cart_count, account_name, body_class="checkout-address-page")


def checkout_delivery_page(
    checkout: dict[str, Any], cart_count: int, account_name: str | None
) -> str:
    selected = str(checkout.get("delivery_method") or "standard").lower()
    body = f"""
    <main id="main" class="checkout-main">
      {_checkout_steps("delivery")}
      <div class="checkout-grid">
        <section class="checkout-card checkout-primary-card">
          <h1>Choose your delivery option</h1>
          <div class="checkout-selected-address"><h2>Delivering to</h2>{_address_markup(_checkout_address(checkout))}</div>
          <form class="checkout-form" method="post" action="/gp/buy/shipoptionselect/handlers/display.html">
            <label class="checkout-choice"><input type="radio" name="deliveryOption" value="standard"{" checked" if selected != "expedited" else ""}><span><strong>Standard delivery</strong><small>FREE · Local simulated delivery estimate shown after ordering</small></span></label>
            <label class="checkout-choice"><input type="radio" name="deliveryOption" value="expedited"{" checked" if selected == "expedited" else ""}><span><strong>Expedited delivery</strong><small>$12.99 · Faster local simulated delivery</small></span></label>
            <button class="checkout-primary-button" type="submit">Continue</button>
          </form>
        </section>
        {_checkout_summary(checkout)}
      </div>
    </main>
    """
    return _checkout_layout("Amazon Checkout - Delivery options", body, cart_count, account_name, body_class="checkout-delivery-page")


def checkout_payment_page(
    checkout: dict[str, Any], cart_count: int, account_name: str | None
) -> str:
    payment_choices = "".join(
        '<label class="checkout-choice"><input type="radio" '
        f'name="paymentMethod" value="{escape(method["identifier"], quote=True)}"'
        f'{" checked" if method["identifier"] == DEFAULT_PAYMENT_METHOD else ""}>'
        f'<span><strong>{escape(method["label"])}</strong>'
        f'<small>{escape(method["description"])}</small></span></label>'
        for method in public_payment_methods()
    )
    body = f"""
    <main id="main" class="checkout-main">
      {_checkout_steps("payment")}
      <div class="checkout-grid">
        <section class="checkout-card checkout-primary-card">
          <h1>Select a payment method</h1>
          {_checkout_reconciliation_notice(checkout)}
          <div class="checkout-payment-warning" role="alert"><strong>Local payment simulation only</strong><p>No real payment is processed. Never enter a card number, expiration date, security code, or CVV on this page.</p></div>
          <form class="checkout-form" method="post" action="/gp/buy/payselect/handlers/display.html">
            {payment_choices}
            <button class="checkout-primary-button" type="submit">Use this payment method</button>
          </form>
        </section>
        {_checkout_summary(checkout)}
      </div>
    </main>
    """
    return _checkout_layout("Amazon Checkout - Payment", body, cart_count, account_name, body_class="checkout-payment-page")


def checkout_review_page(
    checkout: dict[str, Any], cart_count: int, account_name: str | None
) -> str:
    idempotency_key = escape(str(checkout.get("idempotency_key") or checkout.get("idempotencyKey") or ""), quote=True)
    delivery = _delivery_label(checkout.get("delivery_method"))
    buy_now_notice = (
        '<div class="checkout-buy-now-notice" role="note"><strong>Buy Now checkout</strong>'
        '<p>Only this selection will be ordered. Items already in your cart will remain there.</p></div>'
        if str(checkout.get("checkout_mode") or "CART") == "BUY_NOW"
        else ""
    )
    body = f"""
    <main id="main" class="checkout-main">
      {_checkout_steps("review")}
      <div class="checkout-grid">
        <section class="checkout-card checkout-primary-card checkout-review-card">
          <h1>Review your order</h1>
          {_simulation_notice(checkout)}
          {buy_now_notice}
          <div class="checkout-review-details">
            <section><h2>Shipping address</h2>{_address_markup(_checkout_address(checkout))}</section>
            <section><h2>Delivery option</h2><p>{escape(delivery)}</p></section>
            <section><h2>Payment method</h2>{_payment_markup(checkout.get("payment"))}</section>
          </div>
          <section class="checkout-review-items"><h2>Items</h2>{_checkout_item_rows(checkout)}</section>
          <form class="checkout-place-order" method="post" action="/gp/buy/place-order">
            <input type="hidden" name="idempotencyKey" value="{idempotency_key}">
            <button class="checkout-primary-button" type="submit">Place your order</button>
            <p>By placing your order, you agree to this local simulation. No real charge is created.</p>
          </form>
        </section>
        {_checkout_summary(checkout)}
      </div>
    </main>
    """
    return _checkout_layout("Amazon Checkout - Review your order", body, cart_count, account_name, body_class="checkout-review-page")


def _shipment_markup(order: dict[str, Any]) -> str:
    shipment = order.get("shipment")
    if not isinstance(shipment, dict):
        return '<p class="checkout-empty-copy">Shipment preparation is pending.</p>'
    raw_status = str(
        shipment.get("lifecycle_status") or shipment.get("status") or "PREPARING"
    )
    status = escape(_order_status_label(raw_status))
    carrier = escape(str(shipment.get("carrier") or "Local simulated carrier"))
    tracking = escape(
        str(
            shipment.get("tracking_code")
            or "Generated when the simulated shipment advances"
        )
    )
    delivery = _delivery_label(shipment.get("delivery_method") or order.get("delivery_method"))
    timestamps = []
    for label, key in (
        ("Shipped", "shipped_at"),
        ("Delivered", "delivered_at"),
        ("Cancelled", "cancelled_at"),
    ):
        value = shipment.get(key)
        if value:
            timestamps.append(
                f"<dt>{label}</dt><dd>{escape(str(value))}</dd>"
            )
    return (
        '<p class="lifecycle-simulation-note"><strong>Local shipment simulation</strong>'
        "No real carrier or parcel is connected to this order.</p>"
        f'<dl class="shipment-details"><dt>Status</dt><dd>{status}</dd>'
        f"<dt>Delivery</dt><dd>{escape(delivery)}</dd><dt>Carrier</dt><dd>{carrier}</dd>"
        f"<dt>Tracking</dt><dd>{tracking}</dd>{''.join(timestamps)}</dl>"
    )


def _order_status_label(status: Any) -> str:
    labels = {
        "PLACED": "Order placed",
        "PREPARING": "Preparing for shipment",
        "SHIPPED": "Shipped",
        "DELIVERED": "Delivered",
        "CANCELLED": "Cancelled",
        "RETURN_REQUESTED": "Return requested",
        "RETURN_RECEIVED": "Return received",
        "RETURN_REFUNDED": "Return refunded",
        "REQUESTED": "Return requested",
        "RECEIVED": "Return received",
        "REFUNDED": "Return refunded",
    }
    raw = str(status or "PREPARING")
    return labels.get(raw, raw.replace("_", " ").title())


def _refunds_markup(order: dict[str, Any]) -> str:
    refunds = order.get("refunds")
    if not isinstance(refunds, list) or not refunds:
        return ""
    cards = []
    for refund in refunds:
        if not isinstance(refund, dict):
            continue
        kind = (
            "Cancellation refund"
            if refund.get("kind") == "CANCELLATION"
            else "Return refund"
        )
        cards.append(
            '<article class="refund-record">'
            f"<div><strong>{kind}</strong><span>{escape(str(refund.get('created_at') or ''))}</span></div>"
            f"<b>{money(_minor_value(refund.get('amount_minor')), str(refund.get('currency') or order.get('currency') or 'USD'))}</b>"
            f"<small>{escape(str(refund.get('status') or 'COMPLETED').title())}</small>"
            "</article>"
        )
    if not cards:
        return ""
    return (
        '<section class="checkout-card refund-panel"><h2>Refunds</h2>'
        '<p class="lifecycle-simulation-note"><strong>Local refund simulation</strong>'
        "This record models the order state only. No money was moved and no bank or card network was contacted.</p>"
        f"{''.join(cards)}</section>"
    )


def _order_actions_markup(order: dict[str, Any]) -> str:
    raw_order_id = str(order.get("order_id") or "")
    order_id = escape(raw_order_id, quote=True)
    tokens = order.get("action_tokens")
    keys = order.get("action_idempotency_keys")
    tokens = tokens if isinstance(tokens, dict) else {}
    keys = keys if isinstance(keys, dict) else {}
    actions: list[str] = []
    if order.get("can_cancel") and tokens.get("cancel") and keys.get("cancel"):
        actions.append(
            '<form class="order-action-form order-cancel-form" method="post" '
            'action="/gp/your-account/order-cancel">'
            f'<input type="hidden" name="orderID" value="{order_id}">'
            f'<input type="hidden" name="idempotencyKey" value="{escape(str(keys["cancel"]), quote=True)}">'
            f'<input type="hidden" name="actionToken" value="{escape(str(tokens["cancel"]), quote=True)}">'
            '<button class="button secondary" type="submit">Cancel order</button>'
            '<small>Available before this simulated shipment advances. A completed local refund record will be created.</small>'
            "</form>"
        )
    if order.get("can_return"):
        href = (
            "/gp/your-account/returns/create?orderID="
            + quote_plus(raw_order_id)
        )
        actions.append(
            '<div class="order-action-form">'
            f'<a class="button primary" href="{escape(href, quote=True)}">Return items</a>'
            '<small>Start a whole-order return in this local simulation.</small></div>'
        )
    return_request = order.get("return_request")
    if isinstance(return_request, dict):
        raw_return_id = str(return_request.get("return_request_id") or "")
        href = "/gp/your-account/returns/details?returnID=" + quote_plus(
            raw_return_id
        )
        actions.append(
            '<div class="order-action-form">'
            f'<a class="button secondary" href="{escape(href, quote=True)}">View return details</a>'
            f'<small>{escape(_order_status_label(return_request.get("status")))}</small></div>'
        )
    if not actions:
        return ""
    return (
        '<section class="checkout-card order-actions-panel"><h2>Order actions</h2>'
        f'<div class="order-actions-list">{"".join(actions)}</div></section>'
    )


def _local_email_markup(order: dict[str, Any]) -> str:
    email = order.get("email")
    nested_status = email.get("status") if isinstance(email, dict) else None
    status = str(
        nested_status
        or order.get("email_status")
        or order.get("email_notification_status")
        or "Queued in local email outbox"
    )
    if status == "LOCAL_ONLY":
        heading = "Local email notification"
        detail = "Stored only in the protected local outbox; no external email was sent."
    elif status == "SMTP_SENT":
        heading = "Email notification"
        detail = "The configured SMTP service accepted the order confirmation."
    elif status == "SMTP_FAILED":
        heading = "Email notification"
        heading = "Email notification needs attention"
        detail = "The SMTP attempt failed. Provider details are not exposed on this page."
    else:
        heading = "Email notification"
        detail = "Queued for the configured SMTP service; refresh to check again."

    raw_order_id = str(order.get("order_id") or "")
    detail_href = "/gp/your-account/order-details?" + urlencode(
        {"orderID": raw_order_id}
    )
    actions: list[str] = []
    if status == "SMTP_PENDING":
        actions.append(
            f'<a class="button secondary email-status-action" href="{escape(detail_href, quote=True)}">Refresh email status</a>'
        )
    if (
        status == "SMTP_FAILED"
        and isinstance(email, dict)
        and bool(email.get("can_retry"))
    ):
        actions.append(
            '<form class="order-email-retry-form" method="post" '
            'action="/gp/your-account/order-email/retry">'
            f'<input type="hidden" name="orderID" value="{escape(raw_order_id, quote=True)}">'
            '<button class="button secondary email-status-action" type="submit">Retry email delivery</button>'
            '</form>'
        )
    action_markup = (
        f'<span class="local-email-actions">{"".join(actions)}</span>'
        if actions
        else ""
    )
    return (
        '<section class="local-email-status" aria-live="polite">'
        f'<strong>{heading}</strong><span>{escape(status)} · {escape(detail)}</span>'
        f'{action_markup}</section>'
    )


def order_confirmation_page(
    order: dict[str, Any], cart_count: int, account_name: str | None
) -> str:
    order_id = escape(str(order.get("order_id") or "Pending"))
    raw_status = str(order.get("status") or "PREPARING")
    status = escape(_order_status_label(raw_status))
    created_at = escape(str(order.get("created_at") or "Just now"))
    currency = str(order.get("currency") or "USD")
    order_total = money(_minor_value(order.get("total_minor")), currency)
    heading = (
        "Order placed, thank you!"
        if raw_status == "PREPARING"
        else _order_status_label(raw_status)
    )
    success_mark = "✓" if raw_status not in {"CANCELLED"} else "×"
    body = f"""
    <main id="main" class="checkout-main order-confirmation-main">
      {_checkout_steps("confirmation")}
      <section class="order-confirmation-card">
        <div class="order-success-mark" aria-hidden="true">{success_mark}</div>
        <div><h1>{escape(heading)}</h1><p>Order <strong>{order_id}</strong> · {created_at}</p><span class="order-status-badge">{status}</span></div>
      </section>
      {_simulation_notice(order)}
      <div class="order-confirmation-grid">
        <section class="checkout-card"><h2>Shipping to</h2>{_address_markup(_checkout_address(order))}</section>
        <section class="checkout-card"><h2>Shipment</h2>{_shipment_markup(order)}</section>
        <section class="checkout-card order-confirmation-items"><h2>Order items</h2>{_checkout_item_rows(order)}</section>
        <section class="checkout-card"><h2>Payment</h2>{_payment_markup(order.get("payment"))}<p class="order-confirmation-total">Order total: <strong>{order_total}</strong></p></section>
        {_order_actions_markup(order)}
        {_refunds_markup(order)}
      </div>
      {_local_email_markup(order)}
      <div class="order-confirmation-actions"><a class="button primary" href="/gp/css/order-history">View your orders</a><a class="button secondary" href="/">Continue shopping</a></div>
    </main>
    """
    return _checkout_layout("Amazon.com - Order placed", body, cart_count, account_name, body_class="order-confirmation-page")


def order_history_page(
    orders: list[dict[str, Any]], cart_count: int, account_name: str | None
) -> str:
    order_cards: list[str] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        raw_order_id = str(order.get("order_id") or "Pending")
        order_id = escape(raw_order_id)
        details_href = f"/gp/your-account/order-details?orderID={quote_plus(raw_order_id)}"
        status = escape(_order_status_label(order.get("status")))
        created_at = escape(str(order.get("created_at") or "Recently"))
        currency = str(order.get("currency") or "USD")
        total = money(_minor_value(order.get("total_minor")), currency)
        order_cards.append(
            f"""
            <article class="order-history-card">
              <header><dl><div><dt>Order placed</dt><dd>{created_at}</dd></div><div><dt>Total</dt><dd>{total}</dd></div><div><dt>Ship to</dt><dd>{escape(str(_checkout_address(order).get('full_name') or account_name or 'Customer'))}</dd></div></dl><p>Order # {order_id}<a href="{escape(details_href, quote=True)}">View order details</a></p></header>
              <div class="order-history-status"><h2>{status}</h2><span>{_delivery_label(order.get('delivery_method'))}</span></div>
              <div class="order-history-items">{_checkout_item_rows(order)}</div>
              <section class="order-history-shipment"><h3>Shipment</h3>{_shipment_markup(order)}</section>
              <div class="order-history-actions">{_order_actions_markup(order)}{_refunds_markup(order)}</div>
              {_local_email_markup(order)}
            </article>
            """
        )
    if order_cards:
        content = "".join(order_cards)
    else:
        content = '<section class="orders-empty"><h2>No orders yet</h2><p>Orders placed through the local simulated checkout will appear here.</p><a class="button primary" href="/">Start shopping</a></section>'
    body = f"""
    <main id="main" class="orders-main desktop-shell">
      <header class="orders-heading"><div><h1>Your Orders</h1><p>Local simulated order history</p></div><a href="/gp/css/homepage.html">Your Account</a></header>
      {content}
    </main>
    """
    return layout(
        "Amazon.com - Your Orders",
        body,
        cart_count,
        body_class="orders-page",
        account_name=account_name,
    )


def return_request_page(
    order: dict[str, Any], cart_count: int, account_name: str | None
) -> str:
    raw_order_id = str(order.get("order_id") or "")
    tokens = order.get("action_tokens")
    keys = order.get("action_idempotency_keys")
    tokens = tokens if isinstance(tokens, dict) else {}
    keys = keys if isinstance(keys, dict) else {}
    reason_options = (
        ("DAMAGED", "Item or package arrived damaged"),
        ("DEFECTIVE", "Item is defective or does not work"),
        ("NOT_AS_DESCRIBED", "Item was not as described"),
        ("WRONG_ITEM", "Wrong item was received"),
        ("NO_LONGER_NEEDED", "No longer needed"),
    )
    options = "".join(
        f'<option value="{code}">{escape(label)}</option>'
        for code, label in reason_options
    )
    body = f"""
    <main id="main" class="checkout-main return-request-main">
      <nav class="return-breadcrumb"><a href="/gp/css/order-history">Your Orders</a><span>›</span><a href="/gp/your-account/order-details?orderID={quote_plus(raw_order_id)}">Order {escape(raw_order_id)}</a><span>›</span><strong>Return items</strong></nav>
      <section class="checkout-card return-request-card">
        <h1>Return items</h1>
        <p class="lifecycle-simulation-note"><strong>Local return simulation</strong>This workflow records states only. It does not create a real return label, pickup, shipment, or refund.</p>
        <h2>Items in this return</h2>
        <p>The current clone returns the entire simulated order together.</p>
        <div class="return-items">{_checkout_item_rows(order)}</div>
        <form class="checkout-form return-request-form" method="post" action="/gp/your-account/returns/create">
          <input type="hidden" name="orderID" value="{escape(raw_order_id, quote=True)}">
          <input type="hidden" name="idempotencyKey" value="{escape(str(keys.get('return') or ''), quote=True)}">
          <input type="hidden" name="actionToken" value="{escape(str(tokens.get('return') or ''), quote=True)}">
          <label class="checkout-field"><strong>Why are you returning these items?</strong><select name="reasonCode" required><option value="">Choose a reason</option>{options}</select></label>
          <label class="checkout-field"><strong>Comments (optional)</strong><textarea name="customerNote" maxlength="500" rows="5" placeholder="Add details for this local return record"></textarea><small>500 characters maximum</small></label>
          <button class="checkout-primary-button" type="submit">Submit return request</button>
        </form>
      </section>
    </main>
    """
    return _checkout_layout(
        "Amazon Returns Center - Return items",
        body,
        cart_count,
        account_name,
        body_class="return-request-page",
    )


def return_details_page(
    order: dict[str, Any], cart_count: int, account_name: str | None
) -> str:
    request = order.get("return_request")
    if not isinstance(request, dict):
        return error_page("That return request is unavailable.", cart_count, 404)
    request_id = escape(str(request.get("return_request_id") or ""))
    order_id = escape(str(order.get("order_id") or ""))
    reason_labels = {
        "DAMAGED": "Item or package arrived damaged",
        "DEFECTIVE": "Item is defective or does not work",
        "NOT_AS_DESCRIBED": "Item was not as described",
        "WRONG_ITEM": "Wrong item was received",
        "NO_LONGER_NEEDED": "No longer needed",
    }
    item_rows = []
    for item in request.get("items", []):
        if not isinstance(item, dict):
            continue
        item_rows.append(
            '<li><span>'
            f"{escape(str(item.get('title') or item.get('asin') or 'Order item'))}"
            f"</span><strong>Qty: {escape(str(item.get('quantity') or 1))}</strong></li>"
        )
    note = str(request.get("customer_note") or "").strip()
    note_markup = (
        f'<section><h2>Your comments</h2><p class="return-customer-note">{escape(note)}</p></section>'
        if note
        else ""
    )
    body = f"""
    <main id="main" class="checkout-main return-details-main">
      <nav class="return-breadcrumb"><a href="/gp/css/order-history">Your Orders</a><span>›</span><a href="/gp/your-account/order-details?orderID={order_id}">Order {order_id}</a><span>›</span><strong>Return details</strong></nav>
      <section class="order-confirmation-card return-status-card">
        <div class="order-success-mark" aria-hidden="true">↺</div>
        <div><h1>{escape(_order_status_label(request.get('status')))}</h1><p>Return <strong>{request_id}</strong> for order <strong>{order_id}</strong></p><span class="order-status-badge">Local simulation</span></div>
      </section>
      <p class="lifecycle-simulation-note lifecycle-page-note"><strong>No real return or refund</strong>This page tracks only the clone's simulated state. No label, carrier, warehouse, bank, or card network is connected.</p>
      <div class="order-confirmation-grid">
        <section class="checkout-card"><h2>Return summary</h2><dl class="shipment-details"><dt>Status</dt><dd>{escape(_order_status_label(request.get('status')))}</dd><dt>Reason</dt><dd>{escape(reason_labels.get(str(request.get('reason_code')), str(request.get('reason_code') or 'Not specified')))}</dd><dt>Requested</dt><dd>{escape(str(request.get('created_at') or ''))}</dd><dt>Updated</dt><dd>{escape(str(request.get('updated_at') or ''))}</dd></dl></section>
        <section class="checkout-card"><h2>Items</h2><ul class="return-detail-items">{''.join(item_rows)}</ul></section>
        {note_markup}
        {_refunds_markup(order)}
      </div>
      <div class="order-confirmation-actions"><a class="button primary" href="/gp/your-account/order-details?orderID={order_id}">View order details</a><a class="button secondary" href="/gp/css/order-history">Your Orders</a></div>
    </main>
    """
    return _checkout_layout(
        "Amazon Returns Center - Return details",
        body,
        cart_count,
        account_name,
        body_class="return-details-page",
    )


def _compare_detail(product: dict[str, Any]) -> dict[str, Any]:
    detail = product.get("pdp")
    return detail if isinstance(detail, dict) else {}


def _compare_overlay(product: dict[str, Any]) -> dict[str, Any]:
    compare = product.get("compare")
    return compare if isinstance(compare, dict) else {}


def _compare_fact(product: dict[str, Any], *names: str) -> Any:
    for source in (_compare_overlay(product), product, _compare_detail(product)):
        for name in names:
            if name not in source:
                continue
            value = source[name]
            if value is not None and value != "":
                return value
    return None


def _compare_display(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, (list, tuple)):
        clean_values = [str(item) for item in value if item is not None and item != ""]
        return ", ".join(clean_values) if clean_values else "—"
    if isinstance(value, dict):
        return "—"
    return str(value)


def _compare_specs(product: dict[str, Any]) -> dict[str, tuple[str, str]]:
    specs: dict[str, tuple[str, str]] = {}
    raw_sources = (
        _compare_overlay(product).get("specs"),
        product.get("specs"),
        _compare_detail(product).get("specs"),
    )
    for raw_specs in raw_sources:
        entries: list[tuple[Any, Any]] = []
        if isinstance(raw_specs, dict):
            entries.extend(raw_specs.items())
        elif isinstance(raw_specs, list):
            for entry in raw_specs:
                if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    entries.append((entry[0], entry[1]))
                elif isinstance(entry, dict):
                    name = entry.get("name") or entry.get("label") or entry.get("key")
                    if name is not None and "value" in entry:
                        entries.append((name, entry.get("value")))
        for raw_name, raw_value in entries:
            name = str(raw_name).strip()
            value = _compare_display(raw_value)
            if not name or value == "—":
                continue
            normalized = " ".join(name.casefold().split())
            specs.setdefault(normalized, (name, value))
    return specs


def _compare_common_rows(products: list[dict[str, Any]]) -> list[tuple[str, list[str]]]:
    def price(product: dict[str, Any]) -> str:
        price_minor = _compare_fact(product, "price_minor")
        if not isinstance(price_minor, int):
            return "—"
        currency = _compare_display(_compare_fact(product, "currency"))
        return money(price_minor, currency if currency != "—" else "USD")

    def reviews(product: dict[str, Any]) -> str:
        value = _compare_fact(product, "reviews", "review_count")
        return f"{value:,}" if isinstance(value, int) else _compare_display(value)

    def selected_options(product: dict[str, Any]) -> str:
        raw_options = _compare_fact(product, "selected_options")
        if not isinstance(raw_options, dict) or not raw_options:
            return "Default offer"
        return " · ".join(
            f"{label}: {value}" for label, value in raw_options.items()
        )

    facts: tuple[tuple[str, Any], ...] = (
        ("ASIN", lambda product: _compare_display(_compare_fact(product, "asin"))),
        ("Selected options", selected_options),
        ("Price", price),
        ("Currency", lambda product: _compare_display(_compare_fact(product, "currency"))),
        ("Rating", lambda product: _compare_display(_compare_fact(product, "rating"))),
        ("Customer reviews", reviews),
        ("Brand", lambda product: _compare_display(_compare_fact(product, "brand"))),
        ("Category", lambda product: _compare_display(_compare_fact(product, "category", "page_category"))),
        ("Product family", lambda product: _compare_display(_compare_fact(product, "family"))),
        ("Availability", lambda product: _compare_display(_compare_fact(product, "display_availability", "availability", "stock"))),
        ("Ships from", lambda product: _compare_display(_compare_fact(product, "ships_from", "shipper"))),
        ("Sold by", lambda product: _compare_display(_compare_fact(product, "sold_by", "seller"))),
        ("Delivery", lambda product: _compare_display(_compare_fact(product, "delivery", "delivery_copy"))),
        ("Shipping", lambda product: _compare_display(_compare_fact(product, "shipping", "shipping_copy"))),
        ("Returns", lambda product: _compare_display(_compare_fact(product, "returns", "returns_copy"))),
    )
    return [(label, [getter(product) for product in products]) for label, getter in facts]


def compare_page(
    products: list[dict[str, Any]],
    cart_count: int,
    account_name: str | None = None,
    error: str | None = None,
) -> str:
    selected = [
        product
        for product in products
        if isinstance(product, dict)
        and isinstance(product.get("compare_line_id"), str)
    ][:4]
    count = len(selected)
    error_markup = (
        f'<div class="compare-error" role="alert"><strong>Unable to update comparison</strong><p>{escape(error)}</p></div>'
        if error
        else ""
    )
    guidance = (
        f'<div class="compare-guidance" role="status"><strong>Add at least 2 products to compare.</strong><p>{count} of 4 comparison slots selected. Eligible products can be added from their product or search pages.</p></div>'
        if count < 2
        else '<p class="compare-guidance compact">Compare the source-backed facts below. Missing evidence is shown as —.</p>'
    )
    clear_form = (
        '<form class="compare-clear-form" method="post" action="/gp/compare/clear"><button type="submit">Clear comparison</button></form>'
        if count
        else ""
    )

    table_markup = ""
    if count >= 2:
        product_headers: list[str] = []
        product_specs: list[dict[str, tuple[str, str]]] = []
        spec_union: dict[str, str] = {}
        for product in selected:
            asin = str(product.get("asin") or "")
            compare_line_id = escape(
                str(product.get("compare_line_id") or ""), quote=True
            )
            title = escape(str(product.get("title") or asin))
            title_attr = escape(str(product.get("title") or asin), quote=True)
            href = escape(product_href(product), quote=True)
            image_path = _compare_fact(product, "image_path", "main_image")
            image_markup = (
                f'<a class="compare-product-image" href="{href}"><img src="{escape(str(image_path), quote=True)}" width="180" height="180" alt="{title_attr}"></a>'
                if image_path
                else '<span class="compare-image-missing">No image</span>'
            )
            product_headers.append(
                f'<th scope="col"><article class="compare-product-header">{image_markup}<a class="compare-product-title" href="{href}">{title}</a><form method="post" action="/gp/compare/remove"><input type="hidden" name="compareLineID" value="{compare_line_id}"><button type="submit">Remove</button></form></article></th>'
            )
            specs = _compare_specs(product)
            product_specs.append(specs)
            for normalized, (label, _) in specs.items():
                spec_union.setdefault(normalized, label)

        common_rows = "".join(
            f'<tr><th scope="row">{escape(label)}</th>{"".join(f"<td>{escape(value)}</td>" for value in values)}</tr>'
            for label, values in _compare_common_rows(selected)
        )
        spec_rows = ""
        if spec_union:
            spec_rows = f'<tr class="compare-section-row"><th colspan="{count + 1}">Specifications</th></tr>' + "".join(
                f'<tr><th scope="row">{escape(label)}</th>{"".join(f"<td>{escape(specs.get(normalized, (label, "—"))[1])}</td>" for specs in product_specs)}</tr>'
                for normalized, label in spec_union.items()
            )
        table_markup = f"""
        <div class="compare-table-viewport" tabindex="0" aria-label="Scrollable product comparison">
          <table class="compare-table">
            <thead><tr><th class="compare-feature-heading" scope="col">Feature</th>{"".join(product_headers)}</tr></thead>
            <tbody><tr class="compare-section-row"><th colspan="{count + 1}">Product details</th></tr>{common_rows}{spec_rows}</tbody>
          </table>
        </div>
        """
    elif count == 1:
        product = selected[0]
        asin = str(product.get("asin") or "")
        safe_asin = escape(asin, quote=True)
        compare_line_id = escape(
            str(product.get("compare_line_id") or ""), quote=True
        )
        title = escape(str(product.get("title") or asin))
        title_attr = escape(str(product.get("title") or asin), quote=True)
        href = escape(product_href(product), quote=True)
        image_path = _compare_fact(product, "image_path", "main_image")
        image_markup = (
            f'<a href="{href}"><img src="{escape(str(image_path), quote=True)}" width="150" height="150" alt="{title_attr}"></a>'
            if image_path
            else '<span class="compare-image-missing">No image</span>'
        )
        table_markup = f"""
        <section class="compare-waiting" aria-labelledby="compare-selected-heading">
          <h2 id="compare-selected-heading">Selected product</h2>
          <article>{image_markup}<div><a href="{href}">{title}</a><p>ASIN: {safe_asin}</p><p>{escape(_compare_common_rows([product])[1][1][0])}</p><form method="post" action="/gp/compare/remove"><input type="hidden" name="compareLineID" value="{compare_line_id}"><button type="submit">Remove</button></form></div></article>
        </section>
        """
    else:
        table_markup = '<section class="compare-empty"><h2>Your comparison is empty</h2><p>Browse a purchasable, source-backed product and choose Compare.</p><a class="button primary" href="/s?k=portable+ssd">Browse comparable products</a></section>'

    body = f"""
    <main id="main" class="compare-main desktop-shell" data-compare-count="{count}">
      <header class="compare-heading"><div><h1>Compare products</h1><p>Current server quotes, selected variants, and source-backed attributes.</p></div>{clear_form}</header>
      {error_markup}
      {guidance}
      {table_markup}
    </main>
    """
    return layout(
        "Amazon.com - Compare products",
        body,
        cart_count,
        body_class="compare-page",
        account_name=account_name,
    )


def not_found_page(cart_count: int) -> str:
    body = """
    <main id="main" class="not-found-main"><div><h1>Sorry, we couldn't find that page</h1><p>Try searching or go to Amazon's home page.</p><a href="/">Go to Amazon home</a></div><div class="dog-card" aria-hidden="true">🐕</div></main>
    """
    return layout("Page Not Found", body, cart_count, body_class="not-found-page")


def error_page(message: str, cart_count: int, status: int = 409) -> str:
    body = f"""
    <main id="main" class="error-main"><section><h1>There was a problem</h1><p>{escape(message)}</p><a class="button primary" href="{PDP_PATH}">Return to the item</a></section></main>
    """
    return layout(f"Amazon.com - Error {status}", body, cart_count, body_class="error-page")
