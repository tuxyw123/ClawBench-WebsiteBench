"""Small cross-process locks for harness state mutations.

The harness deliberately keeps command execution outside this lock.  Only the
read/check/write transaction that mutates state or the trajectory is locked;
gate completion uses an attempt id as a compare-and-swap token.  This avoids
holding a lock for minutes while still preventing lost updates.
"""

from __future__ import annotations

import os
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class LockError(TimeoutError):
    """Raised when another harness process holds the mutation lock too long."""


_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve()))
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


def _open_lock_stream(path: Path) -> object:
    """Open a single-link regular lock file without following a redirect."""

    if _is_link_or_reparse(path):
        raise LockError(f"harness mutation lock must not be a link/reparse point: {path}")
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise LockError(f"cannot safely open harness mutation lock {path}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        lexical = path.lstat()
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if not stat.S_ISREG(opened.st_mode):
            raise LockError(f"harness mutation lock is not a regular file: {path}")
        if getattr(opened, "st_nlink", 1) != 1:
            raise LockError(f"harness mutation lock must have one hard link: {path}")
        if getattr(opened, "st_file_attributes", 0) & reparse_flag:
            raise LockError(f"harness mutation lock is a reparse point: {path}")
        if not os.path.samestat(opened, lexical):
            raise LockError(f"harness mutation lock changed while opening: {path}")
        return os.fdopen(descriptor, "r+b", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


def _try_lock(stream: object) -> bool:
    if os.name == "nt":  # pragma: win32
        import msvcrt

        try:
            stream.seek(0)  # type: ignore[attr-defined]
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        except OSError:
            return False
        return True

    import fcntl  # pragma: posix

    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
    except BlockingIOError:
        return False
    return True


def _unlock(stream: object) -> None:
    if os.name == "nt":  # pragma: win32
        import msvcrt

        stream.seek(0)  # type: ignore[attr-defined]
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        return

    import fcntl  # pragma: posix

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


@contextmanager
def exclusive_file_lock(path: Path, *, timeout_seconds: float = 30.0) -> Iterator[None]:
    """Hold one byte of *path* exclusively on Windows or POSIX.

    A process-local mutex complements the OS lock because byte-lock semantics
    between threads in one process differ by platform.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    local_lock = _thread_lock(path)
    if not local_lock.acquire(timeout=timeout_seconds):
        raise LockError(f"timed out waiting for harness mutation lock: {path}")
    stream = None
    locked = False
    try:
        stream = _open_lock_stream(path)
        if os.fstat(stream.fileno()).st_size == 0:  # type: ignore[attr-defined]
            stream.seek(0)  # type: ignore[attr-defined]
            stream.write(b"\0")
            stream.flush()
            os.fsync(stream.fileno())
        deadline = time.monotonic() + timeout_seconds
        while not _try_lock(stream):
            if time.monotonic() >= deadline:
                raise LockError(f"timed out waiting for harness mutation lock: {path}")
            time.sleep(0.025)
        locked = True
        yield
    finally:
        if locked and stream is not None:
            _unlock(stream)
        if stream is not None:
            stream.close()
        local_lock.release()


@contextmanager
def site_mutation_lock(state_path: Path) -> Iterator[None]:
    """Serialize state/trajectory transactions for one clone site."""

    lock_path = state_path.parent / ".mutation.lock"
    with exclusive_file_lock(lock_path):
        yield
