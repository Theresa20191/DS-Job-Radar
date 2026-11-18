"""Immutable per-run archive (Gate 5, D1).

Every Career Coach run is archived as a directory holding its decisions.json,
run_meta.json and the exact config and profile files it used, plus archive
metadata. The archive id is derived only from `as_of_date + run_kind + the
run's input hashes`, so the same run always maps to the same directory and a
different run never collides with it — even when the live output folder is
overwritten by the next run.

Immutability: an existing archive is never modified. Archiving the same run
again is a verified no-op; archiving different content under an existing id
is refused. Archived files are written read-only. The learning layer only
reads from here.

Format v2 (operational runs with a run manifest): the archive also holds
run_manifest.json, and the archive id additionally covers the code identity
recorded in that manifest (commit + working-tree fingerprint), so the same
inputs run with different code get a different archive. Re-archiving the
same inputs with the same code is still a verified no-op when the run's
payload (decisions, run meta, config, profile) is identical; the archive
then keeps its first manifest. Archives written in format v1 keep their ids
and are verified exactly as before.
"""

import hashlib
import json
import os
import shutil
from pathlib import Path

import yaml

from src.career_coach.run_lock import require_lock
from src.career_coach.setup import content_hash
from src.models.career_coach import CareerCoachDecision

ARCHIVE_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "archive" / "runs"
ARCHIVE_VERSION = "run_archive_v1"
ARCHIVE_VERSION_V2 = "run_archive_v2"
FILES = ("decisions.json", "run_meta.json", "career_coach.yaml", "profile.yaml")
MANIFEST = "run_manifest.json"
FILES_V2 = FILES + (MANIFEST,)
OUTPUT_FILES = ("decisions.json", "run_meta.json")          # the run outputs a manifest's checksums cover


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_id(as_of_date: str, run_kind: str, input_hashes: dict) -> str:
    key = json.dumps({"as_of_date": as_of_date, "run_kind": run_kind, "input_hashes": input_hashes}, sort_keys=True)
    return f"{as_of_date}_{run_kind}_{_sha(key.encode())[:16]}"


def archive_id_v2(as_of_date: str, run_kind: str, input_hashes: dict, code: dict) -> str:
    """v2 id: the v1 identity plus the code identity (commit and working-tree
    fingerprint), so different code never maps to an existing archive."""
    key = json.dumps({"as_of_date": as_of_date, "run_kind": run_kind, "input_hashes": input_hashes,
                      "code": {"commit": code["commit"], "tree_fingerprint": code["tree_fingerprint"]}}, sort_keys=True)
    return f"{as_of_date}_{run_kind}_{_sha(key.encode())[:16]}"


def _check_manifest(manifest: dict, run: dict, aid: str, outputs: dict, where: str) -> None:
    """A run manifest must name this archive id and run, and its output
    checksums must be the checksums of these outputs (it never covers itself)."""
    if manifest.get("archive_id") != aid:
        raise ValueError(f"{where}: run manifest names archive {manifest.get('archive_id')}, not {aid}")
    if manifest.get("run") != run:
        raise ValueError(f"{where}: run manifest describes a different run")
    if manifest.get("outputs") != outputs:
        raise ValueError(f"{where}: run manifest output checksums do not match the run outputs")


def _run_identity(decisions_bytes: bytes) -> dict:
    decisions = [CareerCoachDecision.model_validate(d) for d in json.loads(decisions_bytes)]
    runs = {json.dumps(d.run.model_dump(mode="json"), sort_keys=True) for d in decisions}
    if len(runs) != 1:
        raise ValueError("decisions.json mixes decisions from more than one run")
    run = json.loads(runs.pop())
    return {"run": run, "decision_count": len(decisions)}


def archive_run(run_dir: Path, config_bytes: bytes, profile_bytes: bytes, root: Path = ARCHIVE_ROOT,
                manifest_bytes: bytes | None = None) -> Path:
    """Archive one run. config/profile bytes must be the exact files the run
    used: their parsed content must reproduce the run's recorded hashes.
    With manifest_bytes the archive is format v2 (see the module docstring).
    Writing under a protected data folder requires the pipeline lock."""
    require_lock(root)
    files = {"decisions.json": (run_dir / "decisions.json").read_bytes(),
             "run_meta.json": (run_dir / "run_meta.json").read_bytes(),
             "career_coach.yaml": config_bytes, "profile.yaml": profile_bytes}
    identity = _run_identity(files["decisions.json"])
    run = identity["run"]
    hashes = run["input_hashes"]
    if content_hash(yaml.safe_load(config_bytes)) != hashes["config"]:
        raise ValueError("config file does not match the config hash recorded in the run")
    if content_hash(yaml.safe_load(profile_bytes)) != hashes["profile"]:
        raise ValueError("profile file does not match the profile hash recorded in the run")

    if manifest_bytes is None:
        aid = archive_id(run["as_of_date"], run["run_kind"], hashes)
        meta = {"archive_version": ARCHIVE_VERSION, "archive_id": aid}
    else:
        manifest = json.loads(manifest_bytes)
        aid = archive_id_v2(run["as_of_date"], run["run_kind"], hashes, manifest["code"])
        _check_manifest(manifest, run, aid, {n: _sha(files[n]) for n in OUTPUT_FILES}, "run manifest")
        files[MANIFEST] = manifest_bytes
        meta = {"archive_version": ARCHIVE_VERSION_V2, "archive_id": aid, "code": manifest["code"]}
    meta.update({"as_of_date": run["as_of_date"], "run_kind": run["run_kind"], "coach_version": run["coach_version"],
                 "input_hashes": hashes, "decision_count": identity["decision_count"],
                 "files": {n: _sha(b) for n, b in files.items()}})
    target = root / aid
    if target.exists():
        existing = verify_archive(target)
        same = existing["files"] == meta["files"] if manifest_bytes is None \
            else all(existing["files"][n] == meta["files"][n] for n in FILES)       # the payload; the first manifest stays
        if not same:
            raise ValueError(f"archive {aid} already exists with different content; archives are immutable")
        return target

    staging = root / f".{aid}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for name, data in {**files, "archive.json": json.dumps(meta, indent=1, sort_keys=True).encode()}.items():
        (staging / name).write_bytes(data)
        os.chmod(staging / name, 0o444)
    staging.rename(target)
    return target


def verify_archive(path: Path) -> dict:
    """Checks every archived file against its recorded hash and the run
    identity against the archive id. Returns the archive metadata. A v2
    archive's manifest is checked independently too: its archive id, run
    identity, code identity and output checksums must match the archive."""
    meta = json.loads((path / "archive.json").read_text())
    v2 = meta.get("archive_version") == ARCHIVE_VERSION_V2
    names = FILES_V2 if v2 else FILES
    if v2 and set(meta["files"]) != set(FILES_V2):
        raise ValueError(f"{path.name}: a v2 archive must list exactly {', '.join(FILES_V2)}")
    for name in names:
        if _sha((path / name).read_bytes()) != meta["files"][name]:
            raise ValueError(f"{path.name}/{name} does not match its recorded hash")
    run = _run_identity((path / "decisions.json").read_bytes())["run"]
    expected = archive_id_v2(run["as_of_date"], run["run_kind"], run["input_hashes"], meta["code"]) if v2 \
        else archive_id(run["as_of_date"], run["run_kind"], run["input_hashes"])
    if expected != meta["archive_id"]:
        raise ValueError(f"{path.name}: archive id does not match its run identity")
    if v2:
        _check_manifest(json.loads((path / MANIFEST).read_bytes()), run, meta["archive_id"],
                        {n: meta["files"][n] for n in OUTPUT_FILES}, path.name)
        if meta["code"] != json.loads((path / MANIFEST).read_bytes())["code"]:
            raise ValueError(f"{path.name}: archive code identity differs from its manifest")
    if meta["archive_id"] != path.name:
        raise ValueError(f"{path.name}: directory name does not match archive id {meta['archive_id']}")
    if content_hash(yaml.safe_load((path / "career_coach.yaml").read_bytes())) != meta["input_hashes"]["config"] or \
            content_hash(yaml.safe_load((path / "profile.yaml").read_bytes())) != meta["input_hashes"]["profile"]:
        raise ValueError(f"{path.name}: archived config/profile do not reproduce the run hashes")
    return meta


def find_archive(as_of_date: str, run_kind: str, input_hashes: dict, root: Path = ARCHIVE_ROOT) -> Path | None:
    """The archive for exactly this run identity, verified; None if absent.
    A v1 archive is found by its id. v2 archives (whose id also covers the
    code) are found by their recorded identity; when runs of the same inputs
    with different code exist, the one whose manifest records the latest
    start time is returned (never a file time)."""
    path = root / archive_id(as_of_date, run_kind, input_hashes)
    if path.exists():
        verify_archive(path)
        return path
    if not root.is_dir():
        return None
    matches = []
    for candidate in sorted(root.iterdir()):
        listed = candidate / "archive.json"
        if candidate.name.startswith(".") or not listed.exists():
            continue
        meta = json.loads(listed.read_text())
        if meta.get("archive_version") != ARCHIVE_VERSION_V2 or (meta["as_of_date"], meta["run_kind"], meta["input_hashes"]) \
                != (as_of_date, run_kind, input_hashes):
            continue
        verify_archive(candidate)
        matches.append((json.loads((candidate / MANIFEST).read_text())["started_utc"], candidate.name, candidate))
    return max(matches)[2] if matches else None


def load_archived_decisions(path: Path) -> dict[str, CareerCoachDecision]:
    verify_archive(path)
    return {d.job.job_id: d for d in (CareerCoachDecision.model_validate(x)
                                      for x in json.loads((path / "decisions.json").read_text()))}
