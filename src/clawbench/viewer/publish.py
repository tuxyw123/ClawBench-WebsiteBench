"""Generate the authenticated public viewer as a path-safe static site."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from .app import PACKAGE_ROOT, create_app
from .auth import AuthSettings, hash_password


ROOT_ATTRIBUTE = re.compile(r'(?P<name>href|src|action)="/(?P<value>[^"]*)"')
PUBLIC_ROUTE_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RESERVED_PUBLIC_ROUTES = {
    "api",
    "clone",
    "compare",
    "data",
    "login",
    "methodology",
    "models",
    "results",
    "runs",
    "signin-with-chatgpt",
    "signout-with-chatgpt",
    "static",
    "tasks",
}


def _normalize_base_path(value: str) -> str:
    value = "/" + value.strip("/") if value.strip("/") else "/"
    return value if value.endswith("/") else value + "/"


def _static_html(
    value: str,
    base_path: str,
    canonical_aliases: dict[str, str] | None = None,
) -> str:
    value = re.sub(
        r'<meta name="csrf-token" content="[^"]*">',
        '<meta name="csrf-token" content="">',
        value,
    )
    value = re.sub(
        r'<a class="link-button" href="/api/reviews/export">.*?</a>', "", value
    )
    value = re.sub(
        r'<form method="post" action="/logout">.*?</form>', "", value
    )
    for canonical, alias in (canonical_aliases or {}).items():
        value = value.replace(f'href="{canonical}', f'href="{alias}')
    prefix = base_path.rstrip("/")
    value = value.replace("http://testserver/", f"{prefix}/")
    value = ROOT_ATTRIBUTE.sub(
        lambda match: f'{match.group("name")}="{prefix}/{match.group("value")}"',
        value,
    )
    csp = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; form-action 'self'"
    )
    social_image = f'{base_path.rstrip("/")}/static/og-v2.png' or "/static/og-v2.png"
    social = "\n  ".join(
        [
            '<meta property="og:type" content="website">',
            '<meta property="og:title" content="WebsiteBench · Agent Reconstruction Viewer">',
            '<meta property="og:description" content="Explore offline reference websites and inspect how future Agents reconstruct them.">',
            f'<meta property="og:image" content="{social_image}">',
            '<meta name="twitter:card" content="summary_large_image">',
            '<meta name="twitter:title" content="WebsiteBench · Agent Reconstruction Viewer">',
            '<meta name="twitter:description" content="Explore offline reference websites and inspect how future Agents reconstruct them.">',
            f'<meta name="twitter:image" content="{social_image}">',
        ]
    )
    return value.replace(
        "<title>",
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">\n  {social}\n  <title>',
        1,
    )


def _output_path(output: Path, route: str) -> Path:
    route_path = urlsplit(route).path.strip("/")
    return output / route_path / "index.html" if route_path else output / "index.html"


def _load_public_routes(repo_root: Path, path: Path | None) -> dict[str, str]:
    config_path = path or (
        repo_root / "websitebench" / "viewer-public-allowlist.json"
    )
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"public route config is invalid: {exc}") from exc
    routes = value.get("routes", {}) if isinstance(value, dict) else {}
    if not isinstance(routes, dict):
        raise ValueError("public route config routes must be an object")
    normalized: dict[str, str] = {}
    for slug, item_key in routes.items():
        if (
            not isinstance(slug, str)
            or not PUBLIC_ROUTE_SLUG.fullmatch(slug)
            or slug in RESERVED_PUBLIC_ROUTES
        ):
            raise ValueError(f"invalid or reserved public route slug: {slug!r}")
        if not isinstance(item_key, str) or not item_key:
            raise ValueError(f"public route {slug!r} must name a corpus item")
        normalized[slug] = item_key
    return normalized


def publish_static_site(
    repo_root: Path,
    output: Path,
    *,
    base_path: str = "/",
    public_allowlist: Path | None = None,
) -> dict[str, object]:
    """Render all public viewer routes and copy their static assets."""

    root = repo_root.resolve()
    destination = output.resolve()
    base_path = _normalize_base_path(base_path)
    password = "static-publisher-only"
    with tempfile.TemporaryDirectory(prefix="websitebench-publish-") as temporary:
        state = Path(temporary)
        app = create_app(
            root,
            profile="public",
            settings=AuthSettings(
                username="publisher",
                password_hash=hash_password(password),
                session_secret="static-publisher-session-secret-" * 2,
                cookie_secure=False,
            ),
            review_root=state / "reviews",
            evidence_root=state / "visual",
            public_allowlist=public_allowlist,
        )
        index = app.state.corpus_index
        routes = ["/", "/tasks", "/models", "/results", "/compare", "/methodology"]
        routes.extend(f'/tasks/{item["key"]}' for item in index.items)
        routes.extend(f'/models/{model["model_key"]}' for model in index.models)
        routes.extend(f'/runs/{run["run_id"]}' for run in index.runs)
        public_routes = _load_public_routes(root, public_allowlist)
        known_item_keys = {item["key"] for item in index.items}
        unknown_route_items = sorted(set(public_routes.values()) - known_item_keys)
        if unknown_route_items:
            raise ValueError(
                "public routes reference unpublished corpus items: "
                + ", ".join(unknown_route_items)
            )
        alias_targets = {
            f"/{slug}": f"/tasks/{item_key}"
            for slug, item_key in public_routes.items()
        }
        canonical_aliases = {
            canonical: alias for alias, canonical in alias_targets.items()
        }
        routes.extend(alias_targets)

        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        with TestClient(app) as client:
            login = client.get("/login")
            token = re.search(r'name="csrf_token" value="([^"]+)"', login.text)
            if token is None:
                raise RuntimeError("publisher could not obtain login token")
            response = client.post(
                "/login",
                data={
                    "username": "publisher",
                    "password": password,
                    "csrf_token": token.group(1),
                    "next_path": "/",
                },
            )
            if response.status_code != 200:
                raise RuntimeError("publisher login failed")
            for route in routes:
                response = client.get(alias_targets.get(route, route))
                if response.status_code != 200:
                    raise RuntimeError(f"publisher route failed: {route}")
                target = _output_path(destination, route)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    _static_html(response.text, base_path, canonical_aliases),
                    encoding="utf-8",
                )

        shutil.copytree(PACKAGE_ROOT / "static", destination / "static")
        index_value = index.as_dict()
        data = destination / "data"
        data.mkdir()
        (data / "index.json").write_text(
            json.dumps(index_value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        manifest = {
            "schema_version": "websitebench.static-site.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "profile": "public",
            "base_path": base_path,
            "items": len(index.items),
            "models": len(index.models),
            "pages": len(routes),
        }
        (destination / "site-manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (destination / ".nojekyll").touch()
        shutil.copy2(destination / "index.html", destination / "404.html")
        return manifest
