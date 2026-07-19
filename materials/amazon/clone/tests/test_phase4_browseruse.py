from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "run_phase4_browseruse.py"
SPEC = importlib.util.spec_from_file_location("amazon_phase4_browseruse", TOOL)
assert SPEC and SPEC.loader
phase4 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = phase4
SPEC.loader.exec_module(phase4)


def test_phase4_contract_pins_browseruse_and_zero_extra_llm_calls() -> None:
    contract = phase4.load_contract()
    assert contract["browserUse"] == {
        "package": "browser-use",
        "version": "0.12.6",
        "driver": "Browser + Tools",
        "controller": "current-codex-deterministic",
        "additionalLlmCalls": 0,
        "userAgentPolicy": {
            "desktop": "Chromium Linux desktop",
            "mobileAtOrBelow480px": "Chromium Android Mobile",
        },
        "modelProvenance": {
            "model": "gpt-5.6-sol",
            "reasoningEffort": "xhigh",
            "source": "/root/.codex/config.toml",
        },
    }
    assert len(contract["trajectories"]) == 5
    assert {item["id"] for item in contract["trajectories"]} == {
        "B01",
        "B02",
        "B03",
        "B04",
        "B05",
    }


def test_source_contract_and_runner_allow_get_only() -> None:
    contract = phase4.load_contract()
    assert contract["sourceSafety"]["allowedRequestMethods"] == ["GET"]
    assert contract["sourceSafety"]["blockedAt"] == "CDP Fetch.requestPaused"
    assert "input" not in phase4.SOURCE_ACTIONS
    assert "select_dropdown" not in phase4.SOURCE_ACTIONS
    assert "send_keys" not in phase4.SOURCE_ACTIONS
    assert "evaluate" not in phase4.SOURCE_ACTIONS
    assert "Mobile Safari" in phase4.user_agent_for({"width": 390, "height": 844})
    assert "Mobile Safari" not in phase4.user_agent_for(
        {"width": 1365, "height": 900}
    )


def test_url_sanitizer_drops_tokens_and_retains_public_search_shape() -> None:
    result = phase4.sanitize_url(
        "https://www.amazon.com/s?k=portable+ssd&i=computers&session-id=secret&ref_=abc#x"
    )
    assert result == "https://www.amazon.com/s?k=portable+ssd&i=computers"
    assert "secret" not in result
    assert "ref_" not in result
    path_result = phase4.sanitize_url(
        "https://www.amazon.com/dp/B0874XN4D8/ref=x/146-8508585-2365468"
    )
    assert "146-8508585-2365468" not in path_result
    assert "<redacted-session>" in path_result
    opaque_path = phase4.sanitize_url(
        "https://aax.amazon.com/x/" + "opaque" * 20 + "/v/"
    )
    assert "opaqueopaque" not in opaque_path
    assert "<redacted-opaque-path>" in opaque_path
    assert phase4.sanitize_result('"key":"' + "A" * 80 + '"') == (
        '"key":"<redacted-opaque-value>"'
    )
    assert phase4.no_elements_found('No elements found matching "#x".')
    assert phase4.no_elements_found('Found 0 elements matching "#x".')
    assert not phase4.no_elements_found('Found 2 elements matching "#x".')
    assert phase4.source_url("https://www.amazon.com", "/s?k=portable+ssd") == (
        "https://www.amazon.com/s?k=portable+ssd&language=en_US"
    )


def test_fetch_guard_continues_get_and_fails_post_before_send() -> None:
    class Domain:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, str | None]] = []

        async def enable(self, *, params: dict, session_id: str | None = None) -> None:
            self.calls.append(("enable", params, session_id))

        async def continueRequest(
            self, *, params: dict, session_id: str | None = None
        ) -> None:
            self.calls.append(("continue", params, session_id))

        async def failRequest(
            self, *, params: dict, session_id: str | None = None
        ) -> None:
            self.calls.append(("fail", params, session_id))

        async def disable(self, *, session_id: str | None = None) -> None:
            self.calls.append(("disable", {}, session_id))

    class RegisterDomain:
        def __init__(self) -> None:
            self.request_handler = None
            self.attach_handler = None

        def requestPaused(self, handler) -> None:
            self.request_handler = handler

        def attachedToTarget(self, handler) -> None:
            self.attach_handler = handler

    class Client:
        def __init__(self) -> None:
            self.send = type("Send", (), {})()
            self.send.Fetch = Domain()
            self.register = type("Register", (), {})()
            self.register.Fetch = RegisterDomain()
            self.register.Target = self.register.Fetch

    class Session:
        def __init__(self, client) -> None:
            self.cdp_client = client
            self.session_id = "page-session"

    class Browser:
        def __init__(self, client) -> None:
            self.session = Session(client)

        async def get_or_create_cdp_session(self):
            return self.session

    async def exercise() -> tuple[phase4.ReadOnlyFetchGuard, Domain]:
        client = Client()
        guard = phase4.ReadOnlyFetchGuard()
        await guard.install(Browser(client))
        handler = client.register.Fetch.request_handler
        assert handler is not None
        handler(
            {"requestId": "g", "request": {"method": "GET", "url": "https://www.amazon.com/"}},
            "page-session",
        )
        handler(
            {"requestId": "p", "request": {"method": "POST", "url": "https://www.amazon.com/telemetry"}},
            "page-session",
        )
        await guard.settle()
        await guard.close()
        return guard, client.send.Fetch

    guard, domain = asyncio.run(exercise())
    assert guard.blocked_non_get == 1
    assert [item["decision"] for item in guard.requests] == [
        "continue",
        "blocked-before-send",
    ]
    assert [call[0] for call in domain.calls] == [
        "enable",
        "continue",
        "fail",
        "disable",
    ]
    assert domain.calls[-2][1]["errorReason"] == "BlockedByClient"


def test_clone_network_observer_suppresses_duplicate_cdp_events() -> None:
    observer = phase4.NetworkObserver()
    event = {
        "requestId": "same-request",
        "request": {
            "method": "POST",
            "url": "http://127.0.0.1:8153/cart/add?token=not-retained",
        },
    }
    observer.record(event, "page-session")
    observer.record(event, "root-session")
    assert observer.requests == [
        {"method": "POST", "url": "http://127.0.0.1:8153/cart/add"}
    ]
    assert observer.duplicate_events_suppressed == 1


def test_clone_terminal_post_is_counted_once_after_cdp_deduplication() -> None:
    observer = phase4.NetworkObserver()
    event = {
        "requestId": "terminal-request",
        "request": {
            "method": "POST",
            "url": "http://127.0.0.1:8153/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance",
        },
    }
    observer.record(event, "page-session")
    observer.record(event, "root-session")
    assert len(phase4.clone_terminal_posts(observer.requests)) == 1


def test_review_keeps_gate_four_pending_for_human() -> None:
    markdown = phase4.build_review(
        {
            "browserUse": {"version": "0.12.6"},
            "summary": {
                "trajectoriesPassed": 5,
                "trajectoryCount": 5,
                "sourceUnavailableClonePassed": 0,
                "sourceRequests": 10,
                "sourceNonGetBlocked": 2,
                "sourceRequestsCanceled": 1,
                "cloneExternalRequests": 0,
                "cloneDuplicateNetworkEventsSuppressed": 3,
                "clonePostCount": 1,
                "cloneTerminalPostCount": 1,
                "cloneTaskTerminalReached": True,
                "cloneEmptyCartCheckoutAffordance": False,
                "screenshotCount": 1,
            },
            "screenshots": [{"file": "x.png", "sha256": "abc", "bytes": 1}],
        }
    )
    assert "Source non-GET transmitted: `0`" in markdown
    assert "pending explicit human approval" in markdown


def test_task_ssds_have_source_equivalent_search_category() -> None:
    server_path = ROOT / "server.py"
    spec = importlib.util.spec_from_file_location("amazon_phase4_server", server_path)
    assert spec and spec.loader
    server = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    assert len(server.PRODUCTS) == 6
    assert {item["department"] for item in server.PRODUCTS} == {"Electronics"}
    assert {item["category"] for item in server.PRODUCTS} == {
        "Computers & Accessories"
    }
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "['computers', 'Computers & Accessories']" in app_js


def test_desktop_product_gallery_cannot_cover_product_copy() -> None:
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    gallery_rule = styles.split(".product-gallery {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" in gallery_rule
