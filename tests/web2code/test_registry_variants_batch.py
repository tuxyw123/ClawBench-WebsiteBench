"""Regression coverage for the registry/compiler/attempt/batch production chain."""

from __future__ import annotations

import json
import importlib.util
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from clawbench.web2code.attempts import (
    AttemptJournal,
    AttemptOutcome,
    AttemptStage,
    OutcomeKind,
    classify_failure,
    retry_advice,
)
from clawbench.web2code.batch import (
    BatchLedger,
    create_plan,
    run_workers,
    verify_frozen_inputs,
)
from clawbench.web2code.calibration import validate_calibration
from clawbench.web2code.commerce import CommercePolicyError, CommerceReference, reference_profile_facts
from clawbench.web2code.commerce_runtime import DomainError, PersistentCommerce
from clawbench.web2code.registry import (
    RegistryValidationError,
    SiteRegistry,
    _contained_path,
    _input_file_records,
)
from clawbench.web2code.run import prepare_run
from clawbench.web2code.scoring import score_evaluation
from clawbench.web2code.variants import (
    VariantValidationError,
    assert_behavioral_variant,
    compile_variant,
    load_variant,
)


ROOT = Path(__file__).resolve().parents[2]
VARIANTS = ROOT / "websitebench" / "variants" / "white-label-commerce-v1"
SCHEMAS = ROOT / "websitebench" / "schemas"


def spec(name: str) -> dict:
    return load_variant(VARIANTS / f"{name}.yaml", SCHEMAS)


def reference(name: str, **state) -> CommerceReference:
    value = spec(name)
    return CommerceReference(
        policies=value["policies"],
        now=datetime(2026, 1, 15, 12, tzinfo=timezone.utc),
        stock=state.pop("stock", {"sku": 30}),
        **state,
    )


def persistent(name: str, tmp_path: Path) -> tuple[PersistentCommerce, dict]:
    value = spec(name)
    fixture = json.loads(
        (
            ROOT
            / "websitebench"
            / value["site_id"]
            / "public"
            / "fixtures"
            / f"{value['seeds']['public']}.json"
        ).read_text()
    )
    return (
        PersistentCommerce(tmp_path / f"{name}.json", spec=value, initial_fixture=fixture),
        fixture,
    )


def test_registry_resolves_all_variants_to_one_validation_split() -> None:
    registry = SiteRegistry.default(ROOT)
    assert registry.site_ids == (
        "ember-drop",
        "foundry-wholesale",
        "harbor-pickup",
        "northstar-market",
    )
    for site_id in registry.site_ids:
        resolved = registry.resolve(site_id)
        assert resolved.family_id == "white-label-commerce-v1"
        assert resolved.split == "validation"
        snapshot = resolved.run_manifest()
        assert snapshot["digest"].startswith("sha256:")
        assert snapshot["execution_seeds"] == dict(resolved.execution_seeds)
        assert set(resolved.manifest["seeds"]) == {"public"}
        schema = json.loads((SCHEMAS / "run-manifest.schema.json").read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(snapshot)
    with pytest.raises(TypeError):
        registry.resolve("foundry-wholesale").service_roles["agent"] = "changed"


def test_registry_paths_reject_traversal_and_all_symlink_components(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    target = root / "target.yaml"
    target.write_text("value: true\n", encoding="utf-8")
    alias = root / "alias.yaml"
    alias.symlink_to(target)
    directory = root / "directory"
    directory.mkdir()
    linked_directory = root / "linked-directory"
    linked_directory.symlink_to(directory, target_is_directory=True)

    with pytest.raises(RegistryValidationError, match="path traversal"):
        _contained_path(root, "../target.yaml", label="test")
    with pytest.raises(RegistryValidationError, match="symlinks are forbidden"):
        _contained_path(root, "alias.yaml", label="test")
    with pytest.raises(RegistryValidationError, match="symlinks are forbidden"):
        _contained_path(root, "linked-directory/fixture.json", label="test", must_exist=False)
    nested_alias = directory / "nested-alias.yaml"
    nested_alias.symlink_to(target)
    with pytest.raises(RegistryValidationError, match="cannot contain a symlink"):
        _input_file_records({directory}, root)


def test_new_variant_dry_run_exports_task_v2_without_private_inputs(tmp_path: Path) -> None:
    run_dir = prepare_run(
        site="foundry-wholesale",
        track="core",
        model="gpt-5.5-codex",
        thinking_level="low",
        output_root=tmp_path,
    )
    task = json.loads((run_dir / "task.json").read_text())
    assert task["schema_version"] == "websitebench.task.v2"
    assert task["family_id"] == "white-label-commerce-v1"
    assert task["variant_id"] == "foundry-wholesale"
    assert task["budget"] == {
        "wall_time_seconds": 7200,
        "token_budget": 400000,
        "browser_actions": 400,
        "candidate_builds": 10,
    }
    assert not (run_dir / "judge").exists()
    assert not (run_dir / "reference").exists()
    assert list((run_dir / "trusted").glob("run-manifest.*.json"))
    assert "run-manifest" not in (run_dir / "task.json").read_text()
    candidate_visible = b"\n".join(
        path.read_bytes()
        for path in (run_dir / "public").rglob("*")
        if path.is_file()
    )
    assert b"9301" not in candidate_visible
    assert b"9399" not in candidate_visible
    journal = json.loads(next((run_dir / "attempts").glob("*.json")).read_text())
    assert journal["job_id"] == task["run_id"]
    assert journal["attempt_number"] == 1


def test_variant_compile_is_byte_stable_and_check_detects_drift(tmp_path: Path) -> None:
    registry = SiteRegistry.default(ROOT)
    source = VARIANTS / "foundry-wholesale.yaml"
    first = compile_variant(source, registry=registry, output_root=tmp_path)
    before = {path.relative_to(first.target): path.read_bytes() for path in first.target.rglob("*") if path.is_file()}
    compile_variant(source, registry=registry, output_root=tmp_path, check=True)
    after = {path.relative_to(first.target): path.read_bytes() for path in first.target.rglob("*") if path.is_file()}
    assert before == after
    (first.target / "public" / "PRD.md").write_text("drift")
    with pytest.raises(VariantValidationError, match="public/PRD.md"):
        compile_variant(source, registry=registry, output_root=tmp_path, check=True)


def test_variant_rejects_split_and_presentation_only_change(tmp_path: Path) -> None:
    value = spec("northstar-standard")
    value.pop("golden_source")
    value["site_id"] = "presentation-only"
    value["variant_id"] = "presentation-only"
    with pytest.raises(VariantValidationError, match="branding/catalog"):
        assert_behavioral_variant(value)
    value["split"] = "test"
    path = tmp_path / "variant.yaml"
    path.write_text(yaml.safe_dump(value))
    with pytest.raises(VariantValidationError, match="split"):
        load_variant(path, SCHEMAS)


def test_foundry_wholesale_case_tiers_shipping_tax_tokens_and_cancel() -> None:
    shop = reference("foundry-wholesale", stock={"sku": 30})
    with pytest.raises(CommercePolicyError, match="login required"):
        shop.validate_quantity(owner="a", sku="sku", quantity=3, authenticated=False)
    with pytest.raises(CommercePolicyError, match="complete case"):
        shop.validate_quantity(owner="a", sku="sku", quantity=4)
    assert shop.line_total(5000, 6) == (28500, 1500)
    assert shop.line_total(5000, 12) == (54000, 6000)
    assert shop.totals([(5000, 6)]) == {
        "subtotal_cents": 28500,
        "tax_cents": 1853,
        "shipping_cents": 0,
        "total_cents": 30353,
    }
    assert (shop.token_expires_at("verification") - shop.now).total_seconds() == 45 * 60
    assert (shop.token_expires_at("reset") - shop.now).total_seconds() == 90 * 60
    order = shop.checkout(owner="a", sku="sku", quantity=3, idempotency_key="one")
    assert shop.cancel(order["number"], "a")["status"] == "cancelled"
    assert shop.stock["sku"] == 30


def test_ember_reservation_does_not_extend_transfers_survives_decline_and_expires() -> None:
    shop = reference("ember-drop", stock={"sku": 2})
    reservation = shop.reserve("device", "sku", 1)
    expiry = reservation.expires_at
    shop.advance(120)
    assert shop.reserve("device", "sku", 1).expires_at == expiry
    shop.transfer_reservation("device", "account", "sku")
    with pytest.raises(CommercePolicyError, match="declined"):
        shop.checkout(owner="account", sku="sku", quantity=1, idempotency_key="decline", payment_ok=False)
    assert ("account", "sku") in shop.reservations
    shop.advance(8 * 60 + 1)
    assert shop.available_stock("sku") == 2
    shop.reserve("account", "sku", 1)
    order = shop.checkout(owner="account", sku="sku", quantity=1, idempotency_key="success")
    with pytest.raises(CommercePolicyError, match="final sale"):
        shop.cancel(order["number"], "account")
    with pytest.raises(CommercePolicyError, match="lifetime"):
        shop.validate_quantity(owner="account", sku="sku", quantity=1)


def test_harbor_checkout_and_cancel_atomically_restore_store_and_slot_once() -> None:
    shop = reference(
        "harbor-pickup",
        stock={},
        store_stock={"harbor-east": {"sku": 1}},
        slot_capacity={"slot-1": 1},
    )
    order = shop.checkout(
        owner="a",
        sku="sku",
        quantity=1,
        idempotency_key="pickup",
        store="harbor-east",
        slot="slot-1",
        slot_starts_at="2026-01-15T14:00:00Z",
    )
    assert shop.store_stock["harbor-east"]["sku"] == 0
    assert shop.slot_capacity["slot-1"] == 0
    cancelled = shop.cancel(order["number"], "a")
    assert cancelled["resources_restored"] is True
    shop.cancel(order["number"], "a")
    assert shop.store_stock["harbor-east"]["sku"] == 1
    assert shop.slot_capacity["slot-1"] == 1


def test_persistent_runtime_executes_foundry_account_order_and_restart(tmp_path: Path) -> None:
    shop, fixture = persistent("foundry-wholesale", tmp_path)
    product = fixture["catalog"]["products"][0]
    with pytest.raises(DomainError, match="Sign in"):
        shop.add_to_cart(product_id=product["id"], quantity=3, user=None, device="device")
    account = fixture["accounts"][0]
    session = shop.login(account["email"], account["password"], device="device")
    user = shop.user_for_session(session)
    assert user is not None
    shop.add_to_cart(product_id=product["id"], quantity=6, user=user, device="device")
    cart = shop.cart(user=user, device="device")
    assert cart["lines"][0]["discount_cents"] > 0
    order = shop.checkout(
        user=user,
        device="device",
        idempotency_key="one",
        card_number="4242424242424242",
    )
    shop.cancel(order["number"], user["id"])
    restored = PersistentCommerce(
        tmp_path / "foundry-wholesale.json",
        spec=spec("foundry-wholesale"),
        initial_fixture=fixture,
    )
    assert restored.order_for(order["number"], user["id"])["status"] == "cancelled"


def test_persistent_runtime_verifies_and_resets_with_single_use_tokens(tmp_path: Path) -> None:
    shop, _fixture = persistent("foundry-wholesale", tmp_path)
    email = "new-account@example.test"
    verification = shop.register(email, "InitialPassword123!", "InitialPassword123!")
    verification_record = shop.data["tokens"][verification]
    assert verification_record["expires_at"] - verification_record["issued_at"] == 45 * 60
    with pytest.raises(DomainError, match="Verify your email"):
        shop.login(email, "InitialPassword123!", device="device")
    shop.verify(verification)
    with pytest.raises(DomainError, match="invalid"):
        shop.verify(verification)
    old_session = shop.login(email, "InitialPassword123!", device="device")
    reset = shop.forgot_password(email)
    assert reset is not None
    reset_record = shop.data["tokens"][reset]
    assert reset_record["expires_at"] - reset_record["issued_at"] == 90 * 60
    shop.reset_password(reset, "ChangedPassword123!", "ChangedPassword123!")
    assert shop.user_for_session(old_session) is None
    with pytest.raises(DomainError, match="invalid"):
        shop.reset_password(reset, "AnotherPassword123!", "AnotherPassword123!")
    with pytest.raises(DomainError, match="incorrect"):
        shop.login(email, "InitialPassword123!", device="device")
    assert shop.user_for_session(
        shop.login(email, "ChangedPassword123!", device="device")
    ) is not None


def test_persistent_runtime_executes_ember_reservation_transfer_and_decline(tmp_path: Path) -> None:
    shop, fixture = persistent("ember-drop", tmp_path)
    product = fixture["catalog"]["products"][0]
    shop.add_to_cart(product_id=product["id"], quantity=1, user=None, device="device")
    original = shop.data["reservations"][f"guest:device|{product['id']}"]["expires_at"]
    shop.advance(120)
    shop.add_to_cart(product_id=product["id"], quantity=1, user=None, device="device")
    assert shop.data["reservations"][f"guest:device|{product['id']}"]["expires_at"] == original
    account = fixture["accounts"][0]
    session = shop.login(account["email"], account["password"], device="device")
    user = shop.user_for_session(session)
    assert user is not None
    reservation_key = f"account:{user['id']}|{product['id']}"
    assert shop.data["reservations"][reservation_key]["expires_at"] == original
    with pytest.raises(DomainError, match="declined"):
        shop.checkout(
            user=user,
            device="device",
            idempotency_key="decline",
            card_number="4000000000000002",
        )
    assert reservation_key in shop.data["reservations"]


def test_persistent_runtime_executes_harbor_atomic_pickup_restore(tmp_path: Path) -> None:
    shop, fixture = persistent("harbor-pickup", tmp_path)
    product = fixture["catalog"]["products"][0]
    account = fixture["accounts"][0]
    session = shop.login(account["email"], account["password"], device="device")
    user = shop.user_for_session(session)
    assert user is not None
    shop.add_to_cart(product_id=product["id"], quantity=1, user=user, device="device")
    before_stock = shop.data["store_stock"]["harbor-east"][product["id"]]
    before_capacity = shop.data["slots"]["pickup-early"]["capacity"]
    order = shop.checkout(
        user=user,
        device="device",
        idempotency_key="pickup",
        card_number="4242424242424242",
        store="harbor-east",
        slot="pickup-early",
    )
    assert shop.data["store_stock"]["harbor-east"][product["id"]] == before_stock - 1
    assert shop.data["slots"]["pickup-early"]["capacity"] == before_capacity - 1
    shop.cancel(order["number"], user["id"])
    shop.cancel(order["number"], user["id"])
    assert shop.data["store_stock"]["harbor-east"][product["id"]] == before_stock
    assert shop.data["slots"]["pickup-early"]["capacity"] == before_capacity


def test_persistent_harbor_concurrency_never_overbooks_shared_slot(tmp_path: Path) -> None:
    shop, fixture = persistent("harbor-pickup", tmp_path)
    product = fixture["catalog"]["products"][0]
    users = []
    for index, device in ((0, "one"), (1, "two")):
        account = fixture["accounts"][index]
        session = shop.login(account["email"], account["password"], device=device)
        user = shop.user_for_session(session)
        assert user is not None
        users.append((user, device))
        shop.add_to_cart(product_id=product["id"], quantity=1, user=user, device=device)
    shop.data["slots"]["pickup-early"]["capacity"] = 1

    def place(item: tuple[dict, str]) -> str:
        user, device = item
        try:
            return shop.checkout(
                user=user,
                device=device,
                idempotency_key=device,
                card_number="4242424242424242",
                store="harbor-east",
                slot="pickup-early",
            )["number"]
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(place, users))
    assert sum(value.startswith("HAR-") for value in outcomes) == 1
    assert outcomes.count("slot_full") == 1
    assert shop.data["slots"]["pickup-early"]["capacity"] == 0
    assert len(shop.data["orders"]) == 1


def test_compiled_reference_app_exposes_real_foundry_browser_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = spec("foundry-wholesale")
    fixture_path = (
        ROOT
        / "websitebench"
        / "foundry-wholesale"
        / "public"
        / "fixtures"
        / f"{value['seeds']['public']}.json"
    )
    monkeypatch.setenv("VARIANT_SPEC", str(VARIANTS / "foundry-wholesale.yaml"))
    monkeypatch.setenv("INITIAL_FIXTURE", str(fixture_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "app-data"))
    monkeypatch.setenv("BENCH_ADMIN_TOKEN", "test-admin-token")
    module_path = ROOT / "websitebench" / "commerce-runtime" / "reference" / "app.py"
    module_name = "_websitebench_foundry_reference_test"
    module_spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert module_spec and module_spec.loader
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    module_spec.loader.exec_module(module)
    from fastapi.testclient import TestClient

    client = TestClient(module.app)
    fixture = json.loads(fixture_path.read_text())
    product = fixture["catalog"]["products"][0]
    assert product["title"] in client.get("/").text
    guest = client.post(
        "/cart/add",
        data={"product_id": product["id"], "quantity": 3, "return_to": "/cart"},
    )
    assert "Sign in before" in guest.text
    account = fixture["accounts"][0]
    login = client.post(
        "/login",
        data={"email": account["email"], "password": account["password"], "next": "/"},
    )
    assert account["email"] in login.text
    cart = client.post(
        "/cart/add",
        data={"product_id": product["id"], "quantity": 3, "return_to": "/cart"},
    )
    assert product["title"] in cart.text
    admin = TestClient(module.admin)
    state = admin.get(
        "/__bench/state", headers={"X-Bench-Admin-Token": "test-admin-token"}
    )
    assert state.status_code == 200
    assert state.json()["policy_profile"]["quantity"]["kind"] == "wholesale_case"


def test_normalized_five_journey_two_seed_score_maps_fifty_to_forty() -> None:
    scoring = json.loads((ROOT / "websitebench" / "foundry-wholesale" / "public" / "scoring.json").read_text())
    facts = {
        "journeys": [
            {
                "id": journey,
                "seed": seed,
                "terminal_passed": True,
                "checkpoints": [{"id": "mandatory", "passed": True, "expected": True, "actual": True, "evidence_ids": []}],
            }
            for journey in ("catalog_observability", "account_lifecycle", "cart_inventory", "checkout_concurrency", "orders_terminal")
            for seed in (1301, 9301)
        ]
    }
    scored = score_evaluation(facts, scoring)
    assert scored["dimensions"]["journeys"]["score"] == 40
    assert scored["dimensions"]["journeys"]["passed"] == 10


@pytest.mark.parametrize(
    ("variant", "module", "parameter_path"),
    [
        ("foundry-wholesale", "pricing", ("tiers", 0, "percent")),
        ("ember-drop", "inventory", ("ttl_minutes",)),
        ("harbor-pickup", "fulfillment", ("slot_capacity",)),
    ],
)
def test_reference_profile_catches_independent_policy_mutations(
    variant: str, module: str, parameter_path: tuple[str | int, ...]
) -> None:
    value = spec(variant)
    identity = reference_profile_facts(value)
    assert len(identity["journeys"]) == 10
    assert all(journey["terminal_passed"] for journey in identity["journeys"])
    observed = json.loads(json.dumps(value["policies"]))
    target = observed[module]["parameters"]
    for part in parameter_path[:-1]:
        target = target[part]
    target[parameter_path[-1]] += 1
    mutated = reference_profile_facts(value, observed_policies=observed)
    assert any(not journey["terminal_passed"] for journey in mutated["journeys"])


@pytest.mark.parametrize(
    ("variant", "module", "parameter_path", "journey"),
    [
        ("foundry-wholesale", "pricing", ("tiers", 0, "percent"), "checkout_concurrency"),
        ("ember-drop", "inventory", ("ttl_minutes",), "cart_inventory"),
        ("harbor-pickup", "fulfillment", ("slot_capacity",), "checkout_concurrency"),
    ],
)
def test_independent_behavior_judge_rejects_mutated_runtime_profile(
    variant: str,
    module: str,
    parameter_path: tuple[str | int, ...],
    journey: str,
) -> None:
    judge_path = (
        ROOT / "websitebench" / "commerce-runtime" / "judge" / "commerce_judge.py"
    )
    module_name = f"_commerce_judge_{variant.replace('-', '_')}"
    judge_spec = importlib.util.spec_from_file_location(module_name, judge_path)
    assert judge_spec and judge_spec.loader
    judge_module = importlib.util.module_from_spec(judge_spec)
    judge_spec.loader.exec_module(judge_module)
    expected = spec(variant)["policies"]
    observed = json.loads(json.dumps(expected))
    target = observed[module]["parameters"]
    for part in parameter_path[:-1]:
        target = target[part]
    target[parameter_path[-1]] += 1
    judge = judge_module.CommerceJudge.__new__(judge_module.CommerceJudge)
    judge.policies = expected
    judge.state = lambda: {"policy_profile": observed}
    assert judge.policy_checkpoint(journey)["passed"] is False


def test_independent_judge_exception_is_a_typed_result_failure() -> None:
    judge_path = ROOT / "websitebench" / "commerce-runtime" / "judge" / "commerce_judge.py"
    module_spec = importlib.util.spec_from_file_location("_commerce_judge_failure", judge_path)
    assert module_spec and module_spec.loader
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    judge = module.CommerceJudge.__new__(module.CommerceJudge)
    judge.failures = []
    judge.reset = lambda _seed: None

    def broken(_seed: int) -> list[dict]:
        raise RuntimeError("candidate endpoint failed")

    journey = judge.evaluate_journey("checkout_concurrency", 9301, broken)
    assert journey["terminal_passed"] is False
    assert judge.failures == [
        {
            "id": "checkout_concurrency-exception",
            "category": "checkout",
            "severity": "critical",
            "summary": "Journey checkout_concurrency raised an exception",
            "expected": "journey completes and emits mandatory checkpoints",
            "actual": "RuntimeError: candidate endpoint failed",
            "reproduction": [
                "Reset seed 9301",
                "Execute journey checkout_concurrency",
            ],
            "evidence_ids": [],
        }
    ]


def test_attempt_journal_is_append_only_and_retry_policy_is_typed(tmp_path: Path) -> None:
    journal = AttemptJournal.create(
        tmp_path / "attempt.json",
        attempt_id="a1",
        run_id="run",
        job_id="job",
        attempt_number=1,
    )
    journal.transition(AttemptStage.AGENT)
    outcome = classify_failure(
        stage=AttemptStage.AGENT,
        reason_code="AGENT_FAILED",
        message="exit 1",
        attempt_number=1,
    )
    assert outcome.kind is OutcomeKind.CANDIDATE_FAILED
    assert outcome.retry.retryable is False
    journal.finish(outcome)
    with pytest.raises(ValueError, match="terminal"):
        journal.transition(AttemptStage.CANDIDATE_FINALIZE)
    assert retry_advice(OutcomeKind.EVALUATOR_FAILED, "EVALUATOR_TIMEOUT", 1).delay_seconds == 5
    assert retry_advice(OutcomeKind.INFRASTRUCTURE_ERROR, "LEASE_EXPIRED", 2).delay_seconds == 30


def test_core_matrix_has_twelve_jobs_and_120_executions_and_recovers_lease(tmp_path: Path) -> None:
    plan = create_plan(
        registry=SiteRegistry.default(ROOT),
        site_ids=["foundry-wholesale", "ember-drop", "harbor-pickup"],
        models=["gpt-5.5-codex"],
        thinking_levels=["xhigh", "high", "medium", "low"],
        tracks=["core"],
        repetitions=1,
        concurrency=2,
    )
    assert len(plan.jobs) == 12
    assert sum(len(job["executions"]) for job in plan.jobs) == 120
    assert [job["thinking_level"] for job in plan.jobs[:4]] == ["xhigh", "high", "medium", "low"]
    ledger = BatchLedger(tmp_path / "batch.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ledger.install_plan(plan, now=now)
    first = ledger.claim(plan.digest, owner="worker-1", lease_seconds=1, now=now)
    second = ledger.claim(plan.digest, owner="worker-2", lease_seconds=1, now=now)
    assert first and second and first.job_id != second.job_id
    assert ledger.claim(plan.digest, owner="worker-3", now=now) is None
    assert ledger.recover_expired(plan.digest, now=now + timedelta(seconds=2)) == 2
    summary = ledger.summary(plan.digest)
    assert summary["jobs"] == 12
    assert summary["journey_seed_executions"] == 120
    assert summary["attempts"] == 2
    assert summary["scheduler_counts"]["retry_wait"] == 2
    retried = ledger.claim(
        plan.digest,
        owner="worker-4",
        lease_seconds=1,
        now=now + timedelta(seconds=8),
    )
    assert retried and retried.attempt_number == 2
    assert ledger.recover_expired(plan.digest, now=now + timedelta(seconds=10)) == 1
    summary = ledger.summary(plan.digest)
    assert summary["attempts"] == 3
    assert summary["retries"] == 1
    assert summary["timings"]["completed_attempts"] == 3
    exhausted = ledger.claim(
        plan.digest,
        owner="worker-5",
        lease_seconds=1,
        now=now + timedelta(seconds=41),
    )
    assert exhausted and exhausted.attempt_number == 3
    assert ledger.recover_expired(plan.digest, now=now + timedelta(seconds=43)) == 1
    summary = ledger.summary(plan.digest)
    assert summary["scheduler_counts"]["terminal"] == 1
    assert [
        row["attempt_number"]
        for row in summary["attempt_history"]
        if row["job_id"] == exhausted.job_id
    ] == [1, 2, 3]


def test_batch_rejects_missing_or_ambiguous_registry_selectors() -> None:
    arguments = {
        "registry": SiteRegistry.default(ROOT),
        "models": ["gpt-5.5-codex"],
        "thinking_levels": ["low"],
        "tracks": ["core"],
        "repetitions": 1,
    }
    with pytest.raises(ValueError, match="exactly one"):
        create_plan(**arguments)
    with pytest.raises(ValueError, match="exactly one"):
        create_plan(
            **arguments,
            site_ids=["foundry-wholesale"],
            family_id="white-label-commerce-v1",
        )


def test_batch_refuses_to_resume_after_frozen_source_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = SiteRegistry.default(ROOT)
    plan = create_plan(
        registry=registry,
        site_ids=["foundry-wholesale"],
        models=["gpt-5.5-codex"],
        thinking_levels=["low"],
        tracks=["core"],
        repetitions=1,
    )
    verify_frozen_inputs(plan, registry)
    monkeypatch.setattr("clawbench.web2code.batch._tree_digest", lambda _root: "0" * 64)
    with pytest.raises(ValueError, match="source_tree_sha256"):
        verify_frozen_inputs(plan, registry)


def test_batch_worker_renews_long_running_lease(tmp_path: Path) -> None:
    plan = create_plan(
        registry=SiteRegistry.default(ROOT),
        site_ids=["foundry-wholesale"],
        models=["gpt-5.5-codex"],
        thinking_levels=["low"],
        tracks=["core"],
        repetitions=1,
    )
    ledger = BatchLedger(tmp_path / "batch.sqlite3")
    ledger.install_plan(plan)
    renewed: list[bool] = []

    def runner(claim):
        time.sleep(0.16)
        with ledger.connect() as connection:
            expires = connection.execute(
                "SELECT lease_expires_at FROM jobs WHERE job_id=?",
                (claim.job_id,),
            ).fetchone()["lease_expires_at"]
        renewed.append(expires > claim.lease_expires_at)
        return classify_failure(
            stage=AttemptStage.AGENT,
            reason_code="AGENT_FAILED",
            message="expected test terminal outcome",
            attempt_number=claim.attempt_number,
        )

    summary = run_workers(
        ledger,
        plan.digest,
        runner=runner,
        lease_seconds=1,
        renewal_interval_seconds=0.03,
    )
    assert renewed == [True]
    assert summary["scheduler_counts"]["terminal"] == 1


def test_batch_exact_metric_counts_candidate_failure_as_ten_zeroes(tmp_path: Path) -> None:
    plan = create_plan(
        registry=SiteRegistry.default(ROOT),
        site_ids=["foundry-wholesale"],
        models=["gpt-5.5-codex"],
        thinking_levels=["xhigh", "low"],
        tracks=["core"],
        repetitions=1,
    )
    ledger = BatchLedger(tmp_path / "batch.sqlite3")
    ledger.install_plan(plan)

    failed = ledger.claim(plan.digest, owner="failure-worker")
    assert failed and failed.configuration["thinking_level"] == "xhigh"
    ledger.finish(
        failed,
        classify_failure(
            stage=AttemptStage.AGENT,
            reason_code="AGENT_FAILED",
            message="candidate did not build",
            attempt_number=1,
        ),
    )

    passed = ledger.claim(plan.digest, owner="score-worker")
    assert passed and passed.configuration["thinking_level"] == "low"
    job = next(item for item in plan.jobs if item["job_id"] == passed.job_id)
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "score": 95,
                "journeys": [
                    {
                        "id": execution["journey_id"],
                        "seed": execution["seed"],
                        "terminal_passed": True,
                        "checkpoints": [{"id": "mandatory", "passed": True}],
                    }
                    for execution in job["executions"]
                ],
            }
        ),
        encoding="utf-8",
    )
    ledger.finish(
        passed,
        AttemptOutcome(
            kind=OutcomeKind.SCORED,
            reason_code="RESULT_FINALIZED",
            stage=AttemptStage.FINALIZED,
            message="scored",
            retry=retry_advice(OutcomeKind.SCORED, "RESULT_FINALIZED", 1),
            result_ref=str(result_path),
            facts_valid=True,
        ),
    )

    exact = ledger.summary(plan.digest)["exact_journey_seed"]
    assert (exact["passed"], exact["total"], exact["planned_total"]) == (10, 20, 20)
    assert exact["candidate_failure_zeroes"] == 10
    assert exact["by_thinking_level"]["xhigh"]["pass_rate"] == 0
    assert exact["by_thinking_level"]["low"]["pass_rate"] == 1
    assert exact["by_visibility"]["public"] == {"passed": 5, "total": 10, "pass_rate": 0.5}
    assert exact["decision_metrics"] == {
        "analysis_complete": True,
        "xhigh_pass_rate": 0.0,
        "effort_spread_percentage_points": 100.0,
        "public_hidden_gap_percentage_points": 0.0,
        "all_zero_sites": [],
        "all_perfect_sites": [],
    }


def test_calibration_contract_has_no_official_score(tmp_path: Path) -> None:
    value = {
        "schema_version": "websitebench.calibration-result.v1",
        "calibration_id": "amazon-xhigh-1",
        "benchmark_id": "amazon-136",
        "model": "gpt-5.5-codex",
        "reasoning_effort": "xhigh",
        "track": "core",
        "time_limit_seconds": 1200,
        "status": "PASS",
        "mandatory_task_passed": True,
        "steps": {"passed": 8, "total": 8, "pass_rate": 1.0},
        "usage": {"input_tokens": 10, "output_tokens": 20, "browser_actions": 12},
        "elapsed_seconds": 42.5,
        "harness_error": None,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:42Z",
    }
    validate_calibration(value, SCHEMAS / "calibration-result.schema.json")
    value["score"] = 100
    with pytest.raises(ValueError):
        validate_calibration(value, SCHEMAS / "calibration-result.schema.json")
