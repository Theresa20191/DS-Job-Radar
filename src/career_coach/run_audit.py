"""Run manifest and audit gates for an operational daily run (P0-1).

The audit only reads: the run's raw inputs, its decisions and population,
the scrape report published with the raw files, and the live-check evidence.
It never changes a decision. Each gate returns PASS, INVESTIGATE, FAIL or
SKIPPED (not evaluable, with the reason); the verdict is FAIL if any gate
fails, else INVESTIGATE if any gate needs review, else PASS. FAIL stops the
daily run before the run is archived; INVESTIGATE completes the run but it is
reported as not cleared.

Gates and their criteria:
  raw_integrity        FAIL  an enabled source's _latest file is missing or
                             empty, a line is not a RawJob, a source id is
                             empty, a record's source is not its file's
                             source, a description is empty, a raw job_id is
                             not <source>_<source_job_id>, or one source id
                             appears with conflicting posting content (rows
                             differing only in the retrieval fields
                             scraped_at / query_used are the same posting
                             returned by overlapping queries, not a conflict);
                       INVESTIGATE  a non-empty source id does not have the
                             source's known format (source-side format change).
  publication_state    FAIL  the scraper's publication journal or staging
                             files are present (the raw files may be mixed).
  scrape_report        FAIL  the scrape report's per-source SHA-256 does not
                             match the current _latest file;
                       INVESTIGATE  no scrape report for these raw files.
  scrape_completeness  INVESTIGATE  any search query failed (recall reduced).
  posting_count_drop   INVESTIGATE  a source's unique postings fell by more
                             than 30% against its previous snapshot (review
                             only; observed drops were at most about 11%).
  decision_integrity   FAIL  decision count differs from the population, a
                             job appears twice, decisions mix run identities,
                             or a decision has no dashboard placement.
  code_state           INVESTIGATE  the code commit is unknown or the working
                             tree has uncommitted changes.
  live_coverage        INVESTIGATE  a Singapore MyCareersFuture job evaluated
                             in full has no eligible live record for its current
                             posting. On an INITIAL run (it scraped) this means
                             the live-check refresh is still required; on a
                             REFRESH run (--skip-scrape) evidence is still
                             missing after the refresh.
  agency_markers       INVESTIGATE  a fully evaluated job's text holds an
                             employment-agency registration or licence marker
                             while its employer is not classified as a
                             recruiter (a job-level warning names the marker).
  experience_citation  INVESTIGATE  a fully evaluated job has a years
                             requirement but no cited source sentence.

run_manifest.json records the run identity, explicit start/finish times
(UTC), the code identity, the input identities (each raw file's SHA-256 and
matching snapshot, the scrape report, the live-check record count), the scrape
statistics, live coverage, the SHA-256 of the run outputs decisions.json and
run_meta.json (never of the manifest itself, nor of the dashboard files, which
are re-rendered from those outputs), every gate result, job-level warnings and
the verdict.
"""

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from src.career_coach.dashboard import live_evidence, primary_section
from src.career_coach.run_archive import MANIFEST, OUTPUT_FILES, archive_id_v2
from src.career_coach.run_lock import require_lock
from src.models.raw_job import RawJob

MANIFEST_SCHEMA = "run_manifest_v1"
COUNT_DROP_LIMIT = 0.30
REPORT_LATEST = "scrape_report_latest.json"
PUBLISH_PREFIX = ".publish-"
SOURCE_ID_FORMAT = {"mycareersfuture": re.compile(r"^[0-9a-f]{32}$"),
                    "hiredly": re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")}
_SNAPSHOT = re.compile(r"^(?P<source>[a-z]+)_(?P<ts>\d{8}T\d{6}Z)\.jsonl$")
# Audit detector, intentionally broader than the classifier: a registration or
# licence keyword shortly before an EA licence number (2 digits, letter, 4-5
# digits) or a personnel registration number (R + 6-9 digits), or JTKSM. The
# gap stays within one sentence but may hold the abbreviations "No." / "Reg.".
AGENCY_MARKER = re.compile(r"(?:\bEA\b|\bLicen[cs]e\b|\bLic\b|\bReg(?:istration)?\b)(?:\b(?:No|Reg)\.|[^.\n]){0,25}?"
                           r"\b(?:\d{2}[A-Z]\d{4,5}|R\d{6,9})\b|\bJTKSM\b", re.I)
VERDICT_ORDER = ("PASS", "INVESTIGATE", "FAIL")


@dataclass
class Gate:
    gate: str
    status: str
    detail: str
    job_warnings: dict[str, list[str]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"gate": self.gate, "status": self.status, "detail": self.detail}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ----------------------------------------------------------------- code identity

def code_identity(repo: Path) -> dict:
    """{commit, tree_clean, tree_fingerprint}. The fingerprint covers the
    uncommitted diff and untracked files, so different uncommitted code gives
    a different identity; it is None for a clean tree."""
    def git(*args) -> bytes:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout
    try:
        commit = git("rev-parse", "HEAD").decode().strip()
        dirty = git("status", "--porcelain", "--untracked-files=all").strip()
        fingerprint = None
        if dirty:
            digest = hashlib.sha256(git("diff", "--binary", "HEAD"))
            for name in sorted(git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")):
                if name:
                    digest.update(name + b"\0" + (repo / name.decode()).read_bytes())
            fingerprint = digest.hexdigest()
        return {"commit": commit, "tree_clean": not dirty, "tree_fingerprint": fingerprint}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": "UNKNOWN", "tree_clean": False, "tree_fingerprint": None}


# ------------------------------------------------------------------------ inputs

def raw_inputs(raw_dir: Path, sources: list[str]) -> dict:
    """Per source: the _latest file's SHA-256, line count, and the timestamped
    snapshot holding the same bytes (None when no snapshot matches)."""
    out = {}
    for source in sources:
        path = raw_dir / f"{source}_latest.jsonl"
        if not path.exists():
            out[source] = {"file": path.name, "sha256": None, "records": 0, "snapshot": None}
            continue
        data = path.read_bytes()
        snapshot = next((p.name for p in sorted(raw_dir.glob(f"{source}_*.jsonl"), reverse=True)
                         if _SNAPSHOT.match(p.name) and p.read_bytes() == data), None)
        out[source] = {"file": path.name, "sha256": sha256(data), "snapshot": snapshot,
                       "records": sum(1 for line in data.splitlines() if line.strip())}
    return out


def load_scrape_report(raw_dir: Path) -> tuple[dict | None, str | None]:
    path = raw_dir / REPORT_LATEST
    if not path.exists():
        return None, None
    data = path.read_bytes()
    return json.loads(data), sha256(data)


def latest_refresh(evidence_root: Path, as_of: str) -> dict | None:
    """The live-check refresh observed on the run's as-of date, if any."""
    path = evidence_root / as_of / "evidence.json"
    if not path.exists():
        return None
    evidence = json.loads(path.read_text())
    return {"date": as_of, "targets": evidence.get("targets"), "observed": evidence.get("observed"),
            "failed": evidence.get("failed")}


# ------------------------------------------------------------------------- gates

RETRIEVAL_FIELDS = {"scraped_at", "query_used"}       # how a row was fetched, not what the posting says


def gate_raw_integrity(raw_dir: Path, sources: list[str]) -> Gate:
    problems, unusual = [], []
    for source in sources:
        path = raw_dir / f"{source}_latest.jsonl"
        lines = [line for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
        if not lines:
            problems.append(f"{source}: no raw records")
            continue
        fmt = SOURCE_ID_FORMAT.get(source)
        content: dict[str, tuple[int, dict]] = {}        # source id -> (first line, posting content)
        for n, line in enumerate(lines, start=1):
            try:
                job = RawJob.model_validate_json(line)
            except ValueError:
                problems.append(f"{source} line {n}: not a raw job record")
                continue
            if not job.source_job_id.strip():
                problems.append(f"{source} line {n}: empty source id")
            elif fmt is not None and not fmt.match(job.source_job_id):
                unusual.append(f"{source} line {n}: source id {job.source_job_id!r} has an unexpected format")
            if job.source != source:
                problems.append(f"{source} line {n}: record source {job.source!r} in the {source} file")
            if not job.job_description.strip():
                problems.append(f"{source} line {n}: empty description ({job.source_job_id})")
            posting = job.model_dump(mode="json", exclude=RETRIEVAL_FIELDS)
            first = content.setdefault(job.source_job_id, (n, posting))
            if first[1] != posting:
                fields = sorted(k for k in posting if posting[k] != first[1].get(k))
                problems.append(f"{source} line {n}: source id {job.source_job_id!r} conflicts with line {first[0]} "
                                f"({', '.join(fields)})")
            if job.job_id != f"{source}_{job.source_job_id}":
                problems.append(f"{source} line {n}: job_id {job.job_id!r} is not {source}_<source id>")
    if problems:
        return Gate("raw_integrity", "FAIL", f"{len(problems)} problem(s): " + "; ".join(problems[:5]))
    if unusual:
        return Gate("raw_integrity", "INVESTIGATE", f"{len(unusual)} record(s): " + "; ".join(unusual[:5]))
    return Gate("raw_integrity", "PASS", "every raw record is valid and enriched")


def gate_publication_state(raw_dir: Path) -> Gate:
    leftovers = sorted(p.name for p in raw_dir.glob(PUBLISH_PREFIX + "*"))
    if leftovers:
        return Gate("publication_state", "FAIL", "an interrupted scraper publication is present: " + ", ".join(leftovers))
    return Gate("publication_state", "PASS", "no interrupted publication")


def gate_scrape_report(report: dict | None, raw: dict) -> Gate:
    if report is None:
        return Gate("scrape_report", "INVESTIGATE", "no scrape report for these raw files; scrape statistics are unavailable")
    mismatched = [s for s, r in raw.items() if (report.get("sources", {}).get(s) or {}).get("sha256") != r["sha256"]]
    if mismatched:
        return Gate("scrape_report", "FAIL", "the scrape report does not describe the current raw files of " + ", ".join(mismatched))
    return Gate("scrape_report", "PASS", f"scrape report {report['snapshot']} matches every raw file")


def gate_scrape_completeness(report: dict | None) -> Gate:
    if report is None:
        return Gate("scrape_completeness", "SKIPPED", "no scrape report")
    failed = {s: r["failed_queries"] for s, r in report["sources"].items() if r.get("failed_queries")}
    if failed:
        return Gate("scrape_completeness", "INVESTIGATE",
                    "failed search queries: " + "; ".join(f"{s}: {', '.join(q)}" for s, q in sorted(failed.items())))
    return Gate("scrape_completeness", "PASS", "every search query completed")


def _unique_postings(path: Path) -> int:
    return len({json.loads(line)["source_job_id"] for line in path.read_text().splitlines() if line.strip()})


def gate_posting_count_drop(raw_dir: Path, raw: dict) -> Gate:
    drops, compared, skipped = [], [], []
    for source, info in raw.items():
        if info["snapshot"] is None:
            skipped.append(f"{source}: current raw file has no snapshot")
            continue
        current_ts = _SNAPSHOT.match(info["snapshot"]).group("ts")
        earlier = sorted((m.group("ts"), p) for p in raw_dir.glob(f"{source}_*.jsonl")
                         if (m := _SNAPSHOT.match(p.name)) and m.group("source") == source and m.group("ts") < current_ts)
        if not earlier:
            skipped.append(f"{source}: no previous snapshot")
            continue
        before, now = _unique_postings(earlier[-1][1]), _unique_postings(raw_dir / info["snapshot"])
        compared.append(f"{source} {before}->{now}")
        if before and (before - now) / before > COUNT_DROP_LIMIT:
            drops.append(f"{source}: unique postings {before} -> {now} (previous snapshot {earlier[-1][0]})")
    if drops:
        return Gate("posting_count_drop", "INVESTIGATE", "drop over 30%: " + "; ".join(drops))
    if not compared:
        return Gate("posting_count_drop", "SKIPPED", "; ".join(skipped))
    return Gate("posting_count_drop", "PASS", "unique postings " + ", ".join(compared) + ("; " + "; ".join(skipped) if skipped else ""))


def gate_decision_integrity(decisions, population) -> Gate:
    problems = []
    ids = [d.job.job_id for d in decisions]
    if len(decisions) != len(population):
        problems.append(f"{len(decisions)} decisions for a population of {len(population)}")
    if len(ids) != len(set(ids)):
        problems.append("a job appears more than once")
    if len({json.dumps(d.run.model_dump(mode='json'), sort_keys=True) for d in decisions}) > 1:
        problems.append("decisions come from more than one run")
    for d in decisions:
        try:
            primary_section(d)
        except ValueError as e:
            problems.append(str(e))
    if problems:
        return Gate("decision_integrity", "FAIL", "; ".join(problems))
    return Gate("decision_integrity", "PASS", f"{len(decisions)} decisions, one per job, one run")


def gate_code_state(code: dict) -> Gate:
    if code["commit"] == "UNKNOWN":
        return Gate("code_state", "INVESTIGATE", "the code commit could not be determined")
    if not code["tree_clean"]:
        return Gate("code_state", "INVESTIGATE", f"uncommitted changes on {code['commit'][:12]}")
    return Gate("code_state", "PASS", f"clean tree at {code['commit'][:12]}")


def live_facts(decisions) -> dict:
    sg = [(d, live_evidence(d, None)) for d in decisions if d.evaluation_scope == "FULL"]
    sg = [(d, live) for d, live in sg if live is not None]
    missing = [{"job_id": d.job.job_id, "reason": "no live record" if live["state"] == "NONE" else "record is for another posting"}
               for d, live in sg if live["state"] != "CURRENT"]
    return {"sg_passed": len(sg), "covered": len(sg) - len(missing), "missing": missing}


def gate_live_coverage(facts: dict, run_stage: str) -> Gate:
    n = len(facts["missing"])
    if not n:
        return Gate("live_coverage", "PASS", f"{facts['covered']} of {facts['sg_passed']} Singapore jobs have current live evidence")
    detail = (f"live-check refresh required before the final run: {n} of {facts['sg_passed']} Singapore jobs lack current evidence"
              if run_stage == "INITIAL" else
              f"{n} of {facts['sg_passed']} Singapore jobs still lack current live evidence after the refresh")
    warnings = {m["job_id"]: [f"Live evidence missing for the current posting ({m['reason']})"] for m in facts["missing"]}
    return Gate("live_coverage", "INVESTIGATE", detail, warnings)


def gate_agency_markers(population, decisions) -> Gate:
    full = {d.job.job_id for d in decisions if d.evaluation_scope == "FULL"}
    warnings = {}
    for c in population:
        job = c.job
        if job.job_id not in full or job.employer_clarity == "RECRUITER":
            continue
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", f"{job.description_raw} {job.requirements_section_raw or ''}"))
        m = AGENCY_MARKER.search(text)
        if m:
            warnings[job.job_id] = [f"Agency registration or licence marker '{m.group(0).strip()}' while the employer "
                                    f"is classified {job.employer_clarity}"]
    if warnings:
        return Gate("agency_markers", "INVESTIGATE", f"{len(warnings)} job(s) with an agency marker not classified as a recruiter: "
                    + ", ".join(sorted(warnings)), warnings)
    return Gate("agency_markers", "PASS", "no agency marker conflicts with an employer classification")


def gate_experience_citation(decisions) -> Gate:
    warnings = {}
    for d in decisions:
        exp = d.candidate_alignment.experience if d.candidate_alignment else None
        if d.evaluation_scope == "FULL" and exp and exp.job_required_years is not None \
                and not any(e.kind == "years_requirement" for e in d.key_evidence):
            warnings[d.job.job_id] = [f"{exp.job_required_years:g} years required but no source sentence is cited"]
    if warnings:
        return Gate("experience_citation", "INVESTIGATE", f"{len(warnings)} job(s) without a cited years sentence", warnings)
    return Gate("experience_citation", "PASS", "every years requirement cites its source sentence")


def verdict(gates: list[Gate]) -> str:
    return max((g.status for g in gates if g.status in VERDICT_ORDER), key=VERDICT_ORDER.index, default="PASS")


# ---------------------------------------------------------------------- manifest

def build_manifest(*, root: Path, out_dir: Path, sources: list[str], decisions, population, run_stage: str,
                   code: dict, started_utc: str, finished_utc: str, live_records: int) -> dict:
    raw_dir = root / "data" / "raw"
    run = decisions[0].run.model_dump(mode="json")
    raw = raw_inputs(raw_dir, sources)
    report, report_sha = load_scrape_report(raw_dir)
    live = live_facts(decisions)
    gates = [gate_raw_integrity(raw_dir, sources), gate_publication_state(raw_dir), gate_scrape_report(report, raw),
             gate_scrape_completeness(report), gate_posting_count_drop(raw_dir, raw),
             gate_decision_integrity(decisions, population), gate_code_state(code), gate_live_coverage(live, run_stage),
             gate_agency_markers(population, decisions), gate_experience_citation(decisions)]
    job_warnings: dict[str, list[str]] = {}
    for g in gates:
        for job_id, texts in g.job_warnings.items():
            job_warnings.setdefault(job_id, []).extend(texts)
    return {
        "schema_version": MANIFEST_SCHEMA,
        "archive_id": archive_id_v2(run["as_of_date"], run["run_kind"], run["input_hashes"], code),
        "run": run, "run_stage": run_stage, "started_utc": started_utc, "finished_utc": finished_utc, "code": code,
        "inputs": {"raw": raw, "scrape_report": {"file": REPORT_LATEST, "sha256": report_sha} if report else None,
                   "live_check_records": live_records},
        "scrape": report["sources"] if report else None,
        "live": {**live, "refresh_required": bool(live["missing"]) and run_stage == "INITIAL",
                 "latest_refresh": latest_refresh(root / "data" / "live_check_evidence", run["as_of_date"])},
        "outputs": {name: sha256((out_dir / name).read_bytes()) for name in OUTPUT_FILES},
        "audit": {"gates": [g.as_dict() for g in gates], "job_warnings": dict(sorted(job_warnings.items())),
                  "verdict": verdict(gates)},
    }


def write_manifest(out_dir: Path, manifest: dict) -> bytes:
    require_lock(out_dir)
    data = json.dumps(manifest, indent=1, sort_keys=True).encode()
    (out_dir / MANIFEST).write_bytes(data)
    return data


def load_manifest(run_dir: Path) -> dict | None:
    path = run_dir / MANIFEST
    return json.loads(path.read_text()) if path.exists() else None
