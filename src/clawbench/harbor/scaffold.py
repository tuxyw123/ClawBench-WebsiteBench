"""Create normalized Harbor authoring skeletons without overwriting user data."""

from __future__ import annotations

import re
from pathlib import Path
from pathlib import PurePosixPath

import yaml


_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _validate_identity(destination: Path, identifier: str, kind: str) -> None:
    if not _SLUG.fullmatch(identifier):
        raise ValueError(f"{kind}_id must be a lowercase hyphenated slug")
    if destination.name != identifier:
        raise ValueError(
            f"{kind}_id must match destination directory name {destination.name!r}"
        )


def _prepare_empty(destination: Path) -> Path:
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"destination is non-empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def _write_yaml(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        yaml.safe_dump(value, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )


def initialize_site(destination: Path, *, site_id: str, display_name: str) -> Path:
    _validate_identity(destination, site_id, "site")
    if not display_name.strip():
        raise ValueError("display_name must not be empty")
    root = _prepare_empty(destination)
    for directory in ("public", "reference", "verifier", "fixtures/hidden", "oracle"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "public" / "README.md").write_text(
        "# Public site contract\n\n"
        "Only Agent-visible API/interface contracts and starter assets belong here. "
        "Never place reference implementation files in this directory.\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "reference" / "server.py").write_text(
        '"""Replace this minimal server with the frozen offline reference."""\n\n'
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "import os\n\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        if self.path == '/healthz':\n"
        "            body = b'ok\\n'\n"
        "            self.send_response(200)\n"
        "            self.send_header('Content-Type', 'text/plain')\n"
        "        else:\n"
        "            body = b'Replace the scaffold reference implementation.\\n'\n"
        "            self.send_response(200)\n"
        "            self.send_header('Content-Type', 'text/plain')\n"
        "        self.send_header('Content-Length', str(len(body)))\n"
        "        self.end_headers()\n"
        "        self.wfile.write(body)\n\n"
        "    def log_message(self, format, *args):\n"
        "        return\n\n"
        "ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), "
        "Handler).serve_forever()\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "reference" / "Dockerfile").write_text(
        "FROM python:3.12-slim\n"
        "WORKDIR /srv/reference\n"
        "COPY . .\n"
        "ENV PORT=8080\n"
        "HEALTHCHECK --interval=2s --timeout=3s --retries=30 "
        "CMD python -c \"import urllib.request; "
        "urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)\"\n"
        'CMD ["python", "server.py"]\n',
        encoding="utf-8",
        newline="\n",
    )
    reference_run = root / "reference" / "run.sh"
    reference_run.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        "exec python server.py\n",
        encoding="utf-8",
        newline="\n",
    )
    reference_run.chmod(0o755)
    (root / "verifier" / "run.py").write_text(
        '"""Site-specific trusted evaluator entry point."""\n\n'
        "raise SystemExit(\n"
        '    "Implement API, Playwright, visual, journey, and robustness checks; "\n'
        '    "write /run/verifier-final/ctrf.json and dimensions.json."\n'
        ")\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "fixtures" / "hidden" / "README.md").write_text(
        "# Hidden fixtures\n\nEvaluator-only reset states and test data.\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "oracle" / "README.md").write_text(
        "# Oracle-only site support\n\n"
        "Private calibration helpers. These files are copied only into solution/.\n",
        encoding="utf-8",
        newline="\n",
    )
    _write_yaml(
        root / "site.yaml",
        {
            "schema_version": "clawbench.harbor.site.v1",
            "site_id": site_id,
            "display_name": display_name,
            "benchmark_kind": "fullstack-offline-reconstruction",
            "runtime": {
                "reference_access": "browser-only",
                "agent_browser": "browser-use-cli",
                "formal_browser": "playwright",
                "reference_url_env": "CLAWBENCH_REFERENCE_URL",
                "candidate_url_env": "CLAWBENCH_CANDIDATE_URL",
                "reference_admin_url_env": "CLAWBENCH_REFERENCE_ADMIN_URL",
                "candidate_admin_url_env": "CLAWBENCH_CANDIDATE_ADMIN_URL",
                "reference_port": 8080,
                "candidate_port": 3000,
                "verifier_reference_port": 18080,
                "verifier_candidate_port": 18901,
                "ready_path": "/healthz",
                "reset_path": "/__admin/reset",
                "judge_network": "offline",
            },
            "paths": {
                "public": "public",
                "reference": "reference",
                "verifier": "verifier",
                "hidden_fixtures": "fixtures/hidden",
                "oracle": "oracle",
            },
            "scoring": {
                "max_points": 100,
                "dimensions": {
                    "contract": 10,
                    "api": 20,
                    "ui": 20,
                    "visual": 15,
                    "journey": 20,
                    "robustness": 15,
                    "efficiency": 0,
                },
            },
        },
    )
    return root / "site.yaml"


def initialize_instance(
    destination: Path,
    *,
    instance_id: str,
    site_manifest: str,
    author_name: str,
    author_email: str,
) -> Path:
    _validate_identity(destination, instance_id, "instance")
    site_parts = PurePosixPath(site_manifest.replace("\\", "/")).parts
    if (
        len(site_parts) != 3
        or site_parts[0] != "sites"
        or site_parts[2] != "site.yaml"
    ):
        raise ValueError(
            "site_manifest must be sites/<site-id>/site.yaml relative to the "
            "authoring root"
        )
    if not author_name.strip():
        raise ValueError("author_name must not be empty")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", author_email):
        raise ValueError("author_email must be a valid email address")
    root = _prepare_empty(destination)
    for directory in ("public", "verifier", "fixtures/hidden", "solution"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "instruction.md").write_text(
        "# Reconstruct the offline website\n\n"
        "Use Browser Use CLI to inspect the browser-only reference website. Rebuild "
        "the scoped frontend and backend in `/app/repo`. The formal verifier uses "
        "Playwright and direct HTTP checks against a fresh candidate instance.\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "public" / "README.md").write_text(
        "# Instance-public files\n\n"
        "Place the candidate scaffold and Agent-visible task contracts here. Keep "
        "`run.sh`: the verifier sets `PORT` and `CLAWBENCH_DATA_DIR`, then runs "
        "it from `/app/repo`.\n",
        encoding="utf-8",
        newline="\n",
    )
    candidate_run = root / "public" / "run.sh"
    candidate_run.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        "echo 'implement the candidate server; listen on $PORT' >&2\n"
        "exit 2\n",
        encoding="utf-8",
        newline="\n",
    )
    candidate_run.chmod(0o755)
    (root / "verifier" / "README.md").write_text(
        "# Instance verifier overlay\n\n"
        "Put evaluator-only checks for this task variant here.\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "fixtures" / "hidden" / "README.md").write_text(
        "# Instance hidden fixtures\n\nEvaluator-only scenario data.\n",
        encoding="utf-8",
        newline="\n",
    )
    solve = root / "solution" / "solve.sh"
    solve.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        "echo 'oracle solution is not implemented' >&2\n"
        "exit 2\n",
        encoding="utf-8",
        newline="\n",
    )
    solve.chmod(0o755)
    _write_yaml(
        root / "instance.yaml",
        {
            "schema_version": "clawbench.harbor.instance.v1",
            "instance_id": instance_id,
            "site_manifest": site_manifest,
            "task": {
                "category": "web-development",
                "type": "fullstack-reconstruction",
                "language": "web",
            },
            "metadata": {
                "author_name": author_name,
                "author_email": author_email,
                "difficulty": "hard",
                "tags": ["browser-checks", "frontend-backend"],
            },
            "budgets": {
                "agent_timeout_sec": 3600,
                "verifier_timeout_sec": 1200,
                "build_timeout_sec": 1200,
                "cpus": 4,
                "memory_mb": 8192,
                "storage_mb": 20480,
            },
            "paths": {
                "instruction": "instruction.md",
                "public": "public",
                "verifier": "verifier",
                "hidden_fixtures": "fixtures/hidden",
                "oracle_solution": "solution/solve.sh",
            },
            "tests": {
                "contract": ["contract::runtime/starts-and-resets"],
                "api": [
                    "api::core/read-path",
                    "api::core/write-path",
                ],
                "ui": [
                    "ui::primary/initial-state",
                    "ui::primary/interaction",
                ],
                "visual": ["visual::primary/reference-checkpoint"],
                "journey": ["journey::primary/end-to-end"],
                "robustness": ["robustness::refresh-and-retry"],
                "efficiency": [],
            },
            "calibration": {
                "nop_max_score": 10,
                "oracle_min_score": 95,
            },
        },
    )
    return root / "instance.yaml"
