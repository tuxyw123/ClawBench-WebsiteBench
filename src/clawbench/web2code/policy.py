"""Candidate source/layout anti-cheat and safety policy checks."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path


TEXT_SUFFIXES = {
    ".css",
    ".go",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".svelte",
    ".toml",
    ".ts",
    ".tsx",
    ".vue",
    ".yaml",
    ".yml",
}
SKIP_PARTS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"}
REQUIRED_PATHS = {
    "frontend",
    "backend",
    "README.md",
    "Dockerfile",
    "docker-compose.yml",
    ".env.example",
    "scripts/seed",
    "scripts/reset",
}
PROHIBITED_PATTERNS = {
    "reference-origin": re.compile(r"(?:reference-app|northstar-target)(?::8080)?", re.I),
    "browser-control-secret": re.compile(r"(?:GATEWAY_TOKEN|BUILDER_TOKEN)", re.I),
    "commercial-memory-name": re.compile(r"\bamazon(?:\.com)?\b", re.I),
    "remote-screenshot-service": re.compile(
        r"(?:urlbox\.io|screenshot(?:layer|api)\.|api\.screenshotone\.com)", re.I
    ),
    "host-network": re.compile(r"network_mode\s*:\s*host", re.I),
    "privileged-container": re.compile(r"privileged\s*:\s*true", re.I),
    "docker-socket": re.compile(r"/var/run/docker\.sock", re.I),
}


@dataclass(frozen=True)
class PolicyFinding:
    code: str
    path: str
    message: str
    hard_failure: bool = True

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _source_files(root: Path):
    for path in root.rglob("*"):
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.is_file() and path.suffix.casefold() in TEXT_SUFFIXES:
            yield path


def scan_candidate(root: Path | str, *, private_reference: Path | str | None = None) -> list[PolicyFinding]:
    root = Path(root).resolve()
    findings: list[PolicyFinding] = []
    for relative in sorted(REQUIRED_PATHS):
        if not (root / relative).exists():
            findings.append(PolicyFinding("MISSING_REQUIRED_PATH", relative, f"missing {relative}", False))
    total_bytes = 0
    candidate_hashes: dict[str, str] = {}
    if root.exists():
        for path in root.rglob("*"):
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            relative = str(path.relative_to(root))
            if path.is_symlink():
                try:
                    path.resolve().relative_to(root)
                except ValueError:
                    findings.append(
                        PolicyFinding("ESCAPING_SYMLINK", relative, "symlink escapes candidate root")
                    )
                continue
            if path.is_file():
                total_bytes += path.stat().st_size
                if path.stat().st_size <= 5 * 1024 * 1024:
                    candidate_hashes[hashlib.sha256(path.read_bytes()).hexdigest()] = relative
        for path in _source_files(root):
            relative = str(path.relative_to(root))
            text = path.read_text(encoding="utf-8", errors="replace")
            if "<iframe" in text.casefold():
                findings.append(PolicyFinding("IFRAME", relative, "candidate contains an iframe"))
            for code, pattern in PROHIBITED_PATTERNS.items():
                if pattern.search(text):
                    findings.append(
                        PolicyFinding(code.upper().replace("-", "_"), relative, f"matched {code}")
                    )
    if total_bytes > 50 * 1024 * 1024:
        findings.append(
            PolicyFinding(
                "SOURCE_SIZE_LIMIT",
                ".",
                f"candidate source is {total_bytes} bytes (limit 52428800)",
                False,
            )
        )
    if private_reference is not None:
        private_root = Path(private_reference).resolve()
        for path in private_root.rglob("*"):
            if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
                continue
            if path.stat().st_size < 256 or path.stat().st_size > 5 * 1024 * 1024:
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in candidate_hashes:
                findings.append(
                    PolicyFinding(
                        "PRIVATE_FILE_COPY",
                        candidate_hashes[digest],
                        f"exactly matches private reference file {path.relative_to(private_root)}",
                    )
                )
    unique = {(finding.code, finding.path, finding.message): finding for finding in findings}
    return sorted(unique.values(), key=lambda item: (item.code, item.path, item.message))

