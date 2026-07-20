"""Trusted host launcher for the final candidate runtime sandbox."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .policy import scan_candidate


def safe_name(value: str, limit: int = 48) -> str:
    normalized = "".join(
        character.casefold() if character.isalnum() else "-" for character in value
    )
    return "-".join(part for part in normalized.split("-") if part)[:limit]


def read_env(path: Path | str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


@dataclass
class CandidateLaunch:
    image: str
    container: str
    volume: str
    project: str
    resources: dict[str, Any]


@dataclass(frozen=True)
class CandidateRuntimeConfig:
    """All site/runtime values consumed by the trusted final launcher."""

    image_prefix: str = "websitebench-final"
    container_prefix: str = "wb-candidate"
    volume_prefix: str = "wb-candidate-data"
    hostname: str = "candidate-app"
    network: str = "candidate-web"
    private_fixture_sources: tuple[Path, ...] = ()
    private_reference_sources: tuple[Path, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    pids_limit: int = 512
    memory: str = "1g"
    cpus: float = 2.0
    tmpfs: str = "/tmp:rw,noexec,nosuid,size=128m"
    health_timeout_seconds: int = 60
    accepted_states: tuple[str, ...] = ("healthy", "running")
    fixture_mount: str = "/bench-fixtures"
    schema_mount: str = "/bench-schemas"

    @classmethod
    def from_run_manifest(
        cls,
        manifest: Mapping[str, Any],
        *,
        corpus_root: Path,
    ) -> "CandidateRuntimeConfig":
        driver = manifest["driver"]
        runtime = driver["candidate_runtime"]
        networks = driver["networks"]
        mounts = driver.get("mounts", [])
        private_fixtures = tuple(
            (corpus_root / mount["source"]).resolve()
            for mount in mounts
            if mount["kind"] == "private_fixture"
        )
        private_references = tuple(
            (corpus_root / mount["source"]).resolve()
            for mount in mounts
            if mount["kind"] == "private_reference"
        )
        environment = tuple(
            sorted((name, str(value).lower() if isinstance(value, bool) else str(value)) for name, value in manifest["candidate_environment"].items())
        )
        return cls(
            image_prefix=runtime["image_prefix"],
            container_prefix=runtime["container_prefix"],
            volume_prefix=runtime["volume_prefix"],
            hostname=runtime["hostname"],
            network=networks[runtime["network_role"]],
            private_fixture_sources=private_fixtures,
            private_reference_sources=private_references,
            environment=environment,
            pids_limit=int(runtime["limits"]["pids"]),
            memory=str(runtime["limits"]["memory"]),
            cpus=float(runtime["limits"]["cpus"]),
            tmpfs=str(runtime["limits"]["tmpfs"]),
            health_timeout_seconds=int(runtime["health"]["timeout_seconds"]),
            accepted_states=tuple(runtime["health"]["accepted_states"]),
            fixture_mount=runtime["fixture_mount"],
            schema_mount=runtime["schema_mount"],
        )


class CandidateRuntime:
    def __init__(
        self,
        *,
        run_dir: Path,
        project: str,
        config: CandidateRuntimeConfig | None = None,
        repository_root: Path | None = None,
    ) -> None:
        self.run_dir = run_dir.resolve()
        # ``repository_root`` remains accepted for task-v1 callers, but runtime
        # behavior is exclusively configuration-driven.
        del repository_root
        self.config = config or CandidateRuntimeConfig()
        self.project = project
        self.candidate_root = self.run_dir / "candidate"
        name = safe_name(self.run_dir.name)
        self.image = f"{self.config.image_prefix}:{name}"
        self.container = f"{self.config.container_prefix}-{name}"
        self.volume = f"{self.config.volume_prefix}-{name}"
        self.log_path = self.run_dir / "eval" / "candidate-runtime.log"
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._peak_memory_bytes = 0

    @staticmethod
    def parse_memory(value: str) -> int:
        number = value.strip().split("/", 1)[0].strip()
        units = {
            "B": 1,
            "kB": 1000,
            "KiB": 1024,
            "MB": 1000**2,
            "MiB": 1024**2,
            "GB": 1000**3,
            "GiB": 1024**3,
        }
        for unit in sorted(units, key=len, reverse=True):
            if number.endswith(unit):
                return int(float(number[: -len(unit)].strip()) * units[unit])
        return int(float(number))

    def command(
        self,
        arguments: list[str],
        *,
        timeout: float = 900,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"$ {' '.join(arguments)}\n{result.stdout}{result.stderr}\n")
        if check and result.returncode:
            raise RuntimeError(f"command failed ({result.returncode}): {' '.join(arguments)}")
        return result

    def source_bytes(self) -> int:
        total = 0
        for path in self.candidate_root.rglob("*"):
            if path.is_file() and not path.is_symlink() and ".git" not in path.parts:
                total += path.stat().st_size
        return total

    def source_digest(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self.candidate_root.rglob("*")):
            if not path.is_file() or path.is_symlink() or ".git" in path.parts:
                continue
            relative = path.relative_to(self.candidate_root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        return digest.hexdigest()

    def _runtime_environment_file(self, values: Mapping[str, str]) -> Path:
        destination = self.run_dir / "candidate-runtime.env"
        lines = []
        for key, value in sorted(values.items()):
            if "\n" in value or "\r" in value:
                raise RuntimeError(f"candidate runtime environment {key} contains a newline")
            lines.append(f"{key}={value}\n")
        destination.write_text("".join(lines), encoding="utf-8")
        destination.chmod(0o600)
        return destination

    def build_and_start(self) -> CandidateLaunch:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        private_reference = self.config.private_reference_sources[0] if self.config.private_reference_sources else None
        findings = scan_candidate(self.candidate_root, private_reference=private_reference)
        (self.run_dir / "eval" / "source-policy.json").write_text(
            json.dumps([finding.to_dict() for finding in findings], indent=2) + "\n",
            encoding="utf-8",
        )
        blocking_findings = [
            finding
            for finding in findings
            if finding.hard_failure or finding.code == "MISSING_REQUIRED_PATH"
        ]
        if blocking_findings:
            summary = ", ".join(
                f"{finding.code}:{finding.path}" for finding in blocking_findings[:8]
            )
            raise RuntimeError(f"candidate source policy failed: {summary}")
        image_manifest_path = self.run_dir / "builds" / "final-image.json"
        if not image_manifest_path.is_file():
            raise RuntimeError("isolated builder did not export final-image.json")
        image_manifest = json.loads(image_manifest_path.read_text(encoding="utf-8"))
        if image_manifest.get("source_sha256") != self.source_digest():
            raise RuntimeError("candidate source changed after its final isolated build")
        archive_name = str(image_manifest.get("archive", ""))
        if not archive_name or Path(archive_name).name != archive_name:
            raise RuntimeError("isolated builder returned an invalid image archive name")
        archive_path = self.run_dir / "builds" / archive_name
        if not archive_path.is_file():
            raise RuntimeError("isolated builder did not export the final image archive")
        archive_digest = hashlib.sha256()
        with archive_path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                archive_digest.update(chunk)
        if archive_digest.hexdigest() != image_manifest.get("archive_sha256"):
            raise RuntimeError("final image archive digest does not match builder manifest")
        source_image = str(image_manifest.get("image", ""))
        if not source_image.startswith("websitebench-candidate:"):
            raise RuntimeError("isolated builder returned an invalid image tag")
        self.command(["docker", "load", "--input", str(archive_path)], check=True)
        self.command(["docker", "tag", source_image, self.image], check=True)
        build_seconds = float(image_manifest.get("build_seconds", 0))
        image_inspect = self.command(
            ["docker", "image", "inspect", self.image, "--format", "{{.Size}}"], check=True
        )
        image_bytes = int(image_inspect.stdout.strip())
        self.command(["docker", "rm", "-f", self.container])
        self.command(["docker", "volume", "rm", self.volume])
        self.command(["docker", "volume", "create", self.volume], check=True)
        values = read_env(self.run_dir / "secrets.env")
        public_fixture_root = self.run_dir / "public" / "fixtures"
        schema_root = self.run_dir / "schemas"
        mounts: list[str] = []
        for index, source in enumerate(self.config.private_fixture_sources):
            destination = self.config.fixture_mount if index == 0 else f"{self.config.fixture_mount}/private-{index}"
            mounts.extend(["--mount", f"type=bind,src={source},dst={destination},readonly"])
        for fixture in sorted(public_fixture_root.glob("*.json")):
            mounts.extend(
                [
                    "--mount",
                    f"type=bind,src={fixture},dst={self.config.fixture_mount}/{fixture.name},readonly",
                ]
            )
        mounts.extend(
            ["--mount", f"type=bind,src={schema_root},dst={self.config.schema_mount},readonly"]
        )
        runtime_environment = dict(self.config.environment)
        runtime_environment.setdefault("BENCH_ADMIN_TOKEN", values["BENCH_ADMIN_TOKEN"])
        runtime_environment.setdefault("MAILBOX_DELIVERY_TOKEN", values["MAILBOX_DELIVERY_TOKEN"])
        runtime_environment_path = self._runtime_environment_file(runtime_environment)
        create = self.command(
            [
                "docker",
                "create",
                "--name",
                self.container,
                "--hostname",
                self.config.hostname,
                "--network",
                "none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                f"--pids-limit={self.config.pids_limit}",
                f"--memory={self.config.memory}",
                f"--cpus={self.config.cpus}",
                "--tmpfs",
                self.config.tmpfs,
                "--mount",
                f"type=volume,src={self.volume},dst=/data",
                *mounts,
                "--env-file",
                str(runtime_environment_path),
                self.image,
            ],
            check=True,
        )
        del create
        self.command(
            [
                "docker",
                "network",
                "connect",
                "--alias",
                self.config.hostname,
                f"{self.project}_{self.config.network}",
                self.container,
            ],
            check=True,
        )
        startup_started = time.monotonic()
        self.command(["docker", "start", self.container], check=True)
        deadline = time.monotonic() + self.config.health_timeout_seconds
        status = ""
        while time.monotonic() < deadline:
            inspect = self.command(
                [
                    "docker",
                    "inspect",
                    self.container,
                    "--format",
                    "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                ],
                timeout=10,
            )
            status = inspect.stdout.strip()
            if status in set(self.config.accepted_states):
                break
            if status in {"unhealthy", "exited", "dead"}:
                raise RuntimeError(f"candidate failed startup health: {status}")
            time.sleep(1)
        else:
            raise RuntimeError(f"candidate readiness timed out: {status}")
        startup_seconds = time.monotonic() - startup_started
        resources = {
            "build_seconds": build_seconds,
            "startup_seconds": startup_seconds,
            "image_bytes": image_bytes,
            "source_bytes": self.source_bytes(),
            "peak_memory_bytes": 0,
            "p95_latency_ms": 0,
        }
        efficiency = {
            "clean_build_seconds": build_seconds,
            "image_bytes": image_bytes,
            "source_bytes": resources["source_bytes"],
        }
        (self.run_dir / "eval" / "resource-facts.json").write_text(
            json.dumps({"resources": resources, "efficiency": efficiency}, indent=2) + "\n",
            encoding="utf-8",
        )
        return CandidateLaunch(
            image=self.image,
            container=self.container,
            volume=self.volume,
            project=self.project,
            resources=resources,
        )

    def start_resource_monitor(self) -> None:
        self._monitor_stop.clear()

        def monitor() -> None:
            while not self._monitor_stop.wait(0.5):
                result = subprocess.run(
                    [
                        "docker",
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{.MemUsage}}",
                        self.container,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    try:
                        self._peak_memory_bytes = max(
                            self._peak_memory_bytes, self.parse_memory(result.stdout)
                        )
                    except ValueError:
                        pass

        self._monitor_thread = threading.Thread(target=monitor, name="candidate-memory", daemon=True)
        self._monitor_thread.start()

    def finish_resource_monitor(self) -> None:
        self._monitor_stop.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=15)
        path = self.run_dir / "eval" / "resource-facts.json"
        if not path.exists():
            return
        value = json.loads(path.read_text())
        value["resources"]["peak_memory_bytes"] = self._peak_memory_bytes
        value["efficiency"]["peak_memory_bytes"] = self._peak_memory_bytes
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def collect_logs(self) -> None:
        if shutil.which("docker"):
            self.command(["docker", "logs", self.container], timeout=30)

    def stop(self, *, remove_volume: bool = False) -> None:
        self.collect_logs()
        self.command(["docker", "rm", "-f", self.container], timeout=30)
        if remove_volume:
            self.command(["docker", "volume", "rm", self.volume], timeout=30)
