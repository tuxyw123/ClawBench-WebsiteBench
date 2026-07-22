from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import urlencode

from render import layout, money, selected_options_markup


WISHLIST_INTRO_PATH = "/hz/wishlist/intro"
WISHLIST_INDEX_PATH = "/hz/wishlist/ls"
WISHLIST_CREATE_PATH = "/hz/wishlist/create"
WISHLIST_RENAME_PATH = "/hz/wishlist/rename"
WISHLIST_DELETE_PATH = "/hz/wishlist/delete"
WISHLIST_ADD_CHOOSER_PATH = "/hz/wishlist/add"
WISHLIST_ADD_ITEM_PATH = "/hz/wishlist/add-item"
WISHLIST_REMOVE_ITEM_PATH = "/hz/wishlist/remove-item"
WISHLIST_MOVE_TO_CART_PATH = "/hz/wishlist/move-to-cart"

WISHLIST_ASSET_ROOT = (
    "/static/assets/source-current/2026-07-22/lists-intro"
)
WISHLIST_STYLESHEET = "/static/wishlist.css?v=20260722-1"


def _wishlist_layout(
    title: str,
    body: str,
    cart_count: int,
    *,
    account_name: str | None = None,
    body_class: str = "",
) -> str:
    return layout(
        title,
        body,
        cart_count,
        account_name=account_name,
        body_class=f"wishlist-surface {body_class}".strip(),
        extra_head=(
            f'<link rel="stylesheet" href="{WISHLIST_STYLESHEET}">'
        ),
    )


def _signin_href(return_to: str = WISHLIST_INDEX_PATH) -> str:
    query = urlencode({"openid.return_to": return_to})
    return f"/ap/signin?{escape(query, quote=True)}"


def _detail_href(list_id: int | str) -> str:
    query = urlencode({"listID": str(list_id)})
    return f"{WISHLIST_INDEX_PATH}?{escape(query, quote=True)}"


def wishlist_entry_form(
    product: dict[str, Any],
    selected_options: dict[str, str],
    *,
    button_label: str = "Add to List",
    css_class: str = "add-to-list-form",
) -> str:
    """Build the PDP/search entry form that opens the authenticated chooser.

    ``data-product-option-field`` lets the existing PDP option controller keep
    this form's complete selection synchronized with visible variant controls.
    The form deliberately contains no browser-supplied price or offer metadata.
    """

    asin = escape(str(product["asin"]), quote=True)
    option_fields = "".join(
        f'<input type="hidden" name="option.{escape(label, quote=True)}" value="{escape(value, quote=True)}" data-product-option-field="{escape(label, quote=True)}">'
        for label, value in selected_options.items()
    )
    return f"""
    <form class="{escape(css_class, quote=True)}" method="get" action="{WISHLIST_ADD_CHOOSER_PATH}" data-wishlist-form="open-add-chooser">
      <input type="hidden" name="ASIN" value="{asin}">
      {option_fields}
      <button type="submit">{escape(button_label)}</button>
    </form>
    """


def wishlist_intro_page(cart_count: int) -> str:
    """Render the anonymous Your Lists introduction at desktop and mobile widths."""

    signin_href = _signin_href()
    desktop_benefits = (
        (
            "save-money.png",
            "Save money",
            "Keep products together and check back when prices change.",
        ),
        (
            "shop-with-friends.png",
            "Shop with friends",
            "Keep gift ideas together for birthdays, holidays, and everyday shopping.",
        ),
        (
            "stay-organized.png",
            "Stay organized",
            "Build as many lists as you need and find every saved item quickly.",
        ),
    )
    desktop_benefit_markup = "".join(
        f"""
        <article class="wishlist-benefit-card">
          <img src="{WISHLIST_ASSET_ROOT}/{image_name}" alt="" loading="lazy">
          <h3>{escape(title)}</h3>
          <p>{escape(copy)}</p>
        </article>
        """
        for image_name, title, copy in desktop_benefits
    )

    desktop_registries = (
        (
            "baby-registry.jpg",
            "Baby Registry",
            "Get ready for your new arrival with everything in one place.",
            "/gp/browse.html?node=16115931011",
        ),
        (
            "wedding-registry.jpg",
            "Wedding Registry",
            "Create a registry that makes gifting simple for everyone.",
            "/gp/browse.html?node=16115931011",
        ),
        (
            "custom-gift.jpg",
            "Gift List",
            "Save gift ideas for birthdays, holidays, and other celebrations.",
            "/gp/browse.html?node=16115931011",
        ),
    )
    desktop_registry_markup = "".join(
        f"""
        <a class="wishlist-registry-card" href="{escape(href, quote=True)}">
          <img src="{WISHLIST_ASSET_ROOT}/{image_name}" alt="" loading="lazy">
          <span><strong>{escape(title)}</strong>{escape(copy)}</span>
        </a>
        """
        for image_name, title, copy, href in desktop_registries
    )

    mobile_benefits = (
        ("Save time", "Add products now and come back whenever you're ready."),
        ("Give great gifts", "Keep thoughtful gift ideas together in one place."),
        ("Check price changes", "See the latest offer each time you open your list."),
        ("Check current deals", "Keep your favorite products together for easy deal checking."),
    )
    mobile_benefit_markup = "".join(
        f"""
        <li><span aria-hidden="true">✓</span><div><strong>{escape(title)}</strong><p>{escape(copy)}</p></div></li>
        """
        for title, copy in mobile_benefits
    )

    body = f"""
    <main id="main" class="wishlist-intro" data-wishlist-page="intro">
      <section class="wishlist-intro-desktop" aria-label="Your Lists">
        <nav class="wishlist-intro-tabs" aria-label="Lists navigation">
          <a class="is-active" href="{WISHLIST_INTRO_PATH}" aria-current="page">Your Lists</a>
          <a href="/hz/wishlist/your-friends">Your Friends</a>
        </nav>

        <section class="wishlist-intro-hero">
          <img src="{WISHLIST_ASSET_ROOT}/desktop-banner.jpg" alt="" fetchpriority="high">
          <div>
            <h1>Lists &amp; Registries</h1>
            <p>Save products you love, keep ideas organized, and plan gifts for the people who matter.</p>
          </div>
        </section>

        <section class="wishlist-benefits" aria-labelledby="wishlist-benefits-title">
          <h2 id="wishlist-benefits-title">Why use Lists?</h2>
          <div class="wishlist-benefit-grid">{desktop_benefit_markup}</div>
          <a class="wishlist-primary-button" href="{signin_href}">Sign In</a>
        </section>

        <section class="wishlist-registries" aria-labelledby="wishlist-registries-title">
          <h2 id="wishlist-registries-title">Gift Registries</h2>
          <div class="wishlist-registry-grid">{desktop_registry_markup}</div>
        </section>
      </section>

      <section class="wishlist-intro-mobile" aria-label="Your Lists">
        <a class="wishlist-primary-button wishlist-mobile-signin" href="{signin_href}">Sign In</a>
        <p class="wishlist-mobile-signin-note">Sign in to see your Lists and Registries.</p>

        <section class="wishlist-mobile-list-kind">
          <img src="{WISHLIST_ASSET_ROOT}/mobile-list-icon.png" alt="">
          <div><h1>Shopping List</h1><p>Save everyday items, compare choices, and buy them when you're ready.</p></div>
        </section>
        <section class="wishlist-mobile-list-kind">
          <img src="{WISHLIST_ASSET_ROOT}/mobile-gift-icon.png" alt="">
          <div><h2>Wish List</h2><p>Collect gift ideas for family, friends, and special occasions.</p></div>
        </section>

        <ul class="wishlist-mobile-benefits" aria-label="List benefits">{mobile_benefit_markup}</ul>

        <section class="wishlist-mobile-registries" aria-labelledby="wishlist-mobile-registries-title">
          <h2 id="wishlist-mobile-registries-title">Create a Registry</h2>
          <a href="/gp/browse.html?node=16115931011"><img src="{WISHLIST_ASSET_ROOT}/mobile-baby-registry.png" alt="Baby Registry"><span>Baby Registry</span></a>
          <a href="/gp/browse.html?node=16115931011"><img src="{WISHLIST_ASSET_ROOT}/mobile-wedding-registry.png" alt="Wedding Registry"><span>Wedding Registry</span></a>
        </section>
      </section>
    </main>
    """
    return _wishlist_layout(
        "Amazon Lists",
        body,
        cart_count,
        body_class="wishlist-intro-body",
    )


def _list_card(wishlist: dict[str, Any]) -> str:
    list_id = int(wishlist["list_id"])
    name = escape(str(wishlist["name"]))
    item_count = int(wishlist.get("item_count", 0))
    item_copy = "1 item" if item_count == 1 else f"{item_count} items"
    default_badge = (
        '<span class="wishlist-default-badge">Default</span>'
        if bool(wishlist.get("is_default"))
        else ""
    )
    return f"""
    <article class="wishlist-list-card" data-list-id="{list_id}">
      <a href="{_detail_href(list_id)}">
        <span class="wishlist-list-card-icon" aria-hidden="true">☰</span>
        <span class="wishlist-list-card-copy"><strong>{name}</strong><small>{item_copy}</small></span>
        {default_badge}
        <span class="wishlist-list-card-arrow" aria-hidden="true">›</span>
      </a>
    </article>
    """


def wishlist_index_page(
    wishlists: list[dict[str, Any]],
    cart_count: int,
    account_name: str,
    *,
    status: str = "",
) -> str:
    """Render the authenticated list index and the create-list affordance."""

    list_markup = "".join(_list_card(wishlist) for wishlist in wishlists)
    status_markup = (
        f'<div class="wishlist-status" role="status">{escape(status)}</div>'
        if status
        else ""
    )
    body = f"""
    <main id="main" class="wishlist-app" data-wishlist-page="index">
      <header class="wishlist-page-heading">
        <div><p>Your Account › Your Lists</p><h1>Your Lists</h1></div>
      </header>
      {status_markup}
      <div class="wishlist-index-layout">
        <section class="wishlist-list-collection" aria-labelledby="wishlist-owned-title">
          <h2 id="wishlist-owned-title">Your lists</h2>
          <div class="wishlist-list-grid">{list_markup}</div>
        </section>
        <aside class="wishlist-create-card">
          <h2>Create a List</h2>
          <p>Use Lists to save products and keep purchases organized.</p>
          <form method="post" action="{WISHLIST_CREATE_PATH}" data-wishlist-form="create">
            <label for="wishlist-new-name">List name</label>
            <input id="wishlist-new-name" name="listName" maxlength="100" autocomplete="off" required>
            <button class="wishlist-primary-button" type="submit">Create List</button>
          </form>
        </aside>
      </div>
    </main>
    """
    return _wishlist_layout(
        "Your Lists",
        body,
        cart_count,
        account_name=account_name,
        body_class="wishlist-app-body",
    )


def _list_rail(wishlists: list[dict[str, Any]], active_list_id: int) -> str:
    links = "".join(
        f"""
        <a href="{_detail_href(int(wishlist['list_id']))}"{' class="is-active" aria-current="page"' if int(wishlist['list_id']) == active_list_id else ''}>
          <span>{escape(str(wishlist['name']))}</span><small>{int(wishlist.get('item_count', 0))}</small>
        </a>
        """
        for wishlist in wishlists
    )
    return f"""
    <aside class="wishlist-list-rail" aria-label="Your lists">
      <a class="wishlist-back-link" href="{WISHLIST_INDEX_PATH}">‹ All Lists</a>
      <nav>{links}</nav>
    </aside>
    """


def _wishlist_item(item: dict[str, Any], list_id: int) -> str:
    item_id = int(item["item_id"])
    asin = escape(str(item["asin"]), quote=True)
    title = escape(str(item["title"]))
    image_path = escape(str(item["image_path"]), quote=True)
    href = escape(str(item["canonical_path"]), quote=True)
    available_to_cart = bool(item.get("available_to_cart"))
    raw_price = item.get("price_minor")
    currency = str(item.get("currency") or "USD")
    options = selected_options_markup(
        item, css_class="wishlist-item-options"
    )
    if available_to_cart and isinstance(raw_price, int):
        offer_markup = f"<strong>{money(raw_price, currency)}</strong>"
        cart_control = f"""
        <form method="post" action="{WISHLIST_MOVE_TO_CART_PATH}" data-wishlist-form="move-to-cart">
          <input type="hidden" name="listID" value="{list_id}">
          <input type="hidden" name="itemID" value="{item_id}">
          <input type="hidden" name="quantity" value="1">
          <button class="wishlist-primary-button" type="submit">Add to Cart</button>
        </form>
        """
        availability_copy = '<p class="wishlist-item-stock">In Stock</p>'
    else:
        offer_markup = '<strong class="wishlist-item-unavailable">Offer unavailable</strong>'
        cart_control = '<button class="wishlist-secondary-button" type="button" disabled>Unavailable for Cart</button>'
        availability_copy = '<p class="wishlist-item-unavailable-copy">No verified local offer for this product.</p>'
    return f"""
    <article class="wishlist-item" data-item-id="{item_id}" data-asin="{asin}">
      <a class="wishlist-item-image" href="{href}"><img src="{image_path}" alt="{escape(str(item['title']), quote=True)}" loading="lazy"></a>
      <div class="wishlist-item-copy">
        <a class="wishlist-item-title" href="{href}">{title}</a>
        {options}
        {availability_copy}
        <p class="wishlist-item-added">Added to this List</p>
      </div>
      <div class="wishlist-item-purchase">
        {offer_markup}
        {cart_control}
        <form method="post" action="{WISHLIST_REMOVE_ITEM_PATH}" data-wishlist-form="remove-item">
          <input type="hidden" name="listID" value="{list_id}">
          <input type="hidden" name="itemID" value="{item_id}">
          <button class="wishlist-secondary-button" type="submit">Delete</button>
        </form>
      </div>
    </article>
    """


def wishlist_detail_page(
    wishlist: dict[str, Any],
    wishlists: list[dict[str, Any]],
    cart_count: int,
    account_name: str,
    *,
    status: str = "",
) -> str:
    """Render one authenticated list with account-owned item controls."""

    list_id = int(wishlist["list_id"])
    name = escape(str(wishlist["name"]))
    items = wishlist.get("items")
    if not isinstance(items, list):
        items = []
    if items:
        item_markup = "".join(_wishlist_item(item, list_id) for item in items)
    else:
        item_markup = """
        <section class="wishlist-empty" aria-label="Empty list">
          <span aria-hidden="true">♡</span>
          <h2>This List is empty</h2>
          <p>Browse products and choose <strong>Add to List</strong> on a product page.</p>
          <a class="wishlist-primary-button" href="/">Continue shopping</a>
        </section>
        """
    status_markup = (
        f'<div class="wishlist-status" role="status">{escape(status)}</div>'
        if status
        else ""
    )
    default_copy = (
        '<span class="wishlist-default-badge">Default List</span>'
        if bool(wishlist.get("is_default"))
        else ""
    )
    delete_control = (
        ""
        if len(wishlists) <= 1
        else f"""
        <form method="post" action="{WISHLIST_DELETE_PATH}" data-wishlist-form="delete-list">
          <input type="hidden" name="listID" value="{list_id}">
          <button class="wishlist-link-button" type="submit">Delete List</button>
        </form>
        """
    )
    body = f"""
    <main id="main" class="wishlist-app" data-wishlist-page="detail" data-list-id="{list_id}">
      {status_markup}
      <div class="wishlist-detail-layout">
        {_list_rail(wishlists, list_id)}
        <section class="wishlist-detail-content">
          <header class="wishlist-detail-heading">
            <div><h1>{name}</h1>{default_copy}<p>{len(items)} {'item' if len(items) == 1 else 'items'}</p></div>
            <details class="wishlist-manage-menu">
              <summary>More</summary>
              <div>
                <form method="post" action="{WISHLIST_RENAME_PATH}" data-wishlist-form="rename-list">
                  <input type="hidden" name="listID" value="{list_id}">
                  <label for="wishlist-rename-{list_id}">Rename List</label>
                  <input id="wishlist-rename-{list_id}" name="listName" value="{escape(str(wishlist['name']), quote=True)}" maxlength="100" required>
                  <button class="wishlist-secondary-button" type="submit">Save</button>
                </form>
                {delete_control}
              </div>
            </details>
          </header>
          <div class="wishlist-items">{item_markup}</div>
        </section>
      </div>
    </main>
    """
    return _wishlist_layout(
        str(wishlist["name"]),
        body,
        cart_count,
        account_name=account_name,
        body_class="wishlist-app-body",
    )


def wishlist_add_chooser_page(
    product: dict[str, Any],
    selected_options: dict[str, str],
    wishlists: list[dict[str, Any]],
    cart_count: int,
    account_name: str,
) -> str:
    """Render a signed-in list chooser without embedding a client price."""

    asin = escape(str(product["asin"]), quote=True)
    title = escape(str(product["title"]))
    image_path = escape(str(product["image_path"]), quote=True)
    default_list = next(
        (wishlist for wishlist in wishlists if wishlist.get("is_default")),
        wishlists[0] if wishlists else None,
    )
    option_fields = "".join(
        f'<input type="hidden" name="option.{escape(label, quote=True)}" value="{escape(value, quote=True)}">'
        for label, value in selected_options.items()
    )
    option_markup = selected_options_markup(
        {"selected_options": selected_options},
        css_class="wishlist-chooser-options",
    )
    list_choices = "".join(
        f"""
        <label class="wishlist-chooser-list">
          <input type="radio" name="listID" value="{int(wishlist['list_id'])}"{' checked' if default_list is not None and int(wishlist['list_id']) == int(default_list['list_id']) else ''} required>
          <span><strong>{escape(str(wishlist['name']))}</strong><small>{int(wishlist.get('item_count', 0))} {'item' if int(wishlist.get('item_count', 0)) == 1 else 'items'}</small></span>
        </label>
        """
        for wishlist in wishlists
    )
    if not list_choices:
        list_choices = '<p class="wishlist-chooser-error">No List is available for this account.</p>'

    body = f"""
    <main id="main" class="wishlist-chooser-page" data-wishlist-page="add-chooser" data-asin="{asin}">
      <section class="wishlist-chooser-card">
        <header><a href="{WISHLIST_INDEX_PATH}">Your Lists</a><h1>Add to List</h1></header>
        <div class="wishlist-chooser-product">
          <img src="{image_path}" alt="{escape(str(product['title']), quote=True)}">
          <div><strong>{title}</strong>{option_markup}</div>
        </div>
        <form method="post" action="{WISHLIST_ADD_ITEM_PATH}" data-wishlist-form="add-item">
          <input type="hidden" name="ASIN" value="{asin}">
          {option_fields}
          <fieldset><legend>Choose a List</legend>{list_choices}</fieldset>
          <div class="wishlist-chooser-actions">
            <a class="wishlist-secondary-button" href="{escape(str(product.get('canonical_path') or '/'), quote=True)}">Cancel</a>
            <button class="wishlist-primary-button" type="submit"{' disabled' if not wishlists else ''}>Add to List</button>
          </div>
        </form>
      </section>
    </main>
    """
    return _wishlist_layout(
        "Add to List",
        body,
        cart_count,
        account_name=account_name,
        body_class="wishlist-chooser-body",
    )
