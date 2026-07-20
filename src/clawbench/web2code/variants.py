"""Deterministic WebsiteBench VariantCompiler and commerce adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

import yaml
from jsonschema import Draft202012Validator

from .commerce import CommercePolicyError, validate_policies
from .registry import SiteRegistry, sha256_value


VARIANT_VERSION = "websitebench.variant.v1"
COMPILER_VERSION = "websitebench.variant-compiler.v1"
ADAPTER_VERSION = "white-label-commerce-v1.1"
JOURNEYS = (
    "catalog_observability",
    "account_lifecycle",
    "cart_inventory",
    "checkout_concurrency",
    "orders_terminal",
)
FORBIDDEN_KEYS = frozenset({"split", "script", "template", "eval", "exec", "import", "dynamic_import", "module"})
ASSERTION_PARAMETERS: Mapping[str, Mapping[str, type]] = {
    "rule_visible": {
        "quantity_cap": int,
        "case_size": int,
        "minimum": int,
        "maximum": int,
        "per_sku_limit": int,
        "reservation_minutes": int,
        "shipping": bool,
        "store_required": bool,
        "slot_required": bool,
    },
    "account_token_lifetime": {"verification_minutes": int, "reset_minutes": int},
    "quantity_policy": {"maximum": int, "minimum": int, "case_size": int, "limit": int},
    "pricing_policy": {"six_percent": int, "twelve_percent": int, "tax_basis_points": int},
    "inventory_policy": {"ttl_minutes": int, "extends": bool, "merge": str, "scope": str},
    "fulfillment_policy": {"atomic_inventory_and_capacity": bool, "shared_slot_capacity": int},
    "payment_decline_safe": {"reservation_retained": bool},
    "idempotent_checkout": {"same_key_same_order": bool, "conflicting_payload_rejected": bool},
    "atomic_concurrency": {"stock_floor": int, "idempotent": bool},
    "order_isolation": {"cross_account_hidden": bool},
    "cancellation_policy": {
        "window_minutes": int,
        "minimum_notice_minutes": int,
        "restore_once": bool,
    },
    "terminal_order": {"state": str, "cancellable": bool},
}
BASELINE_POLICIES = {
    "quantity": {"kind": "standard_cap", "parameters": {"maximum": 5}},
    "pricing": {"kind": "standard", "parameters": {"tax_basis_points": 825}},
    "inventory": {"kind": "checkout_decrement", "parameters": {"atomic": True}},
    "fulfillment": {"kind": "shipping", "parameters": {"standard_cents": 799, "free_threshold_cents": 7500}},
    "cancellation": {"kind": "window", "parameters": {"minutes": 1440}},
    "token_lifetime": {"kind": "fixed", "parameters": {"verification_minutes": 30, "reset_minutes": 60}},
}


class VariantValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CompilationResult:
    site_id: str
    variant_id: str
    target: Path
    digest: str
    behavior_fingerprint: str
    files: tuple[str, ...]
    changed: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "variant_id": self.variant_id,
            "target": str(self.target),
            "digest": self.digest,
            "behavior_fingerprint": self.behavior_fingerprint,
            "files": list(self.files),
            "changed": list(self.changed),
        }


class VariantCompiler(Protocol):
    family_id: str

    def compile(self, spec: Mapping[str, Any], *, source_path: Path) -> Mapping[str, bytes]: ...


def _walk_forbidden(value: Any, location: str = "variant") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in FORBIDDEN_KEYS:
                raise VariantValidationError(f"{location}.{key}: executable or split field is forbidden")
            _walk_forbidden(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_forbidden(item, f"{location}.{index}")


def load_variant(path: Path | str, schemas_root: Path | None = None) -> dict[str, Any]:
    path = Path(path).resolve()
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise VariantValidationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VariantValidationError(f"{path} must contain a mapping")
    _walk_forbidden(value)
    root = schemas_root or path.parents[2] / "schemas"
    schema = json.loads((root / "variant.schema.json").read_text(encoding="utf-8"))
    failures = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda error: list(error.path))
    if failures:
        raise VariantValidationError(
            "invalid variant:\n- "
            + "\n- ".join(
                f"{'.'.join(str(part) for part in failure.path) or '<root>'}: {failure.message}"
                for failure in failures
            )
        )
    if tuple(value["journeys"]) != JOURNEYS:
        raise VariantValidationError(f"journeys must be the fixed canonical sequence: {list(JOURNEYS)}")
    seeds = value["seeds"]
    if len(set(seeds.values())) != 3:
        raise VariantValidationError("public, hidden, and concurrency seeds must be distinct")
    try:
        validate_policies(value["policies"])
    except CommercePolicyError as exc:
        raise VariantValidationError(str(exc)) from exc
    _validate_assertions(value)
    return value


def _validate_assertions(value: Mapping[str, Any]) -> None:
    covered = set()
    for index, assertion in enumerate(value["assertions"]):
        kind = assertion["kind"]
        covered.add(assertion["journey"])
        allowed = ASSERTION_PARAMETERS[kind]
        parameters = assertion["parameters"]
        unknown = set(parameters) - set(allowed)
        if unknown:
            raise VariantValidationError(
                f"assertions.{index}.{kind} has unknown parameters: {sorted(unknown)}"
            )
        if not parameters:
            raise VariantValidationError(f"assertions.{index}.{kind} requires typed parameters")
        for name, item in parameters.items():
            expected = allowed[name]
            valid = isinstance(item, expected)
            if expected is int and isinstance(item, bool):
                valid = False
            if not valid:
                raise VariantValidationError(
                    f"assertions.{index}.{kind}.{name} must be {expected.__name__}"
                )
    missing = set(JOURNEYS) - covered
    if missing:
        raise VariantValidationError(f"assertions do not cover journeys: {sorted(missing)}")


def behavior_payload(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "policies": spec["policies"],
        "journeys": spec["journeys"],
        "assertions": spec["assertions"],
    }


def behavior_fingerprint(spec: Mapping[str, Any]) -> str:
    return sha256_value(behavior_payload(spec))


def assert_behavioral_variant(spec: Mapping[str, Any]) -> None:
    if spec.get("golden_source"):
        return
    if spec["policies"] == BASELINE_POLICIES:
        raise VariantValidationError(
            "variant changes only branding/catalog/presentation; at least one policy rule must differ"
        )


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _yaml(value: Any) -> bytes:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True).encode("utf-8")


def _slug(value: str) -> str:
    return "-".join("".join(ch.casefold() if ch.isalnum() else " " for ch in value).split())


def _fixture(spec: Mapping[str, Any], seed: int, kind: str) -> dict[str, Any]:
    rng = random.Random(seed)
    categories = []
    for index, name in enumerate(spec["catalog"]["categories"]):
        slug = _slug(name)
        categories.append(
            {
                "id": f"cat_{slug.replace('-', '_')}",
                "slug": slug,
                "name": name,
                "description": f"Browse the {name} collection and its variant-specific purchase rules.",
                "image": {"kind": "generated-svg", "key": f"category-{slug}-{seed}", "background": f"#{rng.randrange(0x202020, 0xE0E0E0):06x}", "accent": f"#{rng.randrange(0x202020, 0xE0E0E0):06x}"},
            }
        )
    products = []
    nouns = spec["catalog"]["product_nouns"]
    for index in range(48):
        category = categories[index % 8]
        noun = nouns[index % len(nouns)]
        identity = f"{spec['variant_id'].replace('-', '_')}_{index + 1:02d}"
        slug = f"{_slug(noun)}-{index + 1}"
        products.append(
            {
                "id": f"prod_{identity}",
                "sku": f"{spec['variant_id'][:3].upper()}-{seed % 10000:04d}-{index + 1:03d}",
                "slug": slug,
                "title": f"{noun} {index + 1}",
                "brand": spec["catalog"]["brand"],
                "description": f"A deterministic {noun.casefold()} for testing catalog, cart, checkout, inventory, persistence, and ordering behavior.",
                "category_id": category["id"],
                "tags": [category["slug"], _slug(noun), spec["variant_id"]],
                "price_cents": int(spec["catalog"]["base_price_cents"]) + index * 137 + seed % 97,
                "compare_at_cents": None,
                "inventory": 12 + (seed + index) % 29,
                "rating_basis_points": 350 + (seed + index * 7) % 151,
                "review_count": 5 + (seed * (index + 1)) % 900,
                "featured_rank": index + 1,
                "image": {"kind": "generated-svg", "key": f"product-{identity}-{seed}", "background": f"#{rng.randrange(0x202020, 0xE0E0E0):06x}", "accent": f"#{rng.randrange(0x202020, 0xE0E0E0):06x}"},
            }
        )
    if kind == "concurrency":
        quantity_policy = spec["policies"]["quantity"]
        boundary_quantity = (
            int(quantity_policy["parameters"]["minimum"])
            if quantity_policy["kind"] == "wholesale_case"
            else 1
        )
        pickup = spec["policies"]["fulfillment"]["kind"] == "pickup_slots"
        products[0]["inventory"] = boundary_quantity * (2 if pickup else 1)
    accounts = [
        {
            "id": f"user_{spec['variant_id'].replace('-', '_')}_{seed}_{index}",
            "email": f"shopper{index}.{seed}@example.test",
            "password": f"Variant{seed}Test{index}!",
            "verified": True,
            "full_name": f"Test Shopper {index}",
        }
        for index in range(1, 5)
    ]
    scenario = {
        "kind": kind,
        "content_salt": f"{spec['variant_id']}-content-{seed}",
        "stock_one_product_id": (
            products[0]["id"]
            if kind == "concurrency" and products[0]["inventory"] == 1
            else None
        ),
    }
    if kind == "concurrency" and spec["policies"]["fulfillment"]["kind"] == "pickup_slots":
        scenario["slot_capacity_override"] = 1
    return {
        "schema_version": "websitebench.fixture.v1",
        "fixture_id": f"{spec['variant_id']}-{seed}",
        "seed": seed,
        "now": "2026-01-15T12:00:00Z",
        "catalog": {"categories": categories, "products": products},
        "accounts": accounts,
        "scenario": scenario,
    }


def _rule_lines(spec: Mapping[str, Any]) -> list[str]:
    policies = spec["policies"]
    lines = []
    for name in ("quantity", "pricing", "inventory", "fulfillment", "cancellation", "token_lifetime"):
        policy = policies[name]
        values = ", ".join(f"{key}={value}" for key, value in sorted(policy["parameters"].items()))
        lines.append(f"- **{name.replace('_', ' ').title()}**: `{policy['kind']}` ({values or 'no parameters'}).")
    return lines


def _manifest(spec: Mapping[str, Any], split: str) -> dict[str, Any]:
    seeds = spec["seeds"]
    return {
        "schema_version": "websitebench.site.v1",
        "site_id": spec["site_id"],
        "display_name": spec["display_name"],
        "site_version": spec["site_version"],
        "family_id": spec["family_id"],
        "split": split,
        "difficulty": "hard",
        "description": "A compiled synthetic commerce variant with persistent accounts, carts, checkout, inventory, orders, controlled time, and concurrency rules.",
        "taxonomy": {
            "product_type": "ecommerce-marketplace",
            "capability_tags": ["account-lifecycle", "catalog-search", "checkout", "inventory", "order-management", "password-reset", "persistent-cart"],
            "interaction_tags": ["async-feedback", "form-validation", "responsive-navigation"],
            "roles": ["guest", "customer"],
            "stateful_entities": ["user", "session", "verification-token", "password-reset-token", "cart", "cart-item", "product", "inventory", "order", "order-item"],
        },
        "public": {
            "prd": "public/PRD.md", "candidate_contract": "public/candidate-contract.md",
            "task_schema": "../schemas/task.schema.json", "fixture_schema": "../schemas/fixture.schema.json",
            "admin_contract_schema": "../schemas/admin-contract.schema.json", "visual_checkpoints": "public/visual-checkpoints.json",
            "smoke_cases": "public/public-smoke-cases.json", "scoring": "public/scoring.json", "report_schema": "../schemas/report.schema.json",
        },
        "services": {"public_port": 8080, "admin_port": 8081, "health_path": "/healthz", "mailbox_delivery_path": "/api/v1/messages", "mailbox_query_path": "/api/v1/inbox"},
        "routes": ["/", "/search", "/products/{slug}", "/cart", "/register", "/verify", "/login", "/forgot-password", "/reset-password", "/checkout", "/checkout/success/{order_number}", "/account/orders", "/account/orders/{order_number}"],
        "seeds": {
            "public": [{"id": seeds["public"], "purpose": "Agent exploration and scored public data", "fixture": f"public/fixtures/{seeds['public']}.json"}],
        },
        "tracks": {
            "core": {"enabled": True, "human_messages": 0, "human_minutes": 0, "human_file_edits": False},
            "hitl": {"enabled": True, "human_messages": 12, "human_minutes": 90, "human_file_edits": False},
        },
        "agent_budget": {"wall_time_seconds": 7200, "token_budget": 400000, "browser_actions": 400, "candidate_builds": 10},
        "pilot_agent": {"harness": "codex", "model": "gpt-5.5-codex", "thinking_level": "xhigh"},
        "browser_policy": {"engine": "browser-use", "version": "0.12.6", "reference_access": "continuous", "allowed": ["navigate and interact through the controlled browser", "observe visible rules and test the public seed"], "denied": ["raw HTML, source bundles, private fixtures, evaluator files, or general network access"]},
        "license": {"code": "Apache-2.0", "content": "CC0-1.0 synthetic data", "assets": "CC0-1.0 generated artwork", "trademarks": f"{spec['display_name']} is a synthetic benchmark brand"},
    }


def _scoring(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "websitebench.scoring.v1", "site_version": spec["site_version"], "hard_failure_score": 0,
        "dimensions": {
            "visual": {"max_score": 20, "aggregation": "mean checkpoint similarity times max_score", "checkpoint_metrics": {"ssim": 0.3, "edge_f1": 0.2, "color_histogram": 0.15, "text": 0.2, "geometry": 0.15}, "checkpoint_file": "visual-checkpoints.json"},
            "interactions": {"max_score": 20, "aggregation": "passed assertions divided by total assertions times max_score", "groups": list(JOURNEYS)},
            "journeys": {"max_score": 40, "aggregation_kind": "normalized-executions-v1", "aggregation": "sum execution score divided by 50 times 40", "journey_max_score": 5, "terminal_failure_cap": 2.5, "execution_score_total": 50, "execution_count": 10, "journeys": list(JOURNEYS)},
            "robustness": {"max_score": 15, "aggregation": "one point per passing group", "groups": [f"rule-{index:02d}" for index in range(1, 16)]},
            "efficiency": {"max_score": 5, "aggregation": "one point per satisfied target", "targets": {"clean_build_seconds": {"operator": "<=", "value": 600}, "image_bytes": {"operator": "<=", "value": 1610612736}, "peak_memory_bytes": {"operator": "<=", "value": 1073741824}, "p95_latency_ms_at_10_concurrent": {"operator": "<=", "value": 1000}, "source_bytes": {"operator": "<=", "value": 52428800}}},
        },
        "hard_failures": ["candidate-build-failed", "candidate-startup-failed", "runtime-reference-request", "runtime-internet-request", "private-source-copy", "privileged-runtime"],
    }


class CommerceVariantCompiler:
    family_id = "white-label-commerce-v1"

    def __init__(self, *, split: str, corpus_root: Path) -> None:
        self.split = split
        self.corpus_root = corpus_root

    def compile(self, spec: Mapping[str, Any], *, source_path: Path) -> Mapping[str, bytes]:
        del source_path
        assert_behavioral_variant(spec)
        if spec.get("golden_source"):
            return self._golden(spec)
        seeds = spec["seeds"]
        manifest = _manifest(spec, self.split)
        prd = "\n".join(
            [f"# {spec['display_name']}", "", spec["instruction"], "", "## Observable business rules", "", *_rule_lines(spec), "", "The controlled clock, validation errors, inventory state, totals, and cancellation availability are observable through normal browser flows.", ""]
        ).encode("utf-8")
        contract = "\n".join(
            [
                f"# Candidate contract — {spec['display_name']}",
                "",
                "Build a persistent full-stack application implementing every public rule. No reference proxying or external network dependency is allowed.",
                "",
                "## Runtime and interaction contract",
                "",
                "- Serve the browser application on `PORT` (8080) and the private deterministic control plane on `BENCH_ADMIN_PORT` (8081).",
                "- Implement the routes in `manifest.yaml` with normal HTML forms for registration, login, reset, cart add/update, checkout, order detail, and cancellation.",
                "- Cart add accepts `product_id`, `quantity`, and `return_to`; checkout accepts `idempotency_key`, test card fields, and—when applicable—`store` and `slot`.",
                "- Deliver verification/reset links only to `MAILBOX_API_URL` with `MAILBOX_DELIVERY_TOKEN`.",
                "- Persist accounts, sessions, tokens, carts, reservations, inventory/capacity, idempotency records, and orders below `DATA_DIR` across restarts.",
                "",
                "## Private deterministic control plane",
                "",
                "Require `X-Bench-Admin-Token` for `POST /__bench/reset`, `GET /__bench/state`, and `POST /__bench/clock/advance`. Reset accepts `run_id`, `seed`, `now`, and an evaluator-provided fixture path. State returns normalized entities/resources plus the loaded public `policy_profile`; clock advance accepts non-negative `seconds`. The public port must return 404 for every `/__bench/*` path.",
                "",
            ]
        ).encode("utf-8")
        visual = {"schema_version": "websitebench.visual.v1", "site_version": spec["site_version"], "viewports": {"desktop": {"width": 1440, "height": 1000, "device_scale_factor": 1}, "mobile": {"width": 390, "height": 844, "device_scale_factor": 1}}, "comparison": {"ssim": 0.3, "edge_f1": 0.2, "color_histogram": 0.15, "text": 0.2, "geometry": 0.15}, "checkpoints": [{"id": "home-desktop", "route": "/", "viewport": "desktop", "state": "catalog-ready"}]}
        smoke = {"schema_version": "websitebench.smoke.v1", "site_version": spec["site_version"], "seed": seeds["public"], "cases": [{"id": journey, "journey_kind": journey, "mandatory": True} for journey in JOURNEYS]}
        task = {"schema_version": "websitebench.task.v2", "run_id": f"template-{spec['site_id']}", "task_id": spec["site_id"], "site_id": spec["site_id"], "site_version": spec["site_version"], "family_id": spec["family_id"], "variant_id": spec["variant_id"], "instruction": spec["instruction"], "track": "core", "target_url": "http://reference:8080", "mailbox_url": "http://mailbox:8025", "public_files": {"manifest": "/task/public/manifest.yaml", "prd": "/task/public/PRD.md", "candidate_contract": "/task/public/candidate-contract.md", "smoke_cases": "/task/public/public-smoke-cases.json"}, "budget": manifest["agent_budget"], "browser_gateway": {"url": "http://browser-gateway:7000", "tool_name": "controlled_browser", "reference_access": "continuous"}, "candidate_workspace": "/workspace/candidate", "agent": manifest["pilot_agent"], "issued_at": "2026-01-01T00:00:00Z"}
        assertions = {"schema_version": "websitebench.assertions.v1", "site_id": spec["site_id"], "variant_id": spec["variant_id"], "journeys": list(JOURNEYS), "assertions": spec["assertions"], "policies": spec["policies"]}
        files: dict[str, bytes] = {
            "public/manifest.yaml": _yaml(manifest), "public/PRD.md": prd,
            "public/candidate-contract.md": contract, "public/visual-checkpoints.json": _json(visual),
            "public/public-smoke-cases.json": _json(smoke), "public/scoring.json": _json(_scoring(spec)),
            f"public/fixtures/{seeds['public']}.json": _json(_fixture(spec, seeds["public"], "exploration")),
            f"judge/fixtures/{seeds['hidden']}.json": _json(_fixture(spec, seeds["hidden"], "functional")),
            f"judge/fixtures/{seeds['concurrency']}.json": _json(_fixture(spec, seeds["concurrency"], "concurrency")),
            "judge/assertions.json": _json(assertions), "task.json": _json(task),
            "variant.yaml": _yaml(dict(spec)),
        }
        self._validate_generated(files)
        return files

    def _validate_generated(self, files: Mapping[str, bytes]) -> None:
        validations: list[tuple[str, Any, str]] = [
            ("task v2", json.loads(files["task.json"]), "task.schema.json"),
            (
                "site manifest",
                yaml.safe_load(files["public/manifest.yaml"]),
                "site-manifest.schema.json",
            ),
        ]
        validations.extend(
            (name, json.loads(payload), "fixture.schema.json")
            for name, payload in sorted(files.items())
            if "/fixtures/" in name and name.endswith(".json")
        )
        for label, value, schema_name in validations:
            schema = json.loads(
                (self.corpus_root / "schemas" / schema_name).read_text(encoding="utf-8")
            )
            failures = sorted(
                Draft202012Validator(schema).iter_errors(value),
                key=lambda error: list(error.absolute_path),
            )
            if failures:
                raise VariantValidationError(
                    f"compiler produced invalid {label}: "
                    + "; ".join(error.message for error in failures)
                )
        public_payload = b"\n".join(
            data
            for name, data in sorted(files.items())
            if name.startswith("public/") or name == "task.json"
        )
        private_spec = yaml.safe_load(files["variant.yaml"])
        public_text = public_payload.decode("utf-8", errors="ignore")
        for visibility in ("hidden", "concurrency"):
            private_seed = int(private_spec["seeds"][visibility])
            seed_reference = re.compile(
                rf"(?:\b{private_seed}\.json\b|[\"']?(?:seed|id)[\"']?\s*:\s*[\"']?{private_seed}(?!\d))",
                re.IGNORECASE,
            )
            if seed_reference.search(public_text):
                raise VariantValidationError(
                    f"private {visibility} seed leaked into public output"
                )
        for private_name in (
            name
            for name in files
            if name.startswith("judge/") or name == "variant.yaml"
        ):
            private_payload = files[private_name]
            if len(private_payload) >= 16 and private_payload in public_payload:
                raise VariantValidationError(
                    f"private compiler input leaked into public output: {private_name}"
                )

    def _golden(self, spec: Mapping[str, Any]) -> Mapping[str, bytes]:
        source = (self.corpus_root / spec["golden_source"]).resolve()
        if source.parent != self.corpus_root.resolve():
            raise VariantValidationError("golden source must be a direct corpus site directory")
        files: dict[str, bytes] = {}
        for relative in ("public/manifest.yaml", "public/PRD.md", "public/candidate-contract.md", "public/visual-checkpoints.json", "public/public-smoke-cases.json", "public/scoring.json"):
            files[relative] = (source / relative).read_bytes()
        for seed in spec["seeds"].values():
            candidates = [source / "public" / "fixtures" / f"{seed}.json", source / "judge" / "fixtures" / f"{seed}.json"]
            for candidate in candidates:
                if candidate.exists():
                    relative = candidate.relative_to(source).as_posix()
                    files[relative] = candidate.read_bytes()
        manifest = yaml.safe_load(files["public/manifest.yaml"])
        files["task.json"] = _json(
            {
                "schema_version": "websitebench.task.v2",
                "run_id": f"template-{spec['site_id']}",
                "task_id": spec["site_id"],
                "site_id": spec["site_id"],
                "site_version": spec["site_version"],
                "family_id": spec["family_id"],
                "variant_id": spec["variant_id"],
                "instruction": spec["instruction"],
                "track": "core",
                "target_url": "http://reference:8080",
                "mailbox_url": "http://mailbox:8025",
                "public_files": {
                    "manifest": "/task/public/manifest.yaml",
                    "prd": "/task/public/PRD.md",
                    "candidate_contract": "/task/public/candidate-contract.md",
                    "smoke_cases": "/task/public/public-smoke-cases.json",
                },
                "budget": manifest["agent_budget"],
                "browser_gateway": {
                    "url": "http://browser-gateway:7000",
                    "tool_name": "controlled_browser",
                    "reference_access": "continuous",
                },
                "candidate_workspace": "/workspace/candidate",
                "agent": manifest["pilot_agent"],
                "issued_at": "2026-01-01T00:00:00Z",
            }
        )
        files["judge/assertions.json"] = _json(
            {
                "schema_version": "websitebench.assertions.v1",
                "site_id": spec["site_id"],
                "variant_id": spec["variant_id"],
                "journeys": list(JOURNEYS),
                "assertions": spec["assertions"],
                "policies": spec["policies"],
            }
        )
        files["variant.yaml"] = _yaml(dict(spec))
        self._validate_generated(files)
        return files


def _with_lock(spec: Mapping[str, Any], files: Mapping[str, bytes]) -> tuple[dict[str, bytes], dict[str, Any]]:
    entries = {path: hashlib.sha256(data).hexdigest() for path, data in sorted(files.items())}
    lock_body = {
        "schema_version": "websitebench.variant-lock.v1",
        "compiler_version": COMPILER_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "site_id": spec["site_id"],
        "variant_id": spec["variant_id"],
        "semantic_spec_sha256": sha256_value(spec),
        "behavior_fingerprint": behavior_fingerprint(spec),
        "files": entries,
    }
    lock = {**lock_body, "digest": f"sha256:{sha256_value(lock_body)}"}
    return {**files, "variant.digest.json": _json(lock)}, lock


def _differences(target: Path, files: Mapping[str, bytes]) -> tuple[str, ...]:
    differences = []
    expected = set(files)
    for relative, data in sorted(files.items()):
        path = target / relative
        if not path.is_file() or path.read_bytes() != data:
            differences.append(relative)
    lock = target / "variant.digest.json"
    if lock.exists():
        try:
            old = json.loads(lock.read_text(encoding="utf-8"))
            expected.update(old.get("files", {}))
        except json.JSONDecodeError:
            pass
    for relative in sorted(expected - set(files)):
        if (target / relative).exists():
            differences.append(f"removed:{relative}")
    return tuple(differences)


def compile_variant(spec_path: Path | str, *, registry: SiteRegistry | None = None, output_root: Path | str | None = None, check: bool = False) -> CompilationResult:
    spec_path = Path(spec_path).resolve()
    registry = registry or SiteRegistry.default(spec_path.parents[3])
    spec = load_variant(spec_path, registry.corpus_root / "schemas")
    registered_spec = registry.variant_spec(spec["site_id"])
    if registered_spec.resolve() != spec_path:
        raise VariantValidationError(
            f"{spec['site_id']} must compile from its Registry-owned spec: {registered_spec}"
        )
    split = registry.family_split(spec["family_id"])
    compiler = CommerceVariantCompiler(split=split, corpus_root=registry.corpus_root)
    generated = compiler.compile(spec, source_path=spec_path)
    files, lock = _with_lock(spec, generated)
    target = Path(output_root).resolve() / spec["site_id"] if output_root is not None else registry.corpus_root / spec["site_id"]
    changed = _differences(target, files)
    if check:
        if changed:
            raise VariantValidationError(f"compiled variant drift for {spec['site_id']}: {', '.join(changed)}")
    else:
        target.mkdir(parents=True, exist_ok=True)
        stale: set[str] = set()
        old_lock = target / "variant.digest.json"
        if old_lock.is_file():
            try:
                stale = set(json.loads(old_lock.read_text(encoding="utf-8")).get("files", {})) - set(files)
            except json.JSONDecodeError:
                stale = set()
        for relative in sorted(stale):
            stale_path = (target / relative).resolve()
            if target.resolve() not in stale_path.parents:
                raise VariantValidationError(f"stale generated path escapes target: {relative}")
            if stale_path.is_file() or stale_path.is_symlink():
                stale_path.unlink()
        for relative, data in sorted(files.items()):
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return CompilationResult(spec["site_id"], spec["variant_id"], target, lock["digest"], lock["behavior_fingerprint"], tuple(sorted(files)), changed)


def compile_all(*, registry: SiteRegistry | None = None, output_root: Path | str | None = None, check: bool = False) -> dict[str, Any]:
    registry = registry or SiteRegistry.default()
    results = [compile_variant(path, registry=registry, output_root=output_root, check=check) for path in registry.variant_specs()]
    body = {"schema_version": "websitebench.compile-all.v1", "compiler_version": COMPILER_VERSION, "variants": [{"site_id": item.site_id, "variant_id": item.variant_id, "digest": item.digest, "behavior_fingerprint": item.behavior_fingerprint} for item in results]}
    summary = {**body, "digest": f"sha256:{sha256_value(body)}"}
    if not check and output_root is not None:
        Path(output_root).mkdir(parents=True, exist_ok=True)
        (Path(output_root) / "compile-all.json").write_bytes(_json(summary))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clawbench-variant")
    subparsers = parser.add_subparsers(dest="command", required=True)
    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("spec", nargs="?", type=Path)
    compile_parser.add_argument("--all", action="store_true")
    compile_parser.add_argument("--check", action="store_true")
    compile_parser.add_argument("--output-root", type=Path)
    compile_parser.add_argument("--registry", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry = SiteRegistry(args.registry) if args.registry else SiteRegistry.default()
    try:
        if args.all:
            if args.spec:
                raise VariantValidationError("a spec path cannot be combined with --all")
            result = compile_all(registry=registry, output_root=args.output_root, check=args.check)
        else:
            if not args.spec:
                raise VariantValidationError("spec path is required unless --all is used")
            result = compile_variant(args.spec, registry=registry, output_root=args.output_root, check=args.check).to_dict()
    except VariantValidationError as exc:
        print(exc)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
