"""Load, initialize, and validate an offline-clone harness manifest."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from yaml.constructor import ConstructorError
from yaml.resolver import BaseResolver


MANIFEST_NAME = "clone.yaml"
MANIFEST_SCHEMA = "offline-clone-manifest.schema.json"
ASSET_SCHEMA = "offline-clone-asset-manifest.schema.json"
COVERAGE_SCHEMA = "offline-clone-coverage.schema.json"
PURPOSE_SCHEMA = "offline-clone-purpose.schema.json"
INVARIANTS_SCHEMA = "offline-clone-invariants.schema.json"
CHECKPOINTS_SCHEMA = "offline-clone-checkpoints.schema.json"
ACCEPTANCE_EVIDENCE_SCHEMA = "offline-clone-acceptance-evidence.schema.json"
GATE_ORDER = ("source", "assets", "frontend", "backend", "release")
ACCEPTANCE_EVIDENCE_KINDS = (
    "visual",
    "browser",
    "network",
    "migration",
    "independent-audit",
    "full-suite",
)
MAX_ACCEPTANCE_EVIDENCE_BYTES = 1024 * 1024
MAX_RAW_ACCEPTANCE_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_RAW_JSON_EVIDENCE_BYTES = 16 * 1024 * 1024
MAX_RAW_BYTES_PER_EVIDENCE = 160 * 1024 * 1024
MAX_RAW_BYTES_PER_RELEASE = 384 * 1024 * 1024
MAX_VISUAL_EVIDENCE_PIXELS = 16_777_216
MAX_MANIFEST_BYTES = 4 * 1024 * 1024

_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "accesskey",
        "accesstoken",
        "apikey",
        "auth",
        "authorization",
        "bearer",
        "code",
        "cookie",
        "credential",
        "clientsecret",
        "csrftoken",
        "idtoken",
        "jwt",
        "key",
        "keypairid",
        "passwd",
        "password",
        "privatekey",
        "pwd",
        "sas",
        "secret",
        "secretkey",
        "session",
        "sessionid",
        "sig",
        "signature",
        "token",
    }
)
_SENSITIVE_QUERY_SUFFIXES = (
    "accesstoken",
    "apikey",
    "authorization",
    "credential",
    "idtoken",
    "password",
    "privatekey",
    "secret",
    "secretkey",
    "securitytoken",
    "sessionid",
    "signature",
    "token",
)
_URL_CREDENTIAL_FINDINGS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "bearer_token",
        "client_secret",
        "cookie",
        "credential_flag",
        "jwt",
        "low_entropy_secret_hash",
        "password",
        "passwd",
        "private_key",
        "provider_key",
        "pwd",
        "refresh_token",
        "secret",
        "session_id",
        "session_token",
        "smtp_password",
    }
)
_MALFORMED_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that refuses ambiguous duplicate mapping keys."""


def _construct_unique_yaml_mapping(
    loader: _UniqueKeySafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_yaml_mapping
)


@dataclass(frozen=True)
class ValidationProblem:
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}"


class ManifestValidationError(ValueError):
    def __init__(self, problems: list[ValidationProblem]) -> None:
        self.problems = tuple(problems)
        detail = "\n".join(f"- {problem}" for problem in problems)
        super().__init__(f"offline clone manifest validation failed:\n{detail}")


@dataclass(frozen=True)
class LoadedManifest:
    path: Path
    root: Path
    data: dict[str, Any]
    sha256: str

    def resolve(self, relative: str, *, must_exist: bool = False) -> Path:
        return resolve_inside(self.root, relative, must_exist=must_exist)

    @property
    def state_path(self) -> Path:
        return self.resolve(self.data["paths"]["state_file"])

    @property
    def trajectory_path(self) -> Path:
        return self.resolve(self.data["paths"]["trajectory_file"])

    @property
    def asset_manifest_path(self) -> Path:
        return self.resolve(self.data["paths"]["asset_manifest"], must_exist=True)

    @property
    def coverage_path(self) -> Path:
        return self.resolve(self.data["scope"]["coverage"], must_exist=True)

    @property
    def purpose_path(self) -> Path:
        return self.resolve(self.data["scope"]["purpose"], must_exist=True)

    @property
    def invariants_path(self) -> Path:
        return self.resolve(self.data["scope"]["invariants"], must_exist=True)

    @property
    def checkpoints_path(self) -> Path:
        return self.resolve(self.data["scope"]["checkpoints"], must_exist=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_source_url(value: str) -> None:
    """Reject ambiguous source URLs and URLs that may persist credentials."""

    if value != value.strip():
        raise ValueError("source URL must not have leading or trailing whitespace")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("source URL must not contain an ASCII control character")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"source URL is invalid: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise ValueError("source URL must be an absolute HTTP(S) URL")
    if username is not None or password is not None:
        raise ValueError("source URL must not contain userinfo")
    if "#" in value:
        raise ValueError("source URL must not contain a fragment")
    def decoded_layers(raw: str) -> list[str]:
        layers: list[str] = []
        current = raw
        for _ in range(5):
            if _MALFORMED_PERCENT_ESCAPE.search(current):
                raise ValueError("source URL contains a malformed percent escape")
            try:
                decoded = unquote(current, errors="strict")
            except UnicodeDecodeError as exc:
                raise ValueError("source URL contains invalid percent-encoded UTF-8") from exc
            if any(ord(character) < 32 or ord(character) == 127 for character in decoded):
                raise ValueError("source URL contains a decoded control character")
            layers.append(decoded)
            if decoded == current:
                return layers
            current = decoded
        try:
            extra_decoded = unquote(current, errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("source URL contains invalid percent-encoded UTF-8") from exc
        if extra_decoded != current:
            raise ValueError("source URL exceeds the supported percent-encoding depth")
        return layers

    decoded_values: list[str] = decoded_layers(parsed.path)
    for key, query_value in parse_qsl(
        parsed.query.replace(";", "&"), keep_blank_values=True
    ):
        for decoded_key in decoded_layers(key):
            normalized = re.sub(r"[^a-z0-9]", "", decoded_key.casefold())
            if normalized in _SENSITIVE_QUERY_KEYS or normalized.endswith(
                _SENSITIVE_QUERY_SUFFIXES
            ):
                raise ValueError(
                    f"source URL query key may contain credentials: {decoded_key!r}"
                )
        decoded_values.extend(decoded_layers(query_value))
    # Keys catch ordinary query credentials. Values and path components still
    # need scanning because redirect parameters and percent-encoding can hide
    # bearer/JWT/provider keys or assignments such as ``access_token=...``.
    # Filter to credential findings so benign prose containing "code" does not
    # become an over-broad URL rejection rule.
    from .records import sensitive_findings

    for decoded in decoded_values:
        findings = sorted(
            set(sensitive_findings(decoded)) & _URL_CREDENTIAL_FINDINGS
        )
        if findings:
            raise ValueError(
                "source URL path or query value may contain credentials: "
                + ", ".join(findings)
            )


def resolve_manifest_path(value: Path | str) -> Path:
    path = Path(value).resolve()
    return path / MANIFEST_NAME if path.is_dir() else path


def _schema_path(filename: str) -> Path:
    source_root = Path(__file__).resolve().parents[3]
    source_schema = source_root / "websitebench" / "schemas" / filename
    if source_schema.is_file():
        return source_schema
    bundled = Path(__file__).resolve().parents[1] / "viewer" / "_schemas" / filename
    if bundled.is_file():
        return bundled
    raise FileNotFoundError(f"offline clone schema is unavailable: {filename}")


def load_schema(filename: str) -> dict[str, Any]:
    value = json.loads(_schema_path(filename).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(value)
    return value


def _schema_problems(value: Any, filename: str, location: str) -> list[ValidationProblem]:
    validator = Draft202012Validator(
        load_schema(filename), format_checker=FormatChecker()
    )
    problems: list[ValidationProblem] = []
    for error in sorted(validator.iter_errors(value), key=lambda item: list(item.path)):
        suffix = ".".join(str(part) for part in error.absolute_path)
        problems.append(
            ValidationProblem(f"{location}.{suffix}" if suffix else location, error.message)
        )
    return problems


def resolve_inside(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    raw = Path(relative)
    windows = PureWindowsPath(relative)
    posix = PurePosixPath(relative)
    if raw.is_absolute() or windows.is_absolute() or windows.drive or posix.is_absolute():
        raise ValueError("absolute paths are forbidden")
    if ".." in windows.parts or ".." in posix.parts:
        raise ValueError(f"parent traversal is forbidden: {relative}")
    resolved_root = root.resolve()
    resolved = (resolved_root / raw).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"path escapes the site directory: {relative}")
    if must_exist and not resolved.exists():
        raise ValueError(f"path does not exist: {relative}")
    return resolved


def _is_link_or_reparse(path: Path) -> bool:
    """Inspect one lexical path component without following redirects."""

    try:
        metadata = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if attributes & reparse_flag:
        return True
    return bool(hasattr(path, "is_junction") and path.is_junction())


def resolve_safe_regular_file(root: Path, relative: str) -> Path:
    """Resolve a contained regular file without accepting redirected components."""

    resolved = resolve_inside(root, relative, must_exist=True)
    lexical = root.resolve()
    for component in Path(relative).parts:
        lexical = lexical / component
        if _is_link_or_reparse(lexical):
            raise ValueError(
                "path crosses a symbolic link, junction, or reparse point: "
                f"{relative}"
            )
    try:
        metadata = resolved.lstat()
    except OSError as exc:
        raise ValueError(f"cannot inspect file: {relative}: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"path is not a regular file: {relative}")
    return resolved


def _read_mapping(path: Path, *, yaml_allowed: bool = False) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
        value = (
            _strict_yaml_mapping(payload)
            if yaml_allowed
            else _strict_json_mapping(payload)
        )
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(value, dict):
        raise ValueError("document must contain an object")
    return value


def _strict_yaml_mapping(payload: bytes) -> dict[str, Any]:
    try:
        value = yaml.load(payload.decode("utf-8"), Loader=_UniqueKeySafeLoader)
    except (UnicodeDecodeError, yaml.YAMLError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(value, dict):
        raise ValueError("document must contain an object")
    return value


def _strict_json_mapping(payload: bytes) -> dict[str, Any]:
    """Parse a JSON object while rejecting duplicate keys at every depth."""

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(payload, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(value, dict):
        raise ValueError("document must contain an object")
    return value


def _read_safe_regular_bytes(
    root: Path, relative: str, *, maximum_bytes: int
) -> tuple[Path, bytes]:
    """Read one bounded regular artifact once, avoiding parse/hash divergence."""

    path = resolve_safe_regular_file(root, relative)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open regular file: {relative}: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"path is not a regular file: {relative}")
        if getattr(metadata, "st_nlink", 1) != 1:
            raise ValueError(f"file must have exactly one hard link: {relative}")
        if metadata.st_size > maximum_bytes:
            raise ValueError(
                f"acceptance evidence exceeds {maximum_bytes} bytes"
            )
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > maximum_bytes:
            raise ValueError(
                f"acceptance evidence exceeds {maximum_bytes} bytes"
            )
    finally:
        os.close(descriptor)
    return path, payload


def _hash_safe_regular_file(
    root: Path, relative: str, *, maximum_bytes: int
) -> tuple[Path, str, int, bytes]:
    """Hash one bounded regular file from a single no-follow descriptor."""

    path = resolve_safe_regular_file(root, relative)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open regular file: {relative}: {exc}") from exc
    digest = hashlib.sha256()
    observed = 0
    chunks: list[bytes] = []
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"path is not a regular file: {relative}")
        if getattr(metadata, "st_nlink", 1) != 1:
            raise ValueError(f"raw evidence must have exactly one hard link: {relative}")
        if metadata.st_size > maximum_bytes:
            raise ValueError(f"raw evidence exceeds {maximum_bytes} bytes")
        while chunk := os.read(descriptor, 1024 * 1024):
            observed += len(chunk)
            if observed > maximum_bytes:
                raise ValueError(f"raw evidence exceeds {maximum_bytes} bytes")
            digest.update(chunk)
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    return path, digest.hexdigest(), observed, b"".join(chunks)


def _validated_image_size(payload: bytes) -> tuple[int, int]:
    """Verify a raster without allowing compressed images to expand unboundedly."""

    try:
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                width, height = image.size
                if (
                    width < 1
                    or height < 1
                    or width * height > MAX_VISUAL_EVIDENCE_PIXELS
                ):
                    raise ValueError(
                        "image dimensions exceed the visual evidence pixel budget"
                    )
                image.verify()
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError(f"invalid or unsafe image: {exc}") from exc
    return width, height


def _decoded_rgb_image(payload: bytes) -> Any:
    """Decode a previously bounded raster, enforcing the limit before convert()."""

    try:
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                width, height = image.size
                if (
                    width < 1
                    or height < 1
                    or width * height > MAX_VISUAL_EVIDENCE_PIXELS
                ):
                    raise ValueError(
                        "image dimensions exceed the visual evidence pixel budget"
                    )
                image.load()
                return image.convert("RGB")
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError(f"invalid or unsafe image: {exc}") from exc


def _validate_raw_artifact_content(
    raw: dict[str, Any], payload: bytes
) -> tuple[dict[str, Any] | None, set[str], set[str]]:
    """Ensure a role is backed by parseable content, not a renamed text file."""

    role = raw["role"]
    media_type = raw["media_type"]
    image_roles = {"source-screenshot", "clone-screenshot"}
    json_roles = {
        "browser-trace",
        "network-log",
        "pre-state",
        "post-state",
        "migration-log",
        "state-inventory",
        "audit-report",
    }
    if role in image_roles or (role == "visual-diff" and media_type.startswith("image/")):
        if media_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError(f"{role} requires PNG, JPEG, or WebP media")
        try:
            _validated_image_size(payload)
        except ValueError as exc:
            raise ValueError(f"{role} is not a valid image: {exc}") from exc
        return None, set(), set()
    if role == "visual-diff" or role in json_roles or (
        role == "test-result" and media_type == "application/json"
    ):
        if media_type != "application/json":
            raise ValueError(f"{role} requires application/json")
        document = _strict_json_mapping(payload)
        expected_schema = {
            "visual-diff": "offline-clone.raw.visual-diff.v1",
            "browser-trace": "offline-clone.raw.browser-trace.v1",
            "network-log": "offline-clone.raw.network-log.v1",
            "pre-state": "offline-clone.raw.state-inventory.v1",
            "post-state": "offline-clone.raw.state-inventory.v1",
            "migration-log": "offline-clone.raw.migration-log.v1",
            "state-inventory": "offline-clone.raw.state-inventory.v1",
            "audit-report": "offline-clone.raw.audit-report.v1",
            "test-result": "offline-clone.raw.test-result.v1",
        }[role]
        if document.get("schema_version") != expected_schema:
            raise ValueError(f"{role} has an invalid schema_version")
        if (
            not isinstance(document.get("subject_ids"), list)
            or not document["subject_ids"]
            or any(not isinstance(item, str) or not item for item in document["subject_ids"])
            or len(set(document["subject_ids"])) != len(document["subject_ids"])
        ):
            raise ValueError(f"{role}.subject_ids must be a non-empty unique string array")
        list_field = {
            "visual-diff": "checkpoints",
            "browser-trace": "journeys",
            "network-log": "requests",
            "migration-log": "scenarios",
            "audit-report": "findings",
            "test-result": "tests",
        }.get(role)
        if list_field is not None and not isinstance(document.get(list_field), list):
            raise ValueError(f"{role}.{list_field} must be an array")
        if role == "visual-diff":
            for item in document["checkpoints"]:
                if not isinstance(item, dict) or not all(
                    key in item
                    for key in (
                        "id",
                        "source_artifact_sha256",
                        "clone_artifact_sha256",
                        "score",
                        "threshold",
                        "passed",
                        "metric",
                        "viewport",
                        "comparison_region",
                    )
                ):
                    raise ValueError("visual-diff checkpoint is incomplete")
                if (
                    not isinstance(item["id"], str)
                    or not isinstance(item["score"], (int, float))
                    or isinstance(item["score"], bool)
                    or not isinstance(item["threshold"], (int, float))
                    or isinstance(item["threshold"], bool)
                    or type(item["passed"]) is not bool
                    or not 0 <= item["score"] <= 1
                    or not 0 <= item["threshold"] <= 1
                    or item["passed"] != (item["score"] >= item["threshold"])
                    or item["metric"] != "pixel-mae-similarity-v1"
                    or not re.fullmatch(r"[a-f0-9]{64}", str(item["source_artifact_sha256"]))
                    or not re.fullmatch(r"[a-f0-9]{64}", str(item["clone_artifact_sha256"]))
                    or not isinstance(item["viewport"], dict)
                    or set(item["viewport"]) != {"width", "height"}
                    or not all(
                        type(item["viewport"].get(field)) is int
                        and item["viewport"][field] > 0
                        for field in ("width", "height")
                    )
                    or not isinstance(item["comparison_region"], dict)
                    or set(item["comparison_region"])
                    != {"x", "y", "width", "height"}
                    or not all(
                        type(item["comparison_region"].get(field)) is int
                        for field in ("x", "y", "width", "height")
                    )
                    or item["comparison_region"]["x"] < 0
                    or item["comparison_region"]["y"] < 0
                    or item["comparison_region"]["width"] < 1
                    or item["comparison_region"]["height"] < 1
                    or item["comparison_region"]["x"]
                    + item["comparison_region"]["width"]
                    > item["viewport"]["width"]
                    or item["comparison_region"]["y"]
                    + item["comparison_region"]["height"]
                    > item["viewport"]["height"]
                    or item["viewport"]["width"] * item["viewport"]["height"]
                    > MAX_VISUAL_EVIDENCE_PIXELS
                ):
                    raise ValueError("visual-diff checkpoint values are invalid")
        elif role == "browser-trace":
            for item in document["journeys"]:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("id"), str)
                    or item.get("status") not in {"passed", "failed"}
                    or type(item.get("steps_total")) is not int
                    or type(item.get("steps_passed")) is not int
                    or item["steps_total"] < 1
                    or not 0 <= item["steps_passed"] <= item["steps_total"]
                    or (item["status"] == "passed")
                    != (item["steps_passed"] == item["steps_total"])
                ):
                    raise ValueError("browser-trace journey values are invalid")
        elif role == "network-log":
            for item in document["requests"]:
                request_url = item.get("url") if isinstance(item, dict) else None
                derived_remote = True
                if isinstance(request_url, str):
                    parsed_request = urlsplit(request_url)
                    if not parsed_request.scheme and not parsed_request.netloc:
                        derived_remote = False
                    elif (
                        parsed_request.scheme in {"http", "https"}
                        and parsed_request.hostname
                        in {"localhost", "127.0.0.1", "::1"}
                    ):
                        derived_remote = False
                if (
                    not isinstance(item, dict)
                    or not isinstance(request_url, str)
                    or type(item.get("remote")) is not bool
                    or type(item.get("failed")) is not bool
                    or type(item.get("status")) is not int
                    or not 100 <= item["status"] <= 599
                    or item.get("remote") != derived_remote
                    or item.get("failed") != (item["status"] >= 400)
                ):
                    raise ValueError("network-log request values are invalid")
        elif role in {"pre-state", "post-state", "state-inventory"}:
            if (
                document.get("state_model") not in {"stateful", "stateless"}
                or not isinstance(document.get("persistence_surfaces"), list)
                or not isinstance(document.get("schema_fingerprint"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", document["schema_fingerprint"])
                or not isinstance(document.get("row_counts"), dict)
                or any(type(count) is not int or count < 0 for count in document["row_counts"].values())
            ):
                raise ValueError(f"{role} state inventory values are invalid")
        elif role == "migration-log":
            for item in document["scenarios"]:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("id"), str)
                    or item.get("status") not in {"passed", "failed"}
                    or any(
                        type(item.get(field)) is not int or item[field] < 0
                        for field in ("copies_tested", "schema_checks", "data_checks")
                    )
                ):
                    raise ValueError("migration-log scenario values are invalid")
        elif role == "audit-report":
            if (
                not isinstance(document.get("reviewer_method"), str)
                or not isinstance(document.get("independence_boundary"), str)
                or not isinstance(document.get("checks"), list)
                or not document["checks"]
            ):
                raise ValueError("audit-report reviewer boundary is missing")
            for item in document["checks"]:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("id"), str)
                    or not item["id"]
                    or item.get("status") not in {"passed", "failed"}
                ):
                    raise ValueError("audit-report check values are invalid")
            for item in document["findings"]:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("id"), str)
                    or item.get("priority") not in {"p0", "p1", "p2"}
                    or item.get("status") not in {"open", "closed"}
                ):
                    raise ValueError("audit-report finding values are invalid")
        elif role == "test-result":
            for item in document["tests"]:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("id"), str)
                    or item.get("status") not in {"passed", "failed"}
                ):
                    raise ValueError("test-result test values are invalid")
        record_field = {
            "visual-diff": "checkpoints",
            "browser-trace": "journeys",
            "migration-log": "scenarios",
            "test-result": "tests",
        }.get(role)
        record_ids: set[str] = set()
        if record_field is not None:
            record_id_list = [
                item.get("id")
                for item in document[record_field]
                if isinstance(item, dict)
            ]
            record_ids = set(record_id_list)
            if (
                not record_ids
                or any(not isinstance(item, str) or not item for item in record_id_list)
                or len(record_ids) != len(record_id_list)
            ):
                raise ValueError(
                    f"{role} record IDs must be unique and non-empty"
                )
        witnessed_subject_ids: set[str] = set()
        if role in {"visual-diff", "browser-trace", "migration-log"}:
            if record_ids != set(document["subject_ids"]):
                raise ValueError(
                    f"{role}.subject_ids must exactly equal its witnessed record IDs"
                )
            witnessed_subject_ids = record_ids
        elif role in {"network-log", "test-result", "audit-report"}:
            records = (
                document["requests"]
                if role == "network-log"
                else document["tests"]
                if role == "test-result"
                else document["checks"] + document["findings"]
            )
            for item in records:
                subjects = item.get("subject_ids") if isinstance(item, dict) else None
                if (
                    not isinstance(subjects, list)
                    or not subjects
                    or any(not isinstance(subject, str) or not subject for subject in subjects)
                    or len(set(subjects)) != len(subjects)
                ):
                    raise ValueError(
                        f"{role} records require non-empty unique subject_ids"
                    )
                witnessed_subject_ids.update(subjects)
            if witnessed_subject_ids != set(document["subject_ids"]):
                raise ValueError(
                    f"{role}.subject_ids must exactly equal the union witnessed by its records"
                )
        if role == "audit-report":
            audit_record_ids = [
                item.get("id")
                for item in document["checks"] + document["findings"]
            ]
            if (
                any(not isinstance(item, str) or not item for item in audit_record_ids)
                or len(set(audit_record_ids)) != len(audit_record_ids)
            ):
                raise ValueError(
                    "audit-report check/finding IDs must be unique and non-empty"
                )
            record_ids = set(audit_record_ids)
        return document, witnessed_subject_ids, record_ids
    raise ValueError(f"unsupported raw evidence role/media_type: {role}/{media_type}")


def _physical_file_identity(path: Path) -> tuple[Any, ...]:
    metadata = path.stat(follow_symlinks=False)
    if metadata.st_ino:
        return ("physical", metadata.st_dev, metadata.st_ino)
    return ("path", os.path.normcase(os.path.normpath(str(path))))


def manifest_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    observed = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            observed += len(chunk)
            if observed > MAX_MANIFEST_BYTES:
                raise ValueError(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
            digest.update(chunk)
    return digest.hexdigest()


def load_asset_manifest(path: Path) -> dict[str, Any]:
    value = _read_mapping(path)
    problems = _schema_problems(value, ASSET_SCHEMA, "asset_manifest")
    ids: set[str] = set()
    source_paths: set[str] = set()
    runtime_paths: set[str] = set()
    for index, asset in enumerate(value.get("assets", [])):
        if not isinstance(asset, dict):
            continue
        for field, seen in (
            ("id", ids),
            ("source_path", source_paths),
            ("runtime_path", runtime_paths),
        ):
            item = asset.get(field)
            if isinstance(item, str) and item in seen:
                problems.append(
                    ValidationProblem(
                        f"asset_manifest.assets.{index}.{field}",
                        f"duplicate value: {item}",
                    )
                )
            elif isinstance(item, str):
                seen.add(item)
        source_url = asset.get("source_url")
        if isinstance(source_url, str):
            try:
                validate_source_url(source_url)
            except ValueError as exc:
                problems.append(
                    ValidationProblem(
                        f"asset_manifest.assets.{index}.source_url", str(exc)
                    )
                )
    if problems:
        raise ManifestValidationError(problems)
    return value


def load_coverage_ledger(path: Path) -> dict[str, Any]:
    """Load coverage dimensions and enforce the relations JSON Schema cannot."""

    value = _read_mapping(path)
    problems = _schema_problems(value, COVERAGE_SCHEMA, "coverage")
    status = value.get("status")
    dimensions = value.get("dimensions")
    if status == "frozen" and isinstance(dimensions, list) and not dimensions:
        problems.append(
            ValidationProblem(
                "coverage.dimensions",
                "a frozen coverage ledger must contain at least one dimension",
            )
        )
    if (
        status == "frozen"
        and isinstance(dimensions, list)
        and dimensions
        and not any(
            isinstance(dimension, dict) and bool(dimension.get("required_items"))
            for dimension in dimensions
        )
    ):
        problems.append(
            ValidationProblem(
                "coverage.dimensions",
                "a frozen coverage ledger must include at least one non-empty denominator",
            )
        )
    dimension_ids: set[str] = set()
    for index, dimension in enumerate(dimensions if isinstance(dimensions, list) else []):
        if not isinstance(dimension, dict):
            continue
        dimension_id = dimension.get("id")
        if isinstance(dimension_id, str) and dimension_id in dimension_ids:
            problems.append(
                ValidationProblem(
                    f"coverage.dimensions.{index}.id",
                    f"duplicate dimension id: {dimension_id}",
                )
            )
        elif isinstance(dimension_id, str):
            dimension_ids.add(dimension_id)

        item_sets: dict[str, set[str]] = {}
        for field in ("required_items", "satisfied_items"):
            items = dimension.get(field)
            if not isinstance(items, list):
                continue
            seen: set[str] = set()
            for item_index, item in enumerate(items):
                if isinstance(item, str) and item in seen:
                    problems.append(
                        ValidationProblem(
                            f"coverage.dimensions.{index}.{field}.{item_index}",
                            f"duplicate item id: {item}",
                        )
                    )
                elif isinstance(item, str):
                    seen.add(item)
            item_sets[field] = seen

        required = item_sets.get("required_items")
        satisfied = item_sets.get("satisfied_items")
        if status == "frozen" and satisfied:
            problems.append(
                ValidationProblem(
                    f"coverage.dimensions.{index}.satisfied_items",
                    "a frozen source ledger must leave satisfaction empty; release evidence owns numerators",
                )
            )
        if required == set() and not dimension.get("rationale"):
            problems.append(
                ValidationProblem(
                    f"coverage.dimensions.{index}.rationale",
                    "an empty denominator requires an explicit N/A rationale",
                )
            )
        if required is not None and satisfied is not None:
            for item in sorted(satisfied - required):
                problems.append(
                    ValidationProblem(
                        f"coverage.dimensions.{index}.satisfied_items",
                        f"satisfied item is not required: {item}",
                    )
                )
    if problems:
        raise ManifestValidationError(problems)
    return value


def load_purpose_contract(path: Path) -> dict[str, Any]:
    value = _read_mapping(path)
    problems = _schema_problems(value, PURPOSE_SCHEMA, "purpose")
    if value.get("status") == "frozen" and str(value.get("statement", "")).strip().casefold().startswith("todo"):
        problems.append(
            ValidationProblem(
                "purpose.statement", "a frozen purpose must replace the TODO placeholder"
            )
        )
    if problems:
        raise ManifestValidationError(problems)
    return value


def load_invariants_contract(path: Path) -> dict[str, Any]:
    value = _read_mapping(path)
    problems = _schema_problems(value, INVARIANTS_SCHEMA, "invariants")
    seen: set[str] = set()
    for index, invariant in enumerate(value.get("invariants", [])):
        if not isinstance(invariant, dict) or not isinstance(invariant.get("id"), str):
            continue
        invariant_id = invariant["id"]
        if invariant_id in seen:
            problems.append(
                ValidationProblem(
                    f"invariants.invariants.{index}.id",
                    f"duplicate invariant id: {invariant_id}",
                )
            )
        seen.add(invariant_id)
    if problems:
        raise ManifestValidationError(problems)
    return value


def load_journeys_contract(path: Path) -> dict[str, Any]:
    value = _read_mapping(path)
    problems: list[ValidationProblem] = []
    if value.get("schema_version") != "offline-clone.journeys.v1":
        problems.append(
            ValidationProblem("journeys.schema_version", "invalid schema_version")
        )
    journeys = value.get("journeys")
    if not isinstance(journeys, list):
        problems.append(ValidationProblem("journeys.journeys", "must be an array"))
        journeys = []
    seen: set[str] = set()
    for index, journey in enumerate(journeys):
        location = f"journeys.journeys.{index}"
        if not isinstance(journey, dict):
            problems.append(ValidationProblem(location, "must be an object"))
            continue
        journey_id = journey.get("id")
        if not isinstance(journey_id, str) or not re.fullmatch(
            r"[a-z0-9]+(?:[._:-][a-z0-9]+)*", journey_id
        ):
            problems.append(ValidationProblem(f"{location}.id", "invalid journey id"))
        elif journey_id in seen:
            problems.append(
                ValidationProblem(f"{location}.id", f"duplicate journey id: {journey_id}")
            )
        else:
            seen.add(journey_id)
        if journey.get("priority") not in {"p0", "p1", "p2"}:
            problems.append(
                ValidationProblem(f"{location}.priority", "must be p0, p1, or p2")
            )
        if journey.get("status") not in {"draft", "frozen"}:
            problems.append(
                ValidationProblem(f"{location}.status", "must be draft or frozen")
            )
        if not isinstance(journey.get("steps"), list) or not journey.get("steps"):
            problems.append(
                ValidationProblem(f"{location}.steps", "must contain at least one step")
            )
    if problems:
        raise ManifestValidationError(problems)
    return value


def load_checkpoints_contract(path: Path) -> dict[str, Any]:
    """Load the visual oracle whose thresholds and source rasters are frozen."""

    value = _read_mapping(path)
    problems = _schema_problems(value, CHECKPOINTS_SCHEMA, "checkpoints")
    seen: set[str] = set()
    for index, checkpoint in enumerate(value.get("checkpoints", [])):
        if not isinstance(checkpoint, dict):
            continue
        checkpoint_id = checkpoint.get("id")
        if isinstance(checkpoint_id, str) and checkpoint_id in seen:
            problems.append(
                ValidationProblem(
                    f"checkpoints.checkpoints.{index}.id",
                    f"duplicate checkpoint id: {checkpoint_id}",
                )
            )
        elif isinstance(checkpoint_id, str):
            seen.add(checkpoint_id)
        contract = checkpoint.get("visual_contract")
        if contract is None:
            continue
        if not isinstance(contract, dict):
            continue
        viewport = contract.get("viewport")
        region = contract.get("comparison_region")
        if not isinstance(viewport, dict) or not isinstance(region, dict):
            continue
        values = tuple(
            viewport.get(field) for field in ("width", "height")
        ) + tuple(region.get(field) for field in ("x", "y", "width", "height"))
        if not all(type(item) is int for item in values):
            continue  # JSON Schema reports the individual type errors.
        viewport_width, viewport_height, x, y, width, height = values
        if x + width > viewport_width or y + height > viewport_height:
            problems.append(
                ValidationProblem(
                    f"checkpoints.checkpoints.{index}.visual_contract.comparison_region",
                    "comparison region must be contained by the frozen viewport",
                )
            )
        if viewport_width * viewport_height > MAX_VISUAL_EVIDENCE_PIXELS:
            problems.append(
                ValidationProblem(
                    f"checkpoints.checkpoints.{index}.visual_contract.viewport",
                    "viewport exceeds the visual evidence pixel budget",
                )
            )
    if problems:
        raise ManifestValidationError(problems)
    return value


def _acceptance_evidence_problems(
    value: dict[str, Any], *, manifest: LoadedManifest, kind: str,
    declaration: dict[str, Any], gate_attempt_id: str | None
) -> tuple[list[ValidationProblem], set[str], dict[str, set[str]], int]:
    """Check cross-document facts not expressible in the artifact schema."""

    problems = _schema_problems(value, ACCEPTANCE_EVIDENCE_SCHEMA, f"evidence.{kind}")
    if value.get("kind") != kind:
        problems.append(
            ValidationProblem(
                f"evidence.{kind}.kind",
                f"expected {kind!r}, got {value.get('kind')!r}",
            )
        )
    producer = declaration["producer_command_id"]
    if value.get("producer_command_id") != producer:
        problems.append(
            ValidationProblem(
                f"evidence.{kind}.producer_command_id",
                f"expected producing release command {producer!r}",
            )
        )
    if value.get("manifest_sha256") != manifest.sha256:
        problems.append(
            ValidationProblem(
                f"evidence.{kind}.manifest_sha256",
                "evidence was not generated for the current manifest",
            )
        )
    if gate_attempt_id is not None and value.get("gate_attempt_id") != gate_attempt_id:
        problems.append(
            ValidationProblem(
                f"evidence.{kind}.gate_attempt_id",
                "evidence was not generated by the current release attempt",
            )
        )

    metrics = value.get("metrics")
    status = value.get("status")
    if isinstance(metrics, dict):
        total = metrics.get("checks_total")
        passed = metrics.get("checks_passed")
        failed = metrics.get("checks_failed")
        if not all(type(item) is int for item in (total, passed, failed)):
            problems.append(
                ValidationProblem(
                    f"evidence.{kind}.metrics",
                    "checks_total/checks_passed/checks_failed must be integers",
                )
            )
        else:
            if passed + failed != total:
                problems.append(
                    ValidationProblem(
                        f"evidence.{kind}.metrics",
                        "checks_passed + checks_failed must equal checks_total",
                    )
                )
            if status == "passed" and (total < 1 or failed != 0 or passed != total):
                problems.append(
                    ValidationProblem(
                        f"evidence.{kind}.metrics",
                        "passed evidence requires one or more checks and zero failures",
                    )
                )
            if status == "not_applicable" and (total, passed, failed) != (0, 0, 0):
                problems.append(
                    ValidationProblem(
                        f"evidence.{kind}.metrics",
                        "not_applicable migration evidence requires zero check counts",
                    )
                )
        triplets = {
            "visual": ("checkpoints_total", "checkpoints_passed", "checkpoints_failed"),
            "browser": ("journeys_total", "journeys_passed", "journeys_failed"),
            "migration": (
                "migration_scenarios_total",
                "migration_scenarios_passed",
                "migration_scenarios_failed",
            ),
        }
        triplet = triplets.get(kind)
        if triplet and status == "passed":
            triplet_values = tuple(metrics.get(name) for name in triplet)
            if not all(type(item) is int for item in triplet_values):
                problems.append(
                    ValidationProblem(
                        f"evidence.{kind}.metrics",
                        f"{triplet[0]}/{triplet[1]}/{triplet[2]} must be integers",
                    )
                )
            else:
                domain_total, domain_passed, domain_failed = triplet_values
                if (
                    domain_total < 1
                    or domain_passed + domain_failed != domain_total
                    or domain_failed != 0
                ):
                    problems.append(
                        ValidationProblem(
                            f"evidence.{kind}.metrics",
                            f"{triplet[0]}/{triplet[1]}/{triplet[2]} must describe "
                            "one or more fully passed checks",
                        )
                    )
        if kind == "network" and status == "passed":
            requests = metrics.get("requests_total")
            forbidden = metrics.get("forbidden_remote_requests")
            failures = metrics.get("network_failures")
            if not all(type(item) is int for item in (requests, forbidden, failures)):
                problems.append(
                    ValidationProblem(
                        "evidence.network.metrics",
                        "network metric counts must be integers",
                    )
                )
            elif requests < 1 or forbidden != 0 or failures != 0:
                problems.append(
                    ValidationProblem(
                        "evidence.network.metrics",
                        "network evidence requires observed requests and zero remote-policy/network failures",
                    )
                )
        if kind == "independent-audit" and status == "passed":
            findings_total = metrics.get("findings_total")
            blocking = metrics.get("blocking_findings")
            if not all(type(item) is int for item in (findings_total, blocking)):
                problems.append(
                    ValidationProblem(
                        "evidence.independent-audit.metrics",
                        "audit finding counts must be integers",
                    )
                )
            elif blocking != 0 or findings_total < blocking:
                problems.append(
                    ValidationProblem(
                        "evidence.independent-audit.metrics",
                        "independent audit acceptance requires zero blocking findings",
                    )
                )
            if (
                metrics.get("reviewer_method") != value.get("reviewer_method")
                or metrics.get("independence_boundary")
                != value.get("independence_boundary")
            ):
                problems.append(
                    ValidationProblem(
                        "evidence.independent-audit.metrics",
                        "reviewer method and independence boundary must match the top-level audit declaration",
                    )
                )
        if kind == "full-suite" and status == "passed":
            discovered = metrics.get("tests_discovered")
            tests_passed = metrics.get("tests_passed")
            tests_failed = metrics.get("tests_failed")
            if not all(
                type(item) is int
                for item in (discovered, tests_passed, tests_failed)
            ):
                problems.append(
                    ValidationProblem(
                        "evidence.full-suite.metrics",
                        "full-suite test counts must be integers",
                    )
                )
            elif (
                discovered < 1
                or tests_failed != 0
                or tests_passed != discovered
            ):
                problems.append(
                    ValidationProblem(
                        "evidence.full-suite.metrics",
                        "full-suite evidence requires discovered/passed tests and zero failures",
                    )
                )

    state_model = manifest.data.get("state_model")
    if kind == "migration" and status == "not_applicable" and state_model != "stateless":
        problems.append(
            ValidationProblem(
                "evidence.migration.status",
                "migration may be not_applicable only when manifest state_model is stateless",
            )
        )
    if kind == "migration" and state_model == "stateful" and status != "passed":
        problems.append(
            ValidationProblem(
                "evidence.migration.status",
                "a stateful clone requires passed migration evidence",
            )
        )

    coverage = load_coverage_ledger(manifest.coverage_path)
    required_by_dimension = {
        dimension["id"]: set(dimension["required_items"])
        for dimension in coverage["dimensions"]
    }
    seen_dimensions: set[str] = set()
    for index, verified in enumerate(value.get("verified_coverage", [])):
        if not isinstance(verified, dict):
            continue
        dimension_id = verified.get("dimension_id")
        location = f"evidence.{kind}.verified_coverage.{index}"
        if isinstance(dimension_id, str) and dimension_id in seen_dimensions:
            problems.append(
                ValidationProblem(
                    f"{location}.dimension_id",
                    f"duplicate verified coverage dimension: {dimension_id}",
                )
            )
            continue
        if isinstance(dimension_id, str):
            seen_dimensions.add(dimension_id)
        required = required_by_dimension.get(dimension_id)
        if required is None:
            problems.append(
                ValidationProblem(
                    f"{location}.dimension_id",
                    f"unknown frozen coverage dimension: {dimension_id}",
                )
            )
            continue
        for item in verified.get("items", []):
            if item not in required:
                problems.append(
                    ValidationProblem(
                        f"{location}.items",
                        f"verified item is not in the frozen denominator: {item}",
                    )
                )
    if value.get("status") == "not_applicable" and value.get("verified_coverage"):
        problems.append(
            ValidationProblem(
                f"evidence.{kind}.verified_coverage",
                "not_applicable migration evidence cannot verify coverage items",
            )
        )

    witnessed_subject_ids: set[str] = set()
    raw_record_ids: dict[str, set[str]] = {}
    observed_raw_bytes = 0
    raw_values = value.get("raw_artifacts")
    if not isinstance(raw_values, list):
        return problems, witnessed_subject_ids, raw_record_ids, observed_raw_bytes
    declared_sizes = [
        raw.get("bytes")
        for raw in raw_values
        if isinstance(raw, dict)
    ]
    if (
        len(declared_sizes) != len(raw_values)
        or any(type(size) is not int or size < 0 for size in declared_sizes)
    ):
        problems.append(
            ValidationProblem(
                f"evidence.{kind}.raw_artifacts",
                "every raw artifact requires a non-negative integer byte count",
            )
        )
        return problems, witnessed_subject_ids, raw_record_ids, observed_raw_bytes
    if sum(declared_sizes) > MAX_RAW_BYTES_PER_EVIDENCE:
        problems.append(
            ValidationProblem(
                f"evidence.{kind}.raw_artifacts",
                f"declared raw evidence exceeds the per-evidence budget of "
                f"{MAX_RAW_BYTES_PER_EVIDENCE} bytes",
            )
        )
        return problems, witnessed_subject_ids, raw_record_ids, observed_raw_bytes

    artifact_root = manifest.resolve(manifest.data["paths"]["artifact_root"])
    summary_path = manifest.resolve(declaration["path"])
    seen_raw_identities: set[tuple[Any, ...]] = set()
    raw_documents: dict[str, list[dict[str, Any]]] = {}
    raw_declarations: dict[str, list[dict[str, Any]]] = {}
    raw_payloads: dict[str, dict[str, bytes]] = {}
    for index, raw in enumerate(raw_values):
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("path"), str)
            or not isinstance(raw.get("role"), str)
            or not isinstance(raw.get("media_type"), str)
        ):
            continue
        relative = raw["path"]
        role = raw["role"]
        location = f"evidence.{kind}.raw_artifacts.{index}"
        try:
            maximum_bytes = (
                MAX_RAW_ACCEPTANCE_EVIDENCE_BYTES
                if raw["media_type"].startswith("image/")
                else MAX_RAW_JSON_EVIDENCE_BYTES
            )
            path, observed_sha256, observed_bytes, payload = _hash_safe_regular_file(
                manifest.root,
                relative,
                maximum_bytes=maximum_bytes,
            )
            observed_raw_bytes += observed_bytes
            if observed_raw_bytes > MAX_RAW_BYTES_PER_EVIDENCE:
                problems.append(
                    ValidationProblem(
                        f"{location}.path",
                        f"observed raw evidence exceeds the per-evidence budget of "
                        f"{MAX_RAW_BYTES_PER_EVIDENCE} bytes",
                    )
                )
                break
            raw_document, raw_witnesses, record_ids = _validate_raw_artifact_content(
                raw, payload
            )
        except ValueError as exc:
            problems.append(ValidationProblem(f"{location}.path", str(exc)))
            continue
        if not (path == artifact_root or artifact_root in path.parents):
            problems.append(
                ValidationProblem(
                    f"{location}.path",
                    "raw acceptance evidence must be stored under paths.artifact_root",
                )
            )
        if path == summary_path:
            problems.append(
                ValidationProblem(
                    f"{location}.path",
                    "raw evidence must not reuse its structured summary artifact",
                )
            )
        try:
            identity = _physical_file_identity(path)
        except OSError as exc:
            problems.append(
                ValidationProblem(
                    f"{location}.path", f"cannot re-inspect raw artifact: {exc}"
                )
            )
            continue
        raw_declarations.setdefault(role, []).append(raw)
        if raw_document is not None:
            raw_documents.setdefault(role, []).append(raw_document)
            witnessed_subject_ids.update(raw_witnesses)
            duplicate_record_ids = raw_record_ids.setdefault(role, set()) & record_ids
            if duplicate_record_ids:
                problems.append(
                    ValidationProblem(
                        f"{location}.path",
                        f"raw {role} record IDs repeat across artifacts: "
                        + ", ".join(sorted(duplicate_record_ids)[:20]),
                    )
                )
            raw_record_ids[role].update(record_ids)
            if set(raw_document["subject_ids"]) != set(raw.get("subject_ids", [])):
                problems.append(
                    ValidationProblem(
                        f"{location}.subject_ids",
                        "raw JSON subject_ids must exactly match its declaration",
                    )
                )
        if role in {"source-screenshot", "clone-screenshot"}:
            raw_payloads.setdefault(role, {})[observed_sha256] = payload
        if identity in seen_raw_identities:
            problems.append(
                ValidationProblem(
                    f"{location}.path", "duplicate raw artifact path"
                )
            )
        seen_raw_identities.add(identity)
        if raw.get("sha256") != observed_sha256:
            problems.append(
                ValidationProblem(
                    f"{location}.sha256", "raw artifact hash does not match"
                )
            )
        if raw.get("bytes") != observed_bytes:
            problems.append(
                ValidationProblem(
                    f"{location}.bytes", "raw artifact byte count does not match"
                )
            )
        if raw.get("media_type") in {
            "application/json",
            "application/xml",
            "text/xml",
        }:
            from .records import sensitive_findings

            try:
                raw_text = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                problems.append(
                    ValidationProblem(
                        f"{location}.path", f"textual raw artifact is not UTF-8: {exc}"
                    )
                )
            else:
                findings = sensitive_findings(raw_text)
                if findings:
                    problems.append(
                        ValidationProblem(
                            f"{location}.path",
                            "raw acceptance evidence contains sensitive content: "
                            + ", ".join(findings),
                        )
                    )

    verified_items = {
        item
        for verified in value.get("verified_coverage", [])
        if isinstance(verified, dict)
        for item in verified.get("items", [])
        if isinstance(item, str)
    }
    missing_subjects = sorted(verified_items - witnessed_subject_ids)
    if missing_subjects:
        problems.append(
            ValidationProblem(
                f"evidence.{kind}.raw_artifacts.subject_ids",
                "verified coverage items are not bound to raw artifacts: "
                + ", ".join(missing_subjects[:20]),
            )
        )

    def raw_items(role: str, field: str) -> list[dict[str, Any]]:
        return [
            item
            for document in raw_documents.get(role, [])
            for item in document.get(field, [])
            if isinstance(item, dict)
        ]

    if isinstance(metrics, dict) and status == "passed":
        if kind == "visual":
            checkpoints = raw_items("visual-diff", "checkpoints")
            frozen_checkpoints = load_checkpoints_contract(manifest.checkpoints_path)
            frozen_contracts = {
                checkpoint["id"]: checkpoint["visual_contract"]
                for checkpoint in frozen_checkpoints["checkpoints"]
                if isinstance(checkpoint.get("visual_contract"), dict)
            }
            observed_ids = [item.get("id") for item in checkpoints]
            if (
                frozen_checkpoints.get("status") != "frozen"
                or len(observed_ids) != len(set(observed_ids))
                or set(observed_ids) != set(frozen_contracts)
            ):
                problems.append(
                    ValidationProblem(
                        "evidence.visual.raw_artifacts",
                        "raw visual checkpoints must exactly match the frozen checkpoint IDs",
                    )
                )
            source_hashes = {
                raw["sha256"] for raw in raw_declarations.get("source-screenshot", [])
            }
            clone_hashes = {
                raw["sha256"] for raw in raw_declarations.get("clone-screenshot", [])
            }
            if any(
                item["source_artifact_sha256"] not in source_hashes
                or item["clone_artifact_sha256"] not in clone_hashes
                for item in checkpoints
            ):
                problems.append(
                    ValidationProblem(
                        "evidence.visual.raw_artifacts",
                        "visual diff does not bind the declared source/clone screenshots",
                    )
                )
            for item in checkpoints:
                contract = frozen_contracts.get(item["id"])
                if contract is None:
                    continue
                if (
                    item["source_artifact_sha256"]
                    != contract["source_artifact_sha256"]
                    or item["metric"] != contract["metric"]
                    or item["threshold"] != contract["threshold"]
                    or item["viewport"] != contract["viewport"]
                    or item["comparison_region"] != contract["comparison_region"]
                ):
                    problems.append(
                        ValidationProblem(
                            "evidence.visual.raw_artifacts",
                            f"raw visual checkpoint does not match frozen contract: {item['id']}",
                        )
                    )
                    continue
                source_payload = raw_payloads.get("source-screenshot", {}).get(
                    item["source_artifact_sha256"]
                )
                clone_payload = raw_payloads.get("clone-screenshot", {}).get(
                    item["clone_artifact_sha256"]
                )
                if source_payload is None or clone_payload is None:
                    continue
                try:
                    from PIL import ImageChops, ImageStat

                    source_rgb = _decoded_rgb_image(source_payload)
                    clone_rgb = _decoded_rgb_image(clone_payload)
                    viewport_size = (
                        contract["viewport"]["width"],
                        contract["viewport"]["height"],
                    )
                    if source_rgb.size != viewport_size or clone_rgb.size != viewport_size:
                        raise ValueError(
                            "source and clone screenshot sizes must match the frozen viewport"
                        )
                    region = contract["comparison_region"]
                    box = (
                        region["x"],
                        region["y"],
                        region["x"] + region["width"],
                        region["y"] + region["height"],
                    )
                    difference = ImageChops.difference(
                        source_rgb.crop(box), clone_rgb.crop(box)
                    )
                    similarity = 1.0 - sum(ImageStat.Stat(difference).mean) / (
                        255 * len(difference.getbands())
                    )
                except ValueError as exc:
                    problems.append(
                        ValidationProblem(
                            "evidence.visual.raw_artifacts",
                            f"cannot recompute pixel similarity: {exc}",
                        )
                    )
                    continue
                if (
                    abs(float(item["score"]) - similarity) > 1e-6
                    or item["passed"] != (similarity >= float(item["threshold"]))
                ):
                    problems.append(
                        ValidationProblem(
                            "evidence.visual.raw_artifacts",
                            "declared visual score/pass does not match recomputed pixels",
                        )
                    )
            raw_total = len(checkpoints)
            raw_passed = sum(item.get("passed") is True for item in checkpoints)
            raw_failed = raw_total - raw_passed
            if (
                metrics.get("checkpoints_total"),
                metrics.get("checkpoints_passed"),
                metrics.get("checkpoints_failed"),
            ) != (raw_total, raw_passed, raw_failed):
                problems.append(
                    ValidationProblem(
                        "evidence.visual.metrics",
                        "visual checkpoint metrics do not match the raw diff records",
                    )
                )
        elif kind == "browser":
            journeys = raw_items("browser-trace", "journeys")
            raw_passed = sum(item.get("status") == "passed" for item in journeys)
            if (
                metrics.get("journeys_total"),
                metrics.get("journeys_passed"),
                metrics.get("journeys_failed"),
            ) != (len(journeys), raw_passed, len(journeys) - raw_passed):
                problems.append(
                    ValidationProblem(
                        "evidence.browser.metrics",
                        "browser journey metrics do not match the raw trace",
                    )
                )
        elif kind == "network":
            requests = raw_items("network-log", "requests")
            remote = sum(item.get("remote") is True for item in requests)
            failed_requests = sum(item.get("failed") is True for item in requests)
            if (
                metrics.get("requests_total"),
                metrics.get("forbidden_remote_requests"),
                metrics.get("network_failures"),
            ) != (len(requests), remote, failed_requests):
                problems.append(
                    ValidationProblem(
                        "evidence.network.metrics",
                        "network metrics do not match the raw request log",
                    )
                )
        elif kind == "migration":
            scenarios = raw_items("migration-log", "scenarios")
            raw_passed = sum(item.get("status") == "passed" for item in scenarios)
            observed = (
                len(scenarios),
                raw_passed,
                len(scenarios) - raw_passed,
                sum(item.get("copies_tested", 0) for item in scenarios),
                sum(item.get("schema_checks", 0) for item in scenarios),
                sum(item.get("data_checks", 0) for item in scenarios),
            )
            declared = (
                metrics.get("migration_scenarios_total"),
                metrics.get("migration_scenarios_passed"),
                metrics.get("migration_scenarios_failed"),
                metrics.get("copies_tested"),
                metrics.get("schema_checks"),
                metrics.get("data_checks"),
            )
            if declared != observed:
                problems.append(
                    ValidationProblem(
                        "evidence.migration.metrics",
                        "migration metrics do not match raw scenario results",
                    )
                )
            inventories = raw_documents.get("pre-state", []) + raw_documents.get(
                "post-state", []
            )
            if any(item.get("state_model") != "stateful" for item in inventories):
                problems.append(
                    ValidationProblem(
                        "evidence.migration.raw_artifacts",
                        "stateful migration requires stateful pre/post inventories",
                    )
                )
        elif kind == "independent-audit":
            findings = raw_items("audit-report", "findings")
            blocking = sum(
                item.get("priority") in {"p0", "p1"}
                and item.get("status") == "open"
                for item in findings
            )
            audit_documents = raw_documents.get("audit-report", [])
            audit_checks = [
                check
                for document in audit_documents
                for check in document.get("checks", [])
                if isinstance(check, dict)
            ]
            audit_checks_passed = sum(
                check.get("status") == "passed" for check in audit_checks
            )
            if (
                metrics.get("findings_total") != len(findings)
                or metrics.get("blocking_findings") != blocking
                or metrics.get("checks_total") != len(audit_checks)
                or metrics.get("checks_passed") != audit_checks_passed
                or metrics.get("checks_failed")
                != len(audit_checks) - audit_checks_passed
                or any(
                    document.get("reviewer_method") != value.get("reviewer_method")
                    or document.get("independence_boundary")
                    != value.get("independence_boundary")
                    for document in audit_documents
                )
            ):
                problems.append(
                    ValidationProblem(
                        "evidence.independent-audit.metrics",
                        "audit metrics/boundary do not match the raw report",
                    )
                )
        elif kind == "full-suite":
            tests = raw_items("test-result", "tests")
            raw_passed = sum(item.get("status") == "passed" for item in tests)
            if (
                metrics.get("tests_discovered"),
                metrics.get("tests_passed"),
                metrics.get("tests_failed"),
            ) != (len(tests), raw_passed, len(tests) - raw_passed):
                problems.append(
                    ValidationProblem(
                        "evidence.full-suite.metrics",
                        "full-suite metrics do not match the raw test results",
                    )
                )
    elif kind == "migration" and status == "not_applicable":
        inventories = raw_documents.get("state-inventory", [])
        if (
            not inventories
            or any(
                item.get("state_model") != "stateless"
                or item.get("persistence_surfaces") != []
                for item in inventories
            )
        ):
            problems.append(
                ValidationProblem(
                    "evidence.migration.raw_artifacts",
                    "stateless migration N/A requires an empty stateless inventory",
                )
            )

    # Reuse the trajectory scanner as a fail-closed structured secret/PII
    # guard. The local import avoids a module import cycle.
    from .records import sensitive_findings

    findings = sensitive_findings(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    if findings:
        problems.append(
            ValidationProblem(
                f"evidence.{kind}",
                "structured acceptance evidence contains sensitive content: "
                + ", ".join(findings),
            )
        )
    return problems, witnessed_subject_ids, raw_record_ids, observed_raw_bytes


def verify_acceptance_evidence(
    manifest: LoadedManifest, *, gate_attempt_id: str | None = None
) -> list[dict[str, Any]]:
    """Load and bind all six release artifacts to this manifest and attempt."""

    results: list[dict[str, Any]] = []
    problems: list[ValidationProblem] = []
    raw_bytes_total = 0
    for kind in ACCEPTANCE_EVIDENCE_KINDS:
        try:
            artifact = verify_acceptance_evidence_artifact(
                manifest, kind=kind, gate_attempt_id=gate_attempt_id
            )
        except ManifestValidationError as exc:
            problems.extend(exc.problems)
        else:
            results.append(artifact)
            raw_bytes_total += artifact["_raw_bytes_total"]
            if raw_bytes_total > MAX_RAW_BYTES_PER_RELEASE:
                problems.append(
                    ValidationProblem(
                        "evidence.raw_artifacts",
                        f"release raw evidence exceeds the aggregate budget of "
                        f"{MAX_RAW_BYTES_PER_RELEASE} bytes",
                    )
                )
                break
    raw_path_owners: dict[tuple[Any, ...], tuple[str, str]] = {}
    for artifact in results:
        for raw in artifact.get("raw_artifacts") or []:
            if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
                continue
            try:
                raw_path = resolve_safe_regular_file(manifest.root, raw["path"])
                identity = _physical_file_identity(raw_path)
            except (OSError, ValueError):
                continue  # The per-artifact validation already reports this.
            owner = raw_path_owners.setdefault(
                identity, (artifact["kind"], raw["path"])
            )
            if owner[0] != artifact["kind"]:
                problems.append(
                    ValidationProblem(
                        f"evidence.{artifact['kind']}.raw_artifacts",
                        f"raw artifact physical identity is already owned by evidence "
                        f"kind {owner[0]} at {owner[1]}: {raw['path']}",
                    )
                )
    verified_by_kind: dict[str, dict[str, set[str]]] = {}
    for artifact in results:
        for verified in artifact.get("verified_coverage") or []:
            if not isinstance(verified, dict):
                continue
            dimension_id = verified.get("dimension_id")
            items = verified.get("items")
            if isinstance(dimension_id, str) and isinstance(items, list):
                verified_by_kind.setdefault(artifact["kind"], {}).setdefault(
                    dimension_id, set()
                ).update(
                    item for item in items if isinstance(item, str)
                )
    coverage = load_coverage_ledger(manifest.coverage_path)
    for dimension in coverage["dimensions"]:
        required = set(dimension["required_items"])
        required_kinds = set(dimension["required_evidence_kinds"])
        for kind, dimensions in verified_by_kind.items():
            if dimension["id"] in dimensions and kind not in required_kinds:
                problems.append(
                    ValidationProblem(
                        f"evidence.{kind}.verified_coverage",
                        f"evidence kind is not authorized for coverage dimension {dimension['id']}",
                    )
                )
        for kind in sorted(required_kinds):
            observed = verified_by_kind.get(kind, {}).get(dimension["id"], set())
            missing = sorted(required - observed)
            if missing:
                preview = ", ".join(missing[:20])
                if len(missing) > 20:
                    preview += f" (+{len(missing) - 20} more)"
                problems.append(
                    ValidationProblem(
                        f"evidence.coverage.{dimension['id']}.{kind}",
                        "required evidence kind does not cover frozen items: "
                        + preview,
                    )
                )
    full_suite = next(
        (artifact for artifact in results if artifact["kind"] == "full-suite"), None
    )
    if full_suite is not None:
        observed_test_ids = {
            test_id
            for test_id in full_suite.get("_record_ids_by_role", {}).get(
                "test-result", []
            )
            if isinstance(test_id, str)
        }
        invariants = load_invariants_contract(manifest.invariants_path)
        required_test_ids = {
            test_id
            for invariant in invariants["invariants"]
            if invariant["priority"] == "p0"
            for field in ("positive_test_refs", "negative_test_refs")
            for test_id in invariant[field]
        }
        missing_tests = sorted(required_test_ids - observed_test_ids)
        if missing_tests:
            preview = ", ".join(missing_tests[:20])
            if len(missing_tests) > 20:
                preview += f" (+{len(missing_tests) - 20} more)"
            problems.append(
                ValidationProblem(
                    "evidence.full-suite.raw_artifacts",
                    "raw test results do not include all p0 invariant test refs: "
                    + preview,
                )
            )
    if problems:
        raise ManifestValidationError(problems)
    return [
        {key: value for key, value in artifact.items() if not key.startswith("_")}
        for artifact in results
    ]


def verify_acceptance_evidence_artifact(
    manifest: LoadedManifest, *, kind: str, gate_attempt_id: str | None = None
) -> dict[str, Any]:
    """Validate one producer-bound artifact without requiring the other kinds yet."""

    if kind not in ACCEPTANCE_EVIDENCE_KINDS:
        raise ManifestValidationError(
            [ValidationProblem("evidence.kind", f"unsupported evidence kind: {kind}")]
        )
    declaration = manifest.data["gates"]["release"]["evidence"][kind]
    relative = declaration["path"]
    try:
        _, payload = _read_safe_regular_bytes(
            manifest.root,
            relative,
            maximum_bytes=MAX_ACCEPTANCE_EVIDENCE_BYTES,
        )
        value = _strict_json_mapping(payload)
    except ValueError as exc:
        raise ManifestValidationError(
            [ValidationProblem(f"evidence.{kind}", str(exc))]
        ) from exc
    problems, witnessed_subject_ids, record_ids, raw_bytes_total = _acceptance_evidence_problems(
        value,
        manifest=manifest,
        kind=kind,
        declaration=declaration,
        gate_attempt_id=gate_attempt_id,
    )
    if problems:
        raise ManifestValidationError(problems)
    return {
        "kind": kind,
        "path": relative,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "producer_command_id": value.get("producer_command_id"),
        "gate_attempt_id": value.get("gate_attempt_id"),
        "status": value.get("status"),
        "summary": value.get("summary"),
        "metrics": value.get("metrics"),
        "boundaries": value.get("boundaries"),
        "verified_coverage": value.get("verified_coverage"),
        "raw_artifacts": value.get("raw_artifacts"),
        "_witnessed_subject_ids": sorted(witnessed_subject_ids),
        "_record_ids_by_role": {
            role: sorted(ids) for role, ids in sorted(record_ids.items())
        },
        "_raw_bytes_total": raw_bytes_total,
        **(
            {"reason": value["reason"]}
            if isinstance(value.get("reason"), str)
            else {}
        ),
        **(
            {"reviewer_method": value["reviewer_method"]}
            if isinstance(value.get("reviewer_method"), str)
            else {}
        ),
        **(
            {"independence_boundary": value["independence_boundary"]}
            if isinstance(value.get("independence_boundary"), str)
            else {}
        ),
    }


def acceptance_evidence_path_snapshot(
    manifest: LoadedManifest,
) -> dict[str, str | None]:
    """Fingerprint six structured artifacts for per-command causal checks."""

    snapshot: dict[str, str | None] = {}
    declarations = manifest.data["gates"]["release"]["evidence"]
    for kind in ACCEPTANCE_EVIDENCE_KINDS:
        relative = declarations[kind]["path"]
        candidate = manifest.resolve(relative)
        if not candidate.exists() and not candidate.is_symlink():
            snapshot[kind] = None
            continue
        try:
            _, payload = _read_safe_regular_bytes(
                manifest.root,
                relative,
                maximum_bytes=MAX_ACCEPTANCE_EVIDENCE_BYTES,
            )
        except ValueError as exc:
            raise ManifestValidationError(
                [ValidationProblem(f"evidence.{kind}", str(exc))]
            ) from exc
        snapshot[kind] = hashlib.sha256(payload).hexdigest()
    return snapshot


def acceptance_evidence_fingerprint(evidence: list[dict[str, Any]]) -> str:
    """Fingerprint accepted artifact identities without command output."""

    payload = [
        {"kind": item["kind"], "path": item["path"], "sha256": item["sha256"]}
        for item in evidence
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def require_frozen_coverage(manifest: LoadedManifest) -> dict[str, Any]:
    """Return the ledger or fail closed while any source contract is a draft."""

    ledger = load_coverage_ledger(manifest.coverage_path)
    purpose = load_purpose_contract(manifest.purpose_path)
    invariants = load_invariants_contract(manifest.invariants_path)
    checkpoints = load_checkpoints_contract(manifest.checkpoints_path)
    problems: list[ValidationProblem] = []
    if ledger["status"] != "frozen":
        problems.append(
            ValidationProblem(
                "coverage.status",
                "source acceptance requires status 'frozen'",
            )
        )
    if purpose["status"] != "frozen":
        problems.append(
            ValidationProblem(
                "purpose.status", "source acceptance requires status 'frozen'"
            )
        )
    if invariants["status"] != "frozen":
        problems.append(
            ValidationProblem(
                "invariants.status", "source acceptance requires status 'frozen'"
            )
        )
    if checkpoints["status"] != "frozen":
        problems.append(
            ValidationProblem(
                "checkpoints.status", "source acceptance requires status 'frozen'"
            )
        )
    if problems:
        raise ManifestValidationError(
            problems
        )
    return ledger


def load_manifest(value: Path | str, *, check_references: bool = True) -> LoadedManifest:
    path = resolve_manifest_path(value)
    try:
        payload = path.read_bytes()
        if len(payload) > MAX_MANIFEST_BYTES:
            raise ValueError(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
        data = _strict_yaml_mapping(payload)
    except (OSError, ValueError) as exc:
        raise ManifestValidationError([ValidationProblem(str(path), str(exc))]) from exc
    problems = _schema_problems(data, MANIFEST_SCHEMA, "manifest")
    root = path.parent.resolve()

    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    origins = source.get("origins") if isinstance(source.get("origins"), list) else []
    for index, origin in enumerate(origins):
        if not isinstance(origin, str):
            continue
        try:
            validate_source_url(origin)
        except ValueError as exc:
            problems.append(
                ValidationProblem(f"manifest.source.origins.{index}", str(exc))
            )

    paths = data.get("paths") if isinstance(data.get("paths"), dict) else {}
    scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
    references: list[tuple[str, str, str]] = []
    for name in (
        "purpose",
        "invariants",
        "routes",
        "journeys",
        "checkpoints",
        "claims",
        "coverage",
    ):
        if isinstance(scope.get(name), str):
            references.append((f"scope.{name}", scope[name], "file"))
    for name in ("candidate_root", "asset_manifest"):
        if isinstance(paths.get(name), str):
            references.append(
                (f"paths.{name}", paths[name], "directory" if name == "candidate_root" else "file")
            )

    for name in ("artifact_root", "state_file", "trajectory_file"):
        if isinstance(paths.get(name), str):
            references.append((f"paths.{name}", paths[name], "future"))

    gates = data.get("gates") if isinstance(data.get("gates"), dict) else {}
    for gate_name, gate in gates.items():
        if not isinstance(gate, dict):
            continue
        command_ids: set[str] = set()
        for index, command in enumerate(gate.get("commands", [])):
            if not isinstance(command, dict):
                continue
            command_id = command.get("id")
            if command_id in command_ids:
                problems.append(
                    ValidationProblem(
                        f"manifest.gates.{gate_name}.commands.{index}.id",
                        f"duplicate command id: {command_id}",
                    )
                )
            elif isinstance(command_id, str):
                command_ids.add(command_id)
            if isinstance(command.get("cwd"), str):
                references.append(
                    (
                        f"gates.{gate_name}.commands.{index}.cwd",
                        command["cwd"],
                        "directory",
                    )
                )
        for index, relative in enumerate(gate.get("inputs", [])):
            if isinstance(relative, str):
                references.append((f"gates.{gate_name}.inputs.{index}", relative, "any"))

    source_gate = gates.get("source") if isinstance(gates.get("source"), dict) else {}
    candidate_root: Path | None = None
    if isinstance(paths.get("candidate_root"), str):
        try:
            candidate_root = resolve_inside(root, paths["candidate_root"])
        except ValueError:
            pass
    candidate_exclude_paths: list[Path] = []
    candidate_excludes = paths.get("candidate_excludes")
    if candidate_root is not None and isinstance(candidate_excludes, list):
        for index, relative in enumerate(candidate_excludes):
            if not isinstance(relative, str):
                continue
            location = f"manifest.paths.candidate_excludes.{index}"
            lexical_exclusion = candidate_root
            redirected = False
            for component in Path(relative).parts:
                lexical_exclusion = lexical_exclusion / component
                if _is_link_or_reparse(lexical_exclusion):
                    problems.append(
                        ValidationProblem(
                            location,
                            "candidate exclusion crosses a symbolic link, junction, or reparse point",
                        )
                    )
                    redirected = True
                    break
            if redirected:
                continue
            try:
                excluded = resolve_inside(candidate_root, relative)
            except ValueError as exc:
                problems.append(ValidationProblem(location, str(exc)))
                continue
            if excluded == candidate_root:
                problems.append(
                    ValidationProblem(location, "candidate_root itself cannot be excluded")
                )
                continue
            if excluded.exists() and (
                not excluded.is_dir() or _is_link_or_reparse(excluded)
            ):
                problems.append(
                    ValidationProblem(
                        location,
                        "an existing candidate exclusion must be a real directory",
                    )
                )
                continue
            if any(
                excluded == existing
                or excluded in existing.parents
                or existing in excluded.parents
                for existing in candidate_exclude_paths
            ):
                problems.append(
                    ValidationProblem(
                        location,
                        "candidate exclusions must be distinct and non-overlapping",
                    )
                )
                continue
            candidate_exclude_paths.append(excluded)
    if candidate_root is not None:
        for index, relative in enumerate(source_gate.get("inputs", [])):
            if not isinstance(relative, str):
                continue
            try:
                source_input = resolve_inside(root, relative)
            except ValueError:
                continue
            if source_input == candidate_root or candidate_root in source_input.parents:
                problems.append(
                    ValidationProblem(
                        f"manifest.gates.source.inputs.{index}",
                        "source truth inputs must not come from paths.candidate_root",
                    )
                )
        for index, command in enumerate(source_gate.get("commands", [])):
            if not isinstance(command, dict):
                continue
            cwd = command.get("cwd", ".")
            command_cwd: Path | None = root
            if isinstance(cwd, str):
                try:
                    command_cwd = resolve_inside(root, cwd)
                except ValueError:
                    command_cwd = None
                if command_cwd is not None and (
                    command_cwd == candidate_root or candidate_root in command_cwd.parents
                ):
                    problems.append(
                        ValidationProblem(
                            f"manifest.gates.source.commands.{index}.cwd",
                            "source verifier cwd must not be inside paths.candidate_root",
                        )
                    )
            argv = command.get("argv", [])
            if not isinstance(argv, list):
                continue
            for argument_index, argument in enumerate(argv[:-1]):
                if argument != "-m" or not isinstance(argv[argument_index + 1], str):
                    continue
                module_name = argv[argument_index + 1]
                if not module_name or any(
                    not component.isidentifier() for component in module_name.split(".")
                ):
                    continue
                module_path = Path(*module_name.split("."))
                for search_root in (command_cwd or root, root, root / "src"):
                    for module_candidate in (
                        search_root / module_path.with_suffix(".py"),
                        search_root / module_path,
                    ):
                        resolved_module = module_candidate.resolve()
                        if resolved_module == candidate_root or candidate_root in resolved_module.parents:
                            problems.append(
                                ValidationProblem(
                                    f"manifest.gates.source.commands.{index}.argv.{argument_index + 1}",
                                    "source verifier module must not come from paths.candidate_root",
                                )
                            )
            for argument_index, argument in enumerate(command.get("argv", [])):
                if not isinstance(argument, str):
                    continue
                if "{candidate_root}" in argument:
                    problems.append(
                        ValidationProblem(
                            f"manifest.gates.source.commands.{index}.argv.{argument_index}",
                            "source verifier must not consume paths.candidate_root",
                        )
                    )
                    continue
                expanded = argument
                for marker, replacement in {
                    "{site_dir}": str(root),
                    "{manifest}": str(path),
                }.items():
                    expanded = expanded.replace(marker, replacement)
                if "{python}" in expanded or "{" in expanded or "}" in expanded:
                    continue
                if expanded.startswith("-"):
                    if "=" not in expanded:
                        continue
                    expanded = expanded.split("=", 1)[1]
                    if not expanded:
                        continue
                raw_argument = Path(expanded)
                argument_path = (
                    raw_argument.resolve()
                    if raw_argument.is_absolute()
                    else ((command_cwd or root) / raw_argument).resolve()
                )
                try:
                    argument_path.relative_to(root)
                except ValueError:
                    continue
                if (
                    argument_path == candidate_root or candidate_root in argument_path.parents
                ):
                    problems.append(
                        ValidationProblem(
                            f"manifest.gates.source.commands.{index}.argv.{argument_index}",
                            "source verifier file must not be inside paths.candidate_root",
                        )
                    )
    source_inputs = set(
        item for item in source_gate.get("inputs", []) if isinstance(item, str)
    )
    for name in (
        "purpose",
        "invariants",
        "routes",
        "journeys",
        "checkpoints",
        "claims",
        "coverage",
    ):
        relative = scope.get(name)
        if isinstance(relative, str) and relative not in source_inputs:
            problems.append(
                ValidationProblem(
                    "manifest.gates.source.inputs",
                    f"must include scope.{name} ({relative}) in the frozen source fingerprint",
                )
            )

    release_gate = gates.get("release") if isinstance(gates.get("release"), dict) else {}
    release_commands = {
        command.get("id")
        for command in release_gate.get("commands", [])
        if isinstance(command, dict) and isinstance(command.get("id"), str)
    }
    release_command_definitions = {
        command.get("id"): command
        for command in release_gate.get("commands", [])
        if isinstance(command, dict) and isinstance(command.get("id"), str)
    }
    evidence_declarations = (
        release_gate.get("evidence")
        if isinstance(release_gate.get("evidence"), dict)
        else {}
    )
    artifact_root: Path | None = None
    if isinstance(paths.get("artifact_root"), str):
        try:
            artifact_root = resolve_inside(root, paths["artifact_root"])
        except ValueError:
            pass
    evidence_paths: set[Path] = set()
    for kind in ACCEPTANCE_EVIDENCE_KINDS:
        declaration = evidence_declarations.get(kind)
        if not isinstance(declaration, dict):
            continue
        producer = declaration.get("producer_command_id")
        if isinstance(producer, str) and producer not in release_commands:
            problems.append(
                ValidationProblem(
                    f"manifest.gates.release.evidence.{kind}.producer_command_id",
                    f"does not name a declared release command: {producer}",
                )
            )
        relative = declaration.get("path")
        if not isinstance(relative, str):
            continue
        references.append(
            (f"gates.release.evidence.{kind}.path", relative, "future")
        )
        try:
            resolved_evidence = resolve_inside(root, relative)
        except ValueError:
            continue
        if artifact_root is not None and not (
            resolved_evidence == artifact_root or artifact_root in resolved_evidence.parents
        ):
            problems.append(
                ValidationProblem(
                    f"manifest.gates.release.evidence.{kind}.path",
                    "acceptance evidence must be stored under paths.artifact_root",
                )
            )
        if resolved_evidence in evidence_paths:
            problems.append(
                ValidationProblem(
                    f"manifest.gates.release.evidence.{kind}.path",
                    "each acceptance evidence kind requires a distinct artifact path",
                )
            )
        evidence_paths.add(resolved_evidence)

    independent = evidence_declarations.get("independent-audit")
    independent_producer = (
        independent.get("producer_command_id")
        if isinstance(independent, dict)
        else None
    )
    other_producers = {
        declaration.get("producer_command_id")
        for kind, declaration in evidence_declarations.items()
        if kind != "independent-audit" and isinstance(declaration, dict)
    }
    if (
        isinstance(independent_producer, str)
        and independent_producer in other_producers
    ):
        problems.append(
            ValidationProblem(
                "manifest.gates.release.evidence.independent-audit.producer_command_id",
                "the independent audit command must produce only the audit artifact",
            )
        )
    independent_command = release_command_definitions.get(independent_producer)
    if isinstance(independent_command, dict):
        independent_signature = (
            independent_command.get("argv"),
            independent_command.get("cwd", "."),
        )
        for command_id, command in release_command_definitions.items():
            if command_id == independent_producer:
                continue
            signature = (command.get("argv"), command.get("cwd", "."))
            if signature == independent_signature:
                problems.append(
                    ValidationProblem(
                        f"manifest.gates.release.commands.{command_id}",
                        "independent audit must use a distinct argv/cwd producer boundary",
                    )
                )

    for location, relative, expected in references:
        try:
            resolved = resolve_inside(root, relative)
        except ValueError as exc:
            problems.append(ValidationProblem(f"manifest.{location}", str(exc)))
            continue
        if not check_references or expected == "future":
            continue
        if expected == "file" and not resolved.is_file():
            problems.append(ValidationProblem(f"manifest.{location}", "file is missing"))
        elif expected == "directory" and not resolved.is_dir():
            problems.append(ValidationProblem(f"manifest.{location}", "directory is missing"))
        elif expected == "any" and not resolved.exists():
            problems.append(ValidationProblem(f"manifest.{location}", "input is missing"))

    protected_locations = {"manifest": path}
    for name, relative in scope.items():
        if not isinstance(relative, str):
            continue
        try:
            protected_locations[f"scope.{name}"] = resolve_inside(root, relative)
        except ValueError:
            pass  # The primary reference validation already reports this path.
    writable_locations: dict[str, Path] = {}
    for name in ("state_file", "trajectory_file"):
        if not isinstance(paths.get(name), str):
            continue
        try:
            writable_locations[name] = resolve_inside(root, paths[name])
        except ValueError:
            pass  # The primary reference validation already reports this path.
    asset_manifest_reference = paths.get("asset_manifest")
    if isinstance(asset_manifest_reference, str):
        try:
            protected_locations["paths.asset_manifest"] = resolve_inside(
                root, asset_manifest_reference
            )
        except ValueError:
            pass
    artifact_root_path: Path | None = None
    if isinstance(paths.get("artifact_root"), str):
        try:
            artifact_root_path = resolve_inside(root, paths["artifact_root"])
            protected_locations["paths.artifact_root"] = artifact_root_path
        except ValueError:
            pass
    for kind, declaration in evidence_declarations.items():
        if not isinstance(declaration, dict) or not isinstance(declaration.get("path"), str):
            continue
        try:
            protected_locations[f"gates.release.evidence.{kind}"] = resolve_inside(
                root, declaration["path"]
            )
        except ValueError:
            pass
    protected_input_directories: dict[str, Path] = {}
    for gate_name, gate in gates.items():
        if not isinstance(gate, dict):
            continue
        for index, relative in enumerate(gate.get("inputs", [])):
            if not isinstance(relative, str):
                continue
            try:
                gate_input = resolve_inside(root, relative)
            except ValueError:
                continue
            protected_locations[
                f"gates.{gate_name}.inputs.{index}"
            ] = gate_input
            if gate_input.is_dir():
                protected_input_directories[
                    f"gates.{gate_name}.inputs.{index}"
                ] = gate_input

    for name, writable in writable_locations.items():
        relative = paths[name]
        if writable == root:
            problems.append(
                ValidationProblem(
                    f"manifest.paths.{name}",
                    "writable state path must name a file, not the site directory",
                )
            )
        lexical = root
        for component in Path(relative).parts:
            lexical = lexical / component
            if _is_link_or_reparse(lexical):
                problems.append(
                    ValidationProblem(
                        f"manifest.paths.{name}",
                        "writable state path crosses a symbolic link, junction, or reparse point",
                    )
                )
                break
        if writable.exists():
            try:
                metadata = writable.lstat()
            except OSError as exc:
                problems.append(
                    ValidationProblem(
                        f"manifest.paths.{name}", f"cannot inspect writable path: {exc}"
                    )
                )
            else:
                if not stat.S_ISREG(metadata.st_mode):
                    problems.append(
                        ValidationProblem(
                            f"manifest.paths.{name}",
                            "existing writable state path must be a regular file",
                        )
                    )
                if getattr(metadata, "st_nlink", 1) != 1:
                    problems.append(
                        ValidationProblem(
                            f"manifest.paths.{name}",
                            "existing writable state path must have exactly one hard link",
                        )
                    )
        for protected_name, protected in protected_locations.items():
            overlaps = writable == protected
            if writable.exists() and protected.exists():
                try:
                    overlaps = overlaps or writable.samefile(protected)
                except OSError:
                    pass
            if overlaps:
                problems.append(
                    ValidationProblem(
                        f"manifest.paths.{name}",
                        f"must not overwrite {protected_name}",
                    )
                )
        for protected_name, protected in protected_input_directories.items():
            if protected in writable.parents:
                problems.append(
                    ValidationProblem(
                        f"manifest.paths.{name}",
                        f"must not be stored inside mutable gate input {protected_name}",
                    )
                )
        if artifact_root_path is not None and artifact_root_path in writable.parents:
            problems.append(
                ValidationProblem(
                    f"manifest.paths.{name}",
                    "writable state paths must not be stored under paths.artifact_root",
                )
            )
    if len(set(writable_locations.values())) != len(writable_locations):
        problems.append(
            ValidationProblem(
                "manifest.paths",
                "state_file and trajectory_file must use distinct paths",
            )
        )

    derived_locations: dict[str, Path] = {}
    state_location = writable_locations.get("state_file")
    trajectory_location = writable_locations.get("trajectory_file")
    if state_location is not None:
        derived_locations["mutation_lock"] = state_location.parent / ".mutation.lock"
    if trajectory_location is not None:
        derived_locations["trajectory_pending"] = trajectory_location.with_name(
            f".{trajectory_location.name}.pending.json"
        )
    reserved_writable = {**writable_locations, **derived_locations}
    if len(set(reserved_writable.values())) != len(reserved_writable):
        problems.append(
            ValidationProblem(
                "manifest.paths",
                "state, trajectory, mutation lock, and pending intent paths must be distinct",
            )
        )
    for name, derived in derived_locations.items():
        if artifact_root_path is not None and (
            derived == artifact_root_path or artifact_root_path in derived.parents
        ):
            problems.append(
                ValidationProblem(
                    f"manifest.paths.{name}",
                    "derived harness files must not overlap paths.artifact_root",
                )
            )
        for protected_name, protected in protected_locations.items():
            overlaps = derived == protected
            if derived.exists() and protected.exists():
                try:
                    overlaps = overlaps or derived.samefile(protected)
                except OSError:
                    pass
            if overlaps:
                problems.append(
                    ValidationProblem(
                        f"manifest.paths.{name}",
                        f"derived harness file must not overwrite {protected_name}",
                    )
                )
        for protected_name, protected in protected_input_directories.items():
            if protected in derived.parents:
                problems.append(
                    ValidationProblem(
                        f"manifest.paths.{name}",
                        f"derived harness file must not be stored inside {protected_name}",
                    )
                )

    asset_path = paths.get("asset_manifest")
    if check_references and isinstance(asset_path, str):
        try:
            resolved_asset_path = resolve_inside(root, asset_path)
            if resolved_asset_path.is_file():
                load_asset_manifest(resolved_asset_path)
        except (ValueError, ManifestValidationError) as exc:
            if isinstance(exc, ManifestValidationError):
                problems.extend(exc.problems)
            else:
                problems.append(ValidationProblem("manifest.paths.asset_manifest", str(exc)))

    coverage_path = scope.get("coverage")
    if check_references and isinstance(coverage_path, str):
        try:
            resolved_coverage_path = resolve_inside(root, coverage_path)
            if resolved_coverage_path.is_file():
                load_coverage_ledger(resolved_coverage_path)
        except (ValueError, ManifestValidationError) as exc:
            if isinstance(exc, ManifestValidationError):
                problems.extend(exc.problems)
            else:
                problems.append(ValidationProblem("manifest.scope.coverage", str(exc)))

    for name, loader in (
        ("purpose", load_purpose_contract),
        ("invariants", load_invariants_contract),
        ("journeys", load_journeys_contract),
        ("checkpoints", load_checkpoints_contract),
    ):
        relative = scope.get(name)
        if not check_references or not isinstance(relative, str):
            continue
        try:
            resolved_contract = resolve_inside(root, relative)
            if resolved_contract.is_file():
                loader(resolved_contract)
        except (ValueError, ManifestValidationError) as exc:
            if isinstance(exc, ManifestValidationError):
                problems.extend(exc.problems)
            else:
                problems.append(
                    ValidationProblem(f"manifest.scope.{name}", str(exc))
                )

    if check_references and all(
        isinstance(scope.get(name), str)
        for name in ("purpose", "invariants", "journeys", "checkpoints", "coverage")
    ):
        try:
            purpose_contract = load_purpose_contract(
                resolve_inside(root, scope["purpose"], must_exist=True)
            )
            invariant_contract = load_invariants_contract(
                resolve_inside(root, scope["invariants"], must_exist=True)
            )
            journey_contract = load_journeys_contract(
                resolve_inside(root, scope["journeys"], must_exist=True)
            )
            checkpoint_contract = load_checkpoints_contract(
                resolve_inside(root, scope["checkpoints"], must_exist=True)
            )
            coverage_contract = load_coverage_ledger(
                resolve_inside(root, scope["coverage"], must_exist=True)
            )
        except (ValueError, ManifestValidationError):
            pass  # Individual contract validation above already reports details.
        else:
            journeys_by_id = {
                journey["id"]: journey for journey in journey_contract["journeys"]
            }
            mainline_ids = set(purpose_contract["mainline_journey_ids"])
            for journey_id in sorted(mainline_ids):
                journey = journeys_by_id.get(journey_id)
                if journey is None:
                    problems.append(
                        ValidationProblem(
                            "purpose.mainline_journey_ids",
                            f"unknown journey id: {journey_id}",
                        )
                    )
                elif journey.get("priority") != "p0" or journey.get("status") != "frozen":
                    problems.append(
                        ValidationProblem(
                            "purpose.mainline_journey_ids",
                            f"mainline journey must be frozen p0: {journey_id}",
                        )
                    )
            dimensions_by_id = {
                dimension["id"]: dimension
                for dimension in coverage_contract["dimensions"]
            }
            checkpoint_ids = {
                checkpoint["id"]
                for checkpoint in checkpoint_contract["checkpoints"]
                if isinstance(checkpoint.get("visual_contract"), dict)
            }
            visual_required_items = {
                item
                for dimension in coverage_contract["dimensions"]
                if "visual" in dimension["required_evidence_kinds"]
                for item in dimension["required_items"]
            }
            missing_visual_contracts = sorted(visual_required_items - checkpoint_ids)
            if missing_visual_contracts:
                problems.append(
                    ValidationProblem(
                        "checkpoints.checkpoints",
                        "visual coverage items require frozen checkpoint contracts: "
                        + ", ".join(missing_visual_contracts[:20]),
                    )
                )
            source_input_paths = {
                item
                for item in source_gate.get("inputs", [])
                if isinstance(item, str)
            }
            for index, checkpoint in enumerate(checkpoint_contract["checkpoints"]):
                contract = checkpoint.get("visual_contract")
                if not isinstance(contract, dict):
                    continue
                source_relative = contract["source_artifact_path"]
                location = (
                    f"checkpoints.checkpoints.{index}.visual_contract.source_artifact_path"
                )
                if source_relative not in source_input_paths:
                    problems.append(
                        ValidationProblem(
                            location,
                            "frozen source screenshot must be a source gate input",
                        )
                    )
                    continue
                try:
                    source_path, source_sha256, _, source_payload = (
                        _hash_safe_regular_file(
                            root,
                            source_relative,
                            maximum_bytes=MAX_RAW_ACCEPTANCE_EVIDENCE_BYTES,
                        )
                    )
                    source_size = _validated_image_size(source_payload)
                except ValueError as exc:
                    problems.append(ValidationProblem(location, str(exc)))
                    continue
                if candidate_root is not None and (
                    source_path == candidate_root or candidate_root in source_path.parents
                ):
                    problems.append(
                        ValidationProblem(
                            location,
                            "frozen source screenshot must not come from paths.candidate_root",
                        )
                    )
                if source_sha256 != contract["source_artifact_sha256"]:
                    problems.append(
                        ValidationProblem(location, "frozen source screenshot hash does not match")
                    )
                expected_size = (
                    contract["viewport"]["width"],
                    contract["viewport"]["height"],
                )
                if source_size != expected_size:
                    problems.append(
                        ValidationProblem(
                            location,
                            "frozen source screenshot dimensions do not match visual_contract.viewport",
                        )
                    )
            for invariant in invariant_contract["invariants"]:
                journey_ids = set(invariant["journey_ids"])
                for journey_id in sorted(journey_ids):
                    if journey_id not in journeys_by_id:
                        problems.append(
                            ValidationProblem(
                                f"invariants.{invariant['id']}.journey_ids",
                                f"unknown journey id: {journey_id}",
                            )
                        )
                if invariant["priority"] == "p0" and not (journey_ids & mainline_ids):
                    problems.append(
                        ValidationProblem(
                            f"invariants.{invariant['id']}.journey_ids",
                            "p0 invariant must bind at least one frozen mainline journey",
                        )
                    )
                for dimension_id in invariant["coverage_dimension_ids"]:
                    dimension = dimensions_by_id.get(dimension_id)
                    if dimension is None:
                        problems.append(
                            ValidationProblem(
                                f"invariants.{invariant['id']}.coverage_dimension_ids",
                                f"unknown coverage dimension: {dimension_id}",
                            )
                        )
                    elif invariant["priority"] == "p0" and not dimension["required_items"]:
                        problems.append(
                            ValidationProblem(
                                f"invariants.{invariant['id']}.coverage_dimension_ids",
                                f"p0 invariant cannot bind an N/A dimension: {dimension_id}",
                            )
                        )

    if problems:
        raise ManifestValidationError(problems)
    return LoadedManifest(path, root, data, hashlib.sha256(payload).hexdigest())


def _placeholder_gate(message: str) -> list[dict[str, Any]]:
    return [
        {
            "id": "configure-gate",
            "argv": ["{python}", "-c", f"raise SystemExit({message!r})"],
            "timeout_seconds": 30,
        }
    ]


def initialize_site(
    site_dir: Path | str,
    *,
    site_id: str,
    display_name: str,
    source_url: str,
) -> LoadedManifest:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", site_id):
        raise ValueError("site_id must contain lowercase letters, digits, and single hyphens")
    validate_source_url(source_url)
    if not display_name.strip():
        raise ValueError("display_name must not be empty")

    documents = {
        "scope/purpose.json": {
            "schema_version": "offline-clone.purpose.v1",
            "status": "draft",
            "purpose_id": "configure-purpose",
            "statement": (
                "TODO: freeze the source site's primary user purpose before "
                "source acceptance."
            ),
            "primary_actor_ids": [],
            "mainline_journey_ids": [],
            "out_of_scope": [],
        },
        "scope/invariants.json": {
            "schema_version": "offline-clone.invariants.v1",
            "status": "draft",
            "invariants": [],
        },
        "scope/routes.json": {"schema_version": "offline-clone.routes.v1", "routes": []},
        "scope/journeys.json": {
            "schema_version": "offline-clone.journeys.v1",
            "journeys": [],
        },
        "scope/checkpoints.json": {
            "schema_version": "offline-clone.checkpoints.v1",
            "status": "draft",
            "viewports": {},
            "checkpoints": [],
        },
        "scope/coverage.json": {
            "schema_version": "offline-clone.coverage.v1",
            "status": "draft",
            "dimensions": [],
        },
    }
    asset_manifest = {
        "schema_version": "offline-clone.assets.v1",
        "snapshot_id": f"{site_id}-pending",
        "created_at": utc_now(),
        "remote_runtime_policy": "forbidden",
        "closure_status": "pending",
        "no_assets_reason": None,
        "assets": [],
    }
    manifest = {
        "schema_version": "offline-clone.manifest.v1",
        "site_id": site_id,
        "display_name": display_name,
        "state_model": "stateful",
        "source": {
            "origins": [source_url],
            "baseline": {
                "locale": "en-US",
                "currency": None,
                "delivery_region": None,
                "timezone": None,
                "auth_state": "anonymous",
                "tenant": None,
                "workspace": None,
                "role": None,
                "capabilities": [],
                "feature_flags": [],
                "user_agent": None,
                "viewport": None,
                "capture_id": None,
            },
            "capture_policy": {
                "safe_methods": ["GET", "HEAD"],
                "runtime_remote_requests": "forbidden",
            },
        },
        "scope": {
            "purpose": "scope/purpose.json",
            "invariants": "scope/invariants.json",
            "routes": "scope/routes.json",
            "journeys": "scope/journeys.json",
            "checkpoints": "scope/checkpoints.json",
            "claims": "scope/claims.jsonl",
            "coverage": "scope/coverage.json",
        },
        "paths": {
            "candidate_root": "clone",
            "candidate_excludes": [],
            "asset_manifest": "source-assets/manifest.json",
            "artifact_root": "artifacts/offline-clone",
            "state_file": ".clone-harness/state.json",
            "trajectory_file": ".clone-harness/trajectory.jsonl",
        },
        "gates": {
            "source": {
                "inputs": [
                    "scope/purpose.json",
                    "scope/invariants.json",
                    "scope/routes.json",
                    "scope/journeys.json",
                    "scope/checkpoints.json",
                    "scope/claims.jsonl",
                    "scope/coverage.json",
                ],
                "commands": _placeholder_gate("configure the source evidence gate in clone.yaml"),
            },
            "assets": {
                "inputs": ["source-assets"],
                "commands": _placeholder_gate(
                    "configure the source/runtime network audit in clone.yaml"
                ),
            },
            "frontend": {
                "inputs": ["clone/frontend", "clone/static"],
                "commands": _placeholder_gate("configure the frontend gate in clone.yaml"),
            },
            "backend": {
                "inputs": ["clone/backend"],
                "commands": _placeholder_gate("configure the backend gate in clone.yaml"),
            },
            "release": {
                "inputs": ["clone"],
                "commands": [
                    {
                        "id": "configure-release-evidence",
                        "argv": [
                            "{python}",
                            "-c",
                            "raise SystemExit('configure the five release evidence producers in clone.yaml')",
                        ],
                        "timeout_seconds": 30,
                    },
                    {
                        "id": "configure-independent-audit",
                        "argv": [
                            "{python}",
                            "-c",
                            "raise SystemExit('configure an independent audit producer in clone.yaml')",
                        ],
                        "timeout_seconds": 30,
                    }
                ],
                "evidence": {
                    kind: {
                        "path": f"artifacts/offline-clone/acceptance/{kind}.json",
                        "producer_command_id": (
                            "configure-independent-audit"
                            if kind == "independent-audit"
                            else "configure-release-evidence"
                        ),
                    }
                    for kind in ACCEPTANCE_EVIDENCE_KINDS
                },
            },
        },
    }

    # Validate every generated contract before the first directory or file is created.
    generated_problems = [
        *_schema_problems(manifest, MANIFEST_SCHEMA, "manifest"),
        *_schema_problems(asset_manifest, ASSET_SCHEMA, "asset_manifest"),
        *_schema_problems(
            documents["scope/coverage.json"], COVERAGE_SCHEMA, "coverage"
        ),
    ]
    if generated_problems:
        raise ManifestValidationError(generated_problems)

    requested_root = Path(site_dir)
    is_junction = bool(
        hasattr(requested_root, "is_junction") and requested_root.is_junction()
    )
    if requested_root.is_symlink() or is_junction:
        raise ValueError("site directory must not be a symbolic link or junction")
    if requested_root.exists():
        if not requested_root.is_dir():
            raise FileExistsError(f"site directory is not a directory: {requested_root}")
        if next(requested_root.iterdir(), None) is not None:
            raise FileExistsError(
                f"refusing to initialize non-empty site directory: {requested_root}"
            )
    root = requested_root.resolve()
    directories = (
        "scope",
        "source-assets",
        "clone",
        "clone/frontend",
        "clone/backend",
        "clone/static",
        "clone/static/assets",
        ".clone-harness",
    )
    serialized_documents = {
        relative: json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        for relative, document in documents.items()
    }
    serialized_documents.update(
        {
            "scope/claims.jsonl": "",
            "source-assets/manifest.json": json.dumps(
                asset_manifest, indent=2, ensure_ascii=False
            )
            + "\n",
            MANIFEST_NAME: yaml.safe_dump(
                manifest, sort_keys=False, allow_unicode=True
            ),
        }
    )

    # Resolve and collision-check every destination before performing any write.
    for relative in (*directories, *serialized_documents):
        destination = resolve_inside(root, relative)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"refusing to overwrite init destination: {destination}")

    root.mkdir(parents=True, exist_ok=True)
    for relative in directories:
        (root / relative).mkdir(exist_ok=False)
    for relative, text in serialized_documents.items():
        destination = root / relative
        with destination.open("x", encoding="utf-8", newline="") as stream:
            stream.write(text)

    manifest_path = root / MANIFEST_NAME
    return load_manifest(manifest_path)
