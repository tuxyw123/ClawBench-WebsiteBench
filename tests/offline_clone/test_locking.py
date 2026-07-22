from __future__ import annotations

import os
from pathlib import Path

import pytest

from clawbench.offline_clone.locking import LockError, exclusive_file_lock


def test_lock_file_must_not_be_a_hardlink(tmp_path: Path) -> None:
    protected = tmp_path / "protected.bin"
    protected.write_bytes(b"")
    lock = tmp_path / "mutation.lock"
    os.link(protected, lock)

    with pytest.raises(LockError, match="one hard link"):
        with exclusive_file_lock(lock):
            pass
    assert protected.read_bytes() == b""


def test_lock_file_must_not_follow_a_symlink_or_reparse_point(
    tmp_path: Path,
) -> None:
    protected = tmp_path / "protected.bin"
    protected.write_bytes(b"")
    lock = tmp_path / "mutation.lock"
    try:
        lock.symlink_to(protected)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(LockError, match="link/reparse"):
        with exclusive_file_lock(lock):
            pass
    assert protected.read_bytes() == b""


def test_lock_file_must_be_regular(tmp_path: Path) -> None:
    lock = tmp_path / "mutation.lock"
    lock.mkdir()
    with pytest.raises(LockError, match="safely open|regular file"):
        with exclusive_file_lock(lock):
            pass
