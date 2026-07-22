from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

import clawbench.offline_clone.records as record_module
from clawbench.offline_clone.manifest import load_manifest
from clawbench.offline_clone.records import (
    RecordError,
    append_record,
    sensitive_findings,
    verify_trajectory,
    verify_trajectory_anchor,
)
from clawbench.offline_clone.state import load_state

from .helpers import initialized_site


def _percent_encode(value: str, layers: int) -> str:
    for _ in range(layers):
        value = quote(value, safe="")
    return value


def test_trajectory_is_an_append_only_hash_chain(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    manifest = load_manifest(root)
    first = append_record(
        manifest, kind="feedback", message="Prioritize the primary user journey."
    )
    second = append_record(
        manifest, kind="correction", message="Model variant identity explicitly."
    )
    assert first["previous_sha256"] is None
    assert second["previous_sha256"] == first["record_sha256"]
    assert verify_trajectory(manifest.trajectory_path) == (2, second["record_sha256"])
    assert verify_trajectory_anchor(manifest, load_state(manifest)) == (
        2,
        second["record_sha256"],
    )


@pytest.mark.parametrize(
    "message",
    [
        "password=hunter2",
        "password%3Dhunter2",
        "access_token%253Dhunter2",
        _percent_encode("password=supersecret", 5),
        _percent_encode("password=supersecret", 6),
        "%70%61%73%73%77%6f%72%64%3Dhunter2",
        "password%252525253Dsupersecret",
        '{"password":"hunter2"}',
        '{"password":"hunter2","password":"redacted"}',
        'observed payload {"password":"hunter2"}',
        '{"event":{"credentials":{"otp":"123456"}}}',
        '{"request_body":{"email":"user@example.test","quantity":2}}',
        '{"payment":{"cvv":"123"}}',
        '{"otp_sha256":"' + "a" * 64 + '"}',
        "Authorization: Bearer abcdefghijklmnop",
        "verification code: 123456",
        "card 4111 1111 1111 1111",
        "api_key=sk-abcdefghijklmnopqrstuvwxyz",
        "tool --access-token abcdefghijklmnop",
        "raw request body: email=user@example.test&quantity=2",
        "otp sha256: " + "a" * 64,
        "customer email is person@private-mail.example",
        "shipping_address: 123 Main Street, Springfield",
    ],
)
def test_sensitive_record_is_rejected_before_body_or_hash_is_written(
    tmp_path: Path, message: str
) -> None:
    root = initialized_site(tmp_path)
    manifest = load_manifest(root)
    secret_hash = hashlib.sha256(message.encode()).hexdigest()
    with pytest.raises(RecordError, match="sensitive"):
        append_record(manifest, kind="note", message=message)
    contents = (
        manifest.trajectory_path.read_text(encoding="utf-8")
        if manifest.trajectory_path.exists()
        else ""
    )
    assert message not in contents
    assert secret_hash not in contents
    assert contents == ""


def test_safe_security_retrospective_is_not_a_false_positive(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    manifest = load_manifest(root)
    record = append_record(
        manifest,
        kind="decision",
        message="Password reset must not expose account existence; tokens are redacted.",
    )
    assert record["sequence"] == 1


def test_percent_decode_depth_scans_last_layer_and_fails_closed_beyond_it() -> None:
    assert "password" in sensitive_findings(
        _percent_encode("password=supersecret", 5)
    )
    assert "excessive_percent_encoding" in sensitive_findings(
        _percent_encode("password=supersecret", 6)
    )


def test_explicitly_redacted_structured_security_note_is_allowed(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    manifest = load_manifest(root)
    record = append_record(
        manifest,
        kind="decision",
        message='{"password":"<redacted>","otp":"omitted","token":"none"}',
    )
    assert record["sequence"] == 1


def test_trajectory_tampering_is_detected(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    manifest = load_manifest(root)
    append_record(manifest, kind="note", message="Original safe note")
    row = json.loads(manifest.trajectory_path.read_text(encoding="utf-8"))
    row["message"] = "Rewritten note"
    manifest.trajectory_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(RecordError, match="bad record hash"):
        verify_trajectory(manifest.trajectory_path)


def test_noncanonical_trajectory_rewrite_is_detected_even_when_value_is_unchanged(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    manifest = load_manifest(root)
    append_record(manifest, kind="note", message="Canonical retained note")
    value = json.loads(manifest.trajectory_path.read_text(encoding="utf-8"))
    manifest.trajectory_path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RecordError, match="not canonical JSON"):
        verify_trajectory(manifest.trajectory_path)


def test_duplicate_json_key_cannot_hide_sensitive_raw_trajectory_bytes(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    manifest = load_manifest(root)
    append_record(manifest, kind="note", message="Safe final value")
    line = manifest.trajectory_path.read_text(encoding="utf-8").rstrip("\n")
    tampered = line.replace(
        '\"message\":\"Safe final value\"',
        '\"message\":\"password=hunter2\",\"message\":\"Safe final value\"',
        1,
    )
    assert "password=hunter2" in tampered
    manifest.trajectory_path.write_text(tampered + "\n", encoding="utf-8")
    with pytest.raises(RecordError, match="duplicate JSON object key"):
        verify_trajectory(manifest.trajectory_path)


def test_append_rejects_deleted_tail_against_persisted_anchor(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    manifest = load_manifest(root)
    append_record(manifest, kind="note", message="First retained record")
    append_record(manifest, kind="note", message="Tail that must remain anchored")
    lines = manifest.trajectory_path.read_bytes().splitlines(keepends=True)
    manifest.trajectory_path.write_bytes(lines[0])

    with pytest.raises(RecordError, match="does not match the state anchor"):
        append_record(manifest, kind="note", message="Must not hide the deletion")
    assert len(manifest.trajectory_path.read_bytes().splitlines()) == 1


def test_trajectory_must_not_write_through_a_hardlink(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    manifest = load_manifest(root)
    manifest.trajectory_path.parent.mkdir(parents=True, exist_ok=True)
    protected = tmp_path / "protected.txt"
    protected.write_text("must remain unchanged", encoding="utf-8")
    os.link(protected, manifest.trajectory_path)

    with pytest.raises(RecordError, match="one hard link"):
        append_record(manifest, kind="note", message="Safe note")
    assert protected.read_text(encoding="utf-8") == "must remain unchanged"


def test_pending_intent_recovers_crash_between_append_and_state_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = initialized_site(tmp_path)
    manifest = load_manifest(root)
    original_write_state = record_module.write_state

    def crash_before_anchor(*args: object, **kwargs: object) -> None:
        raise OSError("simulated power loss before state anchor")

    monkeypatch.setattr(record_module, "write_state", crash_before_anchor)
    with pytest.raises(OSError, match="simulated power loss"):
        append_record(manifest, kind="decision", message="Durable pending decision")
    assert verify_trajectory(manifest.trajectory_path)[0] == 1
    assert load_state(manifest)["trajectory"]["count"] == 0

    monkeypatch.setattr(record_module, "write_state", original_write_state)
    stale_state = load_state(manifest)
    assert verify_trajectory_anchor(manifest, stale_state) == (
        1,
        verify_trajectory(manifest.trajectory_path)[1],
    )
    assert stale_state["trajectory"]["count"] == 1
    assert not list(manifest.trajectory_path.parent.glob("*.pending.json"))


def test_pending_intent_repairs_only_its_exact_partial_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = initialized_site(tmp_path)
    manifest = load_manifest(root)
    original_append_line = record_module._append_line

    def partial_append_then_crash(path: Path, payload: bytes) -> None:
        with path.open("ab") as stream:
            stream.write(payload[: len(payload) // 2])
            stream.flush()
            os.fsync(stream.fileno())
        raise OSError("simulated partial append")

    monkeypatch.setattr(record_module, "_append_line", partial_append_then_crash)
    with pytest.raises(OSError, match="partial append"):
        append_record(
            manifest, kind="observation", message="Recover exact partial line"
        )
    monkeypatch.setattr(record_module, "_append_line", original_append_line)

    state = load_state(manifest)
    count, head = verify_trajectory_anchor(manifest, state)
    assert count == 1
    assert head is not None
    assert verify_trajectory(manifest.trajectory_path) == (count, head)


def test_concurrent_processes_append_without_lost_or_forked_records(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    manifest = load_manifest(root)
    repository = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    source_path = str(repository / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (source_path, environment.get("PYTHONPATH", "")) if item
    )
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "clawbench.offline_clone.cli",
                "record",
                "--site",
                str(root),
                "--kind",
                "note",
                "--message",
                f"parallel process record {index}",
            ],
            cwd=repository,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        for index in range(8)
    ]
    completed = [process.communicate(timeout=30) for process in processes]
    failures = [
        (process.returncode, stdout, stderr)
        for process, (stdout, stderr) in zip(processes, completed, strict=True)
        if process.returncode != 0
    ]
    assert not failures
    count, head = verify_trajectory(manifest.trajectory_path)
    assert count == 8
    assert head is not None
    assert verify_trajectory_anchor(manifest, load_state(manifest)) == (count, head)
    sequences = [
        json.loads(line)["sequence"]
        for line in manifest.trajectory_path.read_text(encoding="utf-8").splitlines()
    ]
    assert sequences == list(range(1, 9))
