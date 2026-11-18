"""Gate 5 D1: every Career Coach run is archived immutably with its exact
config and profile, keyed by as_of_date + run_kind + input hashes."""

import copy
import hashlib
import importlib.util
import json
import os
import stat
from datetime import date
from pathlib import Path

import pytest

from src.career_coach import run_archive as ra
from src.career_coach.engine import Coach
from src.career_coach.inputs import build_population
from src.config import CONFIG_DIR, load_career_coach, load_profile, load_scoring
from src.models.processed_job import ProcessedJob

ROOT = Path(__file__).resolve().parent.parent
CONFIG_BYTES = (CONFIG_DIR / "career_coach.yaml").read_bytes()
PROFILE_BYTES = (CONFIG_DIR / "profile.yaml").read_bytes()
WORK = "Responsibilities:\nBuild credit risk models for lending decisions.\nDevelop scorecards for credit risk models.\n"


def _job(job_id, **o):
    f = dict(job_id=job_id, company=f"Co {job_id}", company_normalized=f"co {job_id}", job_title="Data Scientist",
             country="Malaysia", city="Kuala Lumpur", location_normalized="Kuala Lumpur, Malaysia",
             role_family="Data Scientist", role_level="Mid/Unspecified", role_type="TRUE_DS", sources=["hiredly"],
             canonical_url=f"https://example.test/{job_id}", description_raw=WORK, salary_min=9000.0, salary_max=12000.0,
             salary_currency="MYR", salary_period="Monthly", salary_unknown=False, job_age_days=5,
             employer_clarity="DIRECT_EMPLOYER", posted_date="2026-09-15")
    f.update(o)
    return ProcessedJob(**f)


def write_run(out: Path, as_of=date(2026, 9, 25), jobs=None, run_kind="OPERATIONAL") -> Path:
    """TEST FIXTURE: a Coach run over synthetic jobs, written like the runner writes it."""
    coach = Coach(copy.deepcopy(load_profile()), load_career_coach(), set())
    decisions, meta = coach.run(build_population(jobs or [_job("job_a"), _job("job_b", role_level="Principal")],
                                                 load_scoring()), as_of, run_kind, [], "raw-test")
    out.mkdir(parents=True, exist_ok=True)
    (out / "decisions.json").write_text(json.dumps([d.model_dump(mode="json") for d in decisions], sort_keys=True))
    (out / "run_meta.json").write_text(json.dumps(meta, sort_keys=True))
    return out


def test_archive_holds_exact_files_read_only(tmp_path):
    run = write_run(tmp_path / "out")
    path = ra.archive_run(run, CONFIG_BYTES, PROFILE_BYTES, tmp_path / "archive")
    meta = ra.verify_archive(path)
    assert sorted(p.name for p in path.iterdir()) == sorted(ra.FILES + ("archive.json",))
    assert (path / "career_coach.yaml").read_bytes() == CONFIG_BYTES and (path / "profile.yaml").read_bytes() == PROFILE_BYTES
    assert (path / "decisions.json").read_bytes() == (run / "decisions.json").read_bytes()
    assert all(not (p.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)) for p in path.iterdir())
    assert (meta["as_of_date"], meta["run_kind"]) == ("2026-09-25", "OPERATIONAL")
    assert path.name == ra.archive_id("2026-09-25", "OPERATIONAL", meta["input_hashes"])


def test_archives_survive_an_overwritten_output_folder(tmp_path):
    out, root = tmp_path / "out", tmp_path / "archive"
    first = ra.archive_run(write_run(out), CONFIG_BYTES, PROFILE_BYTES, root)
    first_bytes = (first / "decisions.json").read_bytes()
    second = ra.archive_run(write_run(out, as_of=date(2026, 10, 1)), CONFIG_BYTES, PROFILE_BYTES, root)
    assert first != second and sorted(p.name for p in root.iterdir()) == sorted([first.name, second.name])
    assert (first / "decisions.json").read_bytes() == first_bytes                   # untouched by the overwrite
    assert (out / "decisions.json").read_bytes() == (second / "decisions.json").read_bytes()


def test_rearchiving_the_same_run_is_a_verified_no_op(tmp_path):
    run, root = write_run(tmp_path / "out"), tmp_path / "archive"
    path = ra.archive_run(run, CONFIG_BYTES, PROFILE_BYTES, root)
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    assert ra.archive_run(run, CONFIG_BYTES, PROFILE_BYTES, root) == path
    assert {p.name: p.read_bytes() for p in path.iterdir()} == before


def test_different_content_under_an_existing_id_is_refused(tmp_path):
    run, root = write_run(tmp_path / "out"), tmp_path / "archive"
    ra.archive_run(run, CONFIG_BYTES, PROFILE_BYTES, root)
    decisions = json.loads((run / "decisions.json").read_text())
    decisions[0]["primary_reason"]["text"] += " (edited)"                            # same run identity, new bytes
    (run / "decisions.json").write_text(json.dumps(decisions, sort_keys=True))
    with pytest.raises(ValueError, match="immutable"):
        ra.archive_run(run, CONFIG_BYTES, PROFILE_BYTES, root)


def test_config_or_profile_that_did_not_produce_the_run_is_refused(tmp_path):
    run = write_run(tmp_path / "out")
    with pytest.raises(ValueError, match="config file does not match"):
        ra.archive_run(run, CONFIG_BYTES.replace(b"heavy_gap_min: 5", b"heavy_gap_min: 6"), PROFILE_BYTES, tmp_path / "a")
    with pytest.raises(ValueError, match="profile file does not match"):
        ra.archive_run(run, CONFIG_BYTES, PROFILE_BYTES.replace(b"relevant_ds_years: 1", b"relevant_ds_years: 3"),
                       tmp_path / "a")


def test_tampered_archive_fails_verification(tmp_path):
    path = ra.archive_run(write_run(tmp_path / "out"), CONFIG_BYTES, PROFILE_BYTES, tmp_path / "archive")
    target = path / "run_meta.json"
    os.chmod(target, 0o644)
    target.write_text("{}")
    with pytest.raises(ValueError, match="does not match its recorded hash"):
        ra.verify_archive(path)


def test_find_archive_matches_only_the_exact_run_identity(tmp_path):
    path = ra.archive_run(write_run(tmp_path / "out"), CONFIG_BYTES, PROFILE_BYTES, tmp_path / "archive")
    meta = json.loads((path / "archive.json").read_text())
    assert ra.find_archive("2026-09-25", "OPERATIONAL", meta["input_hashes"], tmp_path / "archive") == path
    other = {**meta["input_hashes"], "profile": "0" * 64}
    assert ra.find_archive("2026-09-25", "OPERATIONAL", other, tmp_path / "archive") is None
    assert ra.find_archive("2026-09-20", "OPERATIONAL", meta["input_hashes"], tmp_path / "archive") is None


VERIFIED_RAW = "9c75bb6dc7c294d17cf823e3df61f72dedf34c4cd7b5df3cf26e3ea8818c170e"


def test_runner_archives_every_run_and_decisions_stay_byte_identical(tmp_path, monkeypatch, verified_corpus_root):
    """Change detector: the runner archives the 20 Sep historical replay of the
    CURRENT pipeline over the fixed 24 Aug raw snapshots (in a scratch root,
    never the production raw data), and its deterministic decision bytes are
    pinned. Any change to derived output fails here and must be re-pinned
    deliberately. The archived bytes of the original 20 Sep replay are
    protected separately, by the immutable archive checks in
    test_career_coach_dashboard.py."""
    spec = importlib.util.spec_from_file_location("career_coach_shadow", ROOT / "scripts" / "career_coach_shadow.py")
    shadow = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shadow)
    monkeypatch.setattr(shadow, "ROOT", verified_corpus_root)
    out, root = tmp_path / "out", tmp_path / "archive"
    monkeypatch.setattr("sys.argv", ["career_coach_shadow.py", "--as-of", "2026-09-20", "--out", str(out),
                                     "--archive-root", str(root), "--live-checks", str(tmp_path / "no_live.jsonl")])
    shadow.main()
    archives = list(root.iterdir())
    assert len(archives) == 1 and ra.verify_archive(archives[0])["decision_count"] == 118
    decisions = (archives[0] / "decisions.json").read_bytes()
    assert json.loads(decisions)[0]["run"]["input_hashes"]["raw_corpus"] == VERIFIED_RAW
    assert hashlib.sha256(decisions).hexdigest() == "3e01a831219ff71279d1f88212fbc3e6b5e73adff9e3d7674a7fd1fd4f5a0e7e"
    assert (out / "decisions.json").read_bytes() == decisions
