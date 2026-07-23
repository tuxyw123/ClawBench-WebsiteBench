from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import signal
import sys
import threading
from dataclasses import replace
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit

import auth_views
import render as views
import specialty_store as specialty
import specialty_views
import wishlist_store as wishlist
import wishlist_views
from browse_breadth import load_browse_breadth
from deals_catalog import build_deals_view, load_deals_catalog
from home_catalog import load_home_product_catalog
from mail_transport import (
    SMTPConfig,
    load_local_inbox_url,
    load_smtp_config,
    send_smtp_message,
    smtp_error_summary,
    smtp_public_summary,
)
from product_options import UNAVAILABLE_SELECTION_COPY
from review_catalog import render_reviews_section
from review_store import (
    ReviewAuthenticationRequired,
    ReviewNotFound,
    ReviewPermissionDenied,
    ReviewValidationError,
)
from search_catalog import (
    SearchRequest,
    SearchValidationError,
    build_search_hit,
    candidate_search_hits,
    is_portable_ssd_contract_query,
    parse_search_request,
    refine_search_hits,
)
from search_suggestions import (
    SearchSuggestionValidationError,
    build_suggestion_corpus,
    parse_suggestion_request,
    suggest_search_terms,
)
from store import (
    AddressInUse,
    AddressNotFound,
    AddressRevisionConflict,
    BEST_SELLERS_PATH,
    CART_LINE_ID_PATTERN,
    COMPARE_LINE_ID_PATTERN,
    MAIL_LOCAL_ONLY,
    MAIL_SMTP_PENDING,
    PDP_PATH,
    SUPPORTED_DELIVERY_COUNTRY_CODES,
    TARGET_ASIN,
    TERMINAL_PATHS,
    CheckoutReconciliationRequired,
    ContractError,
    OrderActionTokenInvalid,
    OrderNotFound,
    OrderStateConflict,
    ReturnNotFound,
    Store,
    normalize_email,
)


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = (ROOT / "static").resolve()
FIXTURE_ROOT = (ROOT / "fixtures").resolve()
SCHEMA_PATH = ROOT / "schema.sql"
DEFAULT_DB = ROOT / "runtime" / "amazon.sqlite3"
SESSION_COOKIE = "amazon_clone_session"
FLOW_COOKIE = "amazon_clone_flow"
SESSION_BYTES = 32
TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
# A 10,000-character review body is a supported domain value.  URL-encoded
# non-ASCII text can use up to twelve bytes per Unicode scalar (four UTF-8
# bytes, each percent-encoded), so keep the transport limit above that bound.
MAX_FORM_BYTES = 128 * 1024
MAX_ADMIN_BYTES = 64 * 1024
PDP_ROUTE = re.compile(r"^/([^/]+)/dp/([A-Z0-9]{10})$")
BARE_PDP_ROUTE = re.compile(r"^/dp/([A-Z0-9]{10})$")
PRODUCT_REVIEWS_ROUTE = re.compile(r"^/product-reviews/([A-Z0-9]{10})$")
PRODUCT_REVIEW_HELPFUL_ROUTE = re.compile(
    r"^/product-reviews/([A-Z0-9]{10})/helpful$"
)
HOME_PRODUCT_CATALOG = load_home_product_catalog(FIXTURE_ROOT)
SEARCH_SUGGESTION_CORPUS = build_suggestion_corpus(HOME_PRODUCT_CATALOG)
BROWSE_BREADTH = load_browse_breadth(FIXTURE_ROOT)
SEARCH_DEPARTMENT_SUPPLEMENTS = (
    *BROWSE_BREADTH["department_commerce_supplements"],
    *BROWSE_BREADTH["search_commerce_cards"],
)
SEARCH_COMMERCE_PRODUCT_CATALOG = {
    str(product["asin"]): product
    for product in BROWSE_BREADTH["search_commerce_cards"]
}
PORTABLE_SEARCH_ORDER = (
    "B08HN37XC1",
    "B08GTYFC37",
    "B0F6NKYDTY",
    "B0BGKXX9TK",
    "B0874XN4D8",
    "B0C5JQ68FY",
    "B08GV9M64L",
    "B09VLK9W3S",
    "B0CHFSWM2P",
)
DEALS_CATALOG = load_deals_catalog(
    FIXTURE_ROOT, BROWSE_BREADTH["verified_offers"]
)
DEALS_PRODUCT_CATALOG = {
    str(product["asin"]): product for product in DEALS_CATALOG
}
AUTH_PATHS = frozenset({"/ap/signin", "/ap/register", "/ap/forgotpassword", "/ap/cvf/verify"})
AUTH_SIGNOUT_PATH = "/ap/signout"
ADDRESS_BOOK_PATH = "/a/addresses"
ADDRESS_ADD_PATH = "/a/addresses/add"
ADDRESS_EDIT_PATH = "/a/addresses/edit"
ADDRESS_CREATE_PATH = "/a/addresses/create"
ADDRESS_UPDATE_PATH = "/a/addresses/update"
ADDRESS_DELETE_PATH = "/a/addresses/delete"
ADDRESS_DEFAULT_PATH = "/a/addresses/set-default"
ADDRESS_BOOK_POST_PATHS = frozenset(
    {
        ADDRESS_CREATE_PATH,
        ADDRESS_UPDATE_PATH,
        ADDRESS_DELETE_PATH,
        ADDRESS_DEFAULT_PATH,
    }
)
ADDRESS_FORM_NAMES = frozenset(
    {
        "fullName",
        "addressLine1",
        "addressLine2",
        "city",
        "state",
        "postalCode",
        "countryCode",
        "phoneNumber",
    }
)
CART_MUTATION_PATHS = frozenset(
    {
        "/gp/buy/now",
        "/gp/cart/add.html",
        "/gp/cart/update.html",
        "/gp/cart/delete.html",
        "/gp/cart/save-for-later.html",
        "/gp/cart/move-to-cart.html",
    }
)
BUY_NOW_PATH = "/gp/buy/now"
BUY_NOW_CONTINUE_PATH = "/gp/buy/now/continue"
CHECKOUT_START_PATH = "/gp/buy/spc/handlers/display.html"
CHECKOUT_ADDRESS_PATH = "/gp/buy/addressselect/handlers/display.html"
CHECKOUT_DELIVERY_PATH = "/gp/buy/shipoptionselect/handlers/display.html"
CHECKOUT_PAYMENT_PATH = "/gp/buy/payselect/handlers/display.html"
PLACE_ORDER_PATH = "/gp/buy/place-order"
ORDER_DETAIL_PATH = "/gp/your-account/order-details"
ORDER_EMAIL_RETRY_PATH = "/gp/your-account/order-email/retry"
ORDER_CANCEL_PATH = "/gp/your-account/order-cancel"
RETURN_CREATE_PATH = "/gp/your-account/returns/create"
RETURN_DETAIL_PATH = "/gp/your-account/returns/details"
COMPARE_PATH = "/gp/compare"
COMPARE_MUTATION_PATHS = frozenset(
    {"/gp/compare/add", "/gp/compare/remove", "/gp/compare/clear"}
)
CHECKOUT_STAGE_ORDER = {
    "CART_READY": 0,
    "ADDRESS_SELECTED": 1,
    "DELIVERY_SELECTED": 2,
    "PAYMENT_SELECTED": 3,
    "PLACED": 4,
}
ASIN_PATTERN = re.compile(r"^[A-Z0-9]{10}$")
PROTECTED_ACCOUNT_PATHS = frozenset(
    {"/gp/css/homepage.html", "/gp/css/order-history"}
)
WISHLIST_POST_PATHS = frozenset(
    {
        wishlist_views.WISHLIST_CREATE_PATH,
        wishlist_views.WISHLIST_RENAME_PATH,
        wishlist_views.WISHLIST_DELETE_PATH,
        wishlist_views.WISHLIST_ADD_ITEM_PATH,
        wishlist_views.WISHLIST_REMOVE_ITEM_PATH,
        wishlist_views.WISHLIST_MOVE_TO_CART_PATH,
    }
)
WISHLIST_FRIENDS_PATH = "/hz/wishlist/your-friends"
SITE_DIRECTORY_PATH = "/gp/site-directory"
DELIVERY_PREFERENCE_PATH = "/gp/delivery/ajax/address-change.html"
SEARCH_SUGGESTIONS_PATH = "/search/suggestions"
LANGUAGE_PREFERENCE_PATH = "/customer-preferences/edit"
CUSTOMER_SERVICE_PATH = "/gp/help/customer/display.html"
CUSTOMER_SERVICE_NODE = "508510"
SHIPPING_POLICIES_NODE = "468520"
RETURNS_REPLACEMENTS_NODE = "201819200"
GIFT_CARDS_PATH = specialty.GIFT_CARDS_PATH
SPECIALTY_POST_PATHS = frozenset(
    {
        specialty.GIFT_CARD_PREVIEW_PATH,
        specialty.GIFT_CARD_REDEEM_PATH,
        specialty.SELL_DRAFT_PATH,
        specialty.REGISTRY_CREATE_PATH,
    }
)


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().casefold() in TRUE_ENV_VALUES


def browser_cookie(name: str, value: str, *, max_age: int | None = None) -> str:
    attributes = [f"{name}={value}", "Path=/", "HttpOnly", "SameSite=Lax"]
    if env_flag("AMAZON_COOKIE_SECURE"):
        attributes.append("Secure")
    if max_age is not None:
        attributes.append(f"Max-Age={max_age}")
    return "; ".join(attributes)


def public_basic_auth_credentials() -> tuple[str, str] | None:
    username = os.environ.get("AMAZON_BASIC_AUTH_USERNAME", "")
    password = os.environ.get("AMAZON_BASIC_AUTH_PASSWORD", "")
    if not username and not password:
        return None
    if not username or not password:
        raise ValueError(
            "AMAZON_BASIC_AUTH_USERNAME and AMAZON_BASIC_AUTH_PASSWORD "
            "must be configured together"
        )
    return username, password
PUBLIC_NAVIGATION_LANDINGS: dict[
    str, tuple[str, str, tuple[tuple[str, str], ...]]
] = {
    LANGUAGE_PREFERENCE_PATH: (
        "Language preferences",
        "This local snapshot is available in English (United States). Continue browsing or visit customer service.",
        (
            ("Continue in English", "/"),
            ("Browse books", "/s?k=books"),
            ("Visit Customer Service", "/gp/help/customer/display.html?nodeId=508510"),
        ),
    ),
    DELIVERY_PREFERENCE_PATH: (
        "Choose your delivery location",
        "The current storefront is showing products for delivery to Singapore. A checkout address can be selected when placing an order.",
        (
            ("Browse Singapore picks", "/s?k=top+picks+singapore"),
            ("View your cart", "/gp/cart/view.html"),
            ("Visit Customer Service", "/gp/help/customer/display.html?nodeId=508510"),
        ),
    ),
}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compare_entry_product(
    store: Store,
    product: dict[str, Any] | Mapping[str, Any],
    *,
    eligible_asins: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Annotate a rendered product from the server-owned compare registry."""

    rendered = dict(product)
    asin = str(rendered.get("asin") or "")
    eligible = asin in (
        eligible_asins
        if eligible_asins is not None
        else store.compare_eligible_asins()
    )
    rendered["compare_eligible"] = eligible
    if eligible:
        rendered["default_selected_options"] = store.default_product_options(asin)
    return rendered


def product_for_pdp(store: Store, asin: str) -> dict[str, Any] | None:
    """Prefer newer direct PDP evidence without changing frozen search records."""

    home_product = HOME_PRODUCT_CATALOG.get(asin)
    product: dict[str, Any] | None = None
    if home_product is not None and home_product.get("evidence_tier") == "pdp-direct":
        product = dict(home_product)
    else:
        task_product = store.product(asin)
        if task_product is not None:
            product = task_product
        else:
            commerce_offer = store.commerce_offer(asin)
            if (
                commerce_offer is not None
                and commerce_offer.get("evidence_class") == "direct-deals-card"
            ):
                deals_product = DEALS_PRODUCT_CATALOG.get(asin)
                if deals_product is not None:
                    # Transaction identity comes from commerce_offers; card-only
                    # presentation facts retain their narrower Deals evidence fields.
                    product = {**commerce_offer, **dict(deals_product)}
            elif (
                commerce_offer is not None
                and commerce_offer.get("evidence_class") == "direct-search-card"
            ):
                search_product = SEARCH_COMMERCE_PRODUCT_CATALOG.get(asin)
                if search_product is not None:
                    product = {**commerce_offer, **dict(search_product)}
            elif home_product is not None:
                product = dict(home_product)
    return compare_entry_product(store, product) if product is not None else None


def compare_product_for_view(
    store: Store, compare_item: dict[str, Any]
) -> dict[str, Any]:
    """Keep rich presentation while making the resolved line quote authoritative."""

    asin = str(compare_item.get("asin") or "")
    presentation = product_for_pdp(store, asin) or {}
    return {**presentation, **compare_item, "compare_eligible": True}


def review_product_source_scope(store: Store, asin: str) -> str | None:
    """Return the server-owned product scope that authorizes local reviews."""

    if asin in HOME_PRODUCT_CATALOG:
        return "home_snapshot"
    if store.product(asin) is not None:
        return "catalog_product"
    if store.commerce_offer(asin) is not None:
        return "commerce_offer"
    return None


def valid_account_email(value: str) -> str | None:
    normalized = normalize_email(value)
    if not normalized or len(normalized) > 254 or normalized.count("@") != 1:
        return None
    local, domain = normalized.split("@", 1)
    if not local or not domain or any(character.isspace() or ord(character) < 32 for character in normalized):
        return None
    return normalized


def clean_display_name(value: str) -> str | None:
    cleaned = " ".join(value.strip().split())
    if not cleaned or len(cleaned) > 128 or any(ord(character) < 32 for character in cleaned):
        return None
    return cleaned


def clean_checkout_text(
    value: str, *, maximum: int, required: bool = True
) -> str | None:
    cleaned = " ".join(value.strip().split())
    if required and not cleaned:
        return None
    if len(cleaned) > maximum or any(ord(character) < 32 for character in cleaned):
        return None
    return cleaned


def normalized_address_fields(fields: dict[str, str]) -> dict[str, str] | None:
    """Map one strict browser address form to the store's canonical fields."""

    if not ADDRESS_FORM_NAMES.issubset(fields):
        return None
    address = {
        "full_name": clean_checkout_text(fields["fullName"], maximum=128),
        "address_line1": clean_checkout_text(
            fields["addressLine1"], maximum=200
        ),
        "address_line2": clean_checkout_text(
            fields["addressLine2"], maximum=200, required=False
        ),
        "city": clean_checkout_text(fields["city"], maximum=100),
        "state_region": clean_checkout_text(fields["state"], maximum=100),
        "postal_code": clean_checkout_text(fields["postalCode"], maximum=32),
        "country_code": fields["countryCode"].strip().upper(),
        "phone": clean_checkout_text(
            fields["phoneNumber"], maximum=32, required=False
        ),
    }
    if (
        any(value is None for value in address.values())
        or str(address["country_code"]) not in SUPPORTED_DELIVERY_COUNTRY_CODES
    ):
        return None
    return {name: str(value) for name, value in address.items()}


def review_query_parameters(query: dict[str, list[str]]) -> tuple[int | None, str]:
    """Parse the two public review controls without accepting ambiguous values."""

    star_values = query.get("reviewStar", [])
    sort_values = query.get("reviewSort", [])
    if len(star_values) > 1 or len(sort_values) > 1:
        raise ReviewValidationError("review filters must have one value")
    star: int | None = None
    if star_values:
        if re.fullmatch(r"[1-5]", star_values[0]) is None:
            raise ReviewValidationError("reviewStar must be 1 through 5")
        star = int(star_values[0])
    sort = sort_values[0] if sort_values else "recent"
    if sort not in {"recent", "helpful"}:
        raise ReviewValidationError("reviewSort must be recent or helpful")
    return star, sort


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Do not log tracebacks for ordinary clients closing keep-alive sockets."""

        error = sys.exc_info()[1]
        if isinstance(
            error, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)
        ):
            return
        super().handle_error(request, client_address)


def dispatch_mail_delivery(
    store: Store,
    config: SMTPConfig,
    delivery: dict[str, Any],
) -> bool:
    """Atomically claim and asynchronously deliver one durable SMTP job."""

    kind = str(delivery["kind"])
    email_id = int(delivery["email_id"])
    claim_token = store.claim_mail_delivery(kind, email_id)
    if claim_token is None:
        return False

    def deliver() -> None:
        try:
            send_smtp_message(
                config,
                recipient=str(delivery["recipient"]),
                subject=str(delivery["subject"]),
                body=str(delivery["body"]),
            )
        except Exception as exc:
            store.mark_mail_delivery(
                kind,
                email_id,
                claim_token=claim_token,
                sent=False,
                error_summary=smtp_error_summary(exc),
            )
        else:
            store.mark_mail_delivery(
                kind,
                email_id,
                claim_token=claim_token,
                sent=True,
            )

    worker = threading.Thread(
        target=deliver,
        name=f"amazon-clone-mail-{kind}-{email_id}",
        daemon=True,
    )
    try:
        worker.start()
    except RuntimeError as exc:
        store.mark_mail_delivery(
            kind,
            email_id,
            claim_token=claim_token,
            sent=False,
            error_summary=smtp_error_summary(exc),
        )
        return False
    return True


def wishlist_selection_query(
    query: dict[str, list[str]],
) -> tuple[str, dict[str, str]] | None:
    """Parse one strict, duplicate-free ASIN plus complete option selection."""

    if not query or len(query) > 16 or "ASIN" not in query:
        return None
    fields: dict[str, str] = {}
    for name, values in query.items():
        if len(values) != 1:
            return None
        if name != "ASIN" and (
            not name.startswith("option.") or not name.removeprefix("option.")
        ):
            return None
        fields[name] = values[0]
    asin = fields.pop("ASIN", "")
    if ASIN_PATTERN.fullmatch(asin) is None:
        return None
    return asin, {
        name.removeprefix("option."): value
        for name, value in fields.items()
    }


class PublicHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    store: Store
    smtp_config: SMTPConfig | None = None
    local_inbox_url: str | None = None
    server_version = "AmazonClone/0.1"

    def log_message(self, format_string: str, *args: Any) -> None:
        safe_path = self.path
        if urlsplit(self.path).path.startswith("/ap/"):
            safe_path = urlsplit(self.path).path
        message = format_string % args
        if safe_path != self.path:
            message = message.replace(self.path, safe_path)
        sys.stdout.write(
            json.dumps(
                {
                    "stream": "public",
                    "client": self.client_address[0],
                    "method": self.command,
                    "path": safe_path,
                    "message": message,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        sys.stdout.flush()

    def _security_headers(self) -> dict[str, str]:
        headers = {
            "Content-Security-Policy": (
                "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                "script-src 'self'; connect-src 'self'; form-action 'self'; "
                "frame-ancestors 'none'; base-uri 'self'"
            ),
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Cache-Control": "no-store",
        }
        if env_flag("AMAZON_HSTS"):
            headers["Strict-Transport-Security"] = "max-age=31536000"
        if env_flag("AMAZON_NOINDEX"):
            headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return headers

    def _public_basic_auth_is_valid(self) -> bool:
        try:
            expected = public_basic_auth_credentials()
        except ValueError:
            return False
        if expected is None:
            return True
        header = self.headers.get("Authorization", "")
        scheme, separator, encoded = header.partition(" ")
        if not separator or scheme.casefold() != "basic":
            return False
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except binascii.Error:
            return False
        username, separator, password = decoded.partition(b":")
        if not separator:
            return False
        expected_username, expected_password = expected
        return hmac.compare_digest(
            username, expected_username.encode("utf-8")
        ) and hmac.compare_digest(
            password, expected_password.encode("utf-8")
        )

    def _require_public_basic_auth(self) -> bool:
        if self._public_basic_auth_is_valid():
            return True
        self.close_connection = True
        self._send(
            401,
            b"Authentication required.",
            content_type="text/plain; charset=utf-8",
            headers={
                "Connection": "close",
                "WWW-Authenticate": 'Basic realm="WebsiteBench Amazon", charset="UTF-8"',
            },
        )
        return False

    def _send(
        self,
        status: int,
        body: bytes = b"",
        *,
        content_type: str = "text/html; charset=utf-8",
        headers: dict[str, str] | None = None,
        cookies: list[str] | None = None,
    ) -> None:
        self.send_response(status)
        all_headers = self._security_headers()
        if headers:
            all_headers.update(headers)
        all_headers["Content-Type"] = content_type
        all_headers["Content-Length"] = str(len(body))
        for key, value in all_headers.items():
            self.send_header(key, value)
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD" and body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                # Browsers routinely cancel in-flight static responses when a
                # tab navigates or closes.  The response is already committed,
                # so treat that peer disconnect as a normal end of connection
                # instead of letting socketserver print a misleading traceback.
                self.close_connection = True

    def _send_html(
        self,
        status: int,
        html: str,
        *,
        cookies: list[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._send(status, html.encode("utf-8"), cookies=cookies, headers=headers)

    def _request_cookies(self) -> SimpleCookie[str]:
        jar: SimpleCookie[str] = SimpleCookie()
        raw = self.headers.get("Cookie", "")
        if raw:
            try:
                jar.load(raw)
            except Exception:
                pass
        return jar

    def _session(self) -> tuple[str, str, list[str]]:
        jar = self._request_cookies()
        morsel = jar.get(SESSION_COOKIE)
        token = morsel.value if morsel and len(morsel.value) >= 32 else secrets.token_urlsafe(SESSION_BYTES)
        session_digest = digest(token)
        self.store.ensure_session(session_digest)
        cookies: list[str] = []
        if morsel is None or morsel.value != token:
            cookies.append(browser_cookie(SESSION_COOKIE, token))
        return token, session_digest, cookies

    def _rotate_authenticated_session(
        self, session_digest: str, cookies: list[str]
    ) -> str:
        token = secrets.token_urlsafe(SESSION_BYTES)
        new_session_digest = digest(token)
        self.store.rotate_authenticated_session(session_digest, new_session_digest)
        cookies[:] = [browser_cookie(SESSION_COOKIE, token)]
        return new_session_digest

    def _mail_mode(self) -> str:
        return MAIL_SMTP_PENDING if self.smtp_config is not None else MAIL_LOCAL_ONLY

    def _auth_mail_status(
        self, session_digest: str, purpose: str
    ) -> dict[str, Any] | None:
        if purpose == "password-reset":
            pending = self.store.pending_password_reset(session_digest)
            if pending is not None and pending.get("verified"):
                status = self.store.password_reset_mail_status(session_digest)
                if (
                    self.smtp_config is None
                    and status is not None
                    and status.get("status") in {"SMTP_PENDING", "SMTP_FAILED"}
                ):
                    return {
                        "status": MAIL_LOCAL_ONLY,
                        "delivery_attempts": status.get("delivery_attempts", 0),
                        "can_retry": False,
                    }
                return status
            # Before OTP verification, known and unknown identifiers expose
            # exactly the same neutral surface.  SMTP outcome and Retry are
            # intentionally unavailable until identity ownership is proven.
            return {
                "status": "QUEUED",
                "delivery_attempts": 0,
                "can_retry": False,
            }
        status = self.store.registration_mail_status(session_digest)
        if (
            self.smtp_config is None
            and status is not None
            and status.get("status") in {"SMTP_PENDING", "SMTP_FAILED"}
        ):
            return {
                "status": MAIL_LOCAL_ONLY,
                "delivery_attempts": status.get("delivery_attempts", 0),
                "can_retry": False,
            }
        return status

    def _public_order_mail_state(self, order: dict[str, Any]) -> dict[str, Any]:
        email = order.get("email")
        if (
            self.smtp_config is None
            and isinstance(email, dict)
            and email.get("status") in {"SMTP_PENDING", "SMTP_FAILED"}
        ):
            return {
                **order,
                "email": {
                    **email,
                    "status": MAIL_LOCAL_ONLY,
                    "is_simulation": True,
                    "can_retry": False,
                },
            }
        return order

    def _dispatch_mail(self, delivery: dict[str, Any] | None) -> None:
        """Attempt configured SMTP asynchronously so reset responses stay uniform."""

        config = self.smtp_config
        if delivery is None or config is None:
            return
        dispatch_mail_delivery(self.store, config, delivery)

    def _flow_digest(self) -> str:
        morsel = self._request_cookies().get(FLOW_COOKIE)
        return digest(morsel.value) if morsel else ""

    def _referer(self) -> str:
        return self.headers.get("Referer", "")

    def _same_origin_path(self, raw_url: str, expected_path: str) -> bool:
        parsed = urlsplit(raw_url)
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.netloc == self.headers.get("Host", "")
            and parsed.path == expected_path
        )

    def _terminal_source_is_canonical(self) -> bool:
        referer_ok = self._same_origin_path(self._referer(), PDP_PATH)
        origin = self.headers.get("Origin", "")
        if not origin:
            return referer_ok
        parsed = urlsplit(origin)
        return referer_ok and parsed.scheme in {"http", "https"} and parsed.netloc == self.headers.get("Host", "")

    def _form_fields(self, raw_body: bytes) -> dict[str, str] | None:
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/x-www-form-urlencoded":
            return None
        try:
            pairs = parse_qsl(
                raw_body.decode("utf-8"),
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=16,
            )
        except (UnicodeDecodeError, ValueError):
            return None
        fields: dict[str, str] = {}
        for name, value in pairs:
            if name in fields:
                return None
            fields[name] = value
        return fields

    def _auth_post_origin_is_safe(self) -> bool:
        origin = self.headers.get("Origin", "")
        candidate = origin or self.headers.get("Referer", "")
        if not candidate:
            return False
        parsed = urlsplit(candidate)
        return (
            parsed.scheme in {"http", "https"}
            and parsed.netloc == self.headers.get("Host", "")
        )

    def _serve_static(self, path: str) -> None:
        relative = unquote(path.removeprefix("/static/"))
        candidate = (STATIC_ROOT / relative).resolve()
        if STATIC_ROOT not in candidate.parents or not candidate.is_file():
            self._send(404, b"Not Found", content_type="text/plain; charset=utf-8")
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self._send(
            200,
            candidate.read_bytes(),
            content_type=content_type,
            headers={"Cache-Control": "no-cache"},
        )

    def _send_product_page(
        self,
        product: dict[str, Any],
        *,
        cart_count: int,
        flow_ready: bool,
        account_name: str | None,
        session_digest: str,
        query: dict[str, list[str]],
        path: str,
        cookies: list[str],
    ) -> None:
        product = {
            **compare_entry_product(self.store, product),
            "default_selected_options": self.store.default_product_options(
                str(product["asin"])
            ),
            "option_quote_matrix": self.store.product_option_quotes(
                str(product["asin"])
            ),
            "option_unavailable_copy": UNAVAILABLE_SELECTION_COPY,
        }
        try:
            review_star, review_sort = review_query_parameters(query)
        except ReviewValidationError as exc:
            self._send_html(
                400,
                views.error_page(str(exc), cart_count, 400),
                cookies=cookies,
            )
            return
        review_kwargs: dict[str, Any] = {
            "local_reviews": self.store.reviews_for_session(
                session_digest, product["asin"], sort=review_sort
            ),
            "review_star": review_star,
            "review_sort": review_sort,
            "review_base_path": path,
        }
        page = views.product_page(
            product,
            cart_count,
            flow_ready,
            account_name,
            **review_kwargs,
        )
        self._send_html(200, page, cookies=cookies)

    def _render_search_view(
        self,
        search_request: SearchRequest,
        cart_count: int,
        account_name: str | None,
    ) -> str:
        """Build one evidence-aware search page from the appropriate candidate set."""

        if is_portable_ssd_contract_query(search_request.query):
            products: list[dict[str, Any]] = []
            frozen_by_asin = {
                str(product["asin"]): product for product in self.store.products()
            }
            for asin in PORTABLE_SEARCH_ORDER:
                source_product = frozen_by_asin[asin]
                product = dict(source_product)
                # The frozen products come from the directly observed External
                # SSD ranking/search surface, so Computers is an evidenced
                # department identity rather than a title-based inference.
                product["placements"] = [
                    {
                        "railKey": "best-sellers-computers-accessories",
                        "railTitle": "Computers & Accessories",
                    }
                ]
                default_options = self.store.default_product_options(product["asin"])
                product["default_selected_options"] = default_options
                for quote in self.store.product_option_quotes(product["asin"]):
                    if quote.get("selected_options") == default_options:
                        product["availability"] = quote.get("display_availability")
                        break
                products.append(product)
            products.extend(
                dict(product)
                for product in BROWSE_BREADTH["portable_ssd_supplement"]
            )
            total_candidates = len(products)
            candidates = [
                build_search_hit(
                    product,
                    relevance=total_candidates - index,
                    source_index=index,
                )
                for index, product in enumerate(products)
            ]
        else:
            candidates = candidate_search_hits(
                search_request,
                HOME_PRODUCT_CATALOG,
                SEARCH_DEPARTMENT_SUPPLEMENTS,
            )

        eligible_asins = self.store.compare_eligible_asins()
        candidates = [
            replace(
                hit,
                product=compare_entry_product(
                    self.store,
                    hit.product,
                    eligible_asins=eligible_asins,
                ),
            )
            for hit in candidates
        ]

        search_page = refine_search_hits(search_request, candidates, page_size=16)
        available_brands = tuple(
            dict.fromkeys(
                hit.brand
                for hit in candidates
                if hit.brand is not None
                and (
                    search_request.department == "aps"
                    or search_request.department in hit.departments
                )
            )
        )
        return views.evidence_aware_search_page(
            search_page,
            cart_count,
            account_name,
            available_brands=available_brands,
        )

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        path = request.path or "/"
        if path == "/healthz":
            try:
                self.store.meta()
            except Exception:
                self._send(
                    503,
                    json_bytes({"ok": False}),
                    content_type="application/json; charset=utf-8",
                )
                return
            self._send(
                200,
                json_bytes({"ok": True}),
                content_type="application/json; charset=utf-8",
            )
            return
        if not self._require_public_basic_auth():
            return
        try:
            query = parse_qs(
                request.query, keep_blank_values=True, max_num_fields=64
            )
        except ValueError:
            self._send(
                400,
                b"Too many query parameters.",
                content_type="text/plain; charset=utf-8",
            )
            return

        if path.startswith("/static/"):
            self._serve_static(path)
            return
        if path.startswith("/__bench/"):
            self._send(404, b"Not Found", content_type="text/plain; charset=utf-8")
            return
        if path == "/robots.txt":
            self._send(200, b"User-agent: *\nDisallow: /__bench/\n", content_type="text/plain; charset=utf-8")
            return

        _, session_digest, cookies = self._session()
        cart_count = self.store.cart_count(session_digest)
        current_account = self.store.account_for_session(session_digest)
        account_name = (
            str(current_account["display_name"]) if current_account is not None else None
        )
        referer = self._referer()

        if path == SEARCH_SUGGESTIONS_PATH:
            try:
                suggestion_request = parse_suggestion_request(request.query)
                suggestions = suggest_search_terms(
                    suggestion_request, SEARCH_SUGGESTION_CORPUS
                )
            except SearchSuggestionValidationError as exc:
                self.store.record_read_route(
                    session_digest,
                    "SEARCH_SUGGESTIONS",
                    path,
                    referer,
                    status=400,
                )
                self._send(
                    400,
                    json_bytes({"error": str(exc)}),
                    content_type="application/json; charset=utf-8",
                    cookies=cookies,
                )
                return
            self.store.record_read_route(
                session_digest, "SEARCH_SUGGESTIONS", path, referer
            )
            self._send(
                200,
                json_bytes(
                    {
                        "department": suggestion_request.department,
                        "query": suggestion_request.query,
                        "suggestions": [
                            {
                                "department": suggestion.department,
                                "kind": suggestion.kind,
                                "value": suggestion.value,
                            }
                            for suggestion in suggestions
                        ],
                    }
                ),
                content_type="application/json; charset=utf-8",
                cookies=cookies,
            )
            return

        if path == AUTH_SIGNOUT_PATH:
            self._send(
                405,
                b"Sign out requires POST.",
                content_type="text/plain; charset=utf-8",
                headers={"Allow": "POST"},
                cookies=cookies,
            )
            return

        if path in {"/", "/ref=nav_logo"}:
            self.store.record_read_route(session_digest, "HOME", path, referer)
            page = views.home_page(self.store.products(), cart_count, account_name)
            self._send_html(200, page, cookies=cookies)
            return

        if path == "/Best-Sellers/zgbs":
            self.store.record_read_route(session_digest, "BEST_SELLERS_ROOT", path, referer)
            page = views.best_sellers_root(self.store.products(), cart_count, account_name)
            self._send_html(200, page, cookies=cookies)
            return

        if path == BEST_SELLERS_PATH:
            self.store.record_best_sellers(session_digest, path, referer)
            page = views.external_ssd_best_sellers(self.store.ranking(), cart_count, account_name)
            self._send_html(200, page, cookies=cookies)
            return

        if path == PDP_PATH:
            capability = secrets.token_urlsafe(SESSION_BYTES)
            flow_ready = self.store.record_pdp(
                session_digest,
                path,
                referer,
                digest(capability),
                self._same_origin_path(referer, BEST_SELLERS_PATH),
            )
            if flow_ready:
                cookies.append(browser_cookie(FLOW_COOKIE, capability))
            product = self.store.product(TARGET_ASIN)
            assert product is not None
            self._send_product_page(
                product,
                cart_count=cart_count,
                flow_ready=flow_ready,
                account_name=account_name,
                session_digest=session_digest,
                query=query,
                path=path,
                cookies=cookies,
            )
            return

        if path == f"/gp/aw/d/{TARGET_ASIN}":
            self.store.record_read_route(session_digest, "TARGET_PDP_MOBILE_ALIAS", path, referer)
            product = self.store.product(TARGET_ASIN)
            assert product is not None
            self._send_product_page(
                product,
                cart_count=cart_count,
                flow_ready=False,
                account_name=account_name,
                session_digest=session_digest,
                query=query,
                path=path,
                cookies=cookies,
            )
            return

        bare_product_match = BARE_PDP_ROUTE.fullmatch(path)
        if bare_product_match:
            asin = bare_product_match.group(1)
            product = product_for_pdp(self.store, asin)
            if product is not None:
                self.store.record_read_route(session_digest, "HOME_PRODUCT_DETAIL", path, referer)
                self._send_product_page(
                    product,
                    cart_count=cart_count,
                    flow_ready=False,
                    account_name=account_name,
                    session_digest=session_digest,
                    query=query,
                    path=path,
                    cookies=cookies,
                )
                return

        product_match = PDP_ROUTE.fullmatch(path)
        if product_match:
            slug, asin = product_match.groups()
            product = product_for_pdp(self.store, asin)
            if product is not None and product["slug"] == slug:
                self.store.record_read_route(session_digest, "PRODUCT_DETAIL", path, referer)
                self._send_product_page(
                    product,
                    cart_count=cart_count,
                    flow_ready=False,
                    account_name=account_name,
                    session_digest=session_digest,
                    query=query,
                    path=path,
                    cookies=cookies,
                )
                return

        product_reviews_match = PRODUCT_REVIEWS_ROUTE.fullmatch(path)
        if product_reviews_match:
            asin = product_reviews_match.group(1)
            product = product_for_pdp(self.store, asin)
            if product is None:
                self.store.record_read_route(
                    session_digest, "PRODUCT_REVIEWS_NOT_FOUND", path, referer, status=404
                )
                self._send_html(404, views.not_found_page(cart_count), cookies=cookies)
                return
            try:
                review_star, review_sort = review_query_parameters(query)
                local_reviews = self.store.reviews_for_session(
                    session_digest, asin, sort=review_sort
                )
            except ReviewValidationError as exc:
                self._send_html(
                    400,
                    views.error_page(str(exc), cart_count, 400),
                    cookies=cookies,
                )
                return
            review_html = render_reviews_section(
                asin,
                local_reviews,
                star=review_star,
                sort=review_sort,
                account_name=account_name,
                base_path=path,
                product_label=str(product["title"]),
            )
            self.store.record_read_route(
                session_digest, "PRODUCT_REVIEWS", path, referer
            )
            self._send_html(
                200,
                views.product_reviews_page(
                    product, review_html, cart_count, account_name
                ),
                cookies=cookies,
            )
            return

        if path in {"/s", "/s/ref=nb_sb_noss"}:
            try:
                search_request = parse_search_request(request.query)
                page = self._render_search_view(
                    search_request, cart_count, account_name
                )
            except SearchValidationError as exc:
                self.store.record_read_route(
                    session_digest, "SEARCH", path, referer, status=400
                )
                self._send_html(
                    400,
                    views.error_page(str(exc), cart_count, 400),
                    cookies=cookies,
                )
                return
            self.store.record_read_route(session_digest, "SEARCH", path, referer)
            self._send_html(200, page, cookies=cookies)
            return

        if path == "/gp/cart/view.html":
            self.store.record_read_route(session_digest, "CART", path, referer)
            lines = self.store.cart(session_digest)
            saved_lines = self.store.saved_cart(session_digest)
            page = views.cart_page(
                lines,
                self.store.cart_count(session_digest),
                account_name,
                saved_lines=saved_lines,
            )
            self._send_html(200, page, cookies=cookies)
            return

        if path == COMPARE_PATH:
            compare_products = [
                compare_product_for_view(self.store, item)
                for item in self.store.compare_items(session_digest)
            ]
            error = (query.get("error") or [None])[0]
            self.store.record_read_route(session_digest, "COMPARE", path, referer)
            self._send_html(
                200,
                views.compare_page(compare_products, cart_count, account_name, error),
                cookies=cookies,
            )
            return

        if path == SITE_DIRECTORY_PATH:
            self.store.record_read_route(session_digest, "SITE_DIRECTORY", path, referer)
            page = views.site_directory_page(
                list(BROWSE_BREADTH["rail_sections"]),
                cart_count,
                account_name,
            )
            self._send_html(200, page, cookies=cookies)
            return

        if path == "/gp/goldbox/":
            self.store.record_read_route(session_digest, "TODAYS_DEALS", path, referer)
            self._send_html(
                200,
                views.deals_page(
                    build_deals_view(DEALS_CATALOG, query),
                    cart_count,
                    account_name,
                ),
                cookies=cookies,
            )
            return

        if path == GIFT_CARDS_PATH:
            self.store.record_read_route(session_digest, "GIFT_CARDS", path, referer)
            self._send_html(
                200,
                specialty_views.gift_cards_page(cart_count, account_name),
                cookies=cookies,
            )
            return

        if path == specialty.GIFT_CARD_PREVIEW_PATH:
            preview_values = query.get("previewID")
            preview = (
                specialty.gift_card_preview(
                    self.store, session_digest, preview_values[0]
                )
                if set(query) == {"previewID"}
                and preview_values is not None
                and len(preview_values) == 1
                else None
            )
            if preview is None:
                self.store.record_read_route(
                    session_digest, "GIFT_CARD_PREVIEW_NOT_FOUND", path, referer, status=404
                )
                self._send_html(404, views.not_found_page(cart_count), cookies=cookies)
                return
            self.store.record_read_route(
                session_digest, "GIFT_CARD_PREVIEW", path, referer
            )
            self._send_html(
                200,
                specialty_views.gift_card_preview_page(
                    preview, cart_count, account_name
                ),
                cookies=cookies,
            )
            return

        if path in {
            specialty.GIFT_CARD_BALANCE_PATH,
            specialty.GIFT_CARD_REDEEM_PATH,
        }:
            status_values = query.get("status")
            if query and not (
                set(query) == {"status"} and status_values == ["not-applied"]
            ):
                self.store.record_read_route(
                    session_digest, "GIFT_CARD_BALANCE_INVALID", path, referer, status=400
                )
                self._send_html(
                    400,
                    views.error_page("Check the request and try again.", cart_count, 400),
                    cookies=cookies,
                )
                return
            balance = specialty.gift_card_balance(self.store, session_digest)
            self.store.record_read_route(
                session_digest, "GIFT_CARD_BALANCE", path, referer
            )
            self._send_html(
                200,
                specialty_views.gift_card_balance_page(
                    balance,
                    cart_count,
                    account_name,
                    redemption_result=status_values == ["not-applied"],
                ),
                cookies=cookies,
            )
            return

        if path == specialty.SELL_PATH:
            drafts = specialty.seller_drafts(self.store, session_digest)
            self.store.record_read_route(session_digest, "SELL", path, referer)
            self._send_html(
                200,
                specialty_views.sell_page(drafts, cart_count, account_name),
                cookies=cookies,
            )
            return

        if path == specialty.SELL_DRAFT_PATH:
            draft_values = query.get("draftID")
            draft = (
                specialty.seller_draft(self.store, session_digest, draft_values[0])
                if set(query) == {"draftID"}
                and draft_values is not None
                and len(draft_values) == 1
                else None
            )
            if draft is None:
                self.store.record_read_route(
                    session_digest, "SELL_DRAFT_NOT_FOUND", path, referer, status=404
                )
                self._send_html(404, views.not_found_page(cart_count), cookies=cookies)
                return
            self.store.record_read_route(session_digest, "SELL_DRAFT", path, referer)
            self._send_html(
                200,
                specialty_views.seller_draft_page(draft, cart_count, account_name),
                cookies=cookies,
            )
            return

        if path == specialty.REGISTRY_PATH:
            own_registries = specialty.registries(self.store, session_digest)
            self.store.record_read_route(session_digest, "REGISTRY", path, referer)
            self._send_html(
                200,
                specialty_views.registry_page(
                    own_registries, cart_count, account_name
                ),
                cookies=cookies,
            )
            return

        if path == specialty.REGISTRY_SEARCH_PATH:
            search_values = query.get("query")
            if not (
                set(query) == {"query"}
                and search_values is not None
                and len(search_values) == 1
            ):
                self.store.record_read_route(
                    session_digest, "REGISTRY_SEARCH_INVALID", path, referer, status=400
                )
                self._send_html(
                    400,
                    views.error_page("Check the search and try again.", cart_count, 400),
                    cookies=cookies,
                )
                return
            try:
                normalized_query, results = specialty.search_registries(
                    self.store, session_digest, search_values[0]
                )
            except specialty.SpecialtyValidationError:
                self.store.record_read_route(
                    session_digest, "REGISTRY_SEARCH_INVALID", path, referer, status=400
                )
                self._send_html(
                    400,
                    views.error_page("Check the search and try again.", cart_count, 400),
                    cookies=cookies,
                )
                return
            self.store.record_read_route(session_digest, "REGISTRY_SEARCH", path, referer)
            self._send_html(
                200,
                specialty_views.registry_search_page(
                    normalized_query, results, cart_count, account_name
                ),
                cookies=cookies,
            )
            return

        if path == specialty.REGISTRY_DETAIL_PATH:
            registry_values = query.get("registryID")
            registry = (
                specialty.registry(self.store, session_digest, registry_values[0])
                if set(query) == {"registryID"}
                and registry_values is not None
                and len(registry_values) == 1
                else None
            )
            if registry is None:
                self.store.record_read_route(
                    session_digest, "REGISTRY_DETAIL_NOT_FOUND", path, referer, status=404
                )
                self._send_html(404, views.not_found_page(cart_count), cookies=cookies)
                return
            self.store.record_read_route(
                session_digest, "REGISTRY_DETAIL", path, referer
            )
            self._send_html(
                200,
                specialty_views.registry_detail_page(
                    registry, cart_count, account_name
                ),
                cookies=cookies,
            )
            return

        if path == specialty.PRIME_VIDEO_PATH:
            self.store.record_read_route(
                session_digest, "PRIME_VIDEO_PLACEHOLDER", path, referer
            )
            self._send_html(
                200,
                specialty_views.prime_video_page(cart_count, account_name),
                cookies=cookies,
            )
            return

        if path == CUSTOMER_SERVICE_PATH:
            node_id = (query.get("nodeId") or [CUSTOMER_SERVICE_NODE])[0].strip()
            if node_id == SHIPPING_POLICIES_NODE:
                route_key = "SHIPPING_POLICIES"
                page = views.shipping_policies_page(cart_count, account_name)
            elif node_id == RETURNS_REPLACEMENTS_NODE:
                route_key = "RETURNS_REPLACEMENTS"
                page = views.returns_replacements_page(cart_count, account_name)
            else:
                route_key = "CUSTOMER_SERVICE"
                help_query = (query.get("help_keywords") or [""])[0].strip()
                page = views.customer_service_page(
                    cart_count,
                    account_name,
                    help_query=help_query,
                )
            self.store.record_read_route(session_digest, route_key, path, referer)
            self._send_html(200, page, cookies=cookies)
            return

        if path in PUBLIC_NAVIGATION_LANDINGS:
            title, copy, links = PUBLIC_NAVIGATION_LANDINGS[path]
            self.store.record_read_route(session_digest, "PUBLIC_NAVIGATION", path, referer)
            self._send_html(
                200,
                views.navigation_landing_page(
                    title,
                    copy,
                    links,
                    cart_count,
                    account_name,
                ),
                cookies=cookies,
            )
            return

        if path in AUTH_PATHS:
            self.store.record_read_route(session_digest, "AUTH_FRONTEND", path, referer)
            if path == "/ap/cvf/verify":
                purpose = (query.get("purpose") or ["registration"])[0]
                pending = (
                    self.store.pending_password_reset(session_digest)
                    if purpose == "password-reset"
                    else self.store.pending_registration(session_digest)
                )
                self._send_html(
                    200,
                    auth_views.verification_page(
                        query,
                        masked_destination=(pending or {}).get("masked_email"),
                        mail_delivery_mode=(
                            "SMTP" if self.smtp_config is not None else "LOCAL_ONLY"
                        ),
                        mail_delivery=self._auth_mail_status(
                            session_digest, purpose
                        ),
                        local_inbox_url=self.local_inbox_url,
                    ),
                    cookies=cookies,
                )
                return
            if path == "/ap/forgotpassword" and (
                (query.get("stage") or [""])[0].lower() == "reset-password"
            ):
                pending_reset = self.store.pending_password_reset(session_digest)
                if not pending_reset or not pending_reset["verified"]:
                    self._send(
                        303,
                        b"",
                        headers={"Location": "/ap/forgotpassword"},
                        cookies=cookies,
                    )
                    return
            self._send_html(200, auth_views.page_for(path, query), cookies=cookies)
            return

        if path in {ADDRESS_BOOK_PATH, ADDRESS_ADD_PATH, ADDRESS_EDIT_PATH}:
            if current_account is None:
                return_target = path
                if path == ADDRESS_EDIT_PATH and request.query:
                    return_target += "?" + request.query
                location = "/ap/signin?" + urlencode(
                    {"openid.return_to": return_target}
                )
                self._send(303, b"", headers={"Location": location}, cookies=cookies)
                return
            if path == ADDRESS_BOOK_PATH:
                status_values = query.get("status", [])
                status = (
                    status_values[0]
                    if len(status_values) == 1
                    and status_values[0]
                    in {"added", "updated", "deleted", "default"}
                    else None
                )
                self.store.record_read_route(
                    session_digest, "ADDRESS_BOOK", path, referer
                )
                self._send_html(
                    200,
                    views.address_book_page(
                        self.store.addresses_for_session(session_digest),
                        cart_count,
                        account_name or "Customer",
                        status=status,
                    ),
                    cookies=cookies,
                )
                return
            if path == ADDRESS_ADD_PATH:
                self._send_html(
                    200,
                    views.address_form_page(
                        None, cart_count, account_name or "Customer"
                    ),
                    cookies=cookies,
                )
                return
            address_values = query.get("addressID", [])
            address = (
                self.store.address_for_session(
                    session_digest, address_values[0]
                )
                if len(address_values) == 1
                else None
            )
            if address is None:
                self._send_html(
                    404, views.not_found_page(cart_count), cookies=cookies
                )
                return
            self._send_html(
                200,
                views.address_form_page(
                    address, cart_count, account_name or "Customer"
                ),
                cookies=cookies,
            )
            return

        if path == BUY_NOW_CONTINUE_PATH:
            if current_account is None:
                location = "/ap/signin?" + urlencode(
                    {"openid.return_to": BUY_NOW_CONTINUE_PATH}
                )
                self._send(303, b"", headers={"Location": location}, cookies=cookies)
                return
            try:
                checkout = self.store.resume_buy_now(session_digest)
            except ContractError:
                self._send_html(
                    409,
                    views.error_page(
                        "This Buy Now offer is no longer available. Return to the product page and choose it again.",
                        cart_count,
                        409,
                    ),
                    cookies=cookies,
                )
                return
            if checkout is None:
                self._send(
                    303,
                    b"",
                    headers={"Location": "/gp/cart/view.html"},
                    cookies=cookies,
                )
                return
            self._send(
                303,
                b"",
                headers={"Location": CHECKOUT_ADDRESS_PATH},
                cookies=cookies,
            )
            return

        if path in {
            CHECKOUT_ADDRESS_PATH,
            CHECKOUT_DELIVERY_PATH,
            CHECKOUT_PAYMENT_PATH,
            CHECKOUT_START_PATH,
        }:
            if current_account is None:
                location = "/ap/signin?" + urlencode({"openid.return_to": path})
                self._send(303, b"", headers={"Location": location}, cookies=cookies)
                return
            checkout = self.store.reconcile_checkout(session_digest)
            if path == CHECKOUT_ADDRESS_PATH and checkout is None:
                try:
                    checkout = self.store.start_checkout(session_digest)
                except ContractError:
                    self._send(303, b"", headers={"Location": "/gp/cart/view.html"}, cookies=cookies)
                    return
            if checkout is None:
                self._send(303, b"", headers={"Location": CHECKOUT_ADDRESS_PATH}, cookies=cookies)
                return
            stage = CHECKOUT_STAGE_ORDER.get(str(checkout.get("status")), -1)
            if str(checkout.get("status")) == "PLACED" and checkout.get("order_id"):
                location = ORDER_DETAIL_PATH + "?" + urlencode({"orderID": checkout["order_id"]})
                self._send(303, b"", headers={"Location": location}, cookies=cookies)
                return
            reconciliation_reason = str(
                checkout.get("reconciliation_reason") or ""
            )
            notice_values = query.get("notice", [])
            notice = (
                notice_values[0]
                if len(notice_values) == 1
                and notice_values[0]
                in {
                    "cart-changed",
                    "unsupported-delivery-country",
                    "payment-declined",
                }
                else ""
            )
            if (
                path == CHECKOUT_START_PATH
                and reconciliation_reason == "cart-changed"
                and stage >= CHECKOUT_STAGE_ORDER["DELIVERY_SELECTED"]
            ):
                location = CHECKOUT_PAYMENT_PATH + "?" + urlencode(
                    {"notice": "cart-changed"}
                )
                self._send(
                    303, b"", headers={"Location": location}, cookies=cookies
                )
                return
            if (
                reconciliation_reason == "unsupported-delivery-country"
                and path != CHECKOUT_ADDRESS_PATH
            ):
                location = CHECKOUT_ADDRESS_PATH + "?" + urlencode(
                    {"notice": "unsupported-delivery-country"}
                )
                self._send(
                    303, b"", headers={"Location": location}, cookies=cookies
                )
                return
            if reconciliation_reason:
                checkout = {**checkout, "notice": reconciliation_reason}
            elif notice:
                checkout = {**checkout, "notice": notice}
            checkout_item_count = sum(
                int(item.get("quantity", 0))
                for item in checkout.get("items", [])
                if isinstance(item, dict)
            )
            if path == CHECKOUT_ADDRESS_PATH:
                page = views.checkout_address_page(checkout, checkout_item_count, account_name or "Customer")
            elif path == CHECKOUT_DELIVERY_PATH:
                if stage < CHECKOUT_STAGE_ORDER["ADDRESS_SELECTED"]:
                    self._send(303, b"", headers={"Location": CHECKOUT_ADDRESS_PATH}, cookies=cookies)
                    return
                page = views.checkout_delivery_page(checkout, checkout_item_count, account_name or "Customer")
            elif path == CHECKOUT_PAYMENT_PATH:
                if stage < CHECKOUT_STAGE_ORDER["DELIVERY_SELECTED"]:
                    self._send(303, b"", headers={"Location": CHECKOUT_DELIVERY_PATH}, cookies=cookies)
                    return
                page = views.checkout_payment_page(checkout, checkout_item_count, account_name or "Customer")
            else:
                if stage < CHECKOUT_STAGE_ORDER["PAYMENT_SELECTED"]:
                    fallback = (
                        CHECKOUT_PAYMENT_PATH
                        if stage >= CHECKOUT_STAGE_ORDER["DELIVERY_SELECTED"]
                        else CHECKOUT_DELIVERY_PATH
                        if stage >= CHECKOUT_STAGE_ORDER["ADDRESS_SELECTED"]
                        else CHECKOUT_ADDRESS_PATH
                    )
                    self._send(303, b"", headers={"Location": fallback}, cookies=cookies)
                    return
                page = views.checkout_review_page(checkout, checkout_item_count, account_name or "Customer")
            self._send_html(200, page, cookies=cookies)
            return

        if path == ORDER_DETAIL_PATH:
            if current_account is None:
                location = "/ap/signin?" + urlencode({"openid.return_to": path})
                self._send(303, b"", headers={"Location": location}, cookies=cookies)
                return
            order_values = query.get("orderID", [])
            order = (
                self.store.order_for_session(session_digest, order_values[0])
                if len(order_values) == 1
                else None
            )
            if order is None:
                self._send_html(404, views.not_found_page(cart_count), cookies=cookies)
                return
            order = self._public_order_mail_state(order)
            self._send_html(
                200,
                views.order_confirmation_page(order, cart_count, account_name or "Customer"),
                cookies=cookies,
            )
            return

        if path in {RETURN_CREATE_PATH, RETURN_DETAIL_PATH}:
            if current_account is None:
                return_target = path + ("?" + request.query if request.query else "")
                location = "/ap/signin?" + urlencode(
                    {"openid.return_to": return_target}
                )
                self._send(303, b"", headers={"Location": location}, cookies=cookies)
                return
            if path == RETURN_CREATE_PATH:
                order_values = query.get("orderID", [])
                order = (
                    self.store.order_for_session(session_digest, order_values[0])
                    if len(order_values) == 1
                    else None
                )
                if order is None:
                    self._send_html(
                        404, views.not_found_page(cart_count), cookies=cookies
                    )
                    return
                existing_return = order.get("return_request")
                if isinstance(existing_return, dict):
                    location = RETURN_DETAIL_PATH + "?" + urlencode(
                        {"returnID": existing_return["return_request_id"]}
                    )
                    self._send(303, b"", headers={"Location": location}, cookies=cookies)
                    return
                if not order.get("can_return"):
                    self._send_html(
                        409,
                        views.error_page(
                            "This simulated order is not eligible for a return request in its current state.",
                            cart_count,
                            409,
                        ),
                        cookies=cookies,
                    )
                    return
                self._send_html(
                    200,
                    views.return_request_page(
                        order, cart_count, account_name or "Customer"
                    ),
                    cookies=cookies,
                )
                return
            return_values = query.get("returnID", [])
            order = (
                self.store.return_for_session(session_digest, return_values[0])
                if len(return_values) == 1
                else None
            )
            if order is None:
                self._send_html(
                    404, views.not_found_page(cart_count), cookies=cookies
                )
                return
            self._send_html(
                200,
                views.return_details_page(
                    order, cart_count, account_name or "Customer"
                ),
                cookies=cookies,
            )
            return

        if path == wishlist_views.WISHLIST_INTRO_PATH:
            if current_account is not None:
                self._send(
                    303,
                    b"",
                    headers={"Location": wishlist_views.WISHLIST_INDEX_PATH},
                    cookies=cookies,
                )
                return
            self.store.record_read_route(
                session_digest, "WISHLIST_INTRO", path, referer
            )
            self._send_html(
                200,
                wishlist_views.wishlist_intro_page(cart_count),
                cookies=cookies,
            )
            return

        if path == WISHLIST_FRIENDS_PATH:
            if current_account is None:
                location = "/ap/signin?" + urlencode(
                    {"openid.return_to": WISHLIST_FRIENDS_PATH}
                )
                self._send(
                    303, b"", headers={"Location": location}, cookies=cookies
                )
                return
            self.store.record_read_route(
                session_digest, "WISHLIST_FRIENDS_BOUNDARY", path, referer
            )
            self._send_html(
                200,
                views.navigation_landing_page(
                    "Your Friends' Lists",
                    "Shared-list invitations are outside this local shopping clone. Your private Lists and Registry shopping remain available.",
                    (
                        ("Open Your Lists", wishlist_views.WISHLIST_INDEX_PATH),
                        ("Open Gift Registries", "/gp/browse.html?node=16115931011"),
                    ),
                    cart_count,
                    account_name,
                ),
                cookies=cookies,
            )
            return

        if path in {
            wishlist_views.WISHLIST_INDEX_PATH,
            wishlist_views.WISHLIST_ADD_CHOOSER_PATH,
        }:
            if current_account is None:
                return_target = path + (f"?{request.query}" if request.query else "")
                location = "/ap/signin?" + urlencode(
                    {"openid.return_to": return_target}
                )
                self._send(
                    303, b"", headers={"Location": location}, cookies=cookies
                )
                return

            if path == wishlist_views.WISHLIST_ADD_CHOOSER_PATH:
                selection_query = wishlist_selection_query(query)
                if selection_query is None:
                    self._send(
                        400,
                        b"Malformed Add to List request",
                        content_type="text/plain; charset=utf-8",
                        cookies=cookies,
                    )
                    return
                asin, requested_options = selection_query
                try:
                    product, selected_options = (
                        wishlist.product_selection_for_wishlist(
                            self.store, asin, requested_options
                        )
                    )
                    wishlists = wishlist.lists_for_session(
                        self.store, session_digest
                    )
                except wishlist.WishlistAuthenticationRequired:
                    return_target = path + (
                        f"?{request.query}" if request.query else ""
                    )
                    location = "/ap/signin?" + urlencode(
                        {"openid.return_to": return_target}
                    )
                    self._send(
                        303,
                        b"",
                        headers={"Location": location},
                        cookies=cookies,
                    )
                    return
                except wishlist.WishlistNotFound:
                    self._send_html(
                        404, views.not_found_page(cart_count), cookies=cookies
                    )
                    return
                except wishlist.WishlistValidationError as exc:
                    self._send_html(
                        400,
                        views.error_page(str(exc), cart_count, 400),
                        cookies=cookies,
                    )
                    return
                self.store.record_read_route(
                    session_digest, "WISHLIST_ADD_CHOOSER", path, referer
                )
                self._send_html(
                    200,
                    wishlist_views.wishlist_add_chooser_page(
                        product,
                        selected_options,
                        wishlists,
                        cart_count,
                        account_name or "Customer",
                    ),
                    cookies=cookies,
                )
                return

            if set(query) - {"listID", "status"} or any(
                len(values) != 1 for values in query.values()
            ):
                self._send(
                    400,
                    b"Malformed Wishlist request",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            status_messages = {
                "created": "List created.",
                "renamed": "List renamed.",
                "deleted": "List deleted.",
                "added": "Added to List.",
                "already-added": "This item was already on the List.",
                "removed": "Item removed from List.",
            }
            status_values = query.get("status", [])
            status_token = status_values[0] if status_values else ""
            if status_token and status_token not in status_messages:
                self._send(
                    400,
                    b"Malformed Wishlist status",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            try:
                wishlists = wishlist.lists_for_session(
                    self.store, session_digest
                )
                list_values = query.get("listID", [])
                if list_values:
                    selected_list = wishlist.list_for_session(
                        self.store, session_digest, list_values[0]
                    )
                    page = wishlist_views.wishlist_detail_page(
                        selected_list,
                        wishlists,
                        cart_count,
                        account_name or "Customer",
                        status=status_messages.get(status_token, ""),
                    )
                    route_kind = "WISHLIST_DETAIL"
                else:
                    page = wishlist_views.wishlist_index_page(
                        wishlists,
                        cart_count,
                        account_name or "Customer",
                        status=status_messages.get(status_token, ""),
                    )
                    route_kind = "WISHLIST_INDEX"
            except wishlist.WishlistAuthenticationRequired:
                location = "/ap/signin?" + urlencode(
                    {"openid.return_to": wishlist_views.WISHLIST_INDEX_PATH}
                )
                self._send(
                    303, b"", headers={"Location": location}, cookies=cookies
                )
                return
            except wishlist.WishlistNotFound:
                self._send_html(
                    404, views.not_found_page(cart_count), cookies=cookies
                )
                return
            except wishlist.WishlistValidationError as exc:
                self._send_html(
                    400,
                    views.error_page(str(exc), cart_count, 400),
                    cookies=cookies,
                )
                return
            self.store.record_read_route(
                session_digest, route_kind, path, referer
            )
            self._send_html(200, page, cookies=cookies)
            return

        if path == "/gp/css/order-history" and current_account is not None:
            orders = [
                self._public_order_mail_state(order)
                for order in self.store.orders_for_session(session_digest)
            ]
            self.store.record_read_route(session_digest, "ORDERS", path, referer)
            self._send_html(
                200,
                views.order_history_page(orders, cart_count, account_name or "Customer"),
                cookies=cookies,
            )
            return

        if path in PROTECTED_ACCOUNT_PATHS:
            if current_account is None:
                location = "/ap/signin?" + urlencode({"openid.return_to": path})
                self._send(303, b"", headers={"Location": location}, cookies=cookies)
                return
            self.store.record_read_route(session_digest, "ACCOUNT", path, referer)
            self._send_html(200, views.account_page(current_account, cart_count), cookies=cookies)
            return

        if path.startswith(("/electronics-store/", "/computer-pc-", "/External-Solid-State-Drives/")):
            self.store.record_read_route(session_digest, "CATEGORY", path, referer)
            page = self._render_search_view(
                parse_search_request("k=portable+ssd&i=computers"),
                cart_count,
                account_name,
            )
            self._send_html(200, page, cookies=cookies)
            return

        self.store.record_read_route(session_digest, "NOT_FOUND", path, referer, status=404)
        self._send_html(404, views.not_found_page(cart_count), cookies=cookies)

    def do_POST(self) -> None:
        request = urlsplit(self.path)
        path = request.path
        if not self._require_public_basic_auth():
            return
        query = parse_qs(request.query, keep_blank_values=True)
        _, session_digest, cookies = self._session()
        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self._send(400, b"Invalid Content-Length", content_type="text/plain; charset=utf-8", cookies=cookies)
            return
        if content_length < 0 or content_length > MAX_FORM_BYTES:
            # The request body has deliberately not been consumed.  Close this
            # HTTP/1.1 connection so those bytes cannot be parsed as a second
            # request after the 413 response.
            self.close_connection = True
            self._send_html(
                413,
                views.error_page("The form submission was too large.", self.store.cart_count(session_digest), 413),
                cookies=cookies,
                headers={"Connection": "close"},
            )
            return
        raw_body = self.rfile.read(content_length)

        if path == AUTH_SIGNOUT_PATH:
            if not self._auth_post_origin_is_safe():
                self._send(403, b"Forbidden", content_type="text/plain; charset=utf-8", cookies=cookies)
                return
            self.store.sign_out(session_digest)
            cookies.append(browser_cookie(SESSION_COOKIE, "", max_age=0))
            self._send(
                303,
                b"",
                headers={"Location": "/"},
                cookies=cookies,
            )
            return

        if path in SPECIALTY_POST_PATHS:
            if not self._auth_post_origin_is_safe():
                self._send(
                    403,
                    b"Forbidden",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            fields = self._form_fields(raw_body)
            expected_fields = {
                specialty.GIFT_CARD_PREVIEW_PATH: {
                    "design",
                    "amount",
                    "recipientKind",
                },
                specialty.GIFT_CARD_REDEEM_PATH: {"claimCode"},
                specialty.SELL_DRAFT_PATH: {
                    "title",
                    "category",
                    "condition",
                    "price",
                    "quantity",
                    "description",
                },
                specialty.REGISTRY_CREATE_PATH: {
                    "registryType",
                    "ownerName",
                    "registryName",
                    "eventDate",
                },
            }[path]
            if query or fields is None or set(fields) != expected_fields:
                self._send_html(
                    400,
                    views.error_page("Check the fields and try again.", self.store.cart_count(session_digest), 400),
                    cookies=cookies,
                )
                return
            try:
                if path == specialty.GIFT_CARD_PREVIEW_PATH:
                    result = specialty.create_gift_card_preview(
                        self.store,
                        session_digest,
                        fields["design"],
                        fields["amount"],
                        fields["recipientKind"],
                    )
                    location = specialty.GIFT_CARD_PREVIEW_PATH + "?" + urlencode(
                        {"previewID": int(result["preview_id"])}
                    )
                elif path == specialty.GIFT_CARD_REDEEM_PATH:
                    specialty.redeem_gift_card(
                        self.store, session_digest, fields["claimCode"]
                    )
                    location = specialty.GIFT_CARD_BALANCE_PATH + "?" + urlencode(
                        {"status": "not-applied"}
                    )
                elif path == specialty.SELL_DRAFT_PATH:
                    result = specialty.save_seller_draft(
                        self.store, session_digest, fields
                    )
                    location = specialty.SELL_DRAFT_PATH + "?" + urlencode(
                        {"draftID": int(result["draft_id"])}
                    )
                else:
                    result = specialty.create_registry(
                        self.store, session_digest, fields
                    )
                    location = specialty.REGISTRY_DETAIL_PATH + "?" + urlencode(
                        {"registryID": int(result["registry_id"])}
                    )
            except specialty.SpecialtyValidationError:
                self._send_html(
                    400,
                    views.error_page("Check the fields and try again.", self.store.cart_count(session_digest), 400),
                    cookies=cookies,
                )
                return
            self._send(303, b"", headers={"Location": location}, cookies=cookies)
            return

        if path in AUTH_PATHS:
            fields = self._form_fields(raw_body)
            if not self._auth_post_origin_is_safe():
                self._send(403, b"Forbidden", content_type="text/plain; charset=utf-8", cookies=cookies)
                return

            if path == "/ap/signin":
                if fields is not None and "email" in fields and "password" not in fields:
                    email = valid_account_email(fields["email"])
                    if email is None:
                        self._send_html(
                            400,
                            auth_views.signin_page(
                                query, "There was a problem with your sign-in. Please try again."
                            ),
                            cookies=cookies,
                        )
                        return
                    return_to = auth_views.safe_return_target(
                        {"openid.return_to": [fields.get("openid.return_to", "")]}
                    )
                    if not self.store.account_exists(email):
                        registration_query = {"email": email}
                        if return_to:
                            registration_query["openid.return_to"] = return_to
                        self._send(
                            303,
                            b"",
                            headers={
                                "Location": "/ap/register?"
                                + urlencode(registration_query)
                            },
                            cookies=cookies,
                        )
                        return
                    self.store.begin_signin(session_digest, email, return_to)
                    self._send(
                        303,
                        b"",
                        headers={"Location": "/ap/signin?stage=password"},
                        cookies=cookies,
                    )
                    return

                if fields is not None and "password" in fields and "email" not in fields:
                    password = fields["password"]
                    if 1 <= len(password) <= 1024:
                        authenticated, return_to = self.store.authenticate_session(
                            session_digest, password
                        )
                    else:
                        authenticated, return_to = False, None
                    if authenticated:
                        self._rotate_authenticated_session(session_digest, cookies)
                        self._send(
                            303,
                            b"",
                            headers={"Location": return_to or "/"},
                            cookies=cookies,
                        )
                        return
                    self._send_html(
                        401,
                        auth_views.signin_page(
                            {"stage": ["password"]},
                            "Your email or password is incorrect.",
                        ),
                        cookies=cookies,
                    )
                    return

                self._send_html(
                    400,
                    auth_views.signin_page(
                        query, "There was a problem with your sign-in. Please try again."
                    ),
                    cookies=cookies,
                )
                return

            if path == "/ap/register":
                return_to = auth_views.safe_return_target(
                    {"openid.return_to": [(fields or {}).get("openid.return_to", "")]}
                )
                email = valid_account_email((fields or {}).get("email", ""))
                display_name = clean_display_name((fields or {}).get("customerName", ""))
                password = (fields or {}).get("password", "")
                password_check = (fields or {}).get("passwordCheck", "")
                valid = bool(
                    fields is not None
                    and email is not None
                    and display_name is not None
                    and 6 <= len(password) <= 1024
                    and password == password_check
                )
                pending = bool(
                    valid
                    and self.store.begin_registration(
                        session_digest,
                        email or "",
                        display_name or "",
                        password,
                        return_to,
                        mail_mode=self._mail_mode(),
                    )
                )
                if pending:
                    self._dispatch_mail(
                        self.store.registration_delivery(session_digest)
                    )
                    self._send(
                        303,
                        b"",
                        headers={"Location": "/ap/cvf/verify?purpose=registration"},
                        cookies=cookies,
                    )
                    return
                page_query: dict[str, list[str]] = {}
                if return_to:
                    page_query["openid.return_to"] = [return_to]
                if email is not None and not valid:
                    page_query["email"] = [email]
                self._send_html(
                    400,
                    auth_views.register_page(
                        page_query,
                        "We couldn't create your account. Check your details and try again.",
                    ),
                    cookies=cookies,
                )
                return

            if path == "/ap/cvf/verify":
                purpose_values = query.get("purpose", [])
                if len(purpose_values) > 1 or (
                    purpose_values
                    and purpose_values[0] not in {"registration", "password-reset"}
                ):
                    self._send(
                        400,
                        b"Malformed verification purpose",
                        content_type="text/plain; charset=utf-8",
                        cookies=cookies,
                    )
                    return
                purpose = purpose_values[0] if purpose_values else "registration"
                purpose_query = {"purpose": [purpose]}
                delivery_mode = (
                    "SMTP" if self.smtp_config is not None else "LOCAL_ONLY"
                )

                if purpose == "password-reset":
                    if fields == {"action": "retry-delivery"}:
                        pending_reset = self.store.pending_password_reset(
                            session_digest
                        )
                        queued = bool(
                            self.smtp_config is not None
                            and pending_reset is not None
                            and pending_reset.get("verified")
                            and self.store.retry_password_reset_mail(
                                session_digest
                            )
                        )
                        delivery = (
                            self.store.password_reset_delivery(session_digest)
                            if queued
                            else None
                        )
                        # Unknown, foreign, exhausted, and successfully queued
                        # flows share one response contract.
                        self._send(
                            303,
                            b"",
                            headers={
                                "Location": "/ap/cvf/verify?purpose=password-reset"
                            },
                            cookies=cookies,
                        )
                        self._dispatch_mail(delivery)
                        return
                    if fields == {"action": "resend"}:
                        queued = self.store.resend_password_reset_code(
                            session_digest, mail_mode=self._mail_mode()
                        )
                        delivery = (
                            self.store.password_reset_delivery(session_digest)
                            if queued
                            else None
                        )
                        # Missing, decoy, throttled, and queued flows use the
                        # same public response to avoid account enumeration.
                        self._send(
                            303,
                            b"",
                            headers={
                                "Location": "/ap/cvf/verify?purpose=password-reset"
                            },
                            cookies=cookies,
                        )
                        self._dispatch_mail(delivery)
                        return

                    code = (fields or {}).get("code", "").strip()
                    if set(fields or {}) != {"code"} or not re.fullmatch(
                        r"[0-9]{6}", code
                    ):
                        reset_result = "invalid"
                    else:
                        reset_result = self.store.verify_password_reset_code(
                            session_digest, code
                        )
                    if reset_result == "verified":
                        self._send(
                            303,
                            b"",
                            headers={
                                "Location": "/ap/forgotpassword?stage=reset-password"
                            },
                            cookies=cookies,
                        )
                        return
                    reset_errors = {
                        "invalid": "The verification code you entered is not valid.",
                        "expired": "That verification code has expired. Request a new code.",
                        "locked": "Too many incorrect attempts. Request a new code.",
                        "missing": "Start password assistance again to request a new code.",
                        "used": "That verification code has already been used.",
                    }
                    self._send_html(
                        410 if reset_result == "expired" else 400,
                        auth_views.verification_page(
                            purpose_query,
                            error=reset_errors.get(
                                reset_result,
                                "We couldn't verify that code. Try again.",
                            ),
                            mail_delivery_mode=delivery_mode,
                            mail_delivery=self._auth_mail_status(
                                session_digest, purpose
                            ),
                            local_inbox_url=self.local_inbox_url,
                        ),
                        cookies=cookies,
                    )
                    return

                if fields == {"action": "retry-delivery"}:
                    queued = bool(
                        self.smtp_config is not None
                        and self.store.retry_registration_mail(session_digest)
                    )
                    if queued:
                        self._dispatch_mail(
                            self.store.registration_delivery(session_digest)
                        )
                    self._send(
                        303,
                        b"",
                        headers={"Location": "/ap/cvf/verify?purpose=registration"},
                        cookies=cookies,
                    )
                    return

                if fields == {"action": "resend"}:
                    if self.store.resend_registration_code(
                        session_digest, mail_mode=self._mail_mode()
                    ):
                        self._dispatch_mail(
                            self.store.registration_delivery(session_digest)
                        )
                        self._send(
                            303,
                            b"",
                            headers={"Location": "/ap/cvf/verify?purpose=registration"},
                            cookies=cookies,
                        )
                        return
                    self._send_html(
                        400,
                        auth_views.verification_page(
                            purpose_query,
                            error="Start account creation again to request a new code.",
                            mail_delivery_mode=delivery_mode,
                            mail_delivery=self._auth_mail_status(
                                session_digest, purpose
                            ),
                            local_inbox_url=self.local_inbox_url,
                        ),
                        cookies=cookies,
                    )
                    return

                code = (fields or {}).get("code", "").strip()
                if set(fields or {}) != {"code"} or not re.fullmatch(r"[0-9]{6}", code):
                    result, return_to = "invalid", None
                else:
                    result, return_to = self.store.verify_registration_code(
                        session_digest, code
                    )
                if result == "verified":
                    self._rotate_authenticated_session(session_digest, cookies)
                    self._send(
                        303,
                        b"",
                        headers={"Location": return_to or "/"},
                        cookies=cookies,
                    )
                    return

                pending_registration = self.store.pending_registration(session_digest)
                error_messages = {
                    "invalid": "The verification code you entered is not valid.",
                    "expired": "That verification code has expired. Request a new code.",
                    "locked": "Too many incorrect attempts. Request a new code.",
                    "missing": "Start account creation again to request a new code.",
                    "duplicate": "We couldn't create your account. Sign in or try again.",
                }
                status = 410 if result == "expired" else 400
                self._send_html(
                    status,
                    auth_views.verification_page(
                        purpose_query,
                        masked_destination=(pending_registration or {}).get(
                            "masked_email"
                        ),
                        error=error_messages.get(
                            result, "We couldn't verify that code. Try again."
                        ),
                        mail_delivery_mode=delivery_mode,
                        mail_delivery=self._auth_mail_status(
                            session_digest, purpose
                        ),
                        local_inbox_url=self.local_inbox_url,
                    ),
                    cookies=cookies,
                )
                return

            if path == "/ap/forgotpassword":
                if fields is not None and "email" in fields and not {
                    "password",
                    "passwordCheck",
                }.intersection(fields):
                    if set(fields) not in (
                        {"email"},
                        {"email", "openid.return_to"},
                    ):
                        email = None
                    else:
                        email = valid_account_email(fields.get("email", ""))
                    if email is None:
                        self._send_html(
                            400,
                            auth_views.forgot_password_page(
                                {},
                                "We couldn't process that request. Check the email address and try again.",
                            ),
                            cookies=cookies,
                        )
                        return
                    return_to = auth_views.safe_return_target(
                        {
                            "openid.return_to": [
                                fields.get("openid.return_to", "")
                            ]
                        }
                    )
                    self.store.begin_password_reset(
                        session_digest,
                        email,
                        return_to,
                        mail_mode=self._mail_mode(),
                    )
                    delivery = self.store.password_reset_delivery(session_digest)
                    # This response is identical whether or not an account was
                    # found; only a database-backed account can receive mail.
                    self._send(
                        303,
                        b"",
                        headers={
                            "Location": "/ap/cvf/verify?purpose=password-reset"
                        },
                        cookies=cookies,
                    )
                    self._dispatch_mail(delivery)
                    return

                if fields is not None and set(fields) == {
                    "password",
                    "passwordCheck",
                }:
                    password = fields["password"]
                    password_check = fields["passwordCheck"]
                    if not (
                        6 <= len(password) <= 1024 and password == password_check
                    ):
                        self._send_html(
                            400,
                            auth_views.forgot_password_page(
                                {"stage": ["reset-password"]},
                                "Passwords must match and contain at least 6 characters.",
                            ),
                            cookies=cookies,
                        )
                        return
                    reset_result, return_to = self.store.complete_password_reset(
                        session_digest, password
                    )
                    if reset_result == "reset":
                        self._rotate_authenticated_session(session_digest, cookies)
                        self._send(
                            303,
                            b"",
                            headers={"Location": return_to or "/"},
                            cookies=cookies,
                        )
                        return
                    self._send_html(
                        410 if reset_result == "expired" else 400,
                        auth_views.forgot_password_page(
                            {"stage": ["reset-password"]},
                            "This password reset is no longer valid. Start again.",
                        ),
                        cookies=cookies,
                    )
                    return

                self._send_html(
                    400,
                    auth_views.forgot_password_page(
                        {}, "We couldn't process that password assistance request."
                    ),
                    cookies=cookies,
                )
                return

            self._send(
                400,
                b"Malformed authentication request",
                content_type="text/plain; charset=utf-8",
                cookies=cookies,
            )
            return

        if path in WISHLIST_POST_PATHS:
            if not self._auth_post_origin_is_safe():
                self._send(
                    403,
                    b"Forbidden",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            if query:
                self._send(
                    400,
                    b"Malformed Wishlist request",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            if self.store.account_for_session(session_digest) is None:
                location = "/ap/signin?" + urlencode(
                    {"openid.return_to": wishlist_views.WISHLIST_INDEX_PATH}
                )
                self._send(
                    303, b"", headers={"Location": location}, cookies=cookies
                )
                return
            fields = self._form_fields(raw_body)
            valid_shape = False
            selected_options: dict[str, str] = {}
            if fields is not None:
                expected_fields = {
                    wishlist_views.WISHLIST_CREATE_PATH: {"listName"},
                    wishlist_views.WISHLIST_RENAME_PATH: {
                        "listID",
                        "listName",
                    },
                    wishlist_views.WISHLIST_DELETE_PATH: {"listID"},
                    wishlist_views.WISHLIST_REMOVE_ITEM_PATH: {
                        "listID",
                        "itemID",
                    },
                    wishlist_views.WISHLIST_MOVE_TO_CART_PATH: {
                        "listID",
                        "itemID",
                        "quantity",
                    },
                }
                if path == wishlist_views.WISHLIST_ADD_ITEM_PATH:
                    base_fields = {"listID", "ASIN"}
                    option_fields = {
                        name
                        for name in fields
                        if name.startswith("option.")
                        and name.removeprefix("option.")
                    }
                    valid_shape = bool(
                        base_fields.issubset(fields)
                        and set(fields) == base_fields | option_fields
                    )
                    if valid_shape:
                        selected_options = {
                            name.removeprefix("option."): fields[name]
                            for name in option_fields
                        }
                else:
                    valid_shape = set(fields) == expected_fields[path]
                    if (
                        path == wishlist_views.WISHLIST_MOVE_TO_CART_PATH
                        and fields.get("quantity") != "1"
                    ):
                        valid_shape = False
            if not valid_shape or fields is None:
                self._send(
                    400,
                    b"Malformed Wishlist request",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return

            try:
                if path == wishlist_views.WISHLIST_CREATE_PATH:
                    created_list = wishlist.create_list(
                        self.store, session_digest, fields["listName"]
                    )
                    location = wishlist_views.WISHLIST_INDEX_PATH + "?" + urlencode(
                        {
                            "listID": created_list["list_id"],
                            "status": "created",
                        }
                    )
                elif path == wishlist_views.WISHLIST_RENAME_PATH:
                    renamed_list = wishlist.rename_list(
                        self.store,
                        session_digest,
                        fields["listID"],
                        fields["listName"],
                    )
                    location = wishlist_views.WISHLIST_INDEX_PATH + "?" + urlencode(
                        {
                            "listID": renamed_list["list_id"],
                            "status": "renamed",
                        }
                    )
                elif path == wishlist_views.WISHLIST_DELETE_PATH:
                    wishlist.delete_list(
                        self.store, session_digest, fields["listID"]
                    )
                    location = wishlist_views.WISHLIST_INDEX_PATH + "?" + urlencode(
                        {"status": "deleted"}
                    )
                elif path == wishlist_views.WISHLIST_ADD_ITEM_PATH:
                    result = wishlist.add_item(
                        self.store,
                        session_digest,
                        fields["listID"],
                        fields["ASIN"],
                        selected_options,
                    )
                    location = wishlist_views.WISHLIST_INDEX_PATH + "?" + urlencode(
                        {
                            "listID": fields["listID"],
                            "status": (
                                "added" if result["created"] else "already-added"
                            ),
                        }
                    )
                elif path == wishlist_views.WISHLIST_REMOVE_ITEM_PATH:
                    wishlist.remove_item(
                        self.store,
                        session_digest,
                        fields["listID"],
                        fields["itemID"],
                    )
                    location = wishlist_views.WISHLIST_INDEX_PATH + "?" + urlencode(
                        {"listID": fields["listID"], "status": "removed"}
                    )
                else:
                    item = wishlist.item_for_move_to_cart(
                        self.store,
                        session_digest,
                        fields["listID"],
                        fields["itemID"],
                    )
                    self.store.add_cart_item(
                        session_digest,
                        item["asin"],
                        fields["quantity"],
                        item["selected_options"],
                    )
                    try:
                        wishlist.remove_item(
                            self.store,
                            session_digest,
                            item["list_id"],
                            item["item_id"],
                        )
                    except wishlist.WishlistNotFound:
                        # The cart mutation already succeeded.  A concurrent
                        # removal must not turn that success into a retry that
                        # can double the quantity.
                        pass
                    location = "/gp/cart/view.html"
            except wishlist.WishlistAuthenticationRequired:
                location = "/ap/signin?" + urlencode(
                    {"openid.return_to": wishlist_views.WISHLIST_INDEX_PATH}
                )
            except wishlist.WishlistNotFound:
                self._send_html(
                    404,
                    views.not_found_page(self.store.cart_count(session_digest)),
                    cookies=cookies,
                )
                return
            except wishlist.WishlistValidationError as exc:
                self._send_html(
                    400,
                    views.error_page(
                        str(exc), self.store.cart_count(session_digest), 400
                    ),
                    cookies=cookies,
                )
                return
            except (wishlist.WishlistConflict, ContractError) as exc:
                self._send_html(
                    409,
                    views.error_page(
                        str(exc), self.store.cart_count(session_digest), 409
                    ),
                    cookies=cookies,
                )
                return
            self._send(
                303, b"", headers={"Location": location}, cookies=cookies
            )
            return

        if path in ADDRESS_BOOK_POST_PATHS:
            if not self._auth_post_origin_is_safe():
                self._send(
                    403,
                    b"Forbidden",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            account = self.store.account_for_session(session_digest)
            if account is None:
                location = "/ap/signin?" + urlencode(
                    {"openid.return_to": ADDRESS_BOOK_PATH}
                )
                self._send(303, b"", headers={"Location": location}, cookies=cookies)
                return
            fields = self._form_fields(raw_body)
            make_default = False
            address: dict[str, str] | None = None
            if path == ADDRESS_CREATE_PATH:
                allowed = ADDRESS_FORM_NAMES | {"makeDefault"}
                valid_shape = bool(
                    fields is not None
                    and ADDRESS_FORM_NAMES.issubset(fields)
                    and set(fields).issubset(allowed)
                )
            elif path == ADDRESS_UPDATE_PATH:
                allowed = ADDRESS_FORM_NAMES | {
                    "addressId",
                    "addressRevision",
                    "makeDefault",
                }
                valid_shape = bool(
                    fields is not None
                    and (ADDRESS_FORM_NAMES | {"addressId", "addressRevision"}).issubset(fields)
                    and set(fields).issubset(allowed)
                )
            else:
                valid_shape = bool(
                    fields is not None
                    and set(fields) == {"addressId", "addressRevision"}
                )
            if not valid_shape or (
                fields is not None
                and "makeDefault" in fields
                and fields["makeDefault"] != "1"
            ):
                self._send(
                    400,
                    b"Malformed address request",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            assert fields is not None
            if path in {ADDRESS_CREATE_PATH, ADDRESS_UPDATE_PATH}:
                address = normalized_address_fields(fields)
                if address is None:
                    self._send(
                        400,
                        b"Invalid address",
                        content_type="text/plain; charset=utf-8",
                        cookies=cookies,
                    )
                    return
                make_default = fields.get("makeDefault") == "1"
            try:
                if path == ADDRESS_CREATE_PATH:
                    assert address is not None
                    self.store.create_address(
                        session_digest,
                        address,
                        make_default=make_default,
                    )
                    status_label = "added"
                elif path == ADDRESS_UPDATE_PATH:
                    assert address is not None
                    self.store.update_address(
                        session_digest,
                        fields["addressId"],
                        fields["addressRevision"],
                        address,
                        make_default=make_default,
                    )
                    status_label = "updated"
                elif path == ADDRESS_DEFAULT_PATH:
                    self.store.set_default_address(
                        session_digest,
                        fields["addressId"],
                        fields["addressRevision"],
                    )
                    status_label = "default"
                else:
                    self.store.delete_address(
                        session_digest,
                        fields["addressId"],
                        fields["addressRevision"],
                    )
                    status_label = "deleted"
            except AddressNotFound:
                self._send(
                    404,
                    b"Not Found",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            except (AddressRevisionConflict, AddressInUse) as exc:
                message = (
                    "This address is currently selected by an active checkout. Choose another checkout address before deleting it."
                    if isinstance(exc, AddressInUse)
                    else "This address changed in another request. Review the latest version and try again."
                )
                self._send_html(
                    409,
                    views.address_book_page(
                        self.store.addresses_for_session(session_digest),
                        self.store.cart_count(session_digest),
                        str(account["display_name"]),
                        error=message,
                    ),
                    cookies=cookies,
                )
                return
            except ContractError:
                self._send(
                    400,
                    b"Invalid address request",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            self._send(
                303,
                b"",
                headers={
                    "Location": ADDRESS_BOOK_PATH
                    + "?"
                    + urlencode({"status": status_label})
                },
                cookies=cookies,
            )
            return

        if path in {ORDER_CANCEL_PATH, RETURN_CREATE_PATH}:
            if not self._auth_post_origin_is_safe():
                self._send(
                    403,
                    b"Forbidden",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            account = self.store.account_for_session(session_digest)
            if account is None:
                location = "/ap/signin?" + urlencode(
                    {"openid.return_to": "/gp/css/order-history"}
                )
                self._send(303, b"", headers={"Location": location}, cookies=cookies)
                return
            fields = self._form_fields(raw_body)
            required = (
                {"orderID", "idempotencyKey", "actionToken"}
                if path == ORDER_CANCEL_PATH
                else {
                    "orderID",
                    "reasonCode",
                    "customerNote",
                    "idempotencyKey",
                    "actionToken",
                }
            )
            if (
                fields is None
                or set(fields) != required
                or re.fullmatch(r"[1-9][0-9]{0,18}", fields.get("orderID", ""))
                is None
                or re.fullmatch(
                    r"[A-Za-z0-9_-]{20,128}", fields.get("idempotencyKey", "")
                )
                is None
                or re.fullmatch(r"[0-9a-f]{64}", fields.get("actionToken", ""))
                is None
            ):
                self._send(
                    400,
                    b"Malformed order action",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            try:
                if path == ORDER_CANCEL_PATH:
                    order = self.store.cancel_order(
                        session_digest,
                        fields["orderID"],
                        fields["idempotencyKey"],
                        fields["actionToken"],
                    )
                    location = ORDER_DETAIL_PATH + "?" + urlencode(
                        {"orderID": order["order_id"]}
                    )
                else:
                    order = self.store.create_return_request(
                        session_digest,
                        fields["orderID"],
                        fields["reasonCode"],
                        fields["customerNote"],
                        fields["idempotencyKey"],
                        fields["actionToken"],
                    )
                    request_payload = order.get("return_request")
                    if not isinstance(request_payload, dict):
                        raise OrderStateConflict("return request was not created")
                    location = RETURN_DETAIL_PATH + "?" + urlencode(
                        {"returnID": request_payload["return_request_id"]}
                    )
            except OrderActionTokenInvalid:
                self._send(
                    403,
                    b"Forbidden",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            except OrderNotFound:
                self._send(
                    404,
                    b"Not Found",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            except OrderStateConflict as exc:
                self._send_html(
                    409,
                    views.error_page(str(exc), self.store.cart_count(session_digest), 409),
                    cookies=cookies,
                )
                return
            except ContractError as exc:
                self._send(
                    400,
                    str(exc).encode("utf-8"),
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            self._send(303, b"", headers={"Location": location}, cookies=cookies)
            return

        review_post_match = PRODUCT_REVIEWS_ROUTE.fullmatch(path)
        helpful_post_match = PRODUCT_REVIEW_HELPFUL_ROUTE.fullmatch(path)
        if review_post_match or helpful_post_match:
            if not self._auth_post_origin_is_safe():
                self._send(
                    403,
                    b"Forbidden",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            asin = (review_post_match or helpful_post_match).group(1)
            product = product_for_pdp(self.store, asin)
            source_scope = review_product_source_scope(self.store, asin)
            if product is None or source_scope is None:
                self._send(
                    404,
                    b"Not Found",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            self.store.register_review_product(asin, source_scope)
            fields = self._form_fields(raw_body)
            destination = f"/product-reviews/{asin}#customerReviews"

            if review_post_match:
                if self.store.account_for_session(session_digest) is None:
                    signin_location = "/ap/signin?" + urlencode(
                        {"openid.return_to": f"/product-reviews/{asin}#reviewComposer"}
                    )
                    self._send(
                        303,
                        b"",
                        headers={"Location": signin_location},
                        cookies=cookies,
                    )
                    return
                if (
                    fields is None
                    or set(fields) != {"rating", "headline", "body"}
                    or re.fullmatch(r"[1-5]", fields.get("rating", "")) is None
                ):
                    self._send(
                        400,
                        b"Malformed review request",
                        content_type="text/plain; charset=utf-8",
                        cookies=cookies,
                    )
                    return
                try:
                    self.store.upsert_review(
                        session_digest,
                        asin,
                        int(fields["rating"]),
                        fields["headline"],
                        fields["body"],
                    )
                except ReviewAuthenticationRequired:
                    signin_location = "/ap/signin?" + urlencode(
                        {"openid.return_to": f"/product-reviews/{asin}#reviewComposer"}
                    )
                    self._send(
                        303,
                        b"",
                        headers={"Location": signin_location},
                        cookies=cookies,
                    )
                    return
                except ReviewValidationError as exc:
                    self._send(
                        400,
                        str(exc).encode("utf-8"),
                        content_type="text/plain; charset=utf-8",
                        cookies=cookies,
                    )
                    return
                except ReviewPermissionDenied:
                    self._send(
                        403,
                        b"Forbidden",
                        content_type="text/plain; charset=utf-8",
                        cookies=cookies,
                    )
                    return
                except ReviewNotFound:
                    self._send(
                        404,
                        b"Not Found",
                        content_type="text/plain; charset=utf-8",
                        cookies=cookies,
                    )
                    return
            else:
                if (
                    fields is None
                    or set(fields) != {"reviewId"}
                    or re.fullmatch(r"[1-9][0-9]*", fields.get("reviewId", "")) is None
                ):
                    self._send(
                        400,
                        b"Malformed helpful request",
                        content_type="text/plain; charset=utf-8",
                        cookies=cookies,
                    )
                    return
                try:
                    self.store.toggle_review_helpful(
                        session_digest, asin, fields["reviewId"]
                    )
                except ReviewValidationError as exc:
                    self._send(
                        400,
                        str(exc).encode("utf-8"),
                        content_type="text/plain; charset=utf-8",
                        cookies=cookies,
                    )
                    return
                except ReviewPermissionDenied:
                    self._send(
                        403,
                        b"You cannot mark your own review as helpful.",
                        content_type="text/plain; charset=utf-8",
                        cookies=cookies,
                    )
                    return
                except ReviewNotFound:
                    self._send(
                        404,
                        b"Not Found",
                        content_type="text/plain; charset=utf-8",
                        cookies=cookies,
                    )
                    return

            self._send(
                303,
                b"",
                headers={"Location": destination},
                cookies=cookies,
            )
            return

        if path == ORDER_EMAIL_RETRY_PATH:
            if not self._auth_post_origin_is_safe():
                self._send(
                    403,
                    b"Forbidden",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            fields = self._form_fields(raw_body)
            if (
                query
                or fields is None
                or set(fields) != {"orderID"}
                or re.fullmatch(r"[1-9][0-9]*", fields["orderID"]) is None
            ):
                self._send(
                    400,
                    b"Malformed order email retry request",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            if self.store.account_for_session(session_digest) is None:
                location = "/ap/signin?" + urlencode(
                    {"openid.return_to": ORDER_DETAIL_PATH}
                )
                self._send(
                    303, b"", headers={"Location": location}, cookies=cookies
                )
                return
            order_id = fields["orderID"]
            if self.store.order_for_session(session_digest, order_id) is None:
                self._send(
                    404,
                    b"Not Found",
                    content_type="text/plain; charset=utf-8",
                    cookies=cookies,
                )
                return
            if (
                self.smtp_config is not None
                and self.store.retry_order_mail(session_digest, order_id)
            ):
                self._dispatch_mail(self.store.order_mail_delivery(order_id))
            location = ORDER_DETAIL_PATH + "?" + urlencode({"orderID": order_id})
            self._send(
                303, b"", headers={"Location": location}, cookies=cookies
            )
            return

        checkout_post_paths = {
            CHECKOUT_START_PATH,
            CHECKOUT_ADDRESS_PATH,
            CHECKOUT_DELIVERY_PATH,
            CHECKOUT_PAYMENT_PATH,
            PLACE_ORDER_PATH,
        }
        if path in checkout_post_paths:
            # Preserve the frozen benchmark's explicit nonterminal rejection.
            if path == CHECKOUT_START_PATH and raw_body:
                self.store.record_rejected_post(
                    session_digest,
                    path,
                    self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower(),
                    raw_body,
                )
                self._send(404, b"Not Found", content_type="text/plain; charset=utf-8", cookies=cookies)
                return
            if not self._auth_post_origin_is_safe():
                self._send(403, b"Forbidden", content_type="text/plain; charset=utf-8", cookies=cookies)
                return
            account = self.store.account_for_session(session_digest)
            if account is None:
                location = "/ap/signin?" + urlencode(
                    {"openid.return_to": CHECKOUT_ADDRESS_PATH}
                )
                self._send(303, b"", headers={"Location": location}, cookies=cookies)
                return
            fields = self._form_fields(raw_body)

            if path == CHECKOUT_START_PATH:
                if fields != {}:
                    self._send(400, b"Malformed checkout request", content_type="text/plain; charset=utf-8", cookies=cookies)
                    return
                try:
                    checkout = self.store.start_checkout(session_digest)
                except ContractError:
                    self._send_html(
                        409,
                        views.error_page(
                            "Your cart is empty or checkout cannot be started.",
                            self.store.cart_count(session_digest),
                            409,
                        ),
                        cookies=cookies,
                    )
                    return
                destination = CHECKOUT_ADDRESS_PATH
                if (
                    checkout.get("reconciliation_reason") == "cart-changed"
                    and checkout.get("status") == "DELIVERY_SELECTED"
                ):
                    destination = CHECKOUT_PAYMENT_PATH + "?" + urlencode(
                        {"notice": "cart-changed"}
                    )
                self._send(
                    303,
                    b"",
                    headers={"Location": destination},
                    cookies=cookies,
                )
                return

            if path == CHECKOUT_ADDRESS_PATH:
                if fields is not None and set(fields) == {"addressSelection"}:
                    selection_match = re.fullmatch(
                        r"([1-9][0-9]*):([1-9][0-9]*)",
                        fields["addressSelection"],
                    )
                    if selection_match is None:
                        self._send(
                            400,
                            b"Malformed address selection",
                            content_type="text/plain; charset=utf-8",
                            cookies=cookies,
                        )
                        return
                    try:
                        self.store.select_checkout_address(
                            session_digest,
                            selection_match.group(1),
                            selection_match.group(2),
                        )
                    except ContractError:
                        self._send(
                            409,
                            b"Checkout address conflict",
                            content_type="text/plain; charset=utf-8",
                            cookies=cookies,
                        )
                        return
                    self._send(
                        303,
                        b"",
                        headers={"Location": CHECKOUT_DELIVERY_PATH},
                        cookies=cookies,
                    )
                    return
                allowed = ADDRESS_FORM_NAMES | {"makeDefault"}
                if (
                    fields is None
                    or not ADDRESS_FORM_NAMES.issubset(fields)
                    or not set(fields).issubset(allowed)
                    or (
                        "makeDefault" in fields
                        and fields["makeDefault"] != "1"
                    )
                ):
                    self._send(400, b"Malformed address", content_type="text/plain; charset=utf-8", cookies=cookies)
                    return
                address = normalized_address_fields(fields)
                if address is None:
                    self._send(400, b"Invalid address", content_type="text/plain; charset=utf-8", cookies=cookies)
                    return
                try:
                    self.store.save_checkout_address(
                        session_digest,
                        address,
                        make_default=fields.get("makeDefault") == "1",
                    )
                except ContractError:
                    self._send(409, b"Checkout state conflict", content_type="text/plain; charset=utf-8", cookies=cookies)
                    return
                self._send(303, b"", headers={"Location": CHECKOUT_DELIVERY_PATH}, cookies=cookies)
                return

            if path == CHECKOUT_DELIVERY_PATH:
                if fields is None or set(fields) != {"deliveryOption"}:
                    self._send(400, b"Malformed delivery option", content_type="text/plain; charset=utf-8", cookies=cookies)
                    return
                try:
                    self.store.select_delivery(session_digest, fields["deliveryOption"])
                except ContractError:
                    self._send(409, b"Checkout state conflict", content_type="text/plain; charset=utf-8", cookies=cookies)
                    return
                self._send(303, b"", headers={"Location": CHECKOUT_PAYMENT_PATH}, cookies=cookies)
                return

            if path == CHECKOUT_PAYMENT_PATH:
                if fields is None or set(fields) != {"paymentMethod"}:
                    self._send(400, b"Malformed payment method", content_type="text/plain; charset=utf-8", cookies=cookies)
                    return
                try:
                    checkout = self.store.select_test_payment(
                        session_digest, fields["paymentMethod"]
                    )
                except ContractError:
                    self._send(409, b"Checkout state conflict", content_type="text/plain; charset=utf-8", cookies=cookies)
                    return
                payment_attempt = checkout.get("payment_attempt")
                destination = (
                    CHECKOUT_PAYMENT_PATH
                    + "?"
                    + urlencode({"notice": "payment-declined"})
                    if isinstance(payment_attempt, dict)
                    and payment_attempt.get("status") == "DECLINED"
                    else CHECKOUT_START_PATH
                )
                self._send(
                    303,
                    b"",
                    headers={"Location": destination},
                    cookies=cookies,
                )
                return

            if fields is None or set(fields) != {"idempotencyKey"} or not re.fullmatch(
                r"[A-Za-z0-9_-]{20,160}", fields.get("idempotencyKey", "")
            ):
                self._send(400, b"Malformed place-order request", content_type="text/plain; charset=utf-8", cookies=cookies)
                return
            try:
                order = self.store.place_order(
                    session_digest,
                    fields["idempotencyKey"],
                    mail_mode=self._mail_mode(),
                )
            except CheckoutReconciliationRequired as exc:
                destination = (
                    CHECKOUT_ADDRESS_PATH
                    if exc.reason == "unsupported-delivery-country"
                    else CHECKOUT_PAYMENT_PATH
                ) + "?" + urlencode({"notice": exc.reason})
                self._send(
                    303,
                    b"",
                    headers={"Location": destination},
                    cookies=cookies,
                )
                return
            except ContractError:
                self._send(409, b"Checkout state conflict", content_type="text/plain; charset=utf-8", cookies=cookies)
                return
            self._dispatch_mail(
                self.store.order_mail_delivery(order["order_id"])
            )
            location = ORDER_DETAIL_PATH + "?" + urlencode({"orderID": order["order_id"]})
            self._send(303, b"", headers={"Location": location}, cookies=cookies)
            return

        if path in COMPARE_MUTATION_PATHS:
            if not self._auth_post_origin_is_safe():
                self._send(403, b"Forbidden", content_type="text/plain; charset=utf-8", cookies=cookies)
                return
            fields = self._form_fields(raw_body)
            if path == "/gp/compare/clear":
                if fields != {}:
                    self._send(400, b"Malformed compare request", content_type="text/plain; charset=utf-8", cookies=cookies)
                    return
                self.store.clear_compare(session_digest)
                self._send(303, b"", headers={"Location": COMPARE_PATH}, cookies=cookies)
                return
            if path == "/gp/compare/remove":
                if fields is None or set(fields) != {"compareLineID"}:
                    self._send(400, b"Malformed compare request", content_type="text/plain; charset=utf-8", cookies=cookies)
                    return
                compare_line_id = fields["compareLineID"]
                if COMPARE_LINE_ID_PATTERN.fullmatch(compare_line_id) is None:
                    self._send(400, b"Malformed compare request", content_type="text/plain; charset=utf-8", cookies=cookies)
                    return
                if not self.store.remove_compare(session_digest, compare_line_id):
                    self._send(409, b"Compare item not found", content_type="text/plain; charset=utf-8", cookies=cookies)
                    return
                self._send(303, b"", headers={"Location": COMPARE_PATH}, cookies=cookies)
                return

            option_field_names = {
                name for name in fields or {} if name.startswith("option.")
            }
            if (
                fields is None
                or "ASIN" not in fields
                or set(fields) != {"ASIN"} | option_field_names
                or any(not name.removeprefix("option.") for name in option_field_names)
            ):
                self._send(400, b"Malformed compare request", content_type="text/plain; charset=utf-8", cookies=cookies)
                return
            asin = fields["ASIN"]
            if ASIN_PATTERN.fullmatch(asin) is None:
                self._send(400, b"Malformed compare request", content_type="text/plain; charset=utf-8", cookies=cookies)
                return
            if asin not in self.store.compare_eligible_asins():
                self._send(404, b"Not Found", content_type="text/plain; charset=utf-8", cookies=cookies)
                return
            expected_option_names = {
                f"option.{group['label']}"
                for group in self.store.product_option_spec(asin)
            }
            if option_field_names and option_field_names != expected_option_names:
                self._send(400, b"Malformed compare options", content_type="text/plain; charset=utf-8", cookies=cookies)
                return
            selected_options = (
                {
                    name.removeprefix("option."): fields[name]
                    for name in option_field_names
                }
                if option_field_names
                else None
            )
            try:
                result = self.store.add_compare(
                    session_digest, asin, selected_options
                )
            except ContractError:
                self._send(400, b"Invalid compare options", content_type="text/plain; charset=utf-8", cookies=cookies)
                return
            if result in {"full", "incompatible"}:
                products = [
                    compare_product_for_view(self.store, item)
                    for item in self.store.compare_items(session_digest)
                ]
                account = self.store.account_for_session(session_digest)
                name = str(account["display_name"]) if account else None
                message = (
                    "You can compare up to four products at a time."
                    if result == "full"
                    else "Choose products from the same source-backed product family."
                )
                self._send_html(
                    409,
                    views.compare_page(
                        products,
                        self.store.cart_count(session_digest),
                        name,
                        message,
                    ),
                    cookies=cookies,
                )
                return
            self._send(303, b"", headers={"Location": COMPARE_PATH}, cookies=cookies)
            return

        if path in CART_MUTATION_PATHS:
            if not self._auth_post_origin_is_safe():
                self._send(403, b"Forbidden", content_type="text/plain; charset=utf-8", cookies=cookies)
                return
            fields = self._form_fields(raw_body)
            is_product_mutation = path in {BUY_NOW_PATH, "/gp/cart/add.html"}
            needs_quantity = path in {
                BUY_NOW_PATH,
                "/gp/cart/add.html",
                "/gp/cart/update.html",
            }
            if is_product_mutation:
                base_names = {"ASIN", "quantity"}
            elif needs_quantity:
                base_names = {"lineID", "quantity"}
            else:
                base_names = {"lineID"}
            option_field_names = (
                {name for name in fields or {} if name.startswith("option.")}
                if is_product_mutation
                else set()
            )
            if fields is None or set(fields) != base_names | option_field_names:
                self._send_html(
                    400,
                    views.error_page(
                        "The cart request was incomplete or malformed.",
                        self.store.cart_count(session_digest),
                        400,
                    ),
                    cookies=cookies,
                )
                return
            asin = ""
            line_id = ""
            selected_options: dict[str, str] | None = None
            if is_product_mutation:
                asin = fields["ASIN"]
                if ASIN_PATTERN.fullmatch(asin) is None:
                    self._send_html(
                        400,
                        views.error_page(
                            "The selected item identifier is invalid.",
                            self.store.cart_count(session_digest),
                            400,
                        ),
                        cookies=cookies,
                    )
                    return
                if self.store.commerce_offer(asin) is None:
                    self._send_html(
                        404,
                        views.error_page(
                            "That item has no verified offer in this local marketplace snapshot.",
                            self.store.cart_count(session_digest),
                            404,
                        ),
                        cookies=cookies,
                    )
                    return
                option_spec = self.store.product_option_spec(asin)
                expected_option_names = {
                    f"option.{group['label']}" for group in option_spec
                }
                if option_field_names and option_field_names != expected_option_names:
                    self._send_html(
                        400,
                        views.error_page(
                            "Choose one captured value for every available product option.",
                            self.store.cart_count(session_digest),
                            400,
                        ),
                        cookies=cookies,
                    )
                    return
                selected_options = (
                    {
                        name.removeprefix("option."): fields[name]
                        for name in option_field_names
                    }
                    if option_field_names
                    else None
                )
            else:
                line_id = fields["lineID"]
                if CART_LINE_ID_PATTERN.fullmatch(line_id) is None:
                    self._send_html(
                        400,
                        views.error_page(
                            "The cart line identity is invalid.",
                            self.store.cart_count(session_digest),
                            400,
                        ),
                        cookies=cookies,
                    )
                    return
            quantity = 1
            if needs_quantity:
                raw_quantity = fields["quantity"]
                if not re.fullmatch(r"(?:[1-9]|[12][0-9]|30)", raw_quantity):
                    self._send_html(
                        400,
                        views.error_page(
                            "Choose a quantity from 1 through 30.",
                            self.store.cart_count(session_digest),
                            400,
                        ),
                        cookies=cookies,
                    )
                    return
                quantity = int(raw_quantity)
            try:
                buy_now_result: dict[str, Any] | None = None
                if path == BUY_NOW_PATH:
                    buy_now_result = self.store.begin_buy_now(
                        session_digest,
                        asin,
                        quantity,
                        selected_options=selected_options,
                    )
                    changed = True
                elif path == "/gp/cart/add.html":
                    changed = bool(
                        self.store.add_cart_item(
                            session_digest,
                            asin,
                            quantity,
                            selected_options=selected_options,
                        )
                    )
                elif path == "/gp/cart/update.html":
                    changed = self.store.set_cart_quantity(
                        session_digest, line_id, quantity
                    )
                elif path == "/gp/cart/delete.html":
                    changed = self.store.delete_cart_item(session_digest, line_id)
                elif path == "/gp/cart/save-for-later.html":
                    changed = self.store.save_for_later(session_digest, line_id)
                else:
                    changed = self.store.move_to_cart(session_digest, line_id)
            except ContractError as exc:
                message = (
                    UNAVAILABLE_SELECTION_COPY
                    if str(exc) == UNAVAILABLE_SELECTION_COPY
                    else "That item cannot be added to this local marketplace cart."
                )
                self._send_html(
                    400,
                    views.error_page(
                        message,
                        self.store.cart_count(session_digest),
                        400,
                    ),
                    cookies=cookies,
                )
                return
            if not changed:
                self._send_html(
                    409,
                    views.error_page(
                        "That cart item is no longer in the expected state.",
                        self.store.cart_count(session_digest),
                        409,
                    ),
                    cookies=cookies,
                )
                return
            if path == BUY_NOW_PATH:
                if buy_now_result and buy_now_result.get(
                    "requires_authentication"
                ):
                    location = "/ap/signin?" + urlencode(
                        {"openid.return_to": BUY_NOW_CONTINUE_PATH}
                    )
                else:
                    location = CHECKOUT_ADDRESS_PATH
                self._send(
                    303,
                    b"",
                    headers={"Location": location},
                    cookies=cookies,
                )
                return
            self._send(
                303,
                b"",
                headers={"Location": "/gp/cart/view.html"},
                cookies=cookies,
            )
            return
        if path.startswith("/ap/"):
            self._send(404, b"Not Found", content_type="text/plain; charset=utf-8", cookies=cookies)
            return
        if path.startswith("/__bench/") or path not in TERMINAL_PATHS:
            self.store.record_rejected_post(
                session_digest,
                path,
                self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower(),
                raw_body,
            )
            self._send(404, b"Not Found", content_type="text/plain; charset=utf-8", cookies=cookies)
            return
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        try:
            fields = parse_qsl(raw_body.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
        except (UnicodeDecodeError, ValueError):
            fields = []

        status, outcome = self.store.terminal_request(
            session_digest,
            path,
            media_type,
            raw_body,
            fields,
            self._flow_digest(),
            self._terminal_source_is_canonical(),
        )
        if status == 303:
            cookies.append(browser_cookie(FLOW_COOKIE, "", max_age=0))
            self._send(
                303,
                b"",
                headers={"Location": "/gp/cart/view.html"},
                cookies=cookies,
            )
            return

        message = {
            "wrong-form-body": "Choose quantity 2 and submit the Amazon add-to-cart form once.",
            "missing-navigation-sequence": "Open the External SSD Best Sellers list before this item.",
            "wrong-navigation-stage": "Follow the Best Sellers → Samsung T7 journey in the same browser session.",
            "invalid-flow-capability": "The product-page journey expired. Open the item from Best Sellers again.",
            "invalid-terminal-source": "Submit the Add to cart form from the current Samsung T7 product page.",
            "stale-navigation-sequence": "Return to the Samsung T7 product page before adding it to the cart.",
            "capability-already-consumed": "This add-to-cart action was already completed.",
            "target-line-already-exists": "The target item is already in this cart.",
        }.get(outcome, f"The add-to-cart request did not match the local contract ({outcome}).")
        self._send_html(
            409,
            views.error_page(message, self.store.cart_count(session_digest), 409),
            cookies=cookies,
        )


class AdminHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    store: Store
    admin_token: str
    smtp_summary: dict[str, object] = {"mode": "LOCAL_ONLY"}
    server_version = "AmazonCloneAdmin/0.1"

    def log_message(self, format_string: str, *args: Any) -> None:
        sys.stdout.write(
            json.dumps(
                {
                    "stream": "admin",
                    "client": self.client_address[0],
                    "method": self.command,
                    "path": self.path,
                    "message": format_string % args,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        sys.stdout.flush()

    def _authorized(self) -> bool:
        candidate = self.headers.get("X-Bench-Admin-Token", "")
        return bool(candidate) and secrets.compare_digest(candidate, self.admin_token)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _not_found(self) -> None:
        self._send_json(404, {"error": "not-found"})

    def _payload(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length < 0 or content_length > MAX_ADMIN_BYTES:
            raise ContractError("admin payload is too large")
        if content_length == 0:
            return {}
        raw = self.rfile.read(content_length)

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ContractError(f"duplicate admin field: {key}")
                result[key] = value
            return result

        def reject_constant(value: str) -> None:
            raise ContractError(f"invalid JSON constant: {value}")

        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
        if not isinstance(payload, dict):
            raise ContractError("admin payload must be an object")
        return payload

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self._authorized():
            self._not_found()
            return
        path = urlsplit(self.path).path
        if path == "/__bench/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "schema": "amazon-clone.admin.v1",
                    "snapshot_id": self.store.meta().get("snapshot_id"),
                    "mail_transport": self.smtp_summary,
                    "mail_delivery_status": self.store.mail_delivery_health(),
                },
            )
        elif path == "/__bench/state":
            self._send_json(200, self.store.normalized_state())
        elif path == "/__bench/journal":
            self._send_json(200, {"schema": "amazon-clone.journal.v1", "requests": self.store.journal()})
        elif path == "/__bench/auth/registration-outbox":
            self._send_json(
                200,
                {
                    "schema": "amazon-clone.registration-outbox.v1",
                    "delivery": self.smtp_summary["mode"],
                    "messages": self.store.registration_outbox(),
                },
            )
        elif path == "/__bench/auth/password-reset-outbox":
            self._send_json(
                200,
                {
                    "schema": "amazon-clone.password-reset-outbox.v1",
                    "delivery": self.smtp_summary["mode"],
                    "messages": self.store.password_reset_outbox(),
                },
            )
        elif path == "/__bench/mail/outbox":
            self._send_json(
                200,
                {
                    "schema": "amazon-clone.mail-outbox.v1",
                    "delivery": self.smtp_summary["mode"],
                    "messages": self.store.mail_delivery_outbox(),
                },
            )
        else:
            self._not_found()

    def do_POST(self) -> None:
        if not self._authorized():
            self._not_found()
            return
        path = urlsplit(self.path).path
        try:
            payload = self._payload()
            if path == "/__bench/reset":
                unknown = set(payload) - {"fixture"}
                if unknown:
                    raise ContractError(f"unsupported reset fields: {sorted(unknown)}")
                state = self.store.reset(payload.get("fixture", "task-frozen-900136-v1.json"))
                self._send_json(200, state)
            elif path == "/__bench/clock/advance":
                unknown = set(payload) - {"seconds"}
                if unknown:
                    raise ContractError(f"unsupported clock fields: {sorted(unknown)}")
                now = self.store.advance_clock(int(payload.get("seconds", 0)))
                self._send_json(200, {"controlled_now": now})
            elif path == "/__bench/orders/advance":
                if set(payload) != {"orderID", "targetStatus"}:
                    raise ContractError(
                        "order advancement requires exactly orderID and targetStatus"
                    )
                order_id = payload["orderID"]
                if (
                    isinstance(order_id, bool)
                    or not isinstance(order_id, (int, str))
                    or re.fullmatch(r"[1-9][0-9]{0,18}", str(order_id)) is None
                    or not isinstance(payload["targetStatus"], str)
                ):
                    raise ContractError("invalid order advancement payload")
                order = self.store.advance_order_shipment(
                    order_id, payload["targetStatus"]
                )
                self._send_json(
                    200,
                    {
                        "schema": "amazon-clone.order-lifecycle.v1",
                        "order": order,
                    },
                )
            elif path == "/__bench/returns/advance":
                if set(payload) != {"returnID", "targetStatus"}:
                    raise ContractError(
                        "return advancement requires exactly returnID and targetStatus"
                    )
                return_id = payload["returnID"]
                if (
                    isinstance(return_id, bool)
                    or not isinstance(return_id, (int, str))
                    or re.fullmatch(r"[1-9][0-9]{0,18}", str(return_id)) is None
                    or not isinstance(payload["targetStatus"], str)
                ):
                    raise ContractError("invalid return advancement payload")
                order = self.store.advance_return_request(
                    return_id, payload["targetStatus"]
                )
                self._send_json(
                    200,
                    {
                        "schema": "amazon-clone.return-lifecycle.v1",
                        "order": order,
                    },
                )
            else:
                self._not_found()
        except (OrderNotFound, ReturnNotFound):
            self._not_found()
        except OrderStateConflict as exc:
            self._send_json(
                409, {"error": "state-conflict", "detail": str(exc)}
            )
        except (ContractError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": "invalid-request", "detail": str(exc)})


def resolve_admin_token(admin_host: str, configured_token: str | None) -> str:
    normalized_host = admin_host.strip().strip("[]")
    try:
        is_loopback = ipaddress.ip_address(normalized_host).is_loopback
    except ValueError:
        is_loopback = normalized_host.casefold() == "localhost"
    token = (configured_token or "").strip()
    if is_loopback:
        return token or "local-amazon-bench"
    if (
        len(token) < 32
        or token == "local-amazon-bench"
        or any(ord(character) < 33 or ord(character) > 126 for character in token)
    ):
        raise ValueError(
            "a non-loopback admin host requires an explicit strong "
            "AMAZON_ADMIN_TOKEN or --admin-token (at least 32 visible characters)"
        )
    return token


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the clean local Amazon benchmark clone")
    parser.add_argument("--host", default=os.environ.get("AMAZON_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8153")))
    parser.add_argument("--admin-host", default=os.environ.get("AMAZON_ADMIN_HOST", "127.0.0.1"))
    parser.add_argument("--admin-port", type=int, default=int(os.environ.get("AMAZON_ADMIN_PORT", "8154")))
    parser.add_argument("--admin-token", default=os.environ.get("AMAZON_ADMIN_TOKEN"))
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get("AMAZON_DB_PATH", str(DEFAULT_DB))),
    )
    parser.add_argument("--fixture", default="task-frozen-900136-v1.json")
    args = parser.parse_args()
    try:
        args.admin_token = resolve_admin_token(args.admin_host, args.admin_token)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main() -> int:
    args = parse_args()
    try:
        public_basic_auth_credentials()
    except ValueError as exc:
        print(json.dumps({"event": "amazon-clone-config-error", "error": str(exc)}))
        return 2
    smtp_config = load_smtp_config()
    local_inbox_url = load_local_inbox_url(smtp_config)
    store = Store(args.db.resolve(), SCHEMA_PATH, FIXTURE_ROOT)
    store.ensure_seeded(args.fixture)
    wishlist.ensure_wishlist_schema(store)

    PublicHandler.store = store
    PublicHandler.smtp_config = smtp_config
    PublicHandler.local_inbox_url = local_inbox_url
    AdminHandler.store = store
    AdminHandler.admin_token = args.admin_token
    AdminHandler.smtp_summary = smtp_public_summary(smtp_config)

    released_mail_claims = 0
    replayed_mail_jobs = 0
    expired_mail_jobs = 0
    exhausted_mail_jobs = 0
    localized_mail_jobs = 0
    if smtp_config is not None:
        expired_mail_jobs = store.expire_stale_pending_auth_mail()
        exhausted_mail_jobs = store.fail_exhausted_pending_mail()
        released_mail_claims = store.recover_pending_mail_claims()
        for delivery in store.pending_mail_deliveries():
            if dispatch_mail_delivery(store, smtp_config, delivery):
                replayed_mail_jobs += 1
    else:
        localized_mail_jobs = store.reconcile_mail_for_local_only()

    public_server = ReusableThreadingHTTPServer((args.host, args.port), PublicHandler)
    admin_server = ReusableThreadingHTTPServer((args.admin_host, args.admin_port), AdminHandler)
    admin_thread = threading.Thread(target=admin_server.serve_forever, name="amazon-clone-admin", daemon=True)
    admin_thread.start()

    stopping = threading.Event()

    def stop_servers(*_: Any) -> None:
        if stopping.is_set():
            return
        stopping.set()
        threading.Thread(target=public_server.shutdown, daemon=True).start()
        threading.Thread(target=admin_server.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, stop_servers)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, stop_servers)

    print(
        json.dumps(
            {
                "event": "amazon-clone-started",
                "public": f"http://{args.host}:{args.port}",
                "admin": f"http://{args.admin_host}:{args.admin_port}",
                "db": str(args.db.resolve()),
                "snapshot": store.meta().get("snapshot_id"),
                "mail_transport": smtp_public_summary(smtp_config),
                "mail_startup_replay": {
                    "expired_auth_jobs": expired_mail_jobs,
                    "exhausted_jobs": exhausted_mail_jobs,
                    "released_claims": released_mail_claims,
                    "dispatched": replayed_mail_jobs,
                    "localized_jobs": localized_mail_jobs,
                },
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        public_server.serve_forever()
    finally:
        public_server.server_close()
        admin_server.shutdown()
        admin_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
