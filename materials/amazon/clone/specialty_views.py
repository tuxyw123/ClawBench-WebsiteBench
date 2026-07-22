"""Rendered local Gift Cards, Sell, Registry, and static Video placeholder pages."""

from __future__ import annotations

from html import escape
from typing import Any, Iterable
from urllib.parse import urlencode

from render import layout, money
from specialty_store import (
    GIFT_AMOUNTS_MINOR,
    GIFT_CARD_BALANCE_PATH,
    GIFT_CARD_PREVIEW_PATH,
    GIFT_CARD_REDEEM_PATH,
    GIFT_CARDS_PATH,
    GIFT_DESIGNS,
    PRIME_VIDEO_PATH,
    REGISTRY_CREATE_PATH,
    REGISTRY_DETAIL_PATH,
    REGISTRY_PATH,
    REGISTRY_SEARCH_PATH,
    REGISTRY_TYPES,
    SELL_CATEGORIES,
    SELL_CONDITIONS,
    SELL_DRAFT_PATH,
    SELL_PATH,
)


CUSTOMER_SERVICE_HREF = "/gp/help/customer/display.html?nodeId=508510"


def _simulation_notice(copy: str) -> str:
    return (
        '<aside class="specialty-simulation-notice" role="note">'
        '<strong>Local simulation</strong>'
        f"<span>{escape(copy)}</span></aside>"
    )


def gift_cards_page(cart_count: int, account_name: str | None = None) -> str:
    account_href = (
        "/gp/css/homepage.html"
        if account_name
        else "/ap/signin?openid.return_to=%2Fgift-cards%2Fb%2F"
    )
    account_action = "Go to your account" if account_name else "Sign in"
    design_choices = "".join(
        f'<label class="specialty-design-card specialty-design-{escape(key, quote=True)}">'
        f'<input type="radio" name="design" value="{escape(key, quote=True)}"'
        f'{" checked" if key == "classic" else ""}><span aria-hidden="true"></span>'
        f"<strong>{escape(label)}</strong></label>"
        for key, label in GIFT_DESIGNS.items()
    )
    amount_choices = "".join(
        f'<label><input type="radio" name="amount" value="{amount // 100}"'
        f'{" checked" if amount == 5000 else ""}><span>{money(amount, "USD")}</span></label>'
        for amount in GIFT_AMOUNTS_MINOR
    )
    categories = (
        ("Birthday", "/s?k=birthday+gifts", "Celebrate their day"),
        ("Books", "/s?k=books", "For every kind of reader"),
        ("Home & Kitchen", "/s?k=home+kitchen", "Useful picks for home"),
        ("Toys & Games", "/s?k=toys", "For play and discovery"),
        ("Beauty", "/s?k=beauty+personal+care", "Self-care favorites"),
        ("Deals", "/gp/goldbox/", "Browse current offers"),
    )
    category_cards = "".join(
        f'<a class="specialty-gift-category" href="{href}"><strong>{escape(title)}</strong>'
        f"<span>{escape(copy)}</span><small>Explore ›</small></a>"
        for title, href, copy in categories
    )
    body = f"""
    <main id="main" class="specialty-main specialty-gift-main" data-navigation-page="gift-cards">
      <section class="specialty-gift-hero">
        <div><p>Amazon Gift Cards</p><h1>So many ways to celebrate</h1><span>Choose a card face and amount, then continue to a controlled local preview. No payment information is collected.</span></div>
        <div class="specialty-gift-art specialty-design-classic" aria-hidden="true"><span>amazon</span><strong>Gift Card</strong><i></i></div>
      </section>
      <nav class="specialty-primary-actions" aria-label="Gift Card actions">
        <a href="{GIFT_CARD_REDEEM_PATH}"><span aria-hidden="true">＋</span><strong>Redeem</strong><small>Try a fictional local code</small></a>
        <a href="{GIFT_CARD_BALANCE_PATH}"><span aria-hidden="true">$</span><strong>View balance</strong><small>See the simulated $0.00 balance</small></a>
        <a href="#gift-card-builder"><span aria-hidden="true">↻</span><strong>Reload or buy</strong><small>Build a local purchase preview</small></a>
      </nav>
      {_simulation_notice("Gift Card previews do not create value, charge a card, send email, or connect to Amazon Gift Card services.")}
      <section id="gift-card-builder" class="specialty-panel specialty-builder" aria-labelledby="gift-builder-heading">
        <div class="specialty-section-heading"><p>Customers love these gift card styles</p><h2 id="gift-builder-heading">Build a local Gift Card preview</h2></div>
        <form method="post" action="{GIFT_CARD_PREVIEW_PATH}" class="specialty-form">
          <fieldset><legend>1. Choose a card face</legend><div class="specialty-design-grid">{design_choices}</div></fieldset>
          <fieldset><legend>2. Choose an amount</legend><div class="specialty-amount-grid">{amount_choices}</div></fieldset>
          <fieldset><legend>3. Who is it for?</legend><div class="specialty-choice-row"><label><input type="radio" name="recipientKind" value="gift" checked> Someone else</label><label><input type="radio" name="recipientKind" value="self"> Me</label></div></fieldset>
          <button class="button primary" type="submit">Continue to local preview</button>
        </form>
      </section>
      <section class="specialty-rail-section"><div class="specialty-section-heading"><p>Occasion, category, and delivery ideas</p><h2>Shop gifts by department</h2><span>Find a gift from the local catalog.</span></div><div class="specialty-category-grid">{category_cards}</div></section>
      <section class="specialty-faq"><h2>Gift Card FAQ</h2><details><summary>Can I use a real Gift Card here?</summary><p>No. Do not enter a real claim code. This clone stores only an irreversible fingerprint of a fictional test code and never changes a real balance.</p></details><details><summary>Will the purchase preview charge me?</summary><p>No. It does not request card details and cannot complete a financial transaction.</p></details><details><summary>Where can I get account help?</summary><p><a href="{CUSTOMER_SERVICE_HREF}&amp;help_keywords=gift">Search Customer Service</a> or <a href="{account_href}">{account_action}</a>.</p></details></section>
    </main>
    """
    return layout(
        "Amazon.com Gift Cards",
        body,
        cart_count,
        body_class="specialty-page gift-cards-page",
        account_name=account_name,
    )


def gift_card_preview_page(
    preview: dict[str, Any], cart_count: int, account_name: str | None
) -> str:
    design = str(preview.get("design", "classic"))
    design_label = GIFT_DESIGNS.get(design, GIFT_DESIGNS["classic"])
    recipient = "For me" if preview.get("recipient_kind") == "self" else "For someone else"
    amount = money(int(preview.get("amount_minor", 0)), "USD")
    body = f"""
    <main id="main" class="specialty-main specialty-result-main">
      <nav class="specialty-breadcrumb"><a href="{GIFT_CARDS_PATH}">Gift Cards</a><span>›</span><strong>Local preview</strong></nav>
      {_simulation_notice("This is a saved preview in this browser session. It is not in the cart and no payment or Gift Card value exists.")}
      <section class="specialty-result-card">
        <div class="specialty-gift-art specialty-design-{escape(design, quote=True)}" aria-label="{escape(design_label, quote=True)}"><span>amazon</span><strong>{amount}</strong><i></i></div>
        <div><p>Local Gift Card preview #{int(preview['preview_id'])}</p><h1>{escape(design_label)}</h1><dl><dt>Amount</dt><dd>{amount}</dd><dt>Recipient</dt><dd>{escape(recipient)}</dd><dt>Status</dt><dd>Preview only — not purchased</dd></dl><div class="specialty-inline-actions"><a class="button primary" href="{GIFT_CARDS_PATH}#gift-card-builder">Change selection</a><a class="button secondary" href="{GIFT_CARD_BALANCE_PATH}">View simulated balance</a></div></div>
      </section>
    </main>
    """
    return layout(
        "Gift Card local preview",
        body,
        cart_count,
        body_class="specialty-page specialty-result-page",
        account_name=account_name,
    )


def gift_card_balance_page(
    balance: dict[str, int],
    cart_count: int,
    account_name: str | None,
    *,
    redemption_result: bool = False,
) -> str:
    result = (
        '<div class="specialty-generic-result" role="status"><strong>No balance was applied.</strong><span>This local simulation returns the same result for every well-formed fictional code.</span></div>'
        if redemption_result
        else ""
    )
    body = f"""
    <main id="main" class="specialty-main specialty-balance-main">
      <nav class="specialty-breadcrumb"><a href="{GIFT_CARDS_PATH}">Gift Cards</a><span>›</span><strong>Balance &amp; redemption</strong></nav>
      <section class="specialty-balance-grid">
        <div class="specialty-balance-card"><p>Local simulated balance</p><h1>{money(int(balance.get('balance_minor', 0)), 'USD')}</h1><span>No real Gift Card account is connected.</span><a href="{GIFT_CARDS_PATH}#gift-card-builder">Build a purchase preview</a></div>
        <div class="specialty-panel"><h2>Redeem a fictional test code</h2><p>Do not enter a real Amazon Gift Card claim code. Raw input is never stored; only an irreversible fingerprint is retained.</p>{result}<form class="specialty-form" method="post" action="{GIFT_CARD_REDEEM_PATH}"><label for="claim-code">Fictional claim code</label><input id="claim-code" name="claimCode" autocomplete="off" minlength="8" maxlength="32" pattern="[A-Za-z0-9-]{{8,32}}" required><button class="button primary" type="submit">Try local redemption</button></form><small>Local attempts in this browser session: {int(balance.get('redemption_attempts', 0))}</small></div>
      </section>
      {_simulation_notice("Redemption never contacts Amazon and never applies monetary value. Public responses do not reveal whether any real-looking code exists.")}
    </main>
    """
    return layout(
        "Gift Card balance and redemption",
        body,
        cart_count,
        body_class="specialty-page specialty-balance-page",
        account_name=account_name,
    )


def sell_page(
    drafts: Iterable[dict[str, Any]], cart_count: int, account_name: str | None
) -> str:
    category_options = "".join(
        f'<option value="{escape(key, quote=True)}">{escape(label)}</option>'
        for key, label in SELL_CATEGORIES.items()
    )
    condition_options = "".join(
        f'<option value="{escape(key, quote=True)}">{escape(label)}</option>'
        for key, label in SELL_CONDITIONS.items()
    )
    draft_cards = "".join(
        f'<a class="specialty-draft-row" href="{SELL_DRAFT_PATH}?{urlencode({"draftID": int(draft["draft_id"])})}"><span><strong>{escape(str(draft["title"]))}</strong><small>{escape(SELL_CATEGORIES.get(str(draft["category"]), str(draft["category"])))}</small></span><b>{money(int(draft["price_minor"]), "USD")}</b></a>'
        for draft in drafts
    ) or '<p class="specialty-empty">No listing drafts saved in this browser session yet.</p>'
    body = f"""
    <main id="main" class="specialty-main specialty-sell-main" data-navigation-page="sell">
      <section class="specialty-sell-hero"><div><p>Sell on Amazon</p><h1>Create an Amazon selling account</h1><span>This clone cannot create a real seller account. You can save a server-validated listing draft locally to understand the flow.</span><div class="specialty-inline-actions"><a class="button primary" href="#listing-draft">Start a local draft</a><a class="button secondary" href="/ap/register">Create an account</a></div></div><div class="specialty-sell-visual" aria-hidden="true"><span>1</span><span>2</span><span>3</span></div></section>
      {_simulation_notice("Nothing saved here is published, searchable by other shoppers, or sent to Amazon. No seller identity, tax, bank, or payout data is collected.")}
      <section class="specialty-question-grid"><article><span>1</span><h2>What will you sell?</h2><p>Choose a supported local catalog category and write a clear title.</p></article><article><span>2</span><h2>How will you offer it?</h2><p>Set condition, price, and quantity. The server validates every field.</p></article><article><span>3</span><h2>Ready to publish?</h2><p>Review the local result. Publishing and fulfillment stay intentionally unavailable.</p></article></section>
      <section id="listing-draft" class="specialty-panel specialty-sell-form-panel"><div><p>Getting started</p><h2>Save a local listing draft</h2><p>Estimate gross listing value from price × quantity without implying fees, revenue, or real sales.</p></div><form method="post" action="{SELL_DRAFT_PATH}" class="specialty-form specialty-two-column-form"><label>Product title<input name="title" minlength="5" maxlength="100" required></label><label>Category<select name="category" required>{category_options}</select></label><label>Condition<select name="condition" required>{condition_options}</select></label><label>Price in USD<input name="price" inputmode="decimal" placeholder="29.99" pattern="(?:[1-9][0-9]{{0,3}}|5000)(?:\\.[0-9]{{2}})?" required></label><label>Quantity<input name="quantity" type="number" min="1" max="30" value="1" required></label><label class="specialty-field-wide">Description<textarea name="description" maxlength="500" rows="4"></textarea></label><button class="button primary specialty-field-wide" type="submit">Save local draft</button></form></section>
      <section class="specialty-rail-section"><div class="specialty-section-heading"><p>Your browser session</p><h2>Saved listing drafts</h2></div><div class="specialty-draft-list">{draft_cards}</div></section>
      <section class="specialty-benefit-grid"><article><h2>Why model selling?</h2><p>It connects catalog information, inventory choices, validation, and a review step.</p></article><article><h2>Local incentives</h2><p>There are no promotional credits or seller benefits in this clone.</p></article><article><h2>Next step</h2><p>Use the saved result to review data before a hypothetical publish action.</p></article></section>
    </main>
    """
    return layout(
        "Sell on Amazon - local draft",
        body,
        cart_count,
        body_class="specialty-page specialty-sell-page",
        account_name=account_name,
    )


def seller_draft_page(
    draft: dict[str, Any], cart_count: int, account_name: str | None
) -> str:
    gross_minor = int(draft["price_minor"]) * int(draft["quantity"])
    body = f"""
    <main id="main" class="specialty-main specialty-result-main"><nav class="specialty-breadcrumb"><a href="{SELL_PATH}">Sell on Amazon</a><span>›</span><strong>Listing draft</strong></nav>{_simulation_notice("This draft belongs only to this browser session and is not a public Amazon listing.")}<section class="specialty-result-card specialty-listing-result"><div class="specialty-listing-placeholder" aria-hidden="true">DRAFT</div><div><p>Local listing draft #{int(draft['draft_id'])}</p><h1>{escape(str(draft['title']))}</h1><dl><dt>Category</dt><dd>{escape(SELL_CATEGORIES.get(str(draft['category']), str(draft['category'])))}</dd><dt>Condition</dt><dd>{escape(SELL_CONDITIONS.get(str(draft['item_condition']), str(draft['item_condition'])))}</dd><dt>Unit price</dt><dd>{money(int(draft['price_minor']), 'USD')}</dd><dt>Quantity</dt><dd>{int(draft['quantity'])}</dd><dt>Gross listing value</dt><dd>{money(gross_minor, 'USD')} before any hypothetical fees</dd><dt>Status</dt><dd>Local draft — not published</dd></dl>{f'<p>{escape(str(draft["description"]))}</p>' if draft.get('description') else ''}<a class="button primary" href="{SELL_PATH}#listing-draft">Create another draft</a></div></section></main>
    """
    return layout(
        "Local seller listing draft",
        body,
        cart_count,
        body_class="specialty-page specialty-result-page",
        account_name=account_name,
    )


def registry_page(
    own_registries: Iterable[dict[str, Any]], cart_count: int, account_name: str | None
) -> str:
    type_cards = "".join(
        f'<a class="specialty-registry-type" href="#create-registry"><span aria-hidden="true">{icon}</span><strong>{escape(label)}</strong><small>{escape(copy)}</small></a>'
        for (key, label), icon, copy in zip(
            REGISTRY_TYPES.items(),
            ("💍", "🧸", "🎁"),
            ("Plan shared gift ideas", "Prepare for a new arrival", "Collect ideas for any occasion"),
        )
    )
    type_options = "".join(
        f'<option value="{escape(key, quote=True)}">{escape(label)}</option>'
        for key, label in REGISTRY_TYPES.items()
    )
    own_cards = "".join(
        f'<a class="specialty-draft-row" href="{REGISTRY_DETAIL_PATH}?{urlencode({"registryID": int(item["registry_id"])})}"><span><strong>{escape(str(item["registry_name"]))}</strong><small>{escape(REGISTRY_TYPES.get(str(item["registry_type"]), str(item["registry_type"])))}</small></span><b>Private local draft</b></a>'
        for item in own_registries
    ) or '<p class="specialty-empty">No registry drafts saved in this browser session.</p>'
    body = f"""
    <main id="main" class="specialty-main specialty-registry-main" data-navigation-page="registry">
      <section class="specialty-registry-hero"><div><p>Registry &amp; Gift List</p><h1>Find a registry or create your own</h1><span>Search local demo registries and your own private browser-session drafts.</span></div><form method="get" action="{REGISTRY_SEARCH_PATH}" class="specialty-search-form" role="search"><label for="registry-search">Find a registry</label><div><input id="registry-search" name="query" minlength="2" maxlength="60" placeholder="Name or registry title" required><button class="button primary" type="submit">Search</button></div><a href="#create-registry">Create a registry</a></form></section>
      {_simulation_notice("Registry drafts are private to this browser session. They are not published to Amazon and are never exposed in another visitor's search results.")}
      <section class="specialty-rail-section"><div class="specialty-section-heading"><p>Choose the list that fits</p><h2>Baby Registry, Wedding Registry, and Gift List</h2></div><div class="specialty-registry-types">{type_cards}</div></section>
      <section id="create-registry" class="specialty-panel specialty-registry-form-panel"><div><p>Create and personalize</p><h2>Start a private local registry draft</h2><p>After saving, build the idea list by browsing Books, Home, Toys, Beauty, or Electronics.</p></div><form method="post" action="{REGISTRY_CREATE_PATH}" class="specialty-form specialty-two-column-form"><label>Registry type<select name="registryType" required>{type_options}</select></label><label>Your display name<input name="ownerName" minlength="2" maxlength="80" required></label><label>Registry name<input name="registryName" minlength="3" maxlength="100" required></label><label>Event date (optional)<input name="eventDate" type="date" min="2000-01-01" max="2100-12-31"></label><button class="button primary specialty-field-wide" type="submit">Create private local draft</button></form></section>
      <section class="specialty-rail-section"><div class="specialty-section-heading"><p>Reasons and benefits</p><h2>One place to organize gift ideas</h2></div><div class="specialty-benefit-grid"><article><h3>Personalize</h3><p>Name the event and choose the kind of registry.</p></article><article><h3>Build</h3><p>Use the live catalog links on the saved detail page.</p></article><article><h3>Keep it private</h3><p>Only the creating browser session can open the draft.</p></article></div><div class="specialty-draft-list">{own_cards}</div></section>
    </main>
    """
    return layout(
        "Amazon Registry & Gift List",
        body,
        cart_count,
        body_class="specialty-page specialty-registry-page",
        account_name=account_name,
    )


def registry_search_page(
    query: str,
    results: Iterable[dict[str, Any]],
    cart_count: int,
    account_name: str | None,
) -> str:
    cards: list[str] = []
    for item in results:
        label = REGISTRY_TYPES.get(str(item.get("registry_type")), "Gift List")
        if item.get("is_own"):
            href = REGISTRY_DETAIL_PATH + "?" + urlencode(
                {"registryID": int(item["registry_id"])}
            )
            action = f'<a href="{href}">Open your private draft</a>'
        else:
            action = "<span>Local demo result — no personal data</span>"
        cards.append(
            f'<article class="specialty-search-result"><p>{escape(label)}</p><h2>{escape(str(item["registry_name"]))}</h2><small>{escape(str(item["owner_name"]))}</small>{action}</article>'
        )
    result_markup = "".join(cards) or '<p class="specialty-empty">No local demo or private draft matched that search.</p>'
    body = f"""
    <main id="main" class="specialty-main specialty-search-main"><nav class="specialty-breadcrumb"><a href="{REGISTRY_PATH}">Registry</a><span>›</span><strong>Search</strong></nav><section class="specialty-panel"><form method="get" action="{REGISTRY_SEARCH_PATH}" class="specialty-search-form"><label for="registry-search-results">Find a registry</label><div><input id="registry-search-results" name="query" value="{escape(query, quote=True)}" minlength="2" maxlength="60" required><button class="button primary" type="submit">Search</button></div></form><h1>Results for “{escape(query)}”</h1><div class="specialty-search-results">{result_markup}</div></section>{_simulation_notice("Only built-in demo entries and registries owned by this browser session are searchable here.")}</main>
    """
    return layout(
        "Find a local registry",
        body,
        cart_count,
        body_class="specialty-page specialty-registry-page",
        account_name=account_name,
    )


def registry_detail_page(
    item: dict[str, Any], cart_count: int, account_name: str | None
) -> str:
    date_copy = escape(str(item.get("event_date") or "No event date selected"))
    body = f"""
    <main id="main" class="specialty-main specialty-result-main"><nav class="specialty-breadcrumb"><a href="{REGISTRY_PATH}">Registry</a><span>›</span><strong>Private draft</strong></nav>{_simulation_notice("This registry exists only in the creating browser session and is not published or shared.")}<section class="specialty-result-card specialty-registry-result"><div class="specialty-registry-emblem" aria-hidden="true">🎁</div><div><p>{escape(REGISTRY_TYPES.get(str(item['registry_type']), 'Gift List'))}</p><h1>{escape(str(item['registry_name']))}</h1><dl><dt>Owner</dt><dd>{escape(str(item['owner_name']))}</dd><dt>Event date</dt><dd>{date_copy}</dd><dt>Status</dt><dd>Private local draft</dd></dl><h2>Start building your list</h2><div class="specialty-inline-actions"><a href="/s?k=books">Books</a><a href="/s?k=home+kitchen">Home</a><a href="/s?k=toys">Toys</a><a href="/s?k=beauty+personal+care">Beauty</a></div></div></section></main>
    """
    return layout(
        "Private local registry draft",
        body,
        cart_count,
        body_class="specialty-page specialty-result-page",
        account_name=account_name,
    )


def prime_video_page(cart_count: int, account_name: str | None) -> str:
    body = f"""
    <main id="main" class="specialty-video-main" data-navigation-page="prime-video">
      <nav class="specialty-video-nav" aria-label="Prime Video local placeholder"><a class="specialty-video-brand" href="{PRIME_VIDEO_PATH}">prime video <small>local</small></a><a aria-current="page" href="{PRIME_VIDEO_PATH}">Home</a></nav>
      <section class="specialty-video-placeholder"><div><p>PRIME VIDEO</p><h1>Video service is outside this shopping clone</h1><span>This reachable placeholder does not model a catalog, subscription, stream, rental, purchase, watchlist, or viewing history.</span><div class="specialty-inline-actions"><a class="button primary" href="/">Continue shopping</a><a class="button secondary" href="/gp/help/customer/display.html?nodeId=508510">Customer Service</a></div></div></section>
      <aside class="specialty-video-notice"><strong>No streaming service connected</strong><span>Nothing on this page represents real Prime Video availability or entitlement.</span></aside>
    </main>
    """
    return layout(
        "Prime Video - local placeholder",
        body,
        cart_count,
        body_class="specialty-video-page",
        account_name=account_name,
    )
