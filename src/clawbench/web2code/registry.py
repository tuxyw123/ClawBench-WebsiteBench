"""Trusted WebsiteBench site registry and driver resolution.

The registry is the only authority for both ``site_id -> driver`` and
``family_id -> split``.  Driver documents are private host inputs.  Resolution
turns them into an immutable :class:`ResolvedSite`; preparing a run then writes
a canonical, digest-addressed snapshot without exposing it to the candidate.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from .contracts import validate_site_contract


SCHEMA_VERSION = "websitebench.registry.v1"
DRIVER_VERSION = "websitebench.driver.v1"
RUN_MANIFEST_VERSION = "websitebench.run-manifest.v1"
VALID_SPLITS = frozenset({"train", "validation", "test"})
PRIVATE_KINDS = frozenset(
    {
        "private_reference",
        "private_fixture",
        "private_evaluator",
        "private_assertions",
        "private_variant",
    }
)
UNTRUSTED_ROLES = frozenset({"agent", "browser_gateway"})
PRIVATE_ROLE_ALLOWLIST: Mapping[str, frozenset[str]] = {
    "private_reference": frozenset({"reference"}),
    "private_fixture": frozenset({"reference", "evaluator"}),
    "private_evaluator": frozenset({"evaluator"}),
    "private_assertions": frozenset({"evaluator"}),
    "private_variant": frozenset({"reference"}),
}
_INTERPOLATION = re.compile(r"\$\{(?:(candidate|secret)[.:])?([A-Z][A-Z0-9_]*)\}")


class RegistryValidationError(ValueError):
    """An actionable registry or driver validation failure."""


def canonical_json(value: Any) -> bytes:
    """Return stable UTF-8 JSON used for every protocol digest."""

    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _read_yaml_unique(path: Path) -> dict[str, Any]:
    """Load YAML while rejecting duplicate mapping keys.

    PyYAML normally keeps the last duplicate, which would make conflicting
    family split declarations invisible.
    """

    class UniqueLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader: UniqueLoader, node: yaml.MappingNode, deep: bool = False) -> Any:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                raise RegistryValidationError(f"{path}: duplicate mapping key {key!r}")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping,
    )
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise RegistryValidationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RegistryValidationError(f"{path} must contain a mapping")
    return value


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RegistryValidationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RegistryValidationError(f"{path} must contain a mapping")
    return value


def _schema_validate(value: Any, schema_path: Path, label: str) -> None:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryValidationError(f"cannot read {schema_path}: {exc}") from exc
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    failures = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
    if failures:
        details = []
        for failure in failures:
            suffix = ".".join(str(part) for part in failure.absolute_path)
            details.append(f"{label}{'.' + suffix if suffix else ''}: {failure.message}")
        raise RegistryValidationError("invalid contract:\n- " + "\n- ".join(details))


def _contained_path(root: Path, raw: str, *, label: str, must_exist: bool = True) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute():
        raise RegistryValidationError(f"{label}: absolute paths are forbidden: {raw}")
    if ".." in candidate.parts:
        raise RegistryValidationError(f"{label}: path traversal is forbidden: {raw}")
    resolved_root = root.resolve()
    current = resolved_root
    for part in candidate.parts:
        if part in {"", "."}:
            continue
        current /= part
        if current.is_symlink():
            raise RegistryValidationError(f"{label}: symlinks are forbidden: {raw}")
    resolved = (resolved_root / candidate).resolve(strict=False)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise RegistryValidationError(f"{label}: path escapes permitted corpus root: {raw}")
    if must_exist and not resolved.exists():
        raise RegistryValidationError(f"{label}: path does not exist: {raw}")
    return resolved


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _typed_value(name: str, definition: Mapping[str, Any], override: Any | None = None) -> Any:
    value = definition.get("value") if override is None else override
    kind = definition["type"]
    try:
        if kind == "string":
            if not isinstance(value, str) or not value:
                raise ValueError("must be a non-empty string")
            return value
        if kind == "integer":
            if isinstance(value, bool):
                raise ValueError("must be an integer")
            converted = int(value)
            if str(converted) != str(value) and not isinstance(value, int):
                raise ValueError("must be an integer")
            minimum = definition.get("minimum")
            maximum = definition.get("maximum")
            if minimum is not None and converted < minimum:
                raise ValueError(f"must be >= {minimum}")
            if maximum is not None and converted > maximum:
                raise ValueError(f"must be <= {maximum}")
            return converted
        if kind == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.casefold() in {"true", "false"}:
                return value.casefold() == "true"
            raise ValueError("must be true or false")
        if kind == "url":
            if not isinstance(value, str):
                raise ValueError("must be a URL")
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("must be an absolute HTTP(S) URL")
            return value
        if kind == "path":
            if not isinstance(value, str) or not value.startswith("/") or ".." in Path(value).parts:
                raise ValueError("must be an absolute container path without traversal")
            return value
    except (TypeError, ValueError) as exc:
        raise RegistryValidationError(f"candidate environment {name}: {exc}") from exc
    raise RegistryValidationError(f"candidate environment {name}: unsupported type {kind!r}")


def _interpolate(
    value: Any,
    *,
    candidate: Mapping[str, Any],
    secrets: Mapping[str, str],
    secret_names: set[str],
) -> Any:
    if isinstance(value, dict):
        return {
            key: _interpolate(item, candidate=candidate, secrets=secrets, secret_names=secret_names)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _interpolate(item, candidate=candidate, secrets=secrets, secret_names=secret_names)
            for item in value
        ]
    if not isinstance(value, str) or "${" not in value:
        return value

    residue = _INTERPOLATION.sub("", value)
    if "${" in residue:
        raise RegistryValidationError("driver contains malformed or undeclared interpolation")

    def replace(match: re.Match[str]) -> str:
        namespace, name = match.groups()
        candidate_match = name in candidate and namespace in {None, "candidate"}
        secret_match = name in secret_names and namespace in {None, "secret"}
        if candidate_match and secret_match and namespace is None:
            raise RegistryValidationError(f"ambiguous interpolation ${{{name}}}; add a namespace")
        if candidate_match:
            return str(candidate[name]).lower() if isinstance(candidate[name], bool) else str(candidate[name])
        if secret_match:
            if name not in secrets:
                raise RegistryValidationError(f"required host secret {name} is unavailable")
            return secrets[name]
        raise RegistryValidationError(f"undeclared interpolation ${{{match.group(0)[2:-1]}}}")

    return _INTERPOLATION.sub(replace, value)


def _file_record(path: Path, root: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def _is_contract_input(path: Path) -> bool:
    return (
        "__pycache__" not in path.parts
        and ".git" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
        and path.name not in {".DS_Store"}
    )


def _input_file_records(input_paths: set[Path], root: Path) -> tuple[Mapping[str, Any], ...]:
    records_by_path: dict[str, Mapping[str, Any]] = {}
    for path in sorted(input_paths):
        if path.is_symlink():
            raise RegistryValidationError(f"contract input cannot be a symlink: {path}")
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_symlink():
                    raise RegistryValidationError(f"contract input cannot contain a symlink: {child}")
                if child.is_file() and _is_contract_input(child):
                    record = _freeze(_file_record(child, root))
                    records_by_path[record["path"]] = record
        elif path.is_file():
            record = _freeze(_file_record(path, root))
            records_by_path[record["path"]] = record
    return tuple(records_by_path[key] for key in sorted(records_by_path))


@dataclass(frozen=True)
class ResolvedSite:
    """Fully validated, immutable host-side site configuration."""

    site_id: str
    family_id: str
    variant_id: str
    split: str
    site_version: str
    corpus_root: Path
    driver_path: Path
    manifest_path: Path
    compose_path: Path
    registry_digest: str
    manifest: Mapping[str, Any]
    driver: Mapping[str, Any]
    service_roles: Mapping[str, str]
    candidate_environment: Mapping[str, Any]
    execution_seeds: Mapping[str, int]
    secret_names: tuple[str, ...]
    input_files: tuple[Mapping[str, Any], ...]

    def as_snapshot(self) -> dict[str, Any]:
        driver = _thaw(self.driver)
        # A resolved driver snapshot contains secret names, never secret values.
        return {
            "schema_version": RUN_MANIFEST_VERSION,
            "site_id": self.site_id,
            "family_id": self.family_id,
            "variant_id": self.variant_id,
            "split": self.split,
            "site_version": self.site_version,
            "registry_digest": self.registry_digest,
            "driver": driver,
            "manifest": _thaw(self.manifest),
            "candidate_environment": _thaw(self.candidate_environment),
            "execution_seeds": _thaw(self.execution_seeds),
            "host_secret_names": list(self.secret_names),
            "inputs": [_thaw(item) for item in self.input_files],
        }

    def run_manifest(self) -> dict[str, Any]:
        body = self.as_snapshot()
        digest = sha256_value(body)
        return {**body, "digest": f"sha256:{digest}"}


class SiteRegistry:
    """Load, query, and resolve the checked-in WebsiteBench registry."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).resolve()
        self._document = _read_yaml_unique(self.path)
        self._schemas_root = self.path.parent / "schemas"
        _schema_validate(self._document, self._schemas_root / "registry.schema.json", "registry")
        if self._document.get("schema_version") != SCHEMA_VERSION:
            raise RegistryValidationError(f"unsupported registry version: {self._document.get('schema_version')}")
        self.corpus_root = _contained_path(
            self.path.parent,
            self._document["corpus_root"],
            label="registry.corpus_root",
        )
        self._families = dict(self._document["families"])
        for family_id, split in self._families.items():
            if split not in VALID_SPLITS:
                raise RegistryValidationError(f"family {family_id!r} has invalid split {split!r}")
        self._sites = dict(self._document["sites"])
        for site_id, entry in self._sites.items():
            family_id = entry["family_id"]
            if family_id not in self._families:
                raise RegistryValidationError(
                    f"site {site_id!r} references unknown family {family_id!r}; add one family split mapping"
                )
            _contained_path(
                self.corpus_root,
                entry["driver"],
                label=f"registry.sites.{site_id}.driver",
            )
            if "variant" in entry:
                _contained_path(
                    self.corpus_root,
                    entry["variant"],
                    label=f"registry.sites.{site_id}.variant",
                )
        self.digest = sha256_value(self._document)

    @classmethod
    def default(cls, repository: Path | str | None = None) -> "SiteRegistry":
        if repository is None:
            repository = Path(__file__).resolve().parents[3]
        return cls(Path(repository) / "websitebench" / "registry.yaml")

    @property
    def families(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self._families))

    @property
    def site_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._sites))

    def family_split(self, family_id: str) -> str:
        try:
            return self._families[family_id]
        except KeyError as exc:
            raise RegistryValidationError(f"unknown family {family_id!r}") from exc

    def variant_specs(self, *, family_id: str | None = None) -> tuple[Path, ...]:
        selected: list[Path] = []
        for site_id in sorted(self._sites):
            entry = self._sites[site_id]
            if family_id is not None and entry["family_id"] != family_id:
                continue
            if "variant" in entry:
                selected.append(
                    _contained_path(
                        self.corpus_root,
                        entry["variant"],
                        label=f"registry.sites.{site_id}.variant",
                    )
                )
        return tuple(selected)

    def variant_spec(self, site_id: str) -> Path:
        try:
            entry = self._sites[site_id]
        except KeyError as exc:
            raise RegistryValidationError(f"unknown site {site_id!r}") from exc
        if "variant" not in entry:
            raise RegistryValidationError(f"site {site_id!r} has no registered variant spec")
        return _contained_path(
            self.corpus_root,
            entry["variant"],
            label=f"registry.sites.{site_id}.variant",
        )

    def query(
        self,
        *,
        site_ids: Iterable[str] | None = None,
        family_id: str | None = None,
        split: str | None = None,
    ) -> tuple[str, ...]:
        requested = set(site_ids or ())
        unknown = requested - set(self._sites)
        if unknown:
            raise RegistryValidationError(f"unknown sites: {sorted(unknown)}")
        if family_id is not None and family_id not in self._families:
            raise RegistryValidationError(f"unknown family {family_id!r}")
        if split is not None and split not in VALID_SPLITS:
            raise RegistryValidationError(f"unknown split {split!r}")
        result = []
        for site_id in sorted(self._sites):
            entry = self._sites[site_id]
            if requested and site_id not in requested:
                continue
            if family_id is not None and entry["family_id"] != family_id:
                continue
            if split is not None and self._families[entry["family_id"]] != split:
                continue
            result.append(site_id)
        if not result:
            raise RegistryValidationError("registry selector matched no sites")
        return tuple(result)

    def resolve(
        self,
        site_id: str,
        *,
        candidate_overrides: Mapping[str, Any] | None = None,
        host_secrets: Mapping[str, str] | None = None,
    ) -> ResolvedSite:
        try:
            entry = self._sites[site_id]
        except KeyError as exc:
            raise RegistryValidationError(
                f"unknown site {site_id!r}; registered sites: {', '.join(sorted(self._sites))}"
            ) from exc
        driver_path = _contained_path(
            self.corpus_root,
            entry["driver"],
            label=f"registry.sites.{site_id}.driver",
        )
        raw_driver = _read_yaml_unique(driver_path)
        _schema_validate(raw_driver, self._schemas_root / "driver.schema.json", "driver")
        for identity_key, expected in (
            ("site_id", site_id),
            ("family_id", entry["family_id"]),
            ("variant_id", entry["variant_id"]),
        ):
            if raw_driver.get(identity_key) != expected:
                raise RegistryValidationError(
                    f"driver.{identity_key} {raw_driver.get(identity_key)!r} does not match registry {expected!r}"
                )

        environment = raw_driver.get("environment", {})
        definitions = environment.get("candidate", {})
        overrides = dict(candidate_overrides or {})
        undeclared_overrides = set(overrides) - set(definitions)
        if undeclared_overrides:
            raise RegistryValidationError(
                f"undeclared candidate environment overrides: {sorted(undeclared_overrides)}"
            )
        candidate_values = {
            name: _typed_value(name, definition, overrides.get(name))
            for name, definition in definitions.items()
        }
        secret_names = set(environment.get("host_secret_allowlist", []))
        provided_secrets = dict(host_secrets or {})
        undeclared_secrets = set(provided_secrets) - secret_names
        if undeclared_secrets:
            raise RegistryValidationError(f"host secrets are not allowlisted: {sorted(undeclared_secrets)}")
        runtime_secret_values = {
            name: provided_secrets.get(name, f"${{secret.{name}}}")
            for name in secret_names
        }
        resolved_driver = _interpolate(
            raw_driver,
            candidate=candidate_values,
            secrets=runtime_secret_values,
            secret_names=secret_names,
        )
        # Candidate values are frozen into the snapshot. Secret values remain
        # namespaced placeholders so the trusted manifest is safe to serialize
        # while runtime interpolation can still be audited.
        snapshot_driver = _interpolate(
            raw_driver,
            candidate=candidate_values,
            secrets={name: f"${{secret.{name}}}" for name in secret_names},
            secret_names=secret_names,
        )

        manifest_path = _contained_path(
            self.corpus_root,
            resolved_driver["public_manifest"],
            label="driver.public_manifest",
        )
        compose_path = _contained_path(
            self.corpus_root,
            resolved_driver["compose"],
            label="driver.compose",
        )
        manifest = validate_site_contract(manifest_path, require_fixtures=True)
        family_id = entry["family_id"]
        split = self.family_split(family_id)
        expected_manifest = {
            "site_id": site_id,
            "family_id": family_id,
            "split": split,
            "site_version": resolved_driver["site_version"],
        }
        for key, expected in expected_manifest.items():
            if manifest.get(key) != expected:
                raise RegistryValidationError(
                    f"manifest.{key} {manifest.get(key)!r} does not match resolved value {expected!r}"
                )

        variant_path = _contained_path(
            self.corpus_root,
            entry["variant"],
            label=f"registry.sites.{site_id}.variant",
        )
        variant = _read_yaml_unique(variant_path)
        _schema_validate(
            variant,
            self._schemas_root / "variant.schema.json",
            "variant",
        )
        for identity_key, expected in (
            ("site_id", site_id),
            ("family_id", family_id),
            ("variant_id", entry["variant_id"]),
            ("site_version", resolved_driver["site_version"]),
        ):
            if variant.get(identity_key) != expected:
                raise RegistryValidationError(
                    f"variant.{identity_key} {variant.get(identity_key)!r} does not match Registry/driver {expected!r}"
                )
        raw_seeds = variant.get("seeds")
        if not isinstance(raw_seeds, dict) or set(raw_seeds) != {
            "public",
            "hidden",
            "concurrency",
        }:
            raise RegistryValidationError(
                "variant.seeds must define exactly public, hidden, and concurrency"
            )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in raw_seeds.values()):
            raise RegistryValidationError("variant execution seeds must be non-negative integers")
        if len(set(raw_seeds.values())) != 3:
            raise RegistryValidationError("variant execution seeds must be distinct")
        public_seed_ids = {
            item.get("id")
            for item in manifest["seeds"]["public"]
            if isinstance(item, dict)
        }
        if raw_seeds["public"] not in public_seed_ids:
            raise RegistryValidationError(
                "variant public seed is not declared by the public manifest"
            )
        execution_seeds = {name: int(raw_seeds[name]) for name in ("public", "hidden", "concurrency")}

        service_contract = manifest.get("services", {})
        urls = resolved_driver["urls"]
        for label in ("target", "candidate"):
            parsed = urlsplit(urls[label])
            expected_port = int(service_contract["public_port"])
            actual_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if actual_port != expected_port:
                raise RegistryValidationError(
                    f"driver.urls.{label} port {actual_port} does not match manifest public_port {expected_port}"
                )
        if int(candidate_values.get("PORT", service_contract["public_port"])) != int(service_contract["public_port"]):
            raise RegistryValidationError("candidate PORT contradicts manifest public_port")
        if int(candidate_values.get("BENCH_ADMIN_PORT", service_contract["admin_port"])) != int(service_contract["admin_port"]):
            raise RegistryValidationError("candidate BENCH_ADMIN_PORT contradicts manifest admin_port")

        roles = resolved_driver["service_roles"]
        if len(set(roles.values())) != len(roles):
            raise RegistryValidationError("driver service roles must map to distinct Compose services")
        compose = _read_yaml(compose_path)
        compose_services = compose.get("services", {})
        missing_services = set(roles.values()) - set(compose_services)
        if missing_services:
            raise RegistryValidationError(
                f"driver roles name missing Compose services: {sorted(missing_services)}"
            )

        input_paths: set[Path] = {driver_path, manifest_path, compose_path}
        input_paths.add(variant_path)
        for label, raw_path in (
            ("driver.scoring.policy", resolved_driver["scoring"]["policy"]),
            ("driver.scoring.facts_schema", resolved_driver["scoring"]["facts_schema"]),
            ("driver.scoring.result_schema", resolved_driver["scoring"]["result_schema"]),
        ):
            input_paths.add(_contained_path(self.corpus_root, raw_path, label=label))
        for index, mount in enumerate(resolved_driver.get("mounts", [])):
            source = _contained_path(
                self.corpus_root,
                mount["source"],
                label=f"driver.mounts.{index}.source",
            )
            input_paths.add(source)
            roles_for_mount = set(mount["roles"])
            if not roles_for_mount <= set(roles):
                raise RegistryValidationError(
                    f"driver.mounts.{index}.roles contains undeclared roles {sorted(roles_for_mount - set(roles))}"
                )
            if mount["kind"] in PRIVATE_KINDS:
                exposed = roles_for_mount & UNTRUSTED_ROLES
                if exposed:
                    raise RegistryValidationError(
                        f"private mount {index} is exposed to untrusted roles {sorted(exposed)}"
                    )
                if mount["read_only"] is not True:
                    raise RegistryValidationError(f"private mount {index} must be read-only")
                permitted = PRIVATE_ROLE_ALLOWLIST[mount["kind"]]
                if not roles_for_mount <= permitted:
                    raise RegistryValidationError(
                        f"private mount {index} of kind {mount['kind']} can only be used by "
                        f"{sorted(permitted)}"
                    )
                target = mount["target"]
                for role in roles_for_mount:
                    service_name = roles[role]
                    volumes = compose_services[service_name].get("volumes", [])
                    declared_readonly = False
                    for volume in volumes:
                        if isinstance(volume, str):
                            pieces = volume.rsplit(":", 2)
                            declared_readonly = (
                                len(pieces) == 3
                                and pieces[-2] == target
                                and "ro" in pieces[-1].split(",")
                            )
                        elif isinstance(volume, dict):
                            declared_readonly = (
                                volume.get("target") == target
                                and volume.get("read_only") is True
                            )
                        if declared_readonly:
                            break
                    if not declared_readonly:
                        raise RegistryValidationError(
                            f"private mount {index} target {target} is not mounted read-only "
                            f"by Compose service {service_name}"
                        )
        evaluator = resolved_driver["evaluator"]
        evaluator_role = roles["evaluator"]
        evaluator_service = compose_services[evaluator_role]
        if evaluator["profile"] not in evaluator_service.get("profiles", []):
            raise RegistryValidationError("evaluator profile is not declared by its Compose service")
        declared_argv = list(evaluator.get("argv", []))
        compose_argv = evaluator_service.get("command", [])
        if isinstance(compose_argv, str):
            compose_argv = [compose_argv]
        if declared_argv and list(compose_argv) != declared_argv:
            raise RegistryValidationError("evaluator argv differs between driver and Compose")
        compose_environment = evaluator_service.get("environment", {})
        if isinstance(compose_environment, list):
            compose_environment = {str(item).split("=", 1)[0]: None for item in compose_environment}
        declared_environment = evaluator.get("environment", {})
        if set(compose_environment) != set(declared_environment):
            raise RegistryValidationError(
                "evaluator environment keys differ between driver and Compose; "
                f"driver-only={sorted(set(declared_environment) - set(compose_environment))}, "
                f"compose-only={sorted(set(compose_environment) - set(declared_environment))}"
            )
        for name, declared in declared_environment.items():
            composed = compose_environment[name]
            if declared == "runtime-secret":
                if not isinstance(composed, str) or not composed.startswith(f"${{{name}"):
                    raise RegistryValidationError(
                        f"evaluator environment {name} must be sourced from its runtime secret"
                    )
            elif str(composed) != str(declared):
                raise RegistryValidationError(
                    f"evaluator environment {name} differs between driver and Compose: "
                    f"driver={declared!r}, compose={composed!r}"
                )

        file_records = _input_file_records(input_paths, self.corpus_root)

        return ResolvedSite(
            site_id=site_id,
            family_id=family_id,
            variant_id=entry["variant_id"],
            split=split,
            site_version=resolved_driver["site_version"],
            corpus_root=self.corpus_root,
            driver_path=driver_path,
            manifest_path=manifest_path,
            compose_path=compose_path,
            registry_digest=self.digest,
            manifest=_freeze(manifest),
            driver=_freeze(snapshot_driver),
            service_roles=_freeze(roles),
            candidate_environment=_freeze(candidate_values),
            execution_seeds=_freeze(execution_seeds),
            secret_names=tuple(sorted(secret_names)),
            input_files=file_records,
        )

    def validate_corpus(self) -> dict[str, Any]:
        """Enforce one Registry-owned split across public and private indexes."""

        placements: dict[str, set[str]] = {family: set() for family in self._families}
        sites = []
        for site_id in sorted(self._sites):
            resolved = self.resolve(site_id)
            placements[resolved.family_id].add(str(resolved.manifest["split"]))
            sites.append(
                {
                    "site_id": site_id,
                    "family_id": resolved.family_id,
                    "variant_id": resolved.variant_id,
                    "split": resolved.split,
                    "run_manifest_digest": resolved.run_manifest()["digest"],
                }
            )
        for path in self.variant_specs():
            value = _read_yaml_unique(path)

            def reject_split(item: Any, location: str = "variant") -> None:
                if isinstance(item, dict):
                    for key, child in item.items():
                        if str(key).casefold() == "split":
                            raise RegistryValidationError(f"{path}: {location}.{key} cannot override Registry split")
                        reject_split(child, f"{location}.{key}")
                elif isinstance(item, list):
                    for index, child in enumerate(item):
                        reject_split(child, f"{location}.{index}")

            reject_split(value)
            family = value.get("family_id")
            if family not in self._families:
                raise RegistryValidationError(f"{path}: unknown family {family!r}")
            site_id = value.get("site_id")
            entry = self._sites.get(site_id)
            if not entry or entry.get("family_id") != family or entry.get("variant_id") != value.get("variant_id"):
                raise RegistryValidationError(f"{path}: private compiler index contradicts Registry identity")
        conflicts = {family: sorted(splits) for family, splits in placements.items() if splits != {self._families[family]}}
        if conflicts:
            raise RegistryValidationError(f"families appear in conflicting splits: {conflicts}")
        return {
            "schema_version": "websitebench.corpus-validation.v1",
            "registry_digest": self.digest,
            "families": dict(sorted(self._families.items())),
            "sites": sites,
        }


def write_run_manifest(resolved: ResolvedSite, trusted_dir: Path | str) -> Path:
    """Write an immutable digest-addressed manifest below a host-only directory."""

    trusted = Path(trusted_dir)
    trusted.mkdir(parents=True, exist_ok=True, mode=0o700)
    value = resolved.run_manifest()
    digest = value["digest"].split(":", 1)[1]
    path = trusted / f"run-manifest.{digest}.json"
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise RegistryValidationError(f"digest collision or mutated run manifest: {path}")
        return path
    path.write_text(payload, encoding="utf-8")
    path.chmod(0o400)
    return path


def secret_environment(resolved: ResolvedSite, values: Mapping[str, str] | None = None) -> dict[str, str]:
    """Select only allowlisted runtime secrets without serializing diagnostics."""

    source = dict(os.environ if values is None else values)
    return {name: source[name] for name in resolved.secret_names if name in source}
