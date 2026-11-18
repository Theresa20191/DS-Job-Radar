"""Decision Card V1 (with presentation amendment A1–A11) — a renderer over
ScoredJob, never a second decision engine.

Uses the same 39 real postings as test_transition_lens.py, scored with the
real score_jobs() and apply_lens().
"""

# Public copy: tests that need the private real-posting fixture (transition_lens_jobs.json) are not included;
# the tests below do not use it and are unchanged apart from fictional example-profile values.

import ast
import copy
import json
import re
from pathlib import Path

import pytest

from src.config import load_profile, load_scoring, load_tracks
from src.models.processed_job import ProcessedJob
from src.models.transition import TARGET_TIERS, EvidenceRef
from src.reporting import decision_card as dc
from src.reporting.markdown_report import generate_report
from src.scoring.overall import score_jobs
from src.scoring.technical_alignment import gap_text
from src.transition.lens import apply_lens

ROOT = Path(__file__).resolve().parent.parent


def main(card, section=None):
    return [l for l in card.main if section is None or l.section == section]


def texts(card, section=None):
    return [l.text for l in main(card, section)]


def all_lines(card):
    return card.main + card.drill


# --- frozen outputs untouched, deterministic ---------------------------------------------------


# --- provenance ---------------------------------------------------------------------------------

def _dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_dump(v) for v in value]
    return value


def _numbers_and_lengths(value, out):
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        out.add(float(value))
    elif isinstance(value, dict):
        for v in value.values():
            _numbers_and_lengths(v, out)
    elif isinstance(value, list):
        out.add(float(len(value)))
        for v in value:
            _numbers_and_lengths(v, out)
    return out


# --- structure: action, career direction, flags ---------------------------------------------------


def _track_index(path):
    match = re.search(r"tracks\[(\d+)\]$", path)
    return int(match.group(1)) if match else None


# --- run context: constants move to the run header, deviations stay on the card ---------------------


# --- compensation boundary ----------------------------------------------------------------------


# --- gaps -----------------------------------------------------------------------------------------


# --- UNKNOWN semantics ------------------------------------------------------------------------------

def category(card, path):
    return next(l.category for l in card.main if path in l.source_paths)


# --- What to Verify --------------------------------------------------------------------------



# --- import boundary and inputs -------------------------------------------------------------

def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}


def test_upstream_modules_never_import_the_card():
    upstream = [p for d in ("scoring", "pipeline", "transition", "enrichment", "models") for p in (ROOT / "src" / d).glob("*.py")]
    upstream += [ROOT / "src" / "reporting" / "markdown_report.py", ROOT / "src" / "reporting" / "exporters.py"]
    for path in upstream:
        assert not any("decision_card" in m for m in _imports(path)), path


def test_card_reads_no_profile_config_or_raw_text():
    source = (ROOT / "src" / "reporting" / "decision_card.py").read_text()
    assert "src.config" not in _imports(ROOT / "src" / "reporting" / "decision_card.py")
    for name in ("load_profile", "load_scoring", "load_tracks", "description_raw", "requirements_raw",
                 "responsibilities_raw", "tracks.yaml\"", "open("):
        assert name not in source, name


# --- pinned cards and layout ------------------------------------------------------------------------

# Main-card lines (title and company render as the card heading). Pinned to
# prevent accidental re-expansion; the same cards took 48-77 lines before the
# presentation amendment.


