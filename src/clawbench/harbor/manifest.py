"""Load and semantically validate Harbor site and instance manifests."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterator

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from yaml.constructor import ConstructorError
from yaml.resolver import BaseResolver


SITE_SCHEMA = "harbor-site.schema.json"
INSTANCE_SCHEMA = "harbor-instance.schema.json"
SITE_SCHEMA_VERSION = "clawbench.harbor.site.v1"
INSTANCE_SCHEMA_VERSION = "clawbench.harbor.instance.v1"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
REQUIRED_DIMENSIONS = ("contract", "api", "ui", "visual", "journey", "robustness")
ALL_DIMENSIONS = REQUIRED_DIMENSIONS + ("efficiency",)
MINIMUM_NODES = {
    "contract": 1,
    "api": 2,
    "ui": 2,
    "visual": 1,
    "journey": 1,
    "robustness": 1,
}


class HarborManifestError(ValueError):
    """Raised when an authoring manifest or its declared files are invalid."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        super().__init__(
            "Harbor authoring validation failed:\n"
            + "\n".join(f"- {problem}" for problem in problems)
        )


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
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
    BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


@dataclass(frozen=True)
class LoadedSite:
    path: Path
    root: Path
    data: dict[str, Any]
    sha256: str


@dataclass(frozen=True)
class LoadedInstance:
    path: Path
    root: Path
    corpus_root: Path
    data: dict[str, Any]
    sha256: str
    site: LoadedSite


def _schema_path(filename: str) -> Path:
    source_root = Path(__file__).resolve().parents[3]
    source = source_root / "websitebench" / "schemas" / filename
    if source.is_file():
        return source
    bundled = Path(__file__).resolve().parents[1] / "viewer" / "_schemas" / filename
    if bundled.is_file():
        return bundled
    raise FileNotFoundError(f"Harbor schema is unavailable: {filename}")


def load_schema(filename: str) -> dict[str, Any]:
    value = json.loads(_schema_path(filename).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(value)
    return value


def _read_yaml(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise HarborManifestError([f"{path}: cannot read manifest: {exc}"]) from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise HarborManifestError([f"{path}: manifest exceeds {MAX_MANIFEST_BYTES} bytes"])
    try:
        value = yaml.load(raw.decode("utf-8"), Loader=_UniqueKeySafeLoader)
    except (UnicodeDecodeError, yaml.YAMLError, ValueError) as exc:
        raise HarborManifestError([f"{path}: invalid YAML: {exc}"]) from exc
    if not isinstance(value, dict):
        raise HarborManifestError([f"{path}: manifest must contain an object"])
    return value, raw


def _schema_problems(value: Any, schema_name: str, label: str) -> list[str]:
    validator = Draft202012Validator(
        load_schema(schema_name), format_checker=FormatChecker()
    )
    problems: list[str] = []
    for error in sorted(validator.iter_errors(value), key=lambda item: list(item.path)):
        suffix = ".".join(str(part) for part in error.absolute_path)
        problems.append(f"{label}{'.' + suffix if suffix else ''}: {error.message}")
    return problems


def resolve_inside(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    """Resolve a portable relative path without allowing corpus escape."""

    raw = Path(relative)
    windows = PureWindowsPath(relative)
    posix = PurePosixPath(relative)
    if raw.is_absolute() or windows.is_absolute() or windows.drive or posix.is_absolute():
        raise ValueError(f"absolute paths are forbidden: {relative}")
    if ".." in windows.parts or ".." in posix.parts:
        raise ValueError(f"parent traversal is forbidden: {relative}")
    resolved_root = root.resolve()
    resolved = (resolved_root / raw).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"path escapes the authoring root: {relative}")
    if must_exist and not resolved.exists():
        raise ValueError(f"path does not exist: {relative}")
    return resolved


def _is_link_or_reparse(path: Path) -> bool:
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


def _assert_safe_path(root: Path, relative: str, *, kind: str) -> Path:
    resolved = resolve_inside(root, relative, must_exist=True)
    lexical = root.resolve()
    for component in Path(relative).parts:
        lexical = lexical / component
        if _is_link_or_reparse(lexical):
            raise ValueError(
                f"{kind} crosses a symbolic link, junction, or reparse point: {relative}"
            )
    return resolved


def safe_tree_files(root: Path, relative: str) -> Iterator[tuple[Path, Path]]:
    """Yield ``(absolute, relative-to-declared-root)`` for a safe regular tree."""

    declared = _assert_safe_path(root, relative, kind="directory")
    if not declared.is_dir():
        raise ValueError(f"declared path is not a directory: {relative}")
    for directory, names, filenames in os.walk(declared, followlinks=False):
        directory_path = Path(directory)
        names.sort()
        filenames.sort()
        for name in list(names):
            child = directory_path / name
            if _is_link_or_reparse(child):
                raise ValueError(
                    "source tree contains a symbolic link, junction, or reparse point: "
                    f"{child.relative_to(root)}"
                )
        for filename in filenames:
            child = directory_path / filename
            if _is_link_or_reparse(child) or not child.is_file():
                raise ValueError(f"source tree contains a non-regular file: {child}")
            if child.stat().st_nlink != 1:
                raise ValueError(f"source tree contains a hard-linked file: {child}")
            yield child, child.relative_to(declared)


def safe_regular_file(root: Path, relative: str) -> Path:
    path = _assert_safe_path(root, relative, kind="file")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"cannot inspect file {relative}: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"declared path is not a regular file: {relative}")
    if metadata.st_nlink != 1:
        raise ValueError(f"declared path is a hard-linked file: {relative}")
    return path


def _overlap_problems(root: Path, paths: dict[str, str], label: str) -> list[str]:
    resolved: dict[str, Path] = {}
    problems: list[str] = []
    for name, relative in paths.items():
        try:
            resolved[name] = resolve_inside(root, relative)
        except ValueError as exc:
            problems.append(f"{label}.paths.{name}: {exc}")
    names = sorted(resolved)
    for index, left_name in enumerate(names):
        left = resolved[left_name]
        for right_name in names[index + 1 :]:
            right = resolved[right_name]
            if left == right or left in right.parents or right in left.parents:
                problems.append(
                    f"{label}.paths: visibility roots {left_name!r} and "
                    f"{right_name!r} overlap"
                )
    return problems


def _declared_tree_problems(
    root: Path, paths: dict[str, str], label: str
) -> list[str]:
    problems: list[str] = []
    for name, relative in paths.items():
        try:
            list(safe_tree_files(root, relative))
        except (OSError, ValueError) as exc:
            problems.append(f"{label}.paths.{name}: {exc}")
    return problems


def find_corpus_root(instance_path: Path) -> Path:
    """Find the nearest authoring root containing sibling ``sites``/``instances``."""

    start = instance_path.resolve()
    if start.is_file():
        start = start.parent
    for candidate in (start, *start.parents):
        if (candidate / "sites").is_dir() and (candidate / "instances").is_dir():
            return candidate
    raise HarborManifestError(
        [
            f"{instance_path}: cannot find an authoring root containing both "
            "'sites/' and 'instances/'"
        ]
    )


def load_site(path: Path | str) -> LoadedSite:
    manifest_path = Path(path).resolve()
    if manifest_path.is_dir():
        manifest_path = manifest_path / "site.yaml"
    value, raw = _read_yaml(manifest_path)
    problems = _schema_problems(value, SITE_SCHEMA, "site")
    root = manifest_path.parent
    site_id = value.get("site_id")
    if isinstance(site_id, str) and root.name != site_id:
        problems.append(
            f"site.site_id: must match its directory name {root.name!r}"
        )
    paths = value.get("paths")
    if isinstance(paths, dict):
        visibility = {
            name: relative
            for name, relative in paths.items()
            if name in {"public", "reference", "verifier", "hidden_fixtures", "oracle"}
            and isinstance(relative, str)
        }
        problems.extend(_overlap_problems(root, visibility, "site"))
        problems.extend(_declared_tree_problems(root, visibility, "site"))
        required_files = {
            "reference.Dockerfile": (
                Path(paths["reference"]) / "Dockerfile"
                if isinstance(paths.get("reference"), str)
                else None
            ),
            "reference.run.sh": (
                Path(paths["reference"]) / "run.sh"
                if isinstance(paths.get("reference"), str)
                else None
            ),
            "verifier.run.py": (
                Path(paths["verifier"]) / "run.py"
                if isinstance(paths.get("verifier"), str)
                else None
            ),
        }
        for name, required_path in required_files.items():
            if required_path is None:
                continue
            try:
                safe_regular_file(root, required_path.as_posix())
            except (OSError, ValueError) as exc:
                problems.append(f"site.paths.{name}: {exc}")
    scoring = value.get("scoring")
    if isinstance(scoring, dict) and isinstance(scoring.get("dimensions"), dict):
        dimensions = scoring["dimensions"]
        if all(isinstance(item, int) and not isinstance(item, bool) for item in dimensions.values()):
            if sum(dimensions.values()) != 100:
                problems.append("site.scoring.dimensions: weights must sum to 100")
            for dimension in REQUIRED_DIMENSIONS:
                if dimensions.get(dimension, 0) <= 0:
                    problems.append(
                        f"site.scoring.dimensions.{dimension}: required dimension "
                        "must have a positive weight"
                    )
    runtime = value.get("runtime")
    if isinstance(runtime, dict):
        env_names = [
            runtime.get(name)
            for name in (
                "reference_url_env",
                "candidate_url_env",
                "reference_admin_url_env",
                "candidate_admin_url_env",
            )
        ]
        if all(isinstance(item, str) for item in env_names) and len(set(env_names)) != 4:
            problems.append("site.runtime: all public/admin URL env names must be distinct")
        ports = [
            runtime.get(name)
            for name in (
                "reference_port",
                "candidate_port",
                "verifier_reference_port",
                "verifier_candidate_port",
            )
        ]
        if all(isinstance(item, int) and not isinstance(item, bool) for item in ports):
            if len(set(ports)) != 4:
                problems.append("site.runtime: all Agent/verifier ports must be distinct")
    if problems:
        raise HarborManifestError(problems)
    return LoadedSite(
        path=manifest_path,
        root=root,
        data=value,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def load_instance(
    path: Path | str, *, corpus_root: Path | None = None
) -> LoadedInstance:
    manifest_path = Path(path).resolve()
    if manifest_path.is_dir():
        manifest_path = manifest_path / "instance.yaml"
    value, raw = _read_yaml(manifest_path)
    problems = _schema_problems(value, INSTANCE_SCHEMA, "instance")
    root = manifest_path.parent
    resolved_corpus = (
        Path(corpus_root).resolve() if corpus_root is not None else find_corpus_root(root)
    )

    site: LoadedSite | None = None
    site_relative = value.get("site_manifest")
    if isinstance(site_relative, str):
        site_parts = PurePosixPath(site_relative.replace("\\", "/")).parts
        if (
            len(site_parts) != 3
            or site_parts[0] != "sites"
            or site_parts[2] != "site.yaml"
        ):
            problems.append(
                "instance.site_manifest: must be sites/<site-id>/site.yaml "
                "relative to the authoring root"
            )
        try:
            site_path = safe_regular_file(resolved_corpus, site_relative)
            site = load_site(site_path)
        except (HarborManifestError, OSError, ValueError) as exc:
            problems.append(f"instance.site_manifest: {exc}")

    instance_id = value.get("instance_id")
    if isinstance(instance_id, str) and root.name != instance_id:
        problems.append(
            f"instance.instance_id: must match its directory name {root.name!r}"
        )

    paths = value.get("paths")
    if isinstance(paths, dict):
        visibility = {
            name: relative
            for name, relative in paths.items()
            if name
            in {
                "instruction",
                "public",
                "verifier",
                "hidden_fixtures",
                "oracle_solution",
            }
            and isinstance(relative, str)
        }
        problems.extend(_overlap_problems(root, visibility, "instance"))
        tree_paths = {
            name: relative
            for name, relative in visibility.items()
            if name in {"public", "verifier", "hidden_fixtures"}
        }
        problems.extend(_declared_tree_problems(root, tree_paths, "instance"))
        for name in ("instruction", "oracle_solution"):
            relative = paths.get(name)
            if isinstance(relative, str):
                try:
                    safe_regular_file(root, relative)
                except (OSError, ValueError) as exc:
                    problems.append(f"instance.paths.{name}: {exc}")
        public = paths.get("public")
        if isinstance(public, str):
            try:
                safe_regular_file(root, (Path(public) / "run.sh").as_posix())
            except (OSError, ValueError) as exc:
                problems.append(f"instance.paths.public.run.sh: {exc}")
    tests = value.get("tests")
    if isinstance(tests, dict):
        seen: set[str] = set()
        for dimension, nodes in tests.items():
            if not isinstance(nodes, list):
                continue
            minimum = MINIMUM_NODES.get(dimension, 0)
            if len(nodes) < minimum:
                problems.append(
                    f"instance.tests.{dimension}: full-stack instances require at "
                    f"least {minimum} node(s)"
                )
            for node in nodes:
                if not isinstance(node, str):
                    continue
                if not node.startswith(f"{dimension}::"):
                    problems.append(
                        f"instance.tests.{dimension}: node must start with "
                        f"'{dimension}::': {node}"
                    )
                if node in seen:
                    problems.append(f"instance.tests: duplicate node across groups: {node}")
                seen.add(node)

    calibration = value.get("calibration")
    if isinstance(calibration, dict):
        nop = calibration.get("nop_max_score")
        oracle = calibration.get("oracle_min_score")
        if isinstance(nop, (int, float)) and nop > 20:
            problems.append("instance.calibration.nop_max_score: must be at most 20")
        if isinstance(oracle, (int, float)) and oracle < 90:
            problems.append("instance.calibration.oracle_min_score: must be at least 90")
        if isinstance(nop, (int, float)) and isinstance(oracle, (int, float)) and nop >= oracle:
            problems.append(
                "instance.calibration: nop_max_score must be below oracle_min_score"
            )

    if problems:
        raise HarborManifestError(problems)
    assert site is not None
    return LoadedInstance(
        path=manifest_path,
        root=root,
        corpus_root=resolved_corpus,
        data=value,
        sha256=hashlib.sha256(raw).hexdigest(),
        site=site,
    )
