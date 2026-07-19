"""Build candidate images against an isolated rootless Docker/BuildKit daemon."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException


REQUIRED_PATHS = (
    "frontend",
    "backend",
    "README.md",
    "Dockerfile",
    "docker-compose.yml",
    ".env.example",
    "scripts/seed",
    "scripts/reset",
)


@dataclass
class BuilderConfig:
    token: str
    workspace: Path
    artifacts: Path
    docker_host: str
    max_builds: int
    run_id: str
    admin_token: str
    mailbox_delivery_token: str = "development-mail-token"
    preview_fixture_mount: str = "/bench-public-fixtures"
    preview_fixture_source: Path = Path("/task/public/fixtures/1101.json")
    preview_schema_mount: str = "/bench-schemas"
    mailbox_url: str = "http://mailbox:8025"
    public_mailbox_url: str = "http://mailbox:8025"
    public_site_url: str = "http://rootless-buildkit:18080"


class CandidateBuilder:
    def __init__(self, config: BuilderConfig) -> None:
        self.config = config
        self.config.artifacts.mkdir(parents=True, exist_ok=True)
        self.build_count = 0
        self.lock = asyncio.Lock()
        self.preview_name = f"websitebench-preview-{self._safe_name(config.run_id)}"
        self.last_image: str | None = None
        self.last_source_digest: str | None = None
        self.last_build_seconds = 0.0

    @staticmethod
    def _safe_name(value: str) -> str:
        return "".join(character if character.isalnum() or character in "_.-" else "-" for character in value)[:80]

    def authorize(self, authorization: str | None) -> None:
        expected = f"Bearer {self.config.token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=404)

    def validate_workspace(self) -> list[str]:
        errors: list[str] = []
        root = self.config.workspace.resolve()
        for relative in REQUIRED_PATHS:
            path = root / relative
            if not path.exists():
                errors.append(f"missing {relative}")
            elif path.is_symlink():
                errors.append(f"symlink not allowed: {relative}")
        for relative in ("scripts/seed", "scripts/reset"):
            path = root / relative
            if path.exists() and not path.stat().st_mode & stat.S_IXUSR:
                errors.append(f"not executable: {relative}")
        total = 0
        for path in root.rglob("*") if root.exists() else ():
            if path.is_symlink():
                try:
                    path.resolve().relative_to(root)
                except ValueError:
                    errors.append(f"escaping symlink: {path.relative_to(root)}")
                continue
            if path.is_file() and ".git" not in path.parts:
                total += path.stat().st_size
        if total > 512 * 1024 * 1024:
            errors.append(f"source exceeds 512 MiB safety ceiling: {total} bytes")
        return errors

    def source_digest(self) -> str:
        digest = hashlib.sha256()
        root = self.config.workspace.resolve()
        for path in sorted(root.rglob("*")) if root.exists() else ():
            if not path.is_file() or path.is_symlink() or ".git" in path.parts:
                continue
            relative = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        return digest.hexdigest()

    async def command(self, *args: str, timeout: float = 600) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "--host",
            self.config.docker_host,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return 124, "candidate build timed out"
        return process.returncode or 0, stdout.decode("utf-8", "replace")[-200_000:]

    async def build(self) -> dict[str, Any]:
        async with self.lock:
            if self.build_count >= self.config.max_builds:
                raise HTTPException(status_code=429, detail="candidate build budget exhausted")
            self.build_count += 1
            number = self.build_count
            errors = self.validate_workspace()
            if errors:
                result = {
                    "build": number,
                    "status": "contract_error",
                    "errors": errors,
                    "remaining_builds": self.config.max_builds - number,
                }
                self._record(result)
                return result
            tag = f"websitebench-candidate:{self._safe_name(self.config.run_id)}-{number}"
            started = time.monotonic()
            code, output = await self.command(
                "build",
                "--pull=false",
                "--label",
                f"websitebench.run_id={self.config.run_id}",
                "--tag",
                tag,
                str(self.config.workspace),
            )
            duration = time.monotonic() - started
            log_path = self.config.artifacts / f"build-{number:02d}.log"
            log_path.write_text(output, encoding="utf-8")
            if code:
                result = {
                    "build": number,
                    "status": "build_failed",
                    "exit_code": code,
                    "duration_seconds": duration,
                    "log_tail": output[-12_000:],
                    "remaining_builds": self.config.max_builds - number,
                }
                self._record(result)
                return result
            await self.command("rm", "-f", self.preview_name, timeout=60)
            code, preview_output = await self.command(
                "run",
                "--detach",
                "--name",
                self.preview_name,
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=128m",
                "--tmpfs",
                "/data:rw,nosuid,size=256m",
                "--mount",
                f"type=bind,src={self.config.preview_fixture_mount},dst=/bench-fixtures,readonly",
                "--mount",
                f"type=bind,src={self.config.preview_schema_mount},dst=/bench-schemas,readonly",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--publish",
                "18080:8080",
                "--publish",
                "18081:8081",
                "--env",
                "PORT=8080",
                "--env",
                "BENCH_ADMIN_PORT=8081",
                "--env",
                f"BENCH_ADMIN_TOKEN={self.config.admin_token}",
                "--env",
                "DATA_DIR=/data",
                "--env",
                "BENCH_FIXTURE_DIR=/bench-fixtures",
                "--env",
                "FIXTURE_SCHEMA_PATH=/bench-schemas/fixture.schema.json",
                "--env",
                "BENCH_CLOCK_MODE=controlled",
                "--env",
                f"MAILBOX_API_URL={self.config.mailbox_url}",
                "--env",
                f"MAILBOX_DELIVERY_TOKEN={self.config.mailbox_delivery_token}",
                "--env",
                f"PUBLIC_MAILBOX_URL={self.config.public_mailbox_url}",
                "--env",
                f"PUBLIC_SITE_URL={self.config.public_site_url}",
                tag,
                timeout=90,
            )
            if code == 0:
                preview_ready, readiness = await self.prepare_preview()
                preview_output += f"\n{readiness}"
                if not preview_ready:
                    code = 1
            if code == 0:
                self.last_image = tag
                self.last_source_digest = self.source_digest()
                self.last_build_seconds = duration
            result = {
                "build": number,
                "status": "preview_started" if code == 0 else "preview_failed",
                "exit_code": code,
                "duration_seconds": duration,
                "image": tag,
                "preview_url": "http://rootless-buildkit:18080" if code == 0 else None,
                "preview_output": preview_output[-4000:],
                "remaining_builds": self.config.max_builds - number,
            }
            self._record(result)
            return result

    async def prepare_preview(self) -> tuple[bool, str]:
        try:
            fixture = json.loads(self.config.preview_fixture_source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"cannot read public preview fixture: {exc}"
        deadline = time.monotonic() + 60
        last_detail = "preview did not become healthy"
        async with httpx.AsyncClient(timeout=4) as client:
            while time.monotonic() < deadline:
                try:
                    health = await client.get("http://rootless-buildkit:18080/healthz")
                    if health.status_code == 200:
                        reset = await client.post(
                            "http://rootless-buildkit:18081/__bench/reset",
                            headers={"X-Bench-Admin-Token": self.config.admin_token},
                            json={
                                "schema_version": 1,
                                "run_id": f"preview-{self.config.run_id}",
                                "seed": 1101,
                                "now": fixture["now"],
                                "fixture_path": "/bench-fixtures/1101.json",
                            },
                        )
                        if reset.status_code == 200:
                            return True, "preview healthy and reset to public seed 1101"
                        last_detail = (
                            f"preview reset returned HTTP {reset.status_code}: "
                            f"{reset.text[-1000:]}"
                        )
                        break
                    last_detail = f"preview health returned HTTP {health.status_code}"
                except httpx.HTTPError as exc:
                    last_detail = str(exc)
                await asyncio.sleep(0.5)
        return False, last_detail

    async def finalize(self) -> dict[str, Any]:
        async with self.lock:
            errors = self.validate_workspace()
            if errors:
                return {"status": "contract_error", "errors": errors}
            if self.last_image is None or self.last_source_digest is None:
                return {"status": "no_successful_preview"}
            current_digest = self.source_digest()
            if current_digest != self.last_source_digest:
                return {
                    "status": "source_changed_after_preview",
                    "expected_digest": self.last_source_digest,
                    "actual_digest": current_digest,
                }
            archive = self.config.artifacts / "final-image.tar"
            code, output = await self.command(
                "save", "--output", str(archive), self.last_image, timeout=600
            )
            if code:
                return {
                    "status": "export_failed",
                    "exit_code": code,
                    "log_tail": output[-12_000:],
                }
            archive_digest = hashlib.sha256()
            with archive.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    archive_digest.update(chunk)
            manifest = {
                "schema_version": "websitebench.final-image.v1",
                "status": "exported",
                "image": self.last_image,
                "source_sha256": current_digest,
                "build_seconds": self.last_build_seconds,
                "builds_used": self.build_count,
                "archive": archive.name,
                "archive_sha256": archive_digest.hexdigest(),
            }
            (self.config.artifacts / "final-image.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            return manifest

    def _record(self, result: dict[str, Any]) -> None:
        with (self.config.artifacts / "builds.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result, sort_keys=True) + "\n")

    async def preview_status(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get("http://rootless-buildkit:18080/healthz")
            return {"status": "ready" if response.status_code == 200 else "unhealthy", "http_status": response.status_code}
        except httpx.HTTPError as exc:
            return {"status": "unavailable", "detail": str(exc)}


def create_app(builder: CandidateBuilder) -> FastAPI:
    app = FastAPI(title="Candidate Builder", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "builds_used": builder.build_count, "builds_budget": builder.config.max_builds}

    @app.post("/v1/builds")
    async def build(authorization: str | None = Header(None)) -> dict[str, Any]:
        builder.authorize(authorization)
        return await builder.build()

    @app.get("/v1/preview")
    async def preview(authorization: str | None = Header(None)) -> dict[str, Any]:
        builder.authorize(authorization)
        return await builder.preview_status()

    @app.post("/v1/finalize")
    async def finalize(authorization: str | None = Header(None)) -> dict[str, Any]:
        builder.authorize(authorization)
        return await builder.finalize()

    return app


def from_environment() -> FastAPI:
    return create_app(
        CandidateBuilder(
            BuilderConfig(
                token=os.environ.get("BUILDER_TOKEN", "development-builder-token"),
                workspace=Path(os.environ.get("CANDIDATE_WORKSPACE", "/workspace/candidate")),
                artifacts=Path(os.environ.get("BUILDER_ARTIFACT_DIR", "/tmp/websitebench-builds")),
                docker_host=os.environ.get("DOCKER_HOST", "tcp://rootless-buildkit:2375"),
                max_builds=int(os.environ.get("CANDIDATE_BUILD_BUDGET", "20")),
                run_id=os.environ.get("RUN_ID", "development"),
                admin_token=os.environ.get("BENCH_ADMIN_TOKEN", "development-admin-token"),
                mailbox_delivery_token=os.environ.get(
                    "MAILBOX_DELIVERY_TOKEN", "development-mail-token"
                ),
                preview_fixture_mount=os.environ.get(
                    "PREVIEW_FIXTURE_MOUNT", "/bench-public-fixtures"
                ),
                preview_fixture_source=Path(
                    os.environ.get(
                        "PREVIEW_FIXTURE_SOURCE", "/task/public/fixtures/1101.json"
                    )
                ),
                preview_schema_mount=os.environ.get(
                    "PREVIEW_SCHEMA_MOUNT", "/bench-schemas"
                ),
                mailbox_url=os.environ.get("MAILBOX_API_URL", "http://mailbox:8025"),
                public_mailbox_url=os.environ.get(
                    "PUBLIC_MAILBOX_URL", "http://mailbox:8025"
                ),
                public_site_url=os.environ.get(
                    "PUBLIC_SITE_URL", "http://rootless-buildkit:18080"
                ),
            )
        )
    )


app = from_environment()
