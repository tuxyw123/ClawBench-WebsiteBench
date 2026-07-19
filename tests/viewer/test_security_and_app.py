from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from clawbench.viewer.app import create_app
from clawbench.viewer.auth import AuthSettings, LoginLimiter, hash_password
from clawbench.viewer.reviews import DIMENSIONS


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
        evidence_root=tmp_path / "visual",
    )


def login(client: TestClient) -> str:
    page = client.get("/login")
    token_match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token_match is not None
    token = token_match.group(1)
    response = client.post(
        "/login",
        data={
            "username": "reviewer",
            "password": "strong-password-123",
            "csrf_token": token,
            "next_path": "/",
        },
    )
    assert response.status_code == 200
    home = client.get("/")
    csrf_match = re.search(r'name="csrf-token" content="([^"]+)"', home.text)
    assert csrf_match is not None
    return csrf_match.group(1)


def review_body() -> dict:
    return {
        "expected_revision": 0,
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


def test_deployment_auth_settings_load_all_secrets_from_files(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    values = {
        "CLAWBENCH_VIEWER_USERNAME": "reviewer",
        "CLAWBENCH_VIEWER_PASSWORD_HASH": hash_password("strong-password-123"),
        "CLAWBENCH_VIEWER_SESSION_SECRET": "deployment-secret-" * 3,
        "CLAWBENCH_VIEWER_TRUSTED_HOSTS": "atlas.example.test,localhost",
    }
    for name, value in values.items():
        path = tmp_path / name.lower()
        path.write_text(value, encoding="utf-8")
        monkeypatch.setenv(f"{name}_FILE", str(path))
        monkeypatch.delenv(name, raising=False)
    settings = AuthSettings.from_env()
    assert settings.username == "reviewer"
    assert settings.trusted_hosts == ("atlas.example.test", "localhost")


def test_login_limiter_blocks_after_configured_failures() -> None:
    limiter = LoginLimiter(attempts=2, window_seconds=300)
    assert limiter.allowed("client")
    limiter.failure("client")
    assert limiter.allowed("client")
    limiter.failure("client")
    assert not limiter.allowed("client")
    limiter.success("client")
    assert limiter.allowed("client")


def test_auth_redirect_security_headers_and_core_pages(tmp_path: Path) -> None:
    with TestClient(application(tmp_path)) as client:
        redirect = client.get("/", follow_redirects=False)
        assert redirect.status_code == 303
        assert redirect.headers["location"].startswith("/login")
        login(client)
        for path in (
            "/",
            "/tasks",
            "/tasks/websitebench--northstar-market",
            "/compare",
            "/methodology",
        ):
            response = client.get(path)
            assert response.status_code == 200
            assert "default-src 'self'" in response.headers["content-security-policy"]
            assert response.headers["x-frame-options"] == "DENY"
        tasks = client.get("/tasks").text
        assert tasks.count("data-task-row") == 4
        assert "Official / 100" in tasks or "No candidate result" in tasks
        assert "Legacy verifier" in tasks


def test_review_csrf_revision_and_export(tmp_path: Path) -> None:
    with TestClient(application(tmp_path)) as client:
        csrf = login(client)
        key = "websitebench--northstar-market"
        assert client.put(f"/api/reviews/{key}", json=review_body()).status_code == 403
        saved = client.put(
            f"/api/reviews/{key}",
            json=review_body(),
            headers={"X-CSRF-Token": csrf},
        )
        assert saved.status_code == 200
        assert saved.json()["revision"] == 1
        stale = client.put(
            f"/api/reviews/{key}",
            json=review_body(),
            headers={"X-CSRF-Token": csrf},
        )
        assert stale.status_code == 409
        exported = client.get("/api/reviews/export")
        assert exported.status_code == 200
        assert len(exported.json()["reviews"]) == 1


def test_public_profile_disables_writes_gateway_and_artifacts(tmp_path: Path) -> None:
    with TestClient(application(tmp_path, profile="public")) as client:
        csrf = login(client)
        key = "websitebench--northstar-market"
        response = client.put(
            f"/api/reviews/{key}",
            json=review_body(),
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 403
        assert client.get(f"/clone/{key}/").status_code == 404
        assert client.get(f"/artifacts/{key}/anything.png").status_code == 404


def test_compare_caps_selection_at_four(tmp_path: Path) -> None:
    app = application(tmp_path)
    with TestClient(app) as client:
        login(client)
        keys = [item["key"] for item in app.state.corpus_index.items]
        assert client.get("/compare", params=[("items", key) for key in keys]).status_code == 200
        too_many = client.get(
            "/compare", params=[("items", key) for key in [*keys, keys[0]]]
        )
        # De-duplication keeps this at four.
        assert too_many.status_code == 200
        assert client.get(
            "/compare",
            params=[("items", key) for key in [*keys, "missing--one"]],
        ).status_code == 400


def test_internal_gateway_requires_auth_and_rewrites_clone_response(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    key = "legacy--dev-117-greenhouse-codepath-application"
    monkeypatch.setenv("CLAWBENCH_VIEWER_CLONE_ALLOWLIST", key)
    with TestClient(application(tmp_path)) as client:
        assert client.get(f"/clone/{key}/").status_code == 401
        login(client)
        response = client.get(f"/clone/{key}/")
        assert response.status_code == 200
        assert f"/clone/{key}/static/" in response.text
        policy = response.headers["content-security-policy"]
        assert "script-src 'self' 'nonce-" in policy
        assert "x-clawbench-script-nonce" not in response.headers

        blocked = client.post(
            f"/clone/{key}/api/drafts/4526154007",
            headers={"Origin": "https://attacker.example"},
            json={},
        )
        assert blocked.status_code == 403
        same_origin = client.post(
            f"/clone/{key}/api/drafts/4526154007",
            headers={"Origin": "http://testserver"},
            json={},
        )
        assert same_origin.status_code != 403
