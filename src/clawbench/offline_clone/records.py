"""Tamper-evident trajectory records with secret rejection and crash recovery.

Each append is a small write-ahead transaction: fsync an exact pending intent,
append and fsync its JSONL record, atomically replace the state anchor, then
remove the intent.  The next verify, record, or gate operation automatically
recovers an interrupted transaction.  Recovery only accepts the anchored prior
prefix and the exact intended tail (including a partial prefix of that tail),
so it cannot silently bless deletion or unrelated bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .locking import site_mutation_lock
from .manifest import LoadedManifest, utc_now
from .state import load_state, write_state


RECORD_SCHEMA_VERSION = "offline-clone.trajectory-record.v1"
PENDING_SCHEMA_VERSION = "offline-clone.trajectory-pending.v1"
RECORD_KINDS = {
    "action",
    "correction",
    "decision",
    "feedback",
    "observation",
    "note",
}
SAFE_REDACTIONS = {
    "***",
    "<redacted>",
    "[redacted]",
    "(redacted)",
    "redacted",
    "omitted",
    "none",
    "null",
}

# JSON quoting places a quote between a field name and ``:``, so the plain
# assignment regular expressions below intentionally are not the only line of
# defence.  Trajectory notes are often emitted by automation as structured
# JSON; scan decoded keys recursively before accepting such a record.
STRUCTURED_SENSITIVE_KEYS = {
    "accesskey": "access_key",
    "accesstoken": "access_token",
    "addressline1": "personal_address",
    "addressline2": "personal_address",
    "apikey": "api_key",
    "authorization": "authorization",
    "billingaddress": "personal_address",
    "cardnumber": "payment_card",
    "clientsecret": "client_secret",
    "cookie": "cookie",
    "cvc": "payment_card",
    "cvv": "payment_card",
    "expiry": "payment_card",
    "formbody": "raw_request_body",
    "onetimecode": "verification_code",
    "otp": "verification_code",
    "pan": "payment_card",
    "passwd": "password",
    "password": "password",
    "postaladdress": "personal_address",
    "postbody": "raw_request_body",
    "privatekey": "private_key",
    "pwd": "password",
    "rawbody": "raw_request_body",
    "rawrequestbody": "raw_request_body",
    "refreshtoken": "refresh_token",
    "requestbody": "raw_request_body",
    "secret": "secret",
    "secretkey": "secret_key",
    "sessionid": "session_id",
    "sessiontoken": "session_token",
    "shippingaddress": "personal_address",
    "smtppassword": "smtp_password",
    "token": "token",
    "verificationcode": "verification_code",
    "verifycode": "verification_code",
}
STRUCTURED_HASH_SUBJECTS = (
    "body",
    "code",
    "otp",
    "password",
    "secret",
    "token",
)

SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|client[_ -]?secret|api[_ -]?key|"
    r"access[_ -]?token|refresh[_ -]?token|authorization|cookie|set-cookie|"
    r"session[_ -]?(?:id|token)|smtp[_ -]?password)\b\s*(?:=|:)\s*"
    r"([^\s,;]+)"
)
SECRET_FLAG = re.compile(
    r"(?i)(?:^|\s)--(?:password|passwd|secret|api-key|access-token|refresh-token|"
    r"authorization|cookie|session-token|smtp-password)(?:=|\s+)([^\s,;]+)"
)
BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}")
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
PROVIDER_KEY = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16})\b")
OTP = re.compile(
    r"(?i)\b(?:otp|one[- ]time code|verification code|verify code)\b\s*(?:=|:|is)?\s*\d{4,8}\b"
)
CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
RAW_BODY = re.compile(
    r"(?i)\b(?:raw[_ -]?(?:request[_ -]?)?|request[_ -]?|post[_ -]?|form[_ -]?)body\b"
    r"\s*(?:=|:)\s*([^\r\n]+)"
)
SECRET_HASH = re.compile(
    r"(?i)\b(?:password|otp|verification[_ -]?code|secret|request[_ -]?body|"
    r"raw[_ -]?body|body_sha256)[^\r\n]{0,48}?(?:sha[-_ ]?256|hash|=|:)\s*"
    r"([a-f0-9]{32,128})\b"
)
EMAIL = re.compile(
    r"(?i)(?<![A-Z0-9.!#$%&'*+/=?^_`{|}~-])([A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@([A-Z0-9.-]+))"
)
PERSONAL_ADDRESS = re.compile(
    r"(?i)\b(?:street|postal[_ -]?address|shipping[_ -]?address|"
    r"billing[_ -]?address|address[_ -]?line[12])\b\s*(?:=|:)\s*([^\r\n]+)"
)
QUOTED_FIELD_ASSIGNMENT = re.compile(
    r'''(?x)["']([^"']{1,80})["']\s*:\s*'''
    r'''("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^,\s}\]]+)'''
)


class RecordError(ValueError):
    pass


def _is_reparse(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


def _open_regular_single_link(
    path: Path,
    flags: int,
    *,
    label: str,
    mode: int = 0o600,
) -> int:
    """Open *path* without redirecting reads or writes through another file."""

    try:
        lexical = path.lstat()
    except FileNotFoundError:
        lexical = None
    if lexical is not None:
        if stat.S_ISLNK(lexical.st_mode) or _is_reparse(lexical):
            raise RecordError(f"{label} must not be a link/reparse point: {path}")
        if not stat.S_ISREG(lexical.st_mode):
            raise RecordError(f"{label} must be a regular file: {path}")
        if getattr(lexical, "st_nlink", 1) != 1:
            raise RecordError(f"{label} must have one hard link: {path}")

    safe_flags = flags | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, safe_flags, mode)
    except OSError as exc:
        raise RecordError(f"cannot safely open {label} {path}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise RecordError(f"{label} must be a regular file: {path}")
        if getattr(opened, "st_nlink", 1) != 1:
            raise RecordError(f"{label} must have one hard link: {path}")
        if _is_reparse(opened):
            raise RecordError(f"{label} must not be a reparse point: {path}")
        after_open = path.lstat()
        if not os.path.samestat(opened, after_open):
            raise RecordError(f"{label} changed while opening: {path}")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    descriptor = _open_regular_single_link(path, os.O_RDONLY, label=label)
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def _strict_json_loads(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_strict_object)


def _record_hash(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "record_sha256"}
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _intent_hash(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "intent_sha256"}
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _luhn(value: str) -> bool:
    digits = [int(character) for character in value if character.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _structured_sensitive_findings(value: Any) -> list[str]:
    findings: list[str] = []
    if isinstance(value, list):
        for item in value:
            findings.extend(_structured_sensitive_findings(item))
        return findings
    if not isinstance(value, dict):
        return findings
    for raw_key, item in value.items():
        key = re.sub(r"[^a-z0-9]", "", str(raw_key).casefold())
        safe_redaction = isinstance(item, str) and item.strip().casefold() in SAFE_REDACTIONS
        finding = STRUCTURED_SENSITIVE_KEYS.get(key)
        if finding and item is not None and not safe_redaction:
            findings.append(finding)
        if (
            key.endswith(("hash", "sha256"))
            and any(subject in key for subject in STRUCTURED_HASH_SUBJECTS)
            and item is not None
            and not safe_redaction
        ):
            findings.append("low_entropy_secret_hash")
        findings.extend(_structured_sensitive_findings(item))
    return findings


def _json_sensitive_findings(message: str) -> list[str]:
    stripped = message.strip()
    if not stripped.startswith(("{", "[")):
        return []
    try:
        value = _strict_json_loads(stripped)
    except (json.JSONDecodeError, ValueError):
        # Duplicate keys and other ambiguous JSON must not be allowed to hide
        # a first value behind last-key-wins parsing.
        return ["ambiguous_json"]
    if not isinstance(value, (dict, list)):
        return []
    return _structured_sensitive_findings(value)


def _sensitive_findings_one(message: str) -> list[str]:
    findings: list[str] = _json_sensitive_findings(message)
    for match in QUOTED_FIELD_ASSIGNMENT.finditer(message):
        key = re.sub(r"[^a-z0-9]", "", match.group(1).casefold())
        raw_value = match.group(2).strip()
        if raw_value.startswith('"'):
            try:
                decoded_value = json.loads(raw_value)
            except json.JSONDecodeError:
                decoded_value = raw_value.strip('"')
        else:
            decoded_value = raw_value.strip("'")
        safe_redaction = (
            isinstance(decoded_value, str)
            and decoded_value.strip().casefold() in SAFE_REDACTIONS
        )
        finding = STRUCTURED_SENSITIVE_KEYS.get(key)
        if finding and not safe_redaction:
            findings.append(finding)
        if (
            key.endswith(("hash", "sha256"))
            and any(subject in key for subject in STRUCTURED_HASH_SUBJECTS)
            and not safe_redaction
        ):
            findings.append("low_entropy_secret_hash")
    for match in SECRET_ASSIGNMENT.finditer(message):
        value = match.group(2).strip("\"'").casefold()
        if value not in SAFE_REDACTIONS:
            findings.append(match.group(1).casefold().replace(" ", "_"))
    for match in SECRET_FLAG.finditer(message):
        value = match.group(1).strip("\"'").casefold()
        if value not in SAFE_REDACTIONS:
            findings.append("credential_flag")
    if "-----begin " in message.casefold() and "private key-----" in message.casefold():
        findings.append("private_key")
    if BEARER.search(message):
        findings.append("bearer_token")
    if JWT.search(message):
        findings.append("jwt")
    if PROVIDER_KEY.search(message):
        findings.append("provider_key")
    if OTP.search(message):
        findings.append("verification_code")
    for match in RAW_BODY.finditer(message):
        if match.group(1).strip(" \"'").casefold() not in SAFE_REDACTIONS:
            findings.append("raw_request_body")
    if SECRET_HASH.search(message):
        findings.append("low_entropy_secret_hash")
    for match in EMAIL.finditer(message):
        domain = match.group(2).rstrip(".").casefold()
        if not (
            domain == "localhost"
            or domain.endswith((".test", ".invalid"))
            or domain in {"example.com", "example.org", "example.net"}
            or domain.endswith((".example.com", ".example.org", ".example.net"))
        ):
            findings.append("email_address")
    for match in PERSONAL_ADDRESS.finditer(message):
        if match.group(1).strip(" \"'").casefold() not in SAFE_REDACTIONS:
            findings.append("personal_address")
    if any(_luhn(match.group()) for match in CARD_CANDIDATE.finditer(message)):
        findings.append("payment_card")
    return sorted(set(findings))


def sensitive_findings(message: str) -> list[str]:
    """Scan literal and bounded recursively percent-decoded representations."""

    findings: set[str] = set()
    layer = message
    for _ in range(5):
        findings.update(_sensitive_findings_one(layer))
        decoded = unquote(layer)
        if decoded == layer:
            return sorted(findings)
        layer = decoded
    # Scan the representation produced by the final permitted decode. If it
    # still changes, fail closed: otherwise an attacker can always add one more
    # encoding layer than the bounded scanner.
    findings.update(_sensitive_findings_one(layer))
    if unquote(layer) != layer:
        findings.add("excessive_percent_encoding")
    return sorted(findings)


def _verify_trajectory_payload(payload: bytes) -> tuple[int, str | None]:
    if payload and not payload.endswith(b"\n"):
        raise RecordError("trajectory has an unterminated final line")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise RecordError(f"trajectory is not valid UTF-8: {exc}") from exc
    previous: str | None = None
    count = 0
    for line_number, line in enumerate(lines, start=1):
        if not line:
            raise RecordError(f"trajectory line {line_number} is blank")
        try:
            value = _strict_json_loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            raise RecordError(
                f"trajectory line {line_number} is invalid JSON: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise RecordError(f"trajectory line {line_number} must be an object")
        if value.get("schema_version") != RECORD_SCHEMA_VERSION:
            raise RecordError(f"trajectory line {line_number} has an invalid schema")
        if value.get("sequence") != line_number:
            raise RecordError(
                f"trajectory line {line_number} has a non-contiguous sequence"
            )
        if value.get("previous_sha256") != previous:
            raise RecordError(f"trajectory line {line_number} breaks the hash chain")
        expected = _record_hash(value)
        if value.get("record_sha256") != expected:
            raise RecordError(f"trajectory line {line_number} has a bad record hash")
        if line.encode("utf-8") != _canonical(value):
            raise RecordError(f"trajectory line {line_number} is not canonical JSON")
        findings = sensitive_findings(str(value.get("message", "")))
        if findings:
            raise RecordError(
                f"trajectory line {line_number} contains sensitive content: {', '.join(findings)}"
            )
        previous = expected
        count += 1
    return count, previous


def verify_trajectory(path: Path) -> tuple[int, str | None]:
    if not path.exists():
        return 0, None
    try:
        payload = _read_regular_bytes(path, label="trajectory")
    except (OSError, RecordError) as exc:
        if isinstance(exc, RecordError):
            raise
        raise RecordError(f"cannot read trajectory: {exc}") from exc
    return _verify_trajectory_payload(payload)


def _pending_path(manifest: LoadedManifest) -> Path:
    trajectory = manifest.trajectory_path
    return trajectory.with_name(f".{trajectory.name}.pending.json")


def _fsync_parent(path: Path) -> None:
    try:
        descriptor = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_pending(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_parent(path)
    finally:
        temporary.unlink(missing_ok=True)


def _clear_pending(path: Path) -> None:
    path.unlink(missing_ok=True)
    _fsync_parent(path)


def _append_line(path: Path, payload: bytes) -> None:
    descriptor = _open_regular_single_link(
        path,
        os.O_APPEND | os.O_CREAT | os.O_WRONLY,
        label="trajectory",
    )
    try:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:  # pragma: no cover - defensive OS contract check
                raise OSError("could not append trajectory record")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _truncate(path: Path, size: int) -> None:
    descriptor = _open_regular_single_link(
        path, os.O_WRONLY, label="trajectory"
    )
    try:
        os.ftruncate(descriptor, size)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_pending(path: Path, manifest: LoadedManifest) -> dict[str, Any]:
    try:
        payload = _read_regular_bytes(path, label="trajectory pending intent")
        value = _strict_json_loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, RecordError):
            raise
        raise RecordError(f"invalid trajectory pending intent: {exc}") from exc
    if not isinstance(value, dict):
        raise RecordError("invalid trajectory pending intent: root must be an object")
    if value.get("schema_version") != PENDING_SCHEMA_VERSION:
        raise RecordError("invalid trajectory pending intent schema")
    if value.get("site_id") != manifest.data["site_id"]:
        raise RecordError("trajectory pending intent belongs to another site")
    if value.get("intent_sha256") != _intent_hash(value):
        raise RecordError("trajectory pending intent has a bad hash")
    previous_count = value.get("previous_count")
    trajectory_size = value.get("trajectory_size")
    record = value.get("record")
    if not isinstance(previous_count, int) or previous_count < 0:
        raise RecordError("trajectory pending intent has an invalid previous_count")
    if not isinstance(trajectory_size, int) or trajectory_size < 0:
        raise RecordError("trajectory pending intent has an invalid trajectory_size")
    if not isinstance(record, dict):
        raise RecordError("trajectory pending intent has an invalid record")
    if record.get("schema_version") != RECORD_SCHEMA_VERSION:
        raise RecordError("trajectory pending intent record has an invalid schema")
    if record.get("sequence") != previous_count + 1:
        raise RecordError("trajectory pending intent record has a bad sequence")
    if record.get("previous_sha256") != value.get("previous_head_sha256"):
        raise RecordError("trajectory pending intent record breaks the prior anchor")
    if record.get("record_sha256") != _record_hash(record):
        raise RecordError("trajectory pending intent record has a bad hash")
    findings = sensitive_findings(str(record.get("message", "")))
    if findings:
        raise RecordError(
            "trajectory pending intent contains sensitive content: "
            + ", ".join(findings)
        )
    return value


def _recover_pending_unlocked(manifest: LoadedManifest, state: dict[str, Any]) -> None:
    """Finish a durable record intent. Caller must hold ``site_mutation_lock``.

    The intent is fsynced before the JSONL append.  Recovery accepts only the
    exact prior prefix plus zero, a prefix of, or all of the intended line.  A
    partial line is truncated to the verified prefix and rewritten.  Any other
    bytes or state anchor are treated as tampering instead of guessed at.
    """

    pending_path = _pending_path(manifest)
    if not pending_path.exists():
        return
    intent = _load_pending(pending_path, manifest)
    record = intent["record"]
    previous = (
        intent["previous_count"],
        intent.get("previous_head_sha256"),
    )
    current = (record["sequence"], record["record_sha256"])
    anchor = state.get("trajectory", {})
    state_anchor = (anchor.get("count", 0), anchor.get("head_sha256"))
    if state_anchor not in {previous, current}:
        raise RecordError(
            "trajectory pending intent conflicts with the state anchor "
            f"(state={state_anchor[0]}/{state_anchor[1]})"
        )

    path = manifest.trajectory_path
    try:
        payload = (
            _read_regular_bytes(path, label="trajectory") if path.exists() else b""
        )
    except (OSError, RecordError) as exc:
        if isinstance(exc, RecordError):
            raise
        raise RecordError(f"cannot read trajectory during recovery: {exc}") from exc
    prefix_size = intent["trajectory_size"]
    if len(payload) < prefix_size:
        raise RecordError(
            "trajectory tail was deleted after the pending intent was written"
        )
    prefix = payload[:prefix_size]
    if _verify_trajectory_payload(prefix) != previous:
        raise RecordError("trajectory prefix does not match the pending intent anchor")

    intended_line = _canonical(record) + b"\n"
    suffix = payload[prefix_size:]
    if suffix == intended_line:
        pass
    elif intended_line.startswith(suffix):
        if suffix:
            _truncate(path, prefix_size)
        _append_line(path, intended_line)
    else:
        raise RecordError("trajectory bytes conflict with the pending intent")

    if verify_trajectory(path) != current:
        raise RecordError("trajectory recovery did not produce the intended anchor")
    if state_anchor != current:
        state["trajectory"] = {
            "count": record["sequence"],
            "head_sha256": record["record_sha256"],
        }
        write_state(manifest, state)
    _clear_pending(pending_path)


def verify_trajectory_anchor_unlocked(
    manifest: LoadedManifest, state: dict[str, Any]
) -> tuple[int, str | None]:
    """Recover an intent and verify its anchor while the mutation lock is held."""

    _recover_pending_unlocked(manifest, state)
    count, head = verify_trajectory(manifest.trajectory_path)
    anchor = state.get("trajectory", {})
    expected_count = anchor.get("count", 0)
    expected_head = anchor.get("head_sha256")
    if (expected_count, expected_head) != (count, head):
        raise RecordError(
            "trajectory does not match the state anchor "
            f"(state={expected_count}/{expected_head}, file={count}/{head})"
        )
    return count, head


def append_record(
    manifest: LoadedManifest, *, kind: str, message: str
) -> dict[str, Any]:
    if kind not in RECORD_KINDS:
        raise RecordError(f"unsupported record kind: {kind}")
    if not message.strip() or len(message) > 4000:
        raise RecordError("record message must contain 1 to 4000 characters")
    findings = sensitive_findings(message)
    if findings:
        raise RecordError(
            "record rejected because it contains sensitive content: "
            + ", ".join(findings)
        )
    with site_mutation_lock(manifest.state_path):
        state = load_state(manifest)
        count, previous = verify_trajectory_anchor_unlocked(manifest, state)
        path = manifest.trajectory_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            descriptor = _open_regular_single_link(
                path, os.O_RDONLY, label="trajectory"
            )
            try:
                trajectory_size = os.fstat(descriptor).st_size
            finally:
                os.close(descriptor)
        else:
            trajectory_size = 0
        record: dict[str, Any] = {
            "schema_version": RECORD_SCHEMA_VERSION,
            "sequence": count + 1,
            "recorded_at": utc_now(),
            "kind": kind,
            "message": message.strip(),
            "manifest_sha256": manifest.sha256,
            "previous_sha256": previous,
        }
        record["record_sha256"] = _record_hash(record)
        intent: dict[str, Any] = {
            "schema_version": PENDING_SCHEMA_VERSION,
            "site_id": manifest.data["site_id"],
            "previous_count": count,
            "previous_head_sha256": previous,
            "trajectory_size": trajectory_size,
            "record": record,
        }
        intent["intent_sha256"] = _intent_hash(intent)
        pending_path = _pending_path(manifest)
        _write_pending(pending_path, intent)
        # From this point the durable intent is authoritative.  If any step
        # raises or the process dies, the next verify/record/gate operation
        # deterministically completes this exact record before doing new work.
        _append_line(path, _canonical(record) + b"\n")
        state["trajectory"] = {
            "count": record["sequence"],
            "head_sha256": record["record_sha256"],
        }
        write_state(manifest, state)
        _clear_pending(pending_path)
        return record


def verify_trajectory_anchor(
    manifest: LoadedManifest, state: dict[str, Any]
) -> tuple[int, str | None]:
    # Public readers also participate in recovery so a crash between the JSONL
    # append and state-anchor replace never requires a manual repair command.
    with site_mutation_lock(manifest.state_path):
        current_state = load_state(manifest)
        result = verify_trajectory_anchor_unlocked(manifest, current_state)
        state.clear()
        state.update(current_state)
        return result
