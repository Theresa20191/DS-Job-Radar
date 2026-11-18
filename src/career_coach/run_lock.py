"""Single-writer lock for the pipeline's protected data (F5).

Every process that writes data run_all reads (raw files and scrape reports,
the live-check log, live-check evidence, the run archive, the operational
output folder) holds an exclusive flock on <data>/.pipeline.lock while it
runs. Acquisition never waits: a competing process stops at once with exit
code 5 (LOCK_EXIT) and is told which process holds the lock. A lock that
cannot be set up at all (the lock file cannot be created or written, or the
configuration is unsafe) is a separate error, exit code 1
(LOCK_SETUP_EXIT), reported before anything else runs.

flock is advisory and belongs to the open file description, so:
  * the kernel releases it when the holder's descriptor is closed, including
    when the process is killed; there is no stale lock to clean up. The file
    holds the holder's pid, start time and command for diagnostics only and
    is never deleted; its content is never used to decide anything. It is
    gitignored, so it never changes the code identity of a run;
  * a child process can share its parent's lock: the parent passes the
    descriptor (pass_fds) and names it in DS_JOB_RADAR_LOCK_FD. The child
    checks that the descriptor is the lock file (device and inode) and that
    it holds the lock (a non-blocking re-lock of a description that already
    holds it succeeds at once; one that does not fails at once). The lock
    then stays held until parent and child have both exited;
  * nothing here ever calls flock(LOCK_UN): an inherited descriptor shares
    its lock with the parent, so an explicit unlock in a child would release
    the parent's lock. Releasing means closing the descriptor.

Lock domains. The production data folder (PRODUCTION_DATA) is the one
authoritative domain. DS_JOB_RADAR_DATA_ROOT, an isolation hook for tests,
may move the scripts' lock to a scratch folder only when that folder is
separate from production data: an override that equals, lies inside or
contains the production data folder is refused, and so is any explicit lock
folder that overlaps it without being it. Two lock files can therefore never
guard the same data.

Guard. Low-level writers of protected data call require_lock(path) before
their first write. It finds the protected domain containing the path and
passes only when this process holds that domain's own lock (re-verified on
every call); a lock held on any other folder, an environment variable or a
descriptor alone never satisfies it. Containment is decided by file identity
(device and inode of the path's existing ancestors, symlinks followed), not
by path text, so case variants on a case-insensitive filesystem are caught.
Paths outside every protected domain (scratch roots, temporary test
folders) need no lock.

Requirements: a local filesystem (flock is not reliable on network
filesystems) and a POSIX platform (fcntl).
"""

import fcntl
import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

PRODUCTION_DATA = Path(__file__).resolve().parent.parent.parent / "data"
LOCK_NAME = ".pipeline.lock"
ENV_FD = "DS_JOB_RADAR_LOCK_FD"
ENV_DATA_ROOT = "DS_JOB_RADAR_DATA_ROOT"
LOCK_EXIT = 5
LOCK_SETUP_EXIT = 1


class LockError(RuntimeError):
    """The pipeline lock could not be obtained; nothing was changed."""


class LockHeld(LockError):
    """Another process holds the lock."""


class LockInvalid(LockError):
    """An inherited lock descriptor was rejected."""


class LockSetupError(LockError):
    """The lock could not be set up: I/O failure or an unsafe configuration."""


class LockNotHeld(LockError):
    """A protected write was attempted without the lock."""


@dataclass
class PipelineLock:
    data_dir: Path
    key: tuple
    fd: int
    inherited: bool
    depth: int = 1


_HELD: dict[tuple, PipelineLock] = {}


# ------------------------------------------------------------------ path identity

def path_identity(path: Path) -> tuple | None:
    """(device, inode) of an existing path, symlinks followed; None if absent."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_dev, st.st_ino


def _absolute(path: Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else Path.cwd() / path        # no lexical '..' normalisation


def _ancestor_identities(path: Path) -> list[tuple]:
    """Identities of the existing part of path and all of its real parents.
    The whole path is resolved first: os.path.realpath follows every symlink
    it meets, including one whose target does not exist yet (a write through
    it would create that target) and chains of links, and keeps missing
    components. Only then are missing components dropped; the OS cannot
    traverse a missing directory, so they cannot lead anywhere else."""
    existing = Path(os.path.realpath(_absolute(path)))
    while not os.path.exists(existing) and existing != existing.parent:
        existing = existing.parent
    return [i for d in (existing, *existing.parents) if (i := path_identity(d)) is not None]


def contains(root: Path, path: Path) -> bool:
    """Is path root itself or inside it, by file identity?"""
    root_id = path_identity(root)
    if root_id is None:                                     # a root that does not exist yet: by real path
        real_root = Path(os.path.realpath(_absolute(root)))
        real_path = Path(os.path.realpath(_absolute(path)))
        return real_path == real_root or real_root in real_path.parents
    return root_id in _ancestor_identities(path)


def same_location(a: Path, b: Path) -> bool:
    return contains(a, b) and contains(b, a)


def case_insensitive_dir(directory: Path) -> bool:
    """Does a name lookup in directory ignore case? Probed on the directory's
    own filesystem (an existing entry looked up with its case swapped), never
    assumed. Undeterminable means case-sensitive (exact names)."""
    directory = _absolute(directory)
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return False
    candidates = [(directory, e) for e in entries]
    real = Path(os.path.realpath(directory))
    candidates.append((real.parent, real.name))
    for parent, name in candidates:
        swapped = name.swapcase()
        if swapped != name:
            variant = parent / swapped
            return os.path.exists(variant) and os.path.samefile(parent / name, variant)
    return False


# --------------------------------------------------------------------- domains

def _check_domain(data_dir: Path) -> None:
    """A lock folder is the production data folder itself, or one that neither
    lies inside nor contains it."""
    if same_location(data_dir, PRODUCTION_DATA):
        return
    if contains(PRODUCTION_DATA, data_dir) or contains(data_dir, PRODUCTION_DATA):
        raise LockSetupError(f"lock folder {data_dir} overlaps the production data folder {PRODUCTION_DATA}; "
                             "the production data has one lock, data/.pipeline.lock")


def data_root() -> Path:
    """The data folder the scripts lock: production data, unless the
    DS_JOB_RADAR_DATA_ROOT isolation hook names a separate scratch folder.
    An override that equals, lies inside or contains production data is
    refused (LockSetupError)."""
    override = os.environ.get(ENV_DATA_ROOT)
    if not override:
        return PRODUCTION_DATA
    root = Path(override)
    if contains(PRODUCTION_DATA, root) or contains(root, PRODUCTION_DATA):
        raise LockSetupError(f"{ENV_DATA_ROOT}={override} overlaps the production data folder {PRODUCTION_DATA}; "
                             "it may only name a separate scratch folder")
    return root


def protected_roots() -> list[Path]:
    """Production data always; a valid scratch override too."""
    roots = [PRODUCTION_DATA]
    try:
        override = data_root()
    except LockSetupError:
        return roots
    if override is not PRODUCTION_DATA:
        roots.append(override)
    return roots


def lock_file(data_dir: Path) -> Path:
    return Path(data_dir) / LOCK_NAME


# ------------------------------------------------------------------ diagnostics

def diagnostics(data_dir: Path) -> str:
    """Who holds (or last held) the lock, from the file's diagnostic content."""
    try:
        info = json.loads(lock_file(data_dir).read_text())
        return f"pid {info['pid']}, started {info['started_utc']}, command {' '.join(info['command'])}"
    except (OSError, ValueError, KeyError, TypeError):
        return "holder diagnostics unreadable"


def _write_diagnostics(fd: int, purpose: str) -> None:
    info = {"pid": os.getpid(), "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),   # diagnostics only
            "command": [os.path.basename(sys.argv[0] or "python"), *sys.argv[1:]], "purpose": purpose}
    os.ftruncate(fd, 0)
    os.pwrite(fd, (json.dumps(info, sort_keys=True) + "\n").encode(), 0)


# ------------------------------------------------------------- acquire / inherit

def acquire(data_dir: Path | None = None, purpose: str = "") -> PipelineLock:
    """Takes the lock for data_dir (default data_root()) without waiting.
    Re-entrant within one process. Raises LockHeld when another process
    holds it, LockSetupError when it cannot be set up; on any failure the
    descriptor this call opened is closed again."""
    data_dir = Path(data_dir) if data_dir is not None else data_root()
    _check_domain(data_dir)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise LockSetupError(f"cannot create the lock folder {data_dir}: {e}") from None
    key = path_identity(data_dir)
    if key in _HELD:
        _HELD[key].depth += 1
        return _HELD[key]
    try:
        fd = os.open(lock_file(data_dir), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as e:
        raise LockSetupError(f"cannot open the lock file {lock_file(data_dir)}: {e}") from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise LockHeld(f"another pipeline process holds {lock_file(data_dir)} ({diagnostics(data_dir)}); "
                       "nothing was changed") from None
    except OSError as e:
        os.close(fd)
        raise LockSetupError(f"cannot lock {lock_file(data_dir)}: {e}") from None
    try:
        _write_diagnostics(fd, purpose)
    except OSError as e:
        os.close(fd)                                        # closing releases the lock just taken
        raise LockSetupError(f"cannot write the lock diagnostics to {lock_file(data_dir)}: {e}") from None
    _HELD[key] = PipelineLock(data_dir, key, fd, inherited=False)
    return _HELD[key]


def _same_file(fd: int, path: Path) -> bool:
    try:
        held, expected = os.fstat(fd), os.stat(path)
    except OSError:
        return False
    return (held.st_dev, held.st_ino) == (expected.st_dev, expected.st_ino)


def inherit(data_dir: Path | None = None) -> PipelineLock:
    """Adopts the lock descriptor named in DS_JOB_RADAR_LOCK_FD. The variable
    is removed so it does not leak further. Raises LockInvalid unless the
    descriptor is open, is data_dir's lock file and holds (or can take) the
    lock without waiting. A rejected descriptor is closed only when it is
    this lock file (closing never releases a lock it does not hold); any
    other descriptor is not ours and is left alone."""
    data_dir = Path(data_dir) if data_dir is not None else data_root()
    _check_domain(data_dir)
    raw = os.environ.pop(ENV_FD)
    try:
        fd = int(raw)
        os.fstat(fd)
    except (ValueError, OSError):
        raise LockInvalid(f"{ENV_FD}={raw!r} is not an open descriptor; refusing to run") from None
    if not _same_file(fd, lock_file(data_dir)):
        raise LockInvalid(f"descriptor {fd} from {ENV_FD} is not {lock_file(data_dir)}; refusing to run")
    key = path_identity(data_dir)
    if key in _HELD:
        raise LockInvalid(f"this process already holds the lock for {data_dir}; refusing a second descriptor")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)                                        # this lock file's descriptor, holding nothing
        raise LockInvalid(f"descriptor {fd} from {ENV_FD} does not hold the lock, which another process holds "
                          f"({diagnostics(data_dir)}); refusing to run") from None
    _HELD[key] = PipelineLock(data_dir, key, fd, inherited=True)
    return _HELD[key]


def acquire_or_inherit(data_dir: Path | None = None, purpose: str = "") -> PipelineLock:
    """inherit() when a parent passed its lock, otherwise acquire()."""
    return inherit(data_dir) if ENV_FD in os.environ else acquire(data_dir, purpose)


def release(lock: PipelineLock) -> None:
    """Closes this process's descriptor (never LOCK_UN). The kernel releases
    the lock once no process holds the description any more."""
    lock.depth -= 1
    if lock.depth > 0:
        return
    _HELD.pop(lock.key, None)
    os.close(lock.fd)


@contextmanager
def held(data_dir: Path | None = None, purpose: str = "", inherit_ok: bool = False):
    lock = acquire_or_inherit(data_dir, purpose) if inherit_ok else acquire(data_dir, purpose)
    try:
        yield lock
    finally:
        release(lock)


def child_process_args(lock: PipelineLock) -> dict:
    """subprocess keyword arguments that pass this lock to a child process."""
    return {"pass_fds": (lock.fd,), "env": {**os.environ, ENV_FD: str(lock.fd)}}


def run_locked(purpose: str, main, data_dir: Path | None = None, inherit_ok: bool = False):
    """Runs main(lock) while holding the lock. Contention or a rejected
    inherited lock ends the process with exit 5, a lock that cannot be set up
    with exit 1; either way before main runs."""
    try:
        lock = acquire_or_inherit(data_dir, purpose) if inherit_ok else acquire(data_dir, purpose)
    except LockSetupError as e:
        print(f"{purpose}: FAILED before doing anything (exit {LOCK_SETUP_EXIT}): pipeline lock setup failed: {e}",
              file=sys.stderr, flush=True)
        sys.exit(LOCK_SETUP_EXIT)
    except LockError as e:
        print(f"{purpose}: REFUSED (exit {LOCK_EXIT}): {e}", file=sys.stderr, flush=True)
        sys.exit(LOCK_EXIT)
    try:
        return main(lock)
    finally:
        release(lock)


# ------------------------------------------------------------------------ guard

def require_lock(path: Path) -> None:
    """Raises LockNotHeld unless writing to path is allowed: path lies outside
    every protected domain, or this process holds the lock of the protected
    domain containing it (re-verified on every call)."""
    for root in protected_roots():
        if not contains(root, path):
            continue
        lock = _HELD.get(path_identity(root))
        if lock is None:
            raise LockNotHeld(f"refusing to write {path}: this process does not hold the pipeline lock for {root}")
        if not _same_file(lock.fd, lock_file(lock.data_dir)):
            raise LockNotHeld(f"the lock descriptor for {lock.data_dir} no longer refers to its lock file")
        try:
            fcntl.flock(lock.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise LockNotHeld(f"this process no longer holds the lock for {lock.data_dir}") from None
