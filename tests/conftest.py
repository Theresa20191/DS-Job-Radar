"""Shared test fixtures."""

import hashlib
import shutil
from pathlib import Path

import pytest

from src.career_coach import run_lock
from src.career_coach.operational import enabled_sources

ROOT = Path(__file__).resolve().parent.parent
PRODUCTION_LOCK = ROOT / "data" / run_lock.LOCK_NAME


def _lock_state():
    return (PRODUCTION_LOCK.stat().st_mtime_ns, PRODUCTION_LOCK.read_bytes()) if PRODUCTION_LOCK.exists() else None


@pytest.fixture(scope="session", autouse=True)
def production_lock_untouched():
    """No test may create, take or rewrite the production data's pipeline lock."""
    before = _lock_state()
    yield
    assert _lock_state() == before, "a test touched the production pipeline lock data/.pipeline.lock"


@pytest.fixture(autouse=True)
def isolated_lock_root(tmp_path_factory, monkeypatch):
    """Every test's scripts lock a scratch data root (inherited by subprocesses),
    never the production data folder; locks left held are released afterwards."""
    root = tmp_path_factory.mktemp("isolated_data")
    monkeypatch.setenv(run_lock.ENV_DATA_ROOT, str(root))
    monkeypatch.delenv(run_lock.ENV_FD, raising=False)
    yield root
    for lock in list(run_lock._HELD.values()):
        lock.depth = 1
        run_lock.release(lock)

# The verified Gate 2 / Gate 3 raw corpus: the 24 Aug 2026 scrape. Regression
# tests that assert its fixed results never read data/raw/<source>_latest.jsonl,
# which every production scrape replaces; they read that scrape's timestamped
# snapshots, which the scraper never rewrites.
VERIFIED_RAW = "9c75bb6dc7c294d17cf823e3df61f72dedf34c4cd7b5df3cf26e3ea8818c170e"
VERIFIED_SNAPSHOT = "20260824T115344Z"


@pytest.fixture
def verified_corpus_root(tmp_path) -> Path:
    """TEST FIXTURE: a scratch repository root whose data/raw/<source>_latest.jsonl
    are copies of the verified snapshots (production raw data is only read).
    Skips when the snapshots are not present; a snapshot that is present but no
    longer hashes to the verified corpus is a failure, not a skip."""
    snapshots = {s: ROOT / "data" / "raw" / f"{s}_{VERIFIED_SNAPSHOT}.jsonl" for s in enabled_sources()}
    if not all(path.exists() for path in snapshots.values()):
        pytest.skip(f"verified raw snapshots ({VERIFIED_SNAPSHOT}) not present")
    digest = hashlib.sha256()
    for source, path in snapshots.items():                  # the pipeline's raw-corpus hash, same order
        digest.update(source.encode() + b"\0" + path.read_bytes())
    assert digest.hexdigest() == VERIFIED_RAW, "a verified raw snapshot was rewritten"
    root = tmp_path / "verified_root"
    (root / "data" / "raw").mkdir(parents=True)
    for source, path in snapshots.items():
        shutil.copyfile(path, root / "data" / "raw" / f"{source}_latest.jsonl")
    return root
