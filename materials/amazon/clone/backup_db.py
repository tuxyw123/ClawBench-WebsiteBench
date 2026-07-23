from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "runtime" / "amazon.sqlite3"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_database(source: Path, destination: Path) -> dict[str, object]:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"database does not exist: {source}")
    if source == destination:
        raise ValueError("backup destination must differ from the live database")
    if destination.exists():
        raise FileExistsError(f"backup already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        source_uri = source.as_uri() + "?mode=ro"
        with (
            closing(sqlite3.connect(source_uri, uri=True)) as live,
            closing(sqlite3.connect(destination)) as backup,
        ):
            created = True
            live.backup(backup)
            backup.commit()
            integrity = backup.execute("PRAGMA integrity_check").fetchone()
            if integrity != ("ok",):
                raise RuntimeError(f"backup integrity check failed: {integrity!r}")
    except Exception:
        if created:
            for candidate in (
                destination,
                Path(str(destination) + "-wal"),
                Path(str(destination) + "-shm"),
            ):
                if candidate.is_file():
                    candidate.unlink()
        raise

    return {
        "schema": "amazon-clone.sqlite-backup.v1",
        "source": str(source),
        "destination": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": file_sha256(destination),
        "integrity": "ok",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an application-consistent backup of the Amazon clone SQLite database"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(os.environ.get("AMAZON_DB_PATH", str(DEFAULT_DB))),
    )
    parser.add_argument("--destination", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    destination = args.destination
    if destination is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        destination = (
            args.source.expanduser().resolve().parent
            / "backups"
            / f"amazon-{timestamp}.sqlite3"
        )
    print(json.dumps(backup_database(args.source, destination), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
