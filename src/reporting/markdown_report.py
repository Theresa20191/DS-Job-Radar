from collections import Counter

from src.models.scored_job import ScoredJob
from src.transition.gap_ledger import gap_market

TIER_EMOJI = {"A": "🔥", "B": "✅", "C": "🟡", "D": "⚪"}


def _fmt_score(value) -> str:
    return "unknown" if value is None else str(value)


def _band(dimension) -> str:
    return dimension.band if dimension is not None and dimension.known else "unknown"


def _stability(job: ScoredJob) -> str:
    s = job.decision_stability
    if s is None:
        return "n/a"
    text = s.stability_label
    if s.stability_label != "STABLE":
        text += f" (tier range {s.tier_floor}..{s.tier_ceiling})"
    return text


def _job_block(rank: int, job: ScoredJob) -> str:
    ptf = job.personal_transition_fit
    lines = [
        f"### {rank}. {job.company} — {job.job_title}",
        f"Score: {job.overall_score}  |  Decision stability: {_stability(job)}  |  Data Completeness: {job.data_completeness_pct}%",
        "",
        f"- Location: {job.location_normalized}, {job.country} ({job.remote_type})",
        f"- Job Quality: {job.job_quality_score}  |  Interview Fit: {job.interview_fit_score}  |  "
        f"Personal Transition Fit: {_fmt_score(ptf.score if ptf else None)} "
        f"(technical: {_band(job.technical_alignment)}, domain: {_band(job.domain_alignment)})",
        f"- Compensation: {_fmt_score(job.compensation_score)}"
        + ("" if job.compensation_known else " (undisclosed — not treated as low pay)"),
        f"- Technical evidence gaps: {'; '.join(job.technical_alignment.gaps) if job.technical_alignment and job.technical_alignment.gaps else 'none'}",
        f"- Opportunity types: {', '.join(job.opportunity_types) or 'none'}",
        f"- Employer clarity: {job.employer_clarity} — {job.employer_clarity_reason}",
        f"- Hard constraints: substantive DS = {job.hc_substantive_ds_status}, work authorization = {job.hc_work_authorization_status}"
        + (f" [{job.work_authorization_restriction_type}] ({job.work_authorization_reason})" if job.work_authorization_evidence else ""),
        f"- Job risk: {job.job_risk_score} (penalty: -{job.job_risk_penalty})",
        "",
    ]
    if job.why_apply:
        lines.append("**why_apply:**")
        lines.extend(f"- {r}" for r in job.why_apply)
        lines.append("")
    if job.key_gap:
        lines.append("**key_gap:**")
        lines.extend(f"- {r}" for r in job.key_gap)
        lines.append("")
    if job.decision_stability and job.decision_stability.open_unknowns:
        lines.append("_Could change the decision: " + "; ".join(
            f"{u.field} ({u.direction.replace('_', ' ')}) — {u.reason}" for u in job.decision_stability.open_unknowns) + "_")
        lines.append("")
    if job.unknown_fields:
        lines.append(f"_Unknown fields: {', '.join(job.unknown_fields)}_")
        lines.append("")
    lines.extend(_lens_block(job))
    lines.append(f"**Recommended action: {job.recommended_action}**")
    lines.append("")
    lines.append(f"URL: {job.canonical_url}")
    lines.append("")
    return "\n".join(lines)


def _lens_block(job: ScoredJob) -> list[str]:
    """Transition Lens — annotation only; nothing here changes the score,
    tier, or recommended action shown above."""
    lens = job.transition_lens
    if lens is None:
        return []
    wp, tr = lens.work_profile, lens.trajectory
    work = ", ".join(l.label for l in wp.labels) or f"unknown ({wp.unknown_reason})"
    tracks = ", ".join(f"{m.track_id} [{m.target_tier}]" for m in tr.tracks) or "none"
    trajectory = tr.direction + (f" ({tr.unknown_reason})" if tr.unknown_reason else "") + (" · mixed" if tr.mixed else "")
    lines = ["**Transition lens (annotation only — does not change the score, tier or action):**",
             f"- Work profile: {work}",
             f"- Trajectory: {trajectory} — tracks: {tracks}"]
    for g in tr.guard_failures:
        lines.append(f"- {g.track_id} not mapped: context present, but no substantive DS work evidence")
    if lens.gap_ledger:
        gaps = lens.gap_ledger.required_unevidenced
        if gaps:
            lines.append("- Evidence gaps (required, no evidence in your profile): " + ", ".join(
                f"{g.technique} [{g.candidate_classification}; {g.market_frequency} jobs]" for g in gaps))
        elif lens.gap_ledger.settled_reason:
            lines.append(f"- Evidence gaps: none assessable ({lens.gap_ledger.settled_reason})")
    if lens.constraint_checks:
        floor, auth = lens.constraint_checks.compensation_floor, lens.constraint_checks.work_authorization
        lines.append(f"- Compensation floor: {floor.status} ({floor.reason}) · Work authorization ({auth.country}): {auth.status} ({auth.source})")
    if lens.strategy_flags:
        lines.append("- Flags: " + ", ".join(f.flag for f in lens.strategy_flags))
    lines.append("")
    return lines


def _lens_summary(scored_jobs: list[ScoredJob]) -> list[str]:
    lenses = [j.transition_lens for j in scored_jobs if j.transition_lens]
    if not lenses:
        return []
    directions = Counter(l.trajectory.direction for l in lenses)
    market = gap_market([l.gap_ledger for l in lenses if l.gap_ledger])
    lines = ["## Transition Lens (annotation only)",
             "Describes where each job's work leads relative to your declared target tracks. "
             "It never changes a score, tier, ranking or recommended action.", "",
             "- Trajectory: " + ", ".join(f"{d} {n}" for d, n in directions.most_common()),
             "- Flags: " + ", ".join(f"{f} {n}" for f, n in Counter(f.flag for l in lenses for f in l.strategy_flags).most_common()),
             "", "### Evidence-gap market",
             "Required techniques with no evidence in your profile, by how many jobs in this run require them. "
             "These are evidence gaps, not verdicts: declare, classify or build evidence as you see fit.", ""]
    lines.extend(f"- {technique}: {count} jobs" for technique, count in market)
    lines.append("")
    return lines


def _hard_constraint_exclusions(rejected: list) -> list[str]:
    """One line per job excluded by HC-1 and/or HC-2, built from the
    structured statuses/evidence (not from reason strings)."""
    lines = []
    for job, _ in rejected:
        failed = []
        if job.hc_substantive_ds_status == "FAIL":
            failed.append(f"HC-1 (role_type {job.role_type})")
        if job.hc_work_authorization_status == "FAIL":
            ev = next(e for e in job.work_authorization_evidence if e.restriction_type == job.work_authorization_restriction_type)
            failed.append(f"HC-2 {ev.restriction_type}: '{ev.matched_text}' ({ev.source_field}: \"{ev.context}\")")
        if failed:
            lines.append(f"- {job.company} — {job.job_title}: " + "; ".join(failed))
    return lines


def generate_report(scored_jobs: list[ScoredJob], pipeline_stats: dict, source_completeness: dict, rejected: list | None = None) -> str:
    sections = [f"# Data Scientist Job Radar", f"Scoring run as_of_date: {pipeline_stats.get('as_of_date') or 'unknown'}", ""]

    sections.append("## Pipeline Summary")
    for k, v in pipeline_stats.items():
        sections.append(f"- {k}: {v}")
    sections.append("")

    sections.append("## Source Data Quality (kept separate — not averaged)")
    for source, metrics in source_completeness.items():
        sections.append(f"**{source}**")
        for k, v in metrics.items():
            sections.append(f"  - {k}: {v}")
    sections.append("")

    sections.extend(_lens_summary(scored_jobs))

    sorted_jobs = sorted(scored_jobs, key=lambda j: j.overall_score, reverse=True)
    by_tier: dict[str, list[ScoredJob]] = {"A": [], "B": [], "C": [], "D": []}
    for j in sorted_jobs:
        by_tier.setdefault(j.tier, []).append(j)

    rank = 1
    for tier in ("A", "B", "C", "D"):
        jobs = by_tier.get(tier, [])
        if not jobs:
            continue
        sections.append(f"## {TIER_EMOJI.get(tier, '')} Tier {tier} ({len(jobs)} jobs)")
        sections.append("")
        for job in jobs:
            sections.append(_job_block(rank, job))
            rank += 1

    exclusions = _hard_constraint_exclusions(rejected or [])
    if exclusions:
        sections.append(f"## Excluded by hard constraints ({len(exclusions)} jobs)")
        sections.append("")
        sections.extend(exclusions)
        sections.append("")

    return "\n".join(sections)
