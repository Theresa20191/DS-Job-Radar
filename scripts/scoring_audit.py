"""Scoring audit — per-job before/after comparison of the current scored
output against the pinned PRE-PTF-migration snapshot (O-4).

Read-only over data/output/jobs_scored.parquet and the snapshot; writes
data/output/scoring_audit{suffix}.csv/.md. The snapshot is verified by
hash so a silently replaced "before" can never be compared against.
Legacy columns (technical_fit_*, domain_fit_*, confidence_score,
interview_probability_score) are read ONLY from the snapshot.
"""

import hashlib
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SNAPSHOT = ROOT / "data" / "snapshots" / "pre_ptf_migration" / "jobs_scored.parquet"
SNAPSHOT_SHA256 = "44c558778714cfff4e2b2019f288210ec429fa4511247d826b5ca8bc5f81b113"

BEFORE_COLUMNS = {
    "overall_score": "overall_before", "tier": "tier_before", "recommended_action": "action_before",
    "technical_fit_score": "technical_fit_before", "domain_fit_score": "domain_fit_before",
    "confidence_score": "confidence_before", "opportunity_types": "opportunity_types_before",
}


def _band(dimension) -> str:
    return dimension["band"] if isinstance(dimension, dict) and dimension.get("known") else "UNKNOWN"


def _nested(value, key):
    return value.get(key) if isinstance(value, dict) else None


def _items(value) -> list:
    """Nested lists come back from parquet as numpy arrays; None -> []."""
    return [] if value is None else list(value)


def load_snapshot() -> pd.DataFrame:
    digest = hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest()
    if digest != SNAPSHOT_SHA256:
        raise SystemExit(f"Pinned snapshot hash mismatch: {digest} != {SNAPSHOT_SHA256}")
    return pd.read_parquet(SNAPSHOT)[["job_id", *BEFORE_COLUMNS]].rename(columns=BEFORE_COLUMNS)


def build_audit(after: pd.DataFrame, before: pd.DataFrame) -> pd.DataFrame:
    rows = pd.DataFrame({
        "job_id": after["job_id"], "company": after["company"], "job_title": after["job_title"],
        "overall_after": after["overall_score"], "tier_after": after["tier"],
        "action_after": after["recommended_action"],
        "technical_band": after["technical_alignment"].map(_band),
        "domain_band": after["domain_alignment"].map(_band),
        # object dtype so an UNKNOWN PTF stays None — never coerced to NaN/0
        "ptf": pd.Series([_nested(v, "score") for v in after["personal_transition_fit"]], index=after.index, dtype=object),
        "base_score": after["base_score"],
        "stability": after["decision_stability"].map(lambda v: _nested(v, "stability_label")),
        "tier_range": after["decision_stability"].map(
            lambda v: f"{v['tier_floor']}..{v['tier_ceiling']}" if isinstance(v, dict) else None),
        "opportunity_types_after": after["opportunity_types"].map(list),
        "lens_direction": after["transition_lens"].map(lambda v: _nested(_nested(v, "trajectory"), "direction")),
        "lens_tracks": after["transition_lens"].map(
            lambda v: [m["track_id"] for m in _items(_nested(_nested(v, "trajectory"), "tracks"))]),
        "lens_comp_floor": after["transition_lens"].map(
            lambda v: _nested(_nested(_nested(v, "constraint_checks"), "compensation_floor"), "status")),
        "lens_flags": after["transition_lens"].map(lambda v: [f["flag"] for f in _items(_nested(v, "strategy_flags"))]),
        "as_of_date": after["as_of_date"],
        "hc_substantive_ds_status": after["hc_substantive_ds_status"],
        "hc_work_authorization_status": after["hc_work_authorization_status"],
    })
    audit = rows.merge(before, on="job_id", how="outer", indicator="presence")
    audit["opportunity_types_before"] = audit["opportunity_types_before"].map(
        lambda v: list(v) if v is not None and not isinstance(v, float) else v)
    audit["tier_changed"] = audit["tier_before"] != audit["tier_after"]
    return audit.sort_values(["overall_after"], ascending=False)


def main():
    suffix = sys.argv[1] if len(sys.argv) > 1 else ""
    after = pd.read_parquet(ROOT / "data" / "output" / "jobs_scored.parquet")
    audit = build_audit(after, load_snapshot())

    out_dir = ROOT / "data" / "output"
    audit.to_csv(out_dir / f"scoring_audit{suffix}.csv", index=False)
    with open(out_dir / f"scoring_audit{suffix}.md", "w") as f:
        f.write("# Scoring Audit — before (pinned pre-PTF snapshot) vs after\n\n")
        f.write(f"Snapshot sha256: `{SNAPSHOT_SHA256}`\n\n")
        f.write(f"Scored run as_of_date: `{sorted(set(audit['as_of_date'].dropna()))}`\n\n")
        f.write(audit.to_markdown(index=False))
        f.write("\n")
    print(f"Wrote {len(audit)} rows -> data/output/scoring_audit{suffix}.csv/.md "
          f"({int(audit['tier_changed'].sum())} tier changes; presence: {audit['presence'].value_counts().to_dict()})")


if __name__ == "__main__":
    main()
