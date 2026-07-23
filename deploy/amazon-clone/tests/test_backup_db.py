from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CLONE_ROOT = REPO_ROOT / "materials" / "amazon" / "clone"
if str(CLONE_ROOT) not in sys.path:
    sys.path.insert(0, str(CLONE_ROOT))

from backup_db import backup_database  # noqa: E402


class BackupDatabaseTests(unittest.TestCase):
    def test_backup_is_consistent_and_does_not_modify_the_live_database(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "live.sqlite3"
            destination = root / "backups" / "snapshot.sqlite3"
            with closing(sqlite3.connect(source)) as live:
                live.execute("PRAGMA journal_mode=WAL")
                live.execute("CREATE TABLE records (value TEXT NOT NULL)")
                live.executemany(
                    "INSERT INTO records (value) VALUES (?)",
                    [("first",), ("second",)],
                )
                live.commit()
                result = backup_database(source, destination)

            self.assertEqual(result["integrity"], "ok")
            self.assertEqual(result["bytes"], destination.stat().st_size)
            self.assertEqual(len(str(result["sha256"])), 64)
            with closing(sqlite3.connect(destination)) as backup:
                rows = backup.execute(
                    "SELECT value FROM records ORDER BY rowid"
                ).fetchall()
            self.assertEqual(rows, [("first",), ("second",)])
            with closing(sqlite3.connect(source)) as live:
                self.assertEqual(
                    live.execute("SELECT COUNT(*) FROM records").fetchone(),
                    (2,),
                )

    def test_existing_destination_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "live.sqlite3"
            destination = root / "snapshot.sqlite3"
            with closing(sqlite3.connect(source)) as live:
                live.execute("CREATE TABLE records (value TEXT NOT NULL)")
                live.commit()
            destination.write_bytes(b"keep-me")

            with self.assertRaises(FileExistsError):
                backup_database(source, destination)

            self.assertEqual(destination.read_bytes(), b"keep-me")


if __name__ == "__main__":
    unittest.main()
