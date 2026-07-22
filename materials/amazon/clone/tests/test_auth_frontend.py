from __future__ import annotations

import http.client
import re
import sys
import tempfile
import threading
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import PublicHandler, ReusableThreadingHTTPServer, digest  # noqa: E402
from store import Store  # noqa: E402


CSS_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)", re.IGNORECASE)
PROTECTED_ROUTES = (
    "/gp/css/homepage.html",
    "/gp/css/order-history",
    "/hz/wishlist/ls",
)


def normalized_text(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


class AuthDocumentParser(HTMLParser):
    """Collect auth semantics without depending on whitespace or attribute order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.body_classes: set[str] = set()
        self.class_tokens: set[str] = set()
        self.ids: set[str] = set()
        self.stylesheets: list[str] = []
        self.forms: list[dict[str, object]] = []
        self.logo_nodes: list[dict[str, str]] = []
        self.h1_parts: list[str] = []
        self.text_parts: list[str] = []
        self._form: dict[str, object] | None = None
        self._in_h1 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        self.class_tokens.update(classes)
        if attributes.get("id"):
            self.ids.add(attributes["id"])
        if tag == "body":
            self.body_classes.update(classes)
        if tag == "link" and "stylesheet" in attributes.get("rel", "").lower().split():
            self.stylesheets.append(attributes.get("href", ""))
        if (
            any("logo" in token.lower() for token in classes)
            or attributes.get("aria-label", "").strip().lower() == "amazon"
        ):
            self.logo_nodes.append(attributes)
        if tag == "form":
            self._form = {"attrs": attributes, "inputs": [], "buttons": []}
            self.forms.append(self._form)
        elif tag == "input" and self._form is not None:
            inputs = self._form["inputs"]
            assert isinstance(inputs, list)
            inputs.append(attributes)
        elif tag == "button" and self._form is not None:
            buttons = self._form["buttons"]
            assert isinstance(buttons, list)
            buttons.append(attributes)
        if tag == "h1":
            self._in_h1 = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._form = None
        elif tag == "h1":
            self._in_h1 = False

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._in_h1:
            self.h1_parts.append(data)

    @property
    def text(self) -> str:
        return normalized_text(self.text_parts)

    @property
    def h1(self) -> str:
        return normalized_text(self.h1_parts)


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class AuthFrontendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = Store(
            Path(self.tempdir.name) / "amazon.sqlite3",
            ROOT / "schema.sql",
            ROOT / "fixtures",
        )
        self.store.reset()
        QuietPublicHandler.store = self.store
        QuietPublicHandler.smtp_config = None
        self.server = ReusableThreadingHTTPServer(("127.0.0.1", 0), QuietPublicHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, list[str]], bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        response_headers: dict[str, list[str]] = {}
        for name, value in response.getheaders():
            response_headers.setdefault(name.lower(), []).append(value)
        payload = response.read()
        connection.close()
        return response.status, response_headers, payload

    def document(
        self, path: str, *, headers: dict[str, str] | None = None
    ) -> tuple[AuthDocumentParser, str]:
        status, _, body = self.request("GET", path, headers=headers)
        self.assertEqual(status, 200, path)
        html = body.decode("utf-8")
        parser = AuthDocumentParser()
        parser.feed(html)
        return parser, html

    def form(
        self,
        parser: AuthDocumentParser,
        action_path: str,
        containing_input: str | None = None,
    ) -> dict[str, object]:
        matches: list[dict[str, object]] = []
        for form in parser.forms:
            attributes = form["attrs"]
            assert isinstance(attributes, dict)
            action = str(attributes.get("action", ""))
            if urlsplit(action).path == action_path:
                if containing_input is not None:
                    names = {
                        field.get("name", "")
                        for field in self.inputs(form)
                    }
                    if containing_input not in names:
                        continue
                matches.append(form)
        self.assertEqual(len(matches), 1, f"expected one form targeting {action_path}")
        return matches[0]

    def assert_post_form(self, form: dict[str, object]) -> None:
        attributes = form["attrs"]
        assert isinstance(attributes, dict)
        self.assertEqual(str(attributes.get("method", "")).lower(), "post")
        action = urlsplit(str(attributes.get("action", "")))
        self.assertEqual(action.scheme, "")
        self.assertEqual(action.netloc, "")
        self.assertTrue(action.path.startswith("/") and not action.path.startswith("//"))

    @staticmethod
    def inputs(form: dict[str, object]) -> list[dict[str, str]]:
        inputs = form["inputs"]
        assert isinstance(inputs, list)
        return inputs

    @staticmethod
    def named_inputs(form: dict[str, object]) -> dict[str, dict[str, str]]:
        return {field.get("name", ""): field for field in AuthFrontendTests.inputs(form)}

    def assert_auth_shell(self, parser: AuthDocumentParser) -> None:
        for forbidden in ("site-header", "site-footer", "nav-top", "nav-search"):
            self.assertNotIn(forbidden, parser.class_tokens)
        for forbidden in ("nav-search-bar-form", "nav-cart-count"):
            self.assertNotIn(forbidden, parser.ids)
        self.assertTrue(parser.logo_nodes, "auth layout needs a semantic Amazon logo node")

    def local_stylesheets(self, parser: AuthDocumentParser) -> list[tuple[str, str]]:
        self.assertTrue(parser.stylesheets, "auth document must link local CSS")
        stylesheets: list[tuple[str, str]] = []
        for href in parser.stylesheets:
            parsed = urlsplit(href)
            self.assertEqual(parsed.scheme, "", href)
            self.assertEqual(parsed.netloc, "", href)
            self.assertTrue(parsed.path.startswith("/static/"), href)
            status, _, body = self.request("GET", href)
            self.assertEqual(status, 200, href)
            stylesheets.append((href, body.decode("utf-8")))
        return stylesheets

    def test_signin_uses_independent_auth_layout_and_local_official_assets(self) -> None:
        parser, html = self.document("/ap/signin")
        self.assert_auth_shell(parser)
        self.assertIn("sign in", parser.h1.lower())

        form = self.form(parser, "/ap/signin")
        self.assert_post_form(form)
        attributes = form["attrs"]
        assert isinstance(attributes, dict)
        self.assertEqual(attributes.get("action"), "/ap/signin")
        email = self.named_inputs(form).get("email")
        self.assertIsNotNone(email)
        assert email is not None
        self.assertEqual(email.get("type", "text").lower(), "text")
        self.assertEqual(email.get("autocomplete", "").lower(), "username")

        stylesheets = self.local_stylesheets(parser)
        css = "\n".join(content for _, content in stylesheets)
        css_lower = css.lower()
        self.assertIn("amazon ember", css_lower)
        self.assertIn("amazon-ember-regular.woff2", css_lower)
        self.assertIn("auth-sprite-retail-1x.png", css_lower)
        self.assertIn("auth-sprite-retail-2x.png", css_lower)
        normalized_css = re.sub(r"\s+", " ", css_lower)
        logo_rule = re.search(r"\.auth-logo\s*\{([^}]*)\}", normalized_css)
        self.assertIsNotNone(logo_rule)
        assert logo_rule is not None
        logo_declarations = logo_rule.group(1)
        self.assertIn("width: 103px", logo_declarations)
        self.assertIn("height: 31px", logo_declarations)
        self.assertIn("background-position: -2px -168px", logo_declarations)
        self.assertIn("background-size: 512px 256px", logo_declarations)
        self.assertNotIn("m.media-amazon.com", html.lower())
        self.assertNotIn("m.media-amazon.com", css_lower)

        asset_refs = [
            (href, reference.strip())
            for href, content in stylesheets
            for reference in CSS_URL_RE.findall(content)
        ]
        font_ref = next(
            ((href, ref) for href, ref in asset_refs if "amazon-ember-regular.woff2" in ref.lower()),
            None,
        )
        sprite_ref = next(
            ((href, ref) for href, ref in asset_refs if "auth-sprite-retail-1x.png" in ref.lower()),
            None,
        )
        self.assertIsNotNone(font_ref)
        self.assertIsNotNone(sprite_ref)
        for asset_ref, signature in ((font_ref, b"wOF2"), (sprite_ref, b"\x89PNG\r\n\x1a\n")):
            assert asset_ref is not None
            stylesheet_href, reference = asset_ref
            resolved = urljoin(stylesheet_href, reference)
            self.assertTrue(urlsplit(resolved).path.startswith("/static/"), resolved)
            status, _, payload = self.request("GET", resolved)
            self.assertEqual(status, 200, resolved)
            self.assertTrue(payload.startswith(signature), resolved)

    def test_register_has_distinct_form_and_ordered_account_fields(self) -> None:
        parser, _ = self.document("/ap/register")
        self.assert_auth_shell(parser)
        self.assertIn("create", parser.h1.lower())
        form = self.form(parser, "/ap/register")
        self.assert_post_form(form)

        fields = self.inputs(form)
        expected_names = ["customerName", "email", "password", "passwordCheck"]
        ordered_names = [
            field.get("name", "") for field in fields if field.get("name") in expected_names
        ]
        self.assertEqual(ordered_names, expected_names)
        named = self.named_inputs(form)
        self.assertEqual(named["customerName"].get("autocomplete", "").lower(), "name")
        self.assertIn(named["email"].get("autocomplete", "").lower(), {"email", "username"})
        for name in ("password", "passwordCheck"):
            self.assertEqual(named[name].get("type", "").lower(), "password")
            self.assertEqual(named[name].get("autocomplete", "").lower(), "new-password")

    def test_password_assistance_and_frontend_only_auth_states_are_reachable(self) -> None:
        cases = (
            ("/ap/forgotpassword", "/ap/forgotpassword", "email", "username", "password"),
            ("/ap/signin?stage=password", "/ap/signin", "password", "current-password", "password"),
            (
                "/ap/cvf/verify?purpose=registration",
                "/ap/cvf/verify",
                "code",
                "one-time-code",
                "verif",
            ),
        )
        for path, form_path, field_name, autocomplete, expected_text in cases:
            with self.subTest(path=path):
                parser, _ = self.document(path)
                self.assert_auth_shell(parser)
                self.assertIn(expected_text, parser.text.lower())
                form = self.form(parser, form_path, field_name)
                self.assert_post_form(form)
                field = self.named_inputs(form).get(field_name)
                self.assertIsNotNone(field, f"{path} needs {field_name}")
                assert field is not None
                self.assertEqual(field.get("autocomplete", "").lower(), autocomplete)
                if autocomplete in {"current-password", "new-password"}:
                    self.assertEqual(field.get("type", "").lower(), "password")

        status, headers, body = self.request(
            "GET", "/ap/forgotpassword?stage=reset-password"
        )
        self.assertEqual(
            (status, headers.get("location"), body),
            (303, ["/ap/forgotpassword"], b""),
        )

        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        session_cookie = headers["set-cookie"][0].split(";", 1)[0]
        session_digest = digest(session_cookie.split("=", 1)[1])
        reset_email = "frontend-reset@example.test"
        self.assertTrue(
            self.store.register_account(
                session_digest,
                reset_email,
                "Frontend Reset",
                "Synthetic-Password-921",
            )
        )
        self.store.begin_password_reset(session_digest, reset_email, None)
        reset_code = self.store.password_reset_outbox(session_digest)[0][
            "verification_code"
        ]
        self.assertEqual(
            self.store.verify_password_reset_code(session_digest, reset_code),
            "verified",
        )
        reset_parser, _ = self.document(
            "/ap/forgotpassword?stage=reset-password",
            headers={"Cookie": session_cookie},
        )
        self.assert_auth_shell(reset_parser)
        reset_form = self.form(reset_parser, "/ap/forgotpassword")
        self.assert_post_form(reset_form)
        reset_names = [
            field.get("name", "")
            for field in self.inputs(reset_form)
            if field.get("name") in {"password", "passwordCheck"}
        ]
        self.assertEqual(reset_names, ["password", "passwordCheck"])

    def test_anonymous_account_routes_redirect_to_signin_with_relative_return_target(self) -> None:
        for protected_path in PROTECTED_ROUTES:
            with self.subTest(path=protected_path):
                status, headers, body = self.request("GET", protected_path)
                self.assertEqual(status, 303)
                self.assertEqual(body, b"")
                locations = headers.get("location", [])
                self.assertEqual(len(locations), 1)
                location = urlsplit(locations[0])
                self.assertEqual(location.scheme, "")
                self.assertEqual(location.netloc, "")
                self.assertEqual(location.path, "/ap/signin")
                return_values = parse_qs(location.query).get("openid.return_to", [])
                self.assertEqual(return_values, [protected_path])
                return_target = urlsplit(return_values[0])
                self.assertEqual(return_target.scheme, "")
                self.assertEqual(return_target.netloc, "")
                self.assertTrue(return_target.path.startswith("/") and not return_target.path.startswith("//"))

    def test_external_return_target_is_not_reflected_or_used_as_a_form_action(self) -> None:
        attacks = (
            "https://evil.example/collect",
            "//evil.example/collect",
            "javascript:alert(1)",
        )
        for attack in attacks:
            with self.subTest(return_to=attack):
                query = urlencode({"openid.return_to": attack})
                parser, html = self.document(f"/ap/signin?{query}")
                lowered = html.lower()
                self.assertNotIn("evil.example", lowered)
                self.assertNotIn("javascript:", lowered)
                form = self.form(parser, "/ap/signin")
                self.assert_post_form(form)
                attributes = form["attrs"]
                assert isinstance(attributes, dict)
                self.assertEqual(attributes.get("action"), "/ap/signin")

    def test_account_lists_flyout_has_live_anonymous_and_signed_in_variants(self) -> None:
        status, headers, anonymous_body = self.request("GET", "/")
        self.assertEqual(status, 200)
        anonymous_html = anonymous_body.decode("utf-8")
        self.assertIn('data-account-menu', anonymous_html)
        self.assertIn('aria-controls="nav-flyout-accountList"', anonymous_html)
        self.assertIn('aria-expanded="false"', anonymous_html)
        self.assertIn('aria-hidden="true"', anonymous_html)
        self.assertIn('class="account-flyout-signin"', anonymous_html)
        self.assertIn("New customer?", anonymous_html)
        self.assertIn('<strong id="nav-all-menu-heading">Hello, sign in</strong>', anonymous_html)
        self.assertIn(
            '<a class="all-menu-row" href="/ap/signin?openid.return_to=%2F">Sign In</a>',
            anonymous_html,
        )
        for path in (
            "/ap/signin?openid.return_to=%2Fgp%2Fcss%2Fhomepage.html",
            "/ap/register",
            "/hz/wishlist/intro",
            "/hz/wishlist/ls",
            "/gp/css/homepage.html",
            "/gp/css/order-history",
        ):
            self.assertIn(f'href="{path}"', anonymous_html)
        self.assertNotIn('class="account-flyout-signed-in"', anonymous_html)

        session_cookie = headers["set-cookie"][0].split(";", 1)[0]
        session_digest = digest(session_cookie.split("=", 1)[1])
        self.assertTrue(
            self.store.register_account(
                session_digest,
                "menu-buyer@example.test",
                "Menu Buyer",
                "Synthetic-Password-921",
            )
        )
        status, _, signed_body = self.request(
            "GET", "/", headers={"Cookie": session_cookie}
        )
        self.assertEqual(status, 200)
        signed_html = signed_body.decode("utf-8")
        self.assertIn('class="account-flyout-signed-in"', signed_html)
        self.assertIn("Welcome, Menu", signed_html)
        self.assertIn('<strong id="nav-all-menu-heading">Hello, Menu</strong>', signed_html)
        self.assertIn(
            '<form method="post" action="/ap/signout"><button class="all-menu-row" type="submit">Sign Out</button></form>',
            signed_html,
        )
        self.assertIn('method="post" action="/ap/signout"', signed_html)
        self.assertNotIn('class="account-flyout-signin"', signed_html)

        status, _, css_body = self.request("GET", "/static/styles.css")
        self.assertEqual(status, 200)
        css = css_body.decode("utf-8")
        self.assertIn(".nav-account-wrap.is-open .account-flyout", css)
        self.assertRegex(css, r"\.site-header\s*\{[^}]*z-index:\s*60")
        self.assertNotIn(".delivery-overlay", css)
        self.assertIn("z-index: 90", css)
        self.assertIn(".account-flyout button:focus-visible", css)

        status, _, js_body = self.request("GET", "/static/app.js")
        self.assertEqual(status, 200)
        javascript = js_body.decode("utf-8")
        for semantic in (
            'addEventListener("mouseenter"',
            'addEventListener("mouseleave"',
            'addEventListener("focusin"',
            'event.key !== "Escape"',
            'setAttribute("aria-expanded"',
            'accountMenuTrigger.focus({ preventScroll: true })',
        ):
            self.assertIn(semantic, javascript)

    def test_registration_verification_page_has_otp_and_resend_forms(self) -> None:
        parser, html = self.document("/ap/cvf/verify?purpose=registration")
        self.assertEqual(parser.h1, "Verify email address")
        verify_form = self.form(parser, "/ap/cvf/verify", "code")
        self.assert_post_form(verify_form)
        code = self.named_inputs(verify_form)["code"]
        self.assertEqual(code.get("autocomplete"), "one-time-code")
        self.assertEqual(code.get("maxlength"), "6")
        # There are intentionally two forms sharing the endpoint; select the
        # resend form by its action button rather than weakening form semantics.
        resend_forms = [
            form
            for form in parser.forms
            if any(
                button.get("name") == "action" and button.get("value") == "resend"
                for button in form["buttons"]
            )
        ]
        self.assertEqual(len(resend_forms), 1)
        self.assert_post_form(resend_forms[0])
        self.assertNotIn("verification_code", html)

    def test_recovery_post_is_generic_and_does_not_journal_the_identifier(self) -> None:
        posts = (("/ap/forgotpassword", b"email=canary%40example.test"),)
        journal_before = self.store.journal()
        for path, raw in posts:
            with self.subTest(path=path):
                status, headers, body = self.request(
                    "POST",
                    path,
                    body=raw,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Content-Length": str(len(raw)),
                        "Origin": f"http://{self.host}:{self.port}",
                    },
                )
                self.assertEqual(status, 303)
                self.assertEqual(
                    headers.get("location"),
                    ["/ap/cvf/verify?purpose=password-reset"],
                )
                lowered = body.lower()
                self.assertNotIn(b"secret-123456", lowered)
                self.assertNotIn(b"canary%40example.test", lowered)
                self.assertNotIn(b"canary@example.test", lowered)
        self.assertEqual(self.store.journal(), journal_before)


if __name__ == "__main__":
    unittest.main()
