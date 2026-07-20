from __future__ import annotations

import copy
import re
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from clawbench.viewer.app import create_app
from clawbench.viewer.auth import AuthSettings, LoginLimiter, hash_password
from clawbench.viewer.discovery import AMAZON_ITEM_KEY, discover_corpus
from clawbench.viewer.reviews import DIMENSIONS
import clawbench.viewer.app as viewer_app


REPO_ROOT = Path(__file__).resolve().parents[2]


def application(tmp_path: Path, *, profile: str = "internal"):
    return create_app(
        REPO_ROOT,
        profile=profile,
        settings=AuthSettings(
            username="reviewer",
            password_hash=hash_password("strong-password-123"),
            session_secret="test-secret-" * 4,
            cookie_secure=False,
        ),
        review_root=tmp_path / "reviews",
    )


def login(client: TestClient) -> str:
    page = client.get("/login")
    token_match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token_match is not None
    response = client.post(
        "/login",
        data={
            "username": "reviewer",
            "password": "strong-password-123",
            "csrf_token": token_match.group(1),
            "next_path": "/admin",
        },
    )
    assert response.status_code == 200
    assert response.url.path == "/admin"
    csrf_match = re.search(r'name="csrf-token" content="([^"]+)"', response.text)
    assert csrf_match is not None
    return csrf_match.group(1)


def review_body(*, revision: int = 0) -> dict:
    return {
        "expected_revision": revision,
        "review": {
            "reviewer": "reviewer",
            "gate": "approve",
            "visibility": "internal",
            "dimensions": {
                name: {"rating": "pass", "notes": "ok", "evidence_refs": []}
                for name in DIMENSIONS
            },
            "notes": "ok",
            "evidence_refs": [],
        },
    }


def synthetic_run(run_id: str, *, publishable: bool) -> dict:
    dimensions = {
        name: {"score": score, "max_score": maximum, "passed": 1, "total": 1}
        for name, score, maximum in (
            ("visual", 17, 20),
            ("interactions", 18, 20),
            ("journeys", 34, 40),
            ("robustness", 13, 15),
            ("efficiency", 5, 5),
        )
    }
    return {
        "schema_version": "websitebench.result.v1",
        "run_id": run_id,
        "site_id": "amazon",
        "site_version": "1.0.0",
        "track": "core",
        "status": "passed",
        "score": 87,
        "dimensions": dimensions,
        "hard_failures": [],
        "journeys": [{"private_marker": "HIDDEN_JOURNEY_MARKER"}],
        "seeds": [],
        "resources": {"build_seconds": 1},
        "network": {"internet_requests": 0},
        "failures": [],
        "evidence": [{"private_marker": "HIDDEN_EVIDENCE_MARKER"}],
        "versions": {"judge": "private-version"},
        "usage": {"browser_actions": 1},
        "started_at": "2026-07-18T00:00:00Z",
        "finished_at": "2026-07-18T00:01:00Z",
        "model": "model-a",
        "thinking_level": "high",
        "viewer_public": publishable,
        "publishable": publishable,
        "publication_errors": [] if publishable else ["viewer_public is not true"],
        "report_path": f"artifacts/websitebench/runs/{run_id}/report.json",
        "run_directory": f"artifacts/websitebench/runs/{run_id}",
    }


def test_deployment_auth_settings_load_all_secrets_from_files(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    values = {
        "CLAWBENCH_VIEWER_USERNAME": "reviewer",
        "CLAWBENCH_VIEWER_PASSWORD_HASH": hash_password("strong-password-123"),
        "CLAWBENCH_VIEWER_SESSION_SECRET": "deployment-secret-" * 3,
        "CLAWBENCH_VIEWER_TRUSTED_HOSTS": "viewer.example.test,localhost",
    }
    for name, value in values.items():
        path = tmp_path / name.lower()
        path.write_text(value, encoding="utf-8")
        monkeypatch.setenv(f"{name}_FILE", str(path))
        monkeypatch.delenv(name, raising=False)
    settings = AuthSettings.from_env()
    assert settings.username == "reviewer"
    assert settings.trusted_hosts == ("viewer.example.test", "localhost")


def test_login_limiter_blocks_after_configured_failures() -> None:
    limiter = LoginLimiter(attempts=2, window_seconds=300)
    assert limiter.allowed("client")
    limiter.failure("client")
    assert limiter.allowed("client")
    limiter.failure("client")
    assert not limiter.allowed("client")
    limiter.success("client")
    assert limiter.allowed("client")


def test_public_pages_are_anonymous_and_removed_multi_item_routes_are_404(
    tmp_path: Path,
) -> None:
    banned = (
        "Northstar",
        "Freshdesk",
        "Greenhouse",
        "Idealist",
        "Task Atlas",
        "Compare tasks",
        "Legacy verifier",
    )
    with TestClient(application(tmp_path)) as client:
        for path in ("/", "/benchmark/amazon", "/leaderboard", "/evidence", "/methodology"):
            response = client.get(path)
            assert response.status_code == 200
            assert "default-src 'self'" in response.headers["content-security-policy"]
            assert response.headers["x-frame-options"] == "DENY"
            assert not any(term in response.text for term in banned)
        assert client.get("/tasks").status_code == 404
        assert client.get("/compare").status_code == 404
        assert client.get("/tasks/anything").status_code == 404
        admin = client.get("/admin", follow_redirects=False)
        assert admin.status_code == 303
        assert admin.headers["location"].startswith("/login")


def test_public_assets_use_proxy_safe_same_origin_paths(tmp_path: Path) -> None:
    with TestClient(application(tmp_path), base_url="https://localhost") as client:
        response = client.get("/")
        assert response.status_code == 200
        assert 'href="/static/styles.css"' in response.text
        assert 'src="/static/app.js"' in response.text
        assert 'src="/static/amazon-benchmark-hero.webp"' in response.text
        assert "https://localhost/static/" not in response.text
        hero = client.get("/static/amazon-benchmark-hero.webp")
        assert hero.status_code == 200
        assert hero.headers["content-type"] == "image/webp"


def test_review_csrf_revision_export_and_amazon_only_import(tmp_path: Path) -> None:
    with TestClient(application(tmp_path)) as client:
        assert client.get("/admin/reports/gate2-report").status_code == 401
        csrf = login(client)
        report = client.get("/admin/reports/gate2-report")
        assert report.status_code == 200
        assert report.json()["gate"] == 2
        assert client.get("/admin/reports/not-registered").status_code == 404
        assert client.put(f"/api/reviews/{AMAZON_ITEM_KEY}", json=review_body()).status_code == 403
        saved = client.put(
            f"/api/reviews/{AMAZON_ITEM_KEY}",
            json=review_body(),
            headers={"X-CSRF-Token": csrf},
        )
        assert saved.status_code == 200
        assert saved.json()["revision"] == 1
        stale = client.put(
            f"/api/reviews/{AMAZON_ITEM_KEY}",
            json=review_body(),
            headers={"X-CSRF-Token": csrf},
        )
        assert stale.status_code == 409
        exported = client.get("/api/reviews/export")
        assert [row["item_key"] for row in exported.json()["reviews"]] == [
            AMAZON_ITEM_KEY
        ]
        bundle = exported.json()
        bundle["reviews"][0]["item_key"] = "legacy--disabled"
        rejected = client.post(
            "/api/reviews/import", json=bundle, headers={"X-CSRF-Token": csrf}
        )
        assert rejected.status_code == 422


def test_public_profile_registers_no_admin_write_or_clone_routes(tmp_path: Path) -> None:
    app = create_app(REPO_ROOT, profile="public")
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/login").status_code == 404
        assert client.get("/admin").status_code == 404
        assert client.get("/api/reviews/export").status_code == 404
        assert client.put(f"/api/reviews/{AMAZON_ITEM_KEY}", json={}).status_code == 404
        assert client.get(f"/clone/{AMAZON_ITEM_KEY}/").status_code == 404


def test_public_evidence_route_rejects_unregistered_and_traversal(tmp_path: Path) -> None:
    with TestClient(application(tmp_path)) as client:
        record = client.app.state.evidence_registry.records[0]
        image = client.get(record["url"])
        assert image.status_code == 200
        assert image.headers["content-type"].startswith("image/")
        assert client.get("/evidence/not-registered").status_code == 404
        assert client.get("/evidence/..%2F..%2FREADME.md").status_code == 404


def test_public_run_detail_is_published_only_and_aggregate_only(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    index = discover_corpus(REPO_ROOT)
    item = copy.deepcopy(index.items[0])
    item["official_runs"] = [
        synthetic_run("published-run", publishable=True),
        synthetic_run("internal-run", publishable=False),
    ]
    index.items = [item]
    monkeypatch.setattr(viewer_app, "discover_corpus", lambda *args, **kwargs: index)

    with TestClient(application(tmp_path)) as client:
        published = client.get("/runs/published-run")
        assert published.status_code == 200
        assert "HIDDEN_JOURNEY_MARKER" not in published.text
        assert "HIDDEN_EVIDENCE_MARKER" not in published.text
        assert "report_path" not in published.text
        assert client.get("/runs/internal-run").status_code == 404

        login(client)
        internal = client.get("/admin/runs/internal-run")
        assert internal.status_code == 200
        assert "HIDDEN_JOURNEY_MARKER" in internal.text


def test_internal_gateway_requires_auth_and_allows_only_amazon(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAWBENCH_VIEWER_CLONE_ALLOWLIST", AMAZON_ITEM_KEY)
    with TestClient(application(tmp_path)) as client:
        assert client.get(f"/clone/{AMAZON_ITEM_KEY}/").status_code == 401
        csrf = login(client)
        assert client.get("/clone/legacy--disabled/").status_code == 404
        response = client.get(f"/clone/{AMAZON_ITEM_KEY}/")
        assert response.status_code == 200
        assert f"/clone/{AMAZON_ITEM_KEY}/static/" in response.text
        assert "script-src 'self' 'nonce-" in response.headers["content-security-policy"]
        assert "x-clawbench-script-nonce" not in response.headers
        blocked = client.post(
            f"/clone/{AMAZON_ITEM_KEY}/api/preferences",
            headers={"Origin": "https://attacker.example"},
            json={},
        )
        assert blocked.status_code == 403
        same_origin = client.post(
            f"/clone/{AMAZON_ITEM_KEY}/api/preferences",
            headers={"X-CSRF-Token": csrf},
            json={},
        )
        assert same_origin.status_code != 403

        register = client.get(f"/clone/{AMAZON_ITEM_KEY}/register")
        clone_csrf = re.search(
            r"name=['\"]csrf_token['\"] value=['\"]([^'\"]+)", register.text
        )
        assert clone_csrf is not None
        created = client.post(
            f"/clone/{AMAZON_ITEM_KEY}/register",
            headers={"Origin": "http://testserver"},
            data={
                "email": "gateway-shopper@example.test",
                "password": "gateway-password-42",
                "confirm_password": "gateway-password-42",
                "csrf_token": clone_csrf.group(1),
            },
        )
        verification = re.search(
            rf"href=['\"](/clone/{AMAZON_ITEM_KEY}/verify\?token=[^'\"]+)",
            created.text,
        )
        assert verification is not None
        assert "Email verified" in client.get(verification.group(1)).text

        clone_login = client.get(f"/clone/{AMAZON_ITEM_KEY}/login")
        login_csrf = re.search(
            r"name=['\"]csrf_token['\"] value=['\"]([^'\"]+)", clone_login.text
        )
        assert login_csrf is not None
        signed_in = client.post(
            f"/clone/{AMAZON_ITEM_KEY}/login",
            headers={"Origin": "http://testserver"},
            data={
                "email": "gateway-shopper@example.test",
                "password": "gateway-password-42",
                "csrf_token": login_csrf.group(1),
                "next": "/account",
            },
            follow_redirects=False,
        )
        assert signed_in.status_code == 303
        assert signed_in.headers["location"] == f"/clone/{AMAZON_ITEM_KEY}/account"
        account = client.get(signed_in.headers["location"])
        assert "gateway-shopper@example.test" in account.text
