"""Career Coach dashboard (Gate 3): a time-allocation view built only from
CareerCoachDecision records and their run metadata.

Product contract — the five named sections (V1.2, stable):
  1. Today's Application Queue
  2. Verify First
  3. Resume Work Queue      secondary view: cross-references jobs placed in 1 or 5
  4. Do Not Invest
  5. Full Opportunity View  the complete population (every job, one row each)

Every job has exactly ONE named section as its primary placement, decided by
its placement status (`SECTIONS` below are those statuses, not extra
dashboard sections):
  TODAY                 -> 1. Today's Application Queue
  VERIFY_FIRST          -> 2. Verify First
  DO_NOT_INVEST         -> 4. Do Not Invest
  HARD_FILTER_REJECTED  -> 4. Do Not Invest, collapsed subsection (M12)
  ON_HOLD               -> 5. Full Opportunity View, "On hold" subsection
Section 3 is never a primary placement. A hold caused by resume effort is
also cross-referenced (with detail) in section 3; a research-only hold is not.

The dashboard refuses to render if its totals do not reconcile with the
decisions it was built from. It adds no rule of its own: queue membership,
order and every displayed value come straight from the frozen decision fields.

Presentation context (Dashboard Improvement V1) is display-only and never
feeds back into a decision: the live records the decisions cite (for their
observed values), the previous comparable operational run (for the daily
delta) and the run's archive status. Live coverage counts a current SG
MyCareersFuture job as covered only when its decision cites an eligible record
for the posting the job points to now; "historical eligible live records"
(every eligible record, including records for jobs no longer in the corpus)
is a different number and is never labelled coverage.
"""

import csv
import html
import io
import re
from collections import Counter
from datetime import date, timedelta
from urllib.parse import urlparse

from src.models.career_coach import CareerCoachDecision

SECTIONS = ("TODAY", "VERIFY_FIRST", "ON_HOLD", "DO_NOT_INVEST", "HARD_FILTER_REJECTED")
SECTION_TITLES = {"TODAY": "Today's Application Queue", "VERIFY_FIRST": "Verify First",
                  "ON_HOLD": "On hold (effort exceeds what the job justifies)",
                  "DO_NOT_INVEST": "Do Not Invest", "HARD_FILTER_REJECTED": "Rejected by the hard filter"}
APPLY_GROUPS = (("APPLY", "Apply now"), ("TAILOR_RESUME", "Tailor resume, then apply"),
                ("NETWORK", "Network first"), ("RESEARCH_EMPLOYER", "Research the employer first"))
PRIORITY_ORDER = ("HIGH", "PRIORITY", "SELECTIVE", "VERIFY_FIRST", "DO_NOT_INVEST")
CV_ORDER = ("PRIMARY", "SECONDARY", "ADJACENT", "OFF_VOCABULARY", "UNKNOWN", "NON_TARGET")
REQUIRED_ORDER = ("LOW", "MODERATE", "HIGH", None)
TIER_LABEL = {"primary": "Primary", "secondary": "Secondary", "adjacent_ok": "Adjacent", "non_target": "Non-target"}
VERIFY_LABEL = {"VQ-ELIGIBILITY": "Eligibility", "VQ-COMPENSATION": "Compensation", "VQ-POSTING_OPEN": "Posting open",
                "VQ-APPLICATION_CHANNEL": "Application channel", "VQ-JOB_SCOPE": "Job scope",
                "VQ-ROLE_LEVEL": "Role level"}
SOURCE_HOSTS = {"www.mycareersfuture.gov.sg": "MyCareersFuture", "mycareersfuture.gov.sg": "MyCareersFuture",
                "my.hiredly.com": "Hiredly"}
NO_COMPARISON = "No comparable previous operational run available."
NOT_RECORDED = "not recorded"
COMPENSATION_STATUS = {"FAIL": "BELOW HARD FLOOR", "PASS": "MEETS HARD FLOOR", "UNKNOWN": "VERIFY"}
_POSTING_ID = re.compile(r"([0-9a-f]{32})$")
_RECORD_ID = re.compile(r"^lc-(\d{4}-\d{2}-\d{2})-([0-9a-f]{32})$")


def primary_section(d: CareerCoachDecision) -> str:
    if d.evaluation_scope == "HARD_FILTER_REJECTED":
        return "HARD_FILTER_REJECTED"
    if d.application_priority == "DO_NOT_INVEST":
        return "DO_NOT_INVEST"
    if d.application_priority == "VERIFY_FIRST":
        return "VERIFY_FIRST"
    if d.next_action == "HOLD":
        return "ON_HOLD"
    if d.next_action not in dict(APPLY_GROUPS):
        raise ValueError(f"{d.job.job_id}: next action {d.next_action} has no dashboard section")
    return "TODAY"


def _sort_key(item: dict):
    return (PRIORITY_ORDER.index(item["priority"]), CV_ORDER.index(item["career_value"]) if item["career_value"] in CV_ORDER else 99,
            REQUIRED_ORDER.index(item["required_investment"]), "".join(chr(0x10FFFF - ord(c)) for c in item["posted_date"] or ""),
            item["job_id"])


# ------------------------------------------------- display-only job facts

def _age_days(posted: str | None, as_of: date) -> int | None:
    """Days between the posted date and the run's as-of date; None when the
    posted date is absent or not an ISO date (never estimated)."""
    try:
        return (as_of - date.fromisoformat(posted)).days if posted else None
    except ValueError:
        return None


def _source(url: str) -> str:
    host = urlparse(url or "").netloc
    return SOURCE_HOSTS.get(host, host or NOT_RECORDED)


def posting_id(url: str) -> str | None:
    """The current posting identity: the 32-hex MyCareersFuture posting ID at the
    end of the job's own canonical URL path. None when the URL is missing, is not
    a MyCareersFuture posting, or carries no such ID — never synthesized from
    other fields and never taken from a live-check record."""
    if _source(url) != "MyCareersFuture":
        return None
    match = _POSTING_ID.search(urlparse(url).path.rstrip("/"))
    return match.group(1) if match else None


def posting_identity(d: CareerCoachDecision) -> str:
    """What `posting_changed` compares: the posting ID when the URL carries one,
    otherwise the canonical URL itself."""
    return posting_id(d.job.canonical_url) or d.job.canonical_url


def live_evidence(d: CareerCoachDecision, records: dict | None) -> dict | None:
    """The live evidence a Singapore MyCareersFuture decision used (V-4), or
    None for jobs the SG rule does not evaluate. Observed values come from the
    cited live record; without the record they are None, never guessed. A
    record counts as CURRENT only when the posting it observed is the job's
    current posting ID; a job without a current posting ID is never CURRENT."""
    factor = next((f for f in d.application_viability.factors if f.factor == "sg_application_channel"), None)
    if factor is None:
        return None
    current = posting_id(d.job.canonical_url)
    if factor.live_record_id is None:
        return {"state": "NONE", "current_posting_id": current, "rule": factor.rule_id, "detail": factor.detail}
    record = (records or {}).get(factor.live_record_id)
    parsed = _RECORD_ID.match(factor.live_record_id)
    checked = record.checked_on.isoformat() if record else parsed.group(1) if parsed else None
    observed = record.source_job_id if record else parsed.group(2) if parsed else None
    return {"state": "CURRENT" if observed is not None and observed == current else "OTHER_POSTING",
            "record_id": factor.live_record_id, "checked_on": checked, "observed_posting_id": observed,
            "current_posting_id": current, "rule": factor.rule_id, "detail": factor.detail,
            "page_reviewable": record.page_reviewable if record else None,
            "email_in_jd": record.email_in_jd if record else None,
            "posting_status": record.posting_status if record else None}


def compensation(d: CareerCoachDecision) -> tuple[str, str]:
    """(what the run recorded about pay, status). Only the decision's own
    compensation-floor factor and practical component are used; posted pay
    values are not part of the run output and are never reconstructed."""
    factor = next((f for f in d.application_viability.factors if f.factor == "compensation_floor"), None)
    if factor is not None:
        return factor.detail, COMPENSATION_STATUS.get(factor.result, factor.result)
    state = d.practical_constraint.components.get("compensation") if d.practical_constraint else None
    if state is None:
        return "Not evaluated (rejected by the hard filter)", "—"
    if state == "NO_FLOOR":
        return f"No compensation floor declared for {d.job.country}; posted pay is not recorded in the run output", "—"
    return f"Soft floor: {state}", state


def item(d: CareerCoachDecision, positionings: dict[str, str], records: dict | None = None) -> dict:
    """Every displayed value, taken from one decision (and, for the SG live
    evidence fields, the live record that decision cites)."""
    cv = d.career_value
    tier = next((m.declared_tier for m in cv.matched_tracks if m.track == cv.deciding_track), None) if cv else None
    ca = d.candidate_alignment
    rt = d.resume_track
    queue = d.verification_queue
    pay, pay_status = compensation(d)
    return {
        "job_id": d.job.job_id, "company": d.job.company, "title": d.job.job_title, "market": d.job.country,
        "url": d.job.canonical_url, "posted_date": d.job.posted_date, "section": primary_section(d),
        "scope": d.evaluation_scope, "viability": d.application_viability.state,
        "career_value": cv.state if cv else "NOT_EVALUATED",
        "track": cv.deciding_track if cv else None, "declared_tier": TIER_LABEL.get(tier, "—"),
        "why": d.primary_reason.text, "why_rule": d.primary_reason.rule_id,
        "priority": d.application_priority, "next_action": d.next_action,
        "resume_effort": d.resume_effort or "—", "research_effort": d.research_effort or "—",
        "justified_investment": d.application_investment.justified,
        "required_investment": d.application_investment.required, "investment_fit": d.application_investment.fit,
        "positioning": rt.positioning if hasattr(rt, "positioning") else None,
        "resume_track": f"{positionings.get(rt.positioning, rt.positioning)} (from {rt.source_track}, {TIER_LABEL[rt.declared_tier]})"
                        if hasattr(rt, "positioning") else "—",
        "readiness": d.readiness.state if d.readiness else "—",
        "seniority": f"{ca.seniority.state} ({ca.seniority.job_level})" if ca else "—",
        "experience": f"{ca.experience.state} ({ca.experience.candidate_measure})" if ca else "—",
        "verification": queue[0].question if queue else "—",
        "verification_code": queue[0].code if queue else None,
        "if_confirmed": queue[0].if_resolved_favourably if queue else None,
        "if_not": queue[0].if_resolved_unfavourably if queue else None,
        "uncertainty": d.uncertainty_state,
        "gaps": [g.name for g in d.evidence_gaps],
        "profile_items": list(d.profile_items_affecting_job),
        "blocking_rule": d.blocking_reason.rule_id if d.blocking_reason else None,
        "blocking_text": d.blocking_reason.text if d.blocking_reason else None,
        "age_days": _age_days(d.job.posted_date, d.run.as_of_date),
        "seen_in_current_run": d.run.as_of_date.isoformat(),       # this run's as-of date; no history is tracked
        "source": _source(d.job.canonical_url), "posting_id": posting_id(d.job.canonical_url),
        "live": live_evidence(d, records), "compensation": pay, "compensation_status": pay_status,
        **_hold(d),
    }


def _hold(d: CareerCoachDecision) -> dict:
    """Why a HOLD exceeds the justified investment, read from the decision's own
    effort and investment fields (I-3): the effort dimension(s) at their maximum."""
    if d.next_action != "HOLD":
        return {"hold_causes": [], "hold_reason": None}
    causes = [c for c, at_max in (("resume", d.resume_effort == "HEAVY"), ("research", d.research_effort == "HIGH")) if at_max]
    parts = [f"{'resume effort HEAVY' if c == 'resume' else 'research effort HIGH'}" for c in causes]
    return {"hold_causes": causes,
            "hold_reason": f"{' and '.join(parts)} exceeds the {d.application_investment.justified} investment "
                           f"that {d.application_priority} allows"}


def needs_resume_work(i: dict) -> bool:
    """Resume Work Queue membership: a job in today's queue whose resume effort
    is TARGETED or HEAVY, or a HOLD whose cause includes resume effort. A HOLD
    caused only by research effort is not resume work."""
    if i["section"] == "TODAY":
        return i["resume_effort"] in ("TARGETED", "HEAVY")
    if i["section"] == "ON_HOLD":
        return "resume" in i["hold_causes"]
    return False


# ------------------------------------------- workspace labels (display-only)
# Fixed relabellings of existing placement / next-action / hold / live fields.
# None of them is a decision input or a new classification.

ACTION_BY_SECTION = {"TODAY": "APPLY / PREPARE", "VERIFY_FIRST": "VERIFY", "ON_HOLD": "HOLD",
                     "DO_NOT_INVEST": "STOP", "HARD_FILTER_REJECTED": "STOP"}


def action_label(i: dict) -> str:
    """TODAY with next action TAILOR_RESUME -> RESUME WORK; any other TODAY next
    action -> APPLY / PREPARE; VERIFY_FIRST -> VERIFY; ON_HOLD -> HOLD;
    DO_NOT_INVEST and HARD_FILTER_REJECTED -> STOP."""
    if i["section"] == "TODAY" and i["next_action"] == "TAILOR_RESUME":
        return "RESUME WORK"
    return ACTION_BY_SECTION[i["section"]]


def needs_research_work(i: dict) -> bool:
    """The research counterpart of needs_resume_work: a job in today's queue
    whose next action is RESEARCH_EMPLOYER, or a HOLD whose cause includes
    research effort."""
    if i["section"] == "TODAY":
        return i["next_action"] == "RESEARCH_EMPLOYER"
    if i["section"] == "ON_HOLD":
        return "research" in i["hold_causes"]
    return False


def evidence_status(i: dict) -> str:
    """SG live evidence only: Current = the job's current posting ID is covered by
    an eligible live record; Missing = it is not; Not applicable = the SG live
    rule does not evaluate this job. Says nothing about whether to apply."""
    if i["live"] is None:
        return "Not applicable"
    return "Current" if i["live"]["state"] == "CURRENT" else "Missing"


def workload(dashboard: dict) -> dict:
    """Counts of existing work categories — no weighting, no optimisation."""
    c, items = dashboard["counts"], dashboard["all"]
    return {"apply_prepare": c["TODAY"], "verify": c["VERIFY_FIRST"], "resume_work": len(dashboard["resume_work"]),
            "research_work": sum(needs_research_work(i) for i in items), "on_hold": c["ON_HOLD"],
            "stop": c["DO_NOT_INVEST"], "rejected": c["HARD_FILTER_REJECTED"]}


# --------------------------------------------- run status (display-only)

def live_summary(items: list[dict], meta: dict) -> dict:
    """Coverage against the CURRENT SG MyCareersFuture passed population: a job
    is covered only when its eligible live record observed its current posting."""
    sg = [i for i in items if i["live"] is not None]
    covered = [i for i in sg if i["live"]["state"] == "CURRENT"]
    dates = sorted(i["live"]["checked_on"] for i in covered if i["live"]["checked_on"])
    window = meta.get("live_check_window_days")
    return {"sg_passed": len(sg), "covered": len(covered), "missing": len(sg) - len(covered),
            "missing_no_record": sum(i["live"]["state"] == "NONE" for i in sg),
            "missing_other_posting": sum(i["live"]["state"] == "OTHER_POSTING" for i in sg),
            "latest_observation": dates[-1] if dates else None,
            "valid_through": (date.fromisoformat(dates[0]) + timedelta(days=window)).isoformat()
                             if dates and window is not None else None,
            "window_days": window, "historical_eligible_records": len(meta.get("live_checks_used", []))}


def _change(job_id, before, after, ref) -> dict:
    return {"job_id": job_id, "company": ref.job.company, "title": ref.job.job_title, "before": before, "after": after}


def delta(decisions: list[CareerCoachDecision], previous: dict | None) -> dict:
    """Descriptive differences from the previous comparable operational run.
    `previous` is {"status": "COMPARED", "archive_id", "as_of", "decisions":
    {job_id: decision}, "earlier_job_ids": set, "notes": [...]} or
    {"status": "UNAVAILABLE", "reason": str}. Nothing is inferred:
      new             in the current run; not in the previous run nor in any
                      earlier comparable operational archive
      reappeared      in the current run; not in the previous run; in an
                      earlier comparable operational archive
      removed         in the previous run; not in the current run
      posting_changed in both runs; the posting identity (posting ID, else the
                      canonical URL) differs
    A job present in both runs is never new or reappeared."""
    if not previous or previous.get("status") != "COMPARED":
        return {"available": False, "reason": (previous or {}).get("reason", "no previous run was supplied to the renderer")}
    prev, cur = previous["decisions"], {d.job.job_id: d for d in decisions}
    earlier = previous.get("earlier_job_ids", set())
    absent = sorted(set(cur) - set(prev))
    common = sorted(set(cur) & set(prev))

    def changed(field):
        return [_change(j, field(prev[j]), field(cur[j]), cur[j]) for j in common if field(prev[j]) != field(cur[j])]

    out = {"available": True, "archive_id": previous["archive_id"], "as_of": previous["as_of"],
           "notes": list(previous.get("notes", [])),
           "new": [_change(j, None, None, cur[j]) for j in absent if j not in earlier],
           "reappeared": [_change(j, None, None, cur[j]) for j in absent if j in earlier],
           "removed": [_change(j, None, None, prev[j]) for j in sorted(set(prev) - set(cur))],
           "posting_changed": changed(posting_identity),
           "viability_changed": changed(lambda d: d.application_viability.state),
           "priority_changed": changed(lambda d: d.application_priority),
           "next_action_changed": changed(lambda d: d.next_action),
           "placement_changed": changed(primary_section)}
    fields = (("viability", "viability_changed"), ("priority", "priority_changed"),
              ("next action", "next_action_changed"), ("placement", "placement_changed"))
    moved = {c["job_id"] for _, k in fields for c in out[k]}
    out["unchanged"] = sum(1 for j in common if j not in moved)
    out["unchanged_jobs"] = [_change(j, None, None, cur[j]) for j in common if j not in moved]
    out["decision_changed"] = [{**_change(j, None, None, cur[j]),
                                "changes": [(label, c["before"], c["after"]) for label, k in fields
                                            for c in out[k] if c["job_id"] == j]} for j in common if j in moved]
    return out


# ------------------------------------------------ job browser (display-only)
# One row per Coach job_id across the earlier comparable runs and this run.
# A row shows the job's LATEST stored decision (from the latest run that holds
# it) and, separately, every placement it ever had. Nothing is recomputed: each
# value is read from the decision of the run it belongs to. The browser is a
# view over runs only; it never reads the application log (an application view
# will be a separate dataset, so an applied job stays findable whatever its
# latest placement).

RECENT_DAYS = 7
DATE_PRESETS = (7, 30, 90)
BROWSER_FILTERS = (("decision", "Coach decision (latest)"), ("jobtype", "Job type (Coach track)"),
                   ("employer", "Employer type"), ("resume", "Resume effort"), ("source", "Source"),
                   ("ever", "Ever placed (any run)"))


def _browser_row(job_id: str, path: list[tuple[str, str, CareerCoachDecision]], as_of: date, current: bool) -> dict:
    last_as_of, _, latest = path[-1]
    cv = latest.career_value
    postings = sorted({posting_identity(d) for _, _, d in path})
    return {"job_id": job_id, "company": latest.job.company, "title": latest.job.job_title, "market": latest.job.country,
            "url": latest.job.canonical_url, "source": _source(latest.job.canonical_url),
            "decision": primary_section(latest), "priority": latest.application_priority, "next_action": latest.next_action,
            "jobtype": (cv.deciding_track if cv else None) or "—",
            "employer": latest.practical_constraint.components.get("employer", "—") if latest.practical_constraint else "—",
            "resume": latest.resume_effort or "—",
            "first_run": path[0][0], "latest_run": last_as_of, "in_current_run": current,
            "set": "recent" if date.fromisoformat(last_as_of) >= as_of - timedelta(days=RECENT_DAYS) else "historical",
            "path": [(a, primary_section(d)) for a, _, d in path], "ever": sorted({primary_section(d) for _, _, d in path}),
            "postings": postings}


def job_browser(decisions: list[CareerCoachDecision], history: dict | None) -> dict:
    """history: operational.history_runs() output, or None. The current run is
    always the latest run; earlier runs come only from that verified history."""
    if not decisions:
        return {"available": False, "rows": [], "runs": [], "notes": [], "reason": "no decisions"}
    as_of = decisions[0].run.as_of_date
    runs = [(r["as_of"], r["archive_id"], r["decisions"]) for r in (history or {}).get("runs", [])]
    runs.append((as_of.isoformat(), "current run", decisions))
    paths: dict[str, list] = {}
    for run_as_of, run_id, run_decisions in runs:                       # runs are in as-of order
        for d in run_decisions:
            paths.setdefault(d.job.job_id, []).append((run_as_of, run_id, d))
    current_ids = {d.job.job_id for d in decisions}
    rows = [_browser_row(j, p, as_of, j in current_ids) for j, p in paths.items()]
    rows.sort(key=lambda r: ("".join(chr(0x10FFFF - ord(c)) for c in r["latest_run"]), SECTIONS.index(r["decision"]),
                             r["company"], r["job_id"]))
    by_id = {d.job.job_id: d for d in decisions}
    for r in rows:                                                      # the current run's placement is authoritative
        if r["in_current_run"] and r["decision"] != primary_section(by_id[r["job_id"]]):
            raise ValueError(f"job browser does not reconcile: {r['job_id']} latest decision differs from this run")
    if len({r["job_id"] for r in rows}) != len(rows):
        raise ValueError("job browser does not reconcile: a job appears twice")
    return {"available": bool(history and history.get("status") == "AVAILABLE"), "rows": rows,
            "runs": [(a, i) for a, i, _ in runs], "notes": list((history or {}).get("notes", [])),
            "reason": (history or {}).get("reason", "no history was supplied to the renderer"),
            "as_of": as_of.isoformat(), "recent_days": RECENT_DAYS,
            "cutoffs": {n: (as_of - timedelta(days=n)).isoformat() for n in DATE_PRESETS}}


def build(decisions: list[CareerCoachDecision], meta: dict, context: dict | None = None) -> dict:
    """context (display-only): {"live_records": {record_id: LiveCheckRecord} | None,
    "previous": see delta(), "archive": {"status": ..., "archive_id": ...}}."""
    context = context or {}
    records = context.get("live_records")
    items = sorted((item(d, meta.get("positionings", {}), records) for d in decisions), key=_sort_key)
    window = meta.get("live_check_window_days")
    audit = context.get("audit")                      # the run manifest, when the run has one (display-only)
    warnings = (audit or {}).get("audit", {}).get("job_warnings", {})
    for i in items:                                   # display-only labels from existing fields
        i["action"], i["evidence_status"] = action_label(i), evidence_status(i)
        i["audit_warnings"] = warnings.get(i["job_id"], [])
        if i["live"] is not None and i["live"].get("checked_on") and window is not None:
            i["live"]["valid_through"] = (date.fromisoformat(i["live"]["checked_on"]) + timedelta(days=window)).isoformat()
    sections = {s: [i for i in items if i["section"] == s] for s in SECTIONS}
    run = decisions[0].run.model_dump(mode="json") if decisions else {}
    live = live_summary(items, meta)
    dashboard = {"run": run, "meta": meta,
                 "sections": sections, "all": items,
                 "resume_work": [i for i in items if needs_resume_work(i)],
                 "counts": {s: len(v) for s, v in sections.items()},
                 "priority_counts": dict(Counter(d.application_priority for d in decisions)),
                 "action_counts": dict(Counter(d.next_action for d in decisions)),
                 "live": live, "delta": delta(decisions, context.get("previous")),
                 "browser": job_browser(decisions, context.get("history")), "audit": audit,
                 "health": {"as_of": run.get("as_of_date"), "coach_version": run.get("coach_version"),
                            "raw_corpus": run.get("input_hashes", {}).get("raw_corpus"),
                            "config": run.get("input_hashes", {}).get("config"), "jobs": len(decisions),
                            "archive": context.get("archive") or {"status": "NOT CHECKED"}}}
    dashboard["workload"] = workload(dashboard)
    reconcile(dashboard, decisions)
    return dashboard


def reconcile(dashboard: dict, decisions: list[CareerCoachDecision]) -> None:
    """Totals must match the decisions exactly; nothing may appear twice or vanish."""
    ids = [d.job.job_id for d in decisions]
    placed = [i["job_id"] for s in SECTIONS for i in dashboard["sections"][s]]
    problems = []
    if len(ids) != len(set(ids)):
        problems.append("duplicate job ids in the decisions")
    if sorted(placed) != sorted(ids):
        problems.append(f"sections hold {len(placed)} jobs for {len(ids)} decisions")
    if sorted(i["job_id"] for i in dashboard["all"]) != sorted(ids):
        problems.append("the full opportunity view does not hold every decision")
    if sum(dashboard["counts"].values()) != len(ids):
        problems.append("section counts do not sum to the decision count")
    if dashboard["priority_counts"] != dict(Counter(d.application_priority for d in decisions)):
        problems.append("priority counts do not match the decisions")
    by_id = {d.job.job_id: d for d in decisions}
    for i in dashboard["all"]:
        d = by_id[i["job_id"]]
        if (i["priority"], i["next_action"], i["why_rule"]) != (d.application_priority, d.next_action, d.primary_reason.rule_id):
            problems.append(f"{i['job_id']}: displayed values differ from its decision")
    for i in dashboard["resume_work"]:
        if not needs_resume_work(i) or i["resume_effort"] not in ("TARGETED", "HEAVY"):
            problems.append(f"{i['job_id']}: in the Resume Work Queue without resume work")
    if [i["job_id"] for i in dashboard["resume_work"]] != [i["job_id"] for i in dashboard["all"] if needs_resume_work(i)]:
        problems.append("the Resume Work Queue does not match the jobs that need resume work")
    if problems:
        raise ValueError("dashboard does not reconcile: " + "; ".join(problems))


# ----------------------------------------------------------------- rendering

def _investment(i: dict) -> str:
    return f"{i['justified_investment']} allowed · {i['required_investment'] or '—'} needed · {i['investment_fit']}"


def _cv(i: dict) -> str:
    return f"{i['career_value']}" + (f" · {i['declared_tier']} · {i['track']}" if i["track"] else "")


def _posted(i: dict) -> str:
    if not i["posted_date"]:
        return "not recorded"
    return i["posted_date"] + (f" ({i['age_days']} days before as-of)" if i["age_days"] is not None else "")


def _live_text(live: dict | None) -> str | None:
    """One line of live evidence; missing evidence is never shown as an observation."""
    if live is None:
        return None
    if live["state"] == "NONE":
        return (f"NONE — decision uses the stored-JD fallback [{live['rule']}: {live['detail']}]; "
                f"current posting ID {live['current_posting_id'] or NOT_RECORDED}")
    seen = (f"page reviewable {live['page_reviewable']} · email in JD {live['email_in_jd']} · status {live['posting_status']}"
            if live["page_reviewable"] is not None else "observed values not available in this render")
    posting = ("current posting" if live["state"] == "CURRENT"
               else f"not the current posting ID {live['current_posting_id'] or NOT_RECORDED} — not current coverage")
    return f"checked {live['checked_on']} · posting {live['observed_posting_id']} ({posting}) · {seen} [{live['rule']}]"


def _blocker(i: dict) -> str | None:
    if i["hold_reason"]:
        return f"Hold because: {i['hold_reason']}"
    if i["verification_code"]:
        return (f"Verify ({VERIFY_LABEL[i['verification_code']]}): {i['verification']} "
                f"→ if confirmed: {i['if_confirmed']}; if not: {i['if_not']}")
    if i["blocking_text"]:
        return f"Blocked [{i['blocking_rule']}]: {i['blocking_text']}"
    return None


def audit_lines(dashboard: dict) -> list[str]:
    """The run's audit verdict and its non-passing checks, from the run manifest."""
    audit = dashboard.get("audit")
    if audit is None:
        return ["- Audit verdict: not recorded for this run (no run manifest)"]
    result = audit["audit"]
    cleared = "cleared" if result["verdict"] == "PASS" else "NOT cleared, review the findings"
    lines = [f"- Audit verdict: {result['verdict']} ({cleared}) · run stage {audit['run_stage']} · "
             f"job-level warnings: {len(result['job_warnings'])}"]
    return lines + [f"  - {g['status']} {g['gate']}: {g['detail']}" for g in result["gates"] if g["status"] != "PASS"]


def _verdict_html(dashboard: dict) -> str:
    audit = dashboard.get("audit")
    if audit is None:
        return "<p class=verdict>Audit verdict: not recorded for this run (no run manifest)</p>"
    result = audit["audit"]
    found = "".join(f"<li>{html.escape(g['status'])} {html.escape(g['gate'])}: {html.escape(g['detail'])}</li>"
                    for g in result["gates"] if g["status"] != "PASS")
    cleared = "cleared" if result["verdict"] == "PASS" else "NOT cleared, review the findings"
    return (f"<div class=\"verdict verdict-{html.escape(result['verdict'])}\"><b>Audit verdict: {html.escape(result['verdict'])}</b> "
            f"({cleared}) · run stage {html.escape(audit['run_stage'])} · job-level warnings: {len(result['job_warnings'])}"
            + (f"<ul>{found}</ul>" if found else "") + "</div>")


def run_status_lines(dashboard: dict) -> list[str]:
    h, live, dl = dashboard["health"], dashboard["live"], dashboard["delta"]
    archive = h["archive"]
    lines = ["## Run status", "", "### Run health", "",
             f"- As of: {h['as_of']} · coach `{h['coach_version']}` · config `{(h['config'] or '')[:12]}` · "
             f"raw corpus `{(h['raw_corpus'] or '')[:12]}`",
             *audit_lines(dashboard),
             f"- Jobs evaluated: {h['jobs']} · archive: {archive['status']}"
             + (f" (`{archive['archive_id']}`)" if archive.get("archive_id") else "")
             + (f" — {archive['reason']}" if archive.get("reason") else ""),
             f"- SG MyCareersFuture passed: {live['sg_passed']} · current SG live coverage: {live['covered']} / {live['sg_passed']}"
             f" · latest live observation: {live['latest_observation'] or '—'} · evidence valid through: {live['valid_through'] or '—'}",
             "", "### Live evidence", "",
             f"- Current SG MyCareersFuture passed jobs: {live['sg_passed']}",
             f"- Current postings with a current check: {live['covered']}",
             f"- Coverage: {live['covered']} / {live['sg_passed']}",
             f"- Missing current evidence: {live['missing']} (no live record, stored-JD fallback: {live['missing_no_record']}; "
             f"record for a different posting: {live['missing_other_posting']})",
             f"- Latest observation: {live['latest_observation'] or '—'}",
             f"- Evidence valid through: {live['valid_through'] or '—'} (earliest expiry among the current checks; "
             f"window {live['window_days']} days)",
             f"- Historical eligible live records: {live['historical_eligible_records']} (every eligible record, "
             f"including jobs no longer in the corpus — not coverage)", ""]
    if not dl["available"]:
        return lines + ["### Changes since the previous run", "", f"{NO_COMPARISON} {dl['reason']}", ""]
    lines += [f"### Changes since {dl['as_of']}", "", f"Compared with `{dl['archive_id']}`."
              + (" " + " ".join(dl["notes"]) if dl["notes"] else ""), "",
              "| Change | Jobs |", "|---|---|"]
    rows = [("New jobs", "new"), ("Removed", "removed"), ("Reappeared", "reappeared"),
            ("Canonical posting changed", "posting_changed"), ("Viability changed", "viability_changed"),
            ("Priority changed", "priority_changed"), ("Next action changed", "next_action_changed"),
            ("Placement changed", "placement_changed")]
    lines += [f"| {label} | {len(dl[key])} |" for label, key in rows] + [f"| Decision unchanged | {dl['unchanged']} |", ""]
    for label, key in rows:
        for c in dl[key]:
            change = f": {c['before']} → {c['after']}" if c["before"] is not None else ""
            lines.append(f"- {label}: {c['company']} · {c['title']} (`{c['job_id']}`){change}")
    return lines + [""]


def header_lines(dashboard: dict) -> list[str]:
    run, meta, c = dashboard["run"], dashboard["meta"], dashboard["counts"]
    decl = meta.get("declarations", {})
    affected = meta.get("profile_items_affected_jobs", {})
    ignored = Counter(reason for _, reason in meta.get("live_checks_ignored", []))
    live = dashboard["live"]
    lines = [f"# Career Coach — {run.get('run_kind', '')} as of {run.get('as_of_date', '')}", "",
             f"`{run.get('coach_version', '')}` · config `{run.get('input_hashes', {}).get('config', '')[:12]}` · "
             f"current SG live coverage: {live['covered']} / {live['sg_passed']} · "
             f"historical eligible live records: {live['historical_eligible_records']} (window {meta.get('live_check_window_days')} days)"
             + (f" · ignored: {dict(ignored)}" if ignored else ""), ""]
    lines += run_status_lines(dashboard)
    lines += ["## What to work on today", "",
              f"- **Apply / prepare now:** {c['TODAY']} "
              f"({', '.join(f'{label.lower()}: {sum(1 for i in dashboard['sections']['TODAY'] if i['next_action'] == a)}' for a, label in APPLY_GROUPS)})",
              f"- **Verify before spending time:** {c['VERIFY_FIRST']}",
              f"- **On hold (effort exceeds what the job justifies):** {c['ON_HOLD']} "
              f"(resume-effort holds: {sum('resume' in i['hold_causes'] for i in dashboard['sections']['ON_HOLD'])}, "
              f"research-effort-only holds: {sum(i['hold_causes'] == ['research'] for i in dashboard['sections']['ON_HOLD'])})",
              f"- **Resume work required:** {len(dashboard['resume_work'])}",
              f"- **Research work required:** {dashboard['workload']['research_work']} "
              f"(today's RESEARCH_EMPLOYER jobs and holds that include research effort)",
              f"- **Stop spending time:** {c['DO_NOT_INVEST']} (+ {c['HARD_FILTER_REJECTED']} rejected by the hard filter)",
              f"- **Total:** {sum(c.values())} jobs evaluated", "",
              "## Profile declarations to resolve", ""]
    rvr = decl.get("resume_variants_ready", "absent")
    if rvr is None:
        lines.append(f"- **`resume_variants_ready: null`** — not declared. No resume positioning is treated as ready, "
                     f"so every targeted job needs at least TARGETED resume work (E-R2). Declaring it would change the "
                     f"decision of {affected.get('PD-RESUME_VARIANTS', 0)} job(s) in this run.")
    unknown_auth = sorted(k for k, v in decl.get("work_authorization", {}).items() if v == "UNKNOWN")
    if unknown_auth:
        lines.append(f"- **Work authorization UNKNOWN** for {', '.join(unknown_auth)} — changes the decision of "
                     f"{affected.get('PD-WORK_AUTH', 0)} job(s) if declared.")
    exp = decl.get("experience", {})
    lines.append(f"- Declared relevant experience: DS {exp.get('relevant_ds_years')} yrs, ML {exp.get('relevant_ml_years')} yrs "
                 f"(tenure {exp.get('current_role_years')} yrs is never compared).")
    sen = decl.get("seniority", {})
    lines.append(f"- Declared levels: aligned {sen.get('aligned_levels')}, stretch {sen.get('stretch_levels')}.")
    return lines + [""]


def _job_block(i: dict) -> list[str]:
    """Operational order: what to do, why, what blocks it, effort — then detail."""
    lines = [f"**{i['company']} · {i['title']}** ({i['market']}) — `{i['job_id']}`",
             f"- Priority **{i['priority']}** · Next action **{i['next_action']}** · Action **{i['action']}**"
             f" · Evidence status: {i['evidence_status']}",
             f"- Why here: {i['why']} [{i['why_rule']}]"]
    lines += [f"- Audit warning: {w}" for w in i["audit_warnings"]]
    blocker = _blocker(i)
    if blocker:
        lines.append(f"- {blocker}")
    lines += [f"- Effort: resume {i['resume_effort']}, research {i['research_effort']} · Investment: {_investment(i)}",
              f"- Career value: {_cv(i)} · viability {i['viability']}",
              f"- Resume track: {i['resume_track']}",
              f"- Readiness: {i['readiness']} · seniority {i['seniority']} · experience {i['experience']}"]
    if i["uncertainty"] != "RESOLVED" or i["gaps"]:
        lines.append(f"- Uncertainty: {i['uncertainty']}" + (f" · evidence gaps ({len(i['gaps'])}): {', '.join(i['gaps'][:6])}"
                                                           + (" …" if len(i["gaps"]) > 6 else "") if i["gaps"] else ""))
    lines.append(f"- Job facts: posted {_posted(i)} · seen in current run {i['seen_in_current_run']} · source {i['source']}"
                 f" · canonical posting ID {i['posting_id'] or NOT_RECORDED}")
    live = _live_text(i["live"])
    if live:
        lines.append(f"- Live evidence: {live}")
    lines.append(f"- Compensation: {i['compensation']} · status {i['compensation_status']}")
    return lines + [""]


def _collapse(reason: str) -> str:
    return re.sub(r"\d+", "#", reason)


def render_markdown(dashboard: dict) -> str:
    s = dashboard["sections"]
    lines = header_lines(dashboard)

    lines += [f"## 1. {SECTION_TITLES['TODAY']} ({len(s['TODAY'])})", ""]
    if not s["TODAY"]:
        lines += ["_No job is currently viable, targeted and ready for application effort under the frozen rules._", ""]
    for action, label in APPLY_GROUPS:
        group = [i for i in s["TODAY"] if i["next_action"] == action]
        if group:
            lines += [f"### {label} ({len(group)})", ""]
            for i in group:
                lines += _job_block(i)

    lines += [f"## 2. Verify First ({len(s['VERIFY_FIRST'])})", ""]
    for code, label in VERIFY_LABEL.items():
        group = [i for i in s["VERIFY_FIRST"] if i["verification_code"] == code]
        if group:
            lines += [f"### {label} ({len(group)})", ""]
            for i in group:
                lines += _job_block(i)

    resume_jobs = dashboard["resume_work"]
    resume_holds = [i for i in resume_jobs if i["section"] == "ON_HOLD"]
    ready = dashboard["meta"].get("declarations", {}).get("resume_variants_ready")
    lines += [f"## 3. Resume Work Queue ({len(resume_jobs)} jobs: {len(resume_jobs) - len(resume_holds)} to tailor, "
              f"{len(resume_holds)} on hold for resume effort)", "",
              "_Jobs that need resume work: today's jobs with TARGETED or HEAVY resume effort, and holds caused by "
              "resume effort. Holds caused only by research effort are listed in the Full Opportunity View._", ""]
    if ready is None:
        lines += ["_`resume_variants_ready` is not declared, so no positioning is marked ready._", ""]
    groups: dict[str, list[dict]] = {}
    for i in resume_jobs:
        groups.setdefault(i["positioning"] or "NONE", []).append(i)
    for pos in sorted(groups):
        group = groups[pos]
        label = dashboard["meta"].get("positionings", {}).get(pos, "No resume positioning")
        shared = [g for g, n in Counter(g for i in group for g in i["gaps"]).most_common() if n >= 2][:5]
        lines += [f"### {label} — variant ready: {'yes' if ready and pos in ready else 'not declared' if ready is None else 'no'}"
                  f" ({len(group)})", ""]
        if shared:
            lines += [f"Shared evidence gaps: {', '.join(shared)}", ""]
        for i in group:
            lines += [f"- {i['company']} · {i['title']} — **{i['next_action']}** · resume {i['resume_effort']}, "
                      f"research {i['research_effort']} · {_investment(i)} · {i['why']}"]
        lines.append("")
    if resume_holds:
        lines += [f"### On hold for resume effort — detail ({len(resume_holds)})", ""]
        for i in resume_holds:
            lines += _job_block(i)

    lines += [f"## 4. Do Not Invest ({len(s['DO_NOT_INVEST'])} + {len(s['HARD_FILTER_REJECTED'])} hard-filter rejections)", ""]
    for rule, group in sorted(_group(s["DO_NOT_INVEST"], "why_rule").items()):
        lines += [f"### {rule} ({len(group)})", ""]
        for i in group:
            lines.append(f"- {i['company']} · {i['title']} ({i['market']}) — {i['why']} · career value {_cv(i)}")
        lines.append("")
    reasons = Counter(_collapse(r) for i in s["HARD_FILTER_REJECTED"] for r in i["blocking_text"].split("; "))
    lines += [f"### Rejected by the hard filter ({len(s['HARD_FILTER_REJECTED'])} jobs; a job can have several reasons)", "",
              "| Reason | Jobs |", "|---|---|"] + [f"| {r} | {n} |" for r, n in reasons.most_common()] + [""]

    lines += [f"## 5. Full Opportunity View ({len(dashboard['all'])})", ""]
    other_holds = [i for i in s["ON_HOLD"] if not needs_resume_work(i)]
    if other_holds:
        lines += [f"### On hold — not resume work ({len(other_holds)})", ""]
        for i in other_holds:
            lines += _job_block(i)
        lines += ["### All jobs", ""]
    lines += ["| Section | Company · Title | Market | Viability | Career value · tier | Priority | Next action | Resume | Research | Investment fit | Readiness | Verify | Posted |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for sec in SECTIONS:
        for i in s[sec]:
            lines.append(f"| {sec} | {i['company']} · {i['title']} | {i['market']} | {i['viability']} | {_cv(i)} | "
                         f"{i['priority']} | {i['next_action']} | {i['resume_effort']} | {i['research_effort']} | "
                         f"{i['investment_fit']} | {i['readiness']} | {VERIFY_LABEL.get(i['verification_code'], '—')} | "
                         f"{i['posted_date'] or '—'} |")
    return "\n".join(lines) + "\n"


def _group(items: list[dict], key: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for i in items:
        out.setdefault(i[key], []).append(i)
    return out


CSV_COLUMNS = ("job_id", "section", "company", "title", "market", "scope", "viability", "career_value", "declared_tier",
               "track", "priority", "next_action", "why_rule", "why", "resume_effort", "research_effort",
               "justified_investment", "required_investment", "investment_fit", "resume_track", "readiness", "seniority",
               "experience", "verification_code", "verification", "uncertainty", "gaps", "resume_work_required",
               "hold_reason", "url", "posted_date", "age_days", "seen_in_current_run", "source", "posting_id", "live_state",
               "live_checked_on", "live_observed_posting_id", "live_page_reviewable", "live_email_in_jd",
               "live_posting_status", "compensation", "compensation_status", "action", "evidence_status")


def render_csv(dashboard: dict) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for i in dashboard["all"]:
        live = i["live"] or {}
        writer.writerow({**i, "gaps": "; ".join(i["gaps"]), "resume_work_required": "YES" if needs_resume_work(i) else "NO",
                         "live_state": live.get("state", ""), "live_checked_on": live.get("checked_on") or "",
                         "live_observed_posting_id": live.get("observed_posting_id") or "",
                         "live_page_reviewable": live.get("page_reviewable") or "",
                         "live_email_in_jd": live.get("email_in_jd") or "",
                         "live_posting_status": live.get("posting_status") or ""})
    return out.getvalue()


FILTERS = ("market", "section", "priority", "career_value", "declared_tier", "positioning", "readiness", "seniority",
           "resume_effort", "research_effort", "investment_fit", "verification_code", "viability", "scope")
SEARCH_FIELDS = ("company", "title", "market", "section", "viability", "career_value", "declared_tier", "track",
                 "positioning", "priority", "next_action", "verification", "resume_track", "why", "hold_reason")


def search_text(i: dict) -> str:
    """The lower-cased text the keyword search matches (displayed fields only)."""
    return " ".join(str(i.get(f) or "") for f in SEARCH_FIELDS).lower()


def _dl(pairs) -> str:
    """Rows of an HTML definition list; values are pre-escaped HTML."""
    return "<dl>" + "".join(f"<dt>{html.escape(k)}</dt><dd>{v}</dd>" for k, v in pairs if v not in (None, "")) + "</dl>"


def _detail_html(i: dict) -> str:
    """Expanded job detail: Decision · Readiness · Verification · Job facts ·
    Live evidence (SG only). Every value is an existing field, shown once."""
    e = html.escape

    def group(title, pairs):
        return f"<div class=grp><h4>{e(title)}</h4>{_dl(pairs)}</div>"

    gaps = ", ".join(i["gaps"]) if i["gaps"] else "none recorded"
    groups = [
        group("Decision", [("Viability", e(i["viability"])), ("Priority", e(i["priority"])),
                           ("Next action", e(i["next_action"])), ("Reason rule", e(f"{i['why_rule']}: {i['why']}")),
                           ("Career value", e(i["career_value"] + (f" · {i['track']}" if i["track"] else ""))),
                           ("Tier", e(i["declared_tier"])), ("Section", e(i["section"])), ("Action", e(i["action"]))]),
        group("Readiness", [("Positioning", e(i["resume_track"])), ("Resume readiness", e(i["readiness"])),
                            ("Resume effort", e(i["resume_effort"])), ("Research effort", e(i["research_effort"])),
                            ("Investment", e(_investment(i))), ("Seniority", e(i["seniority"])),
                            ("Relevant experience", e(i["experience"])), ("Evidence gaps", e(gaps))]),
        group("Verification", [("Verification code", e(i["verification_code"] or "none")),
                               ("Question", e(i["verification"])), ("If confirmed", e(i["if_confirmed"] or "")),
                               ("If not", e(i["if_not"] or "")), ("Uncertainty", e(i["uncertainty"])),
                               ("Blocker", e(_blocker(i) or "none recorded"))]),
        group("Job facts", [("Market", e(i["market"])), ("Posted", e(i["posted_date"] or NOT_RECORDED)),
                            ("Posted age", e(f"{i['age_days']} days before as-of" if i["age_days"] is not None else NOT_RECORDED)),
                            ("Seen in current run", e(i["seen_in_current_run"])), ("Source", e(i["source"])),
                            ("Canonical posting ID", e(i["posting_id"] or NOT_RECORDED)),
                            ("Job URL", f'<a href="{e(i["url"])}">{e(i["url"])}</a>' if i["url"] else NOT_RECORDED),
                            ("Compensation", e(f"{i['compensation']} · status {i['compensation_status']}"))]),
    ]
    live = i["live"]
    if live is not None:
        if live["state"] == "NONE":
            pairs = [("Coverage status", "Missing"), ("Live evidence", "NONE"),
                     ("Decision uses", e(f"stored-JD fallback [{live['rule']}]: {live['detail']}"))]
        else:
            unavailable = "not available in this render"
            pairs = [("Coverage status", e(i["evidence_status"])), ("Observed on", e(live["checked_on"] or "—")),
                     ("Valid through", e(live.get("valid_through") or "—")),
                     ("Posting identity match", "yes" if live["state"] == "CURRENT"
                      else e(f"no — observed {live['observed_posting_id'] or '—'}, current "
                             f"{live['current_posting_id'] or NOT_RECORDED} (not current coverage)")),
                     ("Page reviewable", e(live["page_reviewable"] or unavailable)),
                     ("Email in JD", e(live["email_in_jd"] or unavailable)),
                     ("Posting status", e(live["posting_status"] or unavailable)),
                     ("Rule", e(f"{live['rule']}: {live['detail']}"))]
        groups.append(group("Live evidence", pairs))
    return f"<details class=detail><summary>Detail</summary><div class=grps>{''.join(groups)}</div></details>"


def _badge(i: dict) -> str:
    slug = re.sub(r"[^a-z]+", "-", i["action"].lower()).strip("-")
    return f"<span class=\"act act-{slug}\">{html.escape(i['action'])}</span>"


def _evidence_chip(i: dict) -> str:
    slug = i["evidence_status"].lower().replace(" ", "-")
    return f"<span class=\"ev ev-{slug}\">Evidence: {html.escape(i['evidence_status'])}</span>"


def _effort(i: dict) -> str:
    return f"Resume: {i['resume_effort']} · Research: {i['research_effort']}"


def _card_html(i: dict) -> str:
    """Today / On hold item: action first, then why, blocker, effort — detail below."""
    e = html.escape
    gaps = (f"<p class=muted><b>Evidence gaps ({len(i['gaps'])})</b> {e(', '.join(i['gaps']))}</p>" if i["gaps"] else "")
    return (f"<article class=card data-job=\"{e(i['job_id'])}\"><div class=top>{_badge(i)}{_evidence_chip(i)}</div>"
            f"<h3><a href=\"{e(i['url'])}\">{e(i['company'])} · {e(i['title'])}</a></h3>"
            f"<p class=meta>Priority {e(i['priority'])} · Next action {e(i['next_action'])} · {e(i['market'])}</p>"
            f"<p><b>Why</b> {e(i['why'])} <span class=muted>[{e(i['why_rule'])}]</span></p>"
            + "".join(f"<p class=warn><b>Audit warning</b> {e(w)}</p>" for w in i["audit_warnings"]) +
            f"<p class=block><b>Blocker</b> {e(_blocker(i) or 'none recorded')}</p>{gaps}"
            f"<p class=effort>{e(_effort(i))}</p>{_detail_html(i)}</article>")


def _verify_html(items: list[dict]) -> str:
    """Verify First grouped by the decision's own verification code, in the
    dashboard's existing order within each group."""
    e = html.escape
    out = []
    for code, label in VERIFY_LABEL.items():
        group = [i for i in items if i["verification_code"] == code]
        if not group:
            continue
        rows = "".join(
            f"<tr class=vrow data-job=\"{e(i['job_id'])}\"><td><a href=\"{e(i['url'])}\">{e(i['company'])} · {e(i['title'])}</a>"
            f"<div class=why>{e(i['why'])} [{e(i['why_rule'])}]</div>"
            + "".join(f"<div class=warn>Audit warning: {e(w)}</div>" for w in i["audit_warnings"]) +
            f"{_detail_html(i)}</td>"
            f"<td><code>{e(code)}</code><div>{e(i['verification'])}</div>"
            f"<div class=muted>if confirmed: {e(i['if_confirmed'] or '—')} · if not: {e(i['if_not'] or '—')}</div></td>"
            f"<td data-label=Priority>{e(i['priority'])}</td><td data-label=\"Next action\">{e(i['next_action'])}</td>"
            f"<td data-label=\"Research effort\">{e(i['research_effort'])}</td>"
            f"<td data-label=Evidence>{_evidence_chip(i)}</td></tr>" for i in group)
        out.append(f"<h3 class=vgroup>{e(label)} verification ({len(group)})</h3><div class=wrap><table class=vtable>"
                   f"<thead><tr><th>Job · why</th><th>Verification</th><th>Priority</th><th>Next action</th>"
                   f"<th>Research effort</th><th>Evidence</th></tr></thead><tbody>{rows}</tbody></table></div>")
    unlisted = [i for i in items if i["verification_code"] not in VERIFY_LABEL]
    if unlisted:                                      # defensive: never drop a verify-first job from view
        out.append("<h3 class=vgroup>Other</h3><div class=cards>" + "".join(_card_html(i) for i in unlisted) + "</div>")
    return "".join(out) or "<p class=muted>None.</p>"


def _changes_html(dashboard: dict) -> str:
    e = html.escape
    dl = dashboard["delta"]
    if not dl["available"]:
        return f"<p>{e(NO_COMPARISON)} {e(dl['reason'])}</p>"

    def job(c):
        return f"{e(c['company'])} · {e(c['title'])} <span class=muted>({e(c['job_id'])})</span>"

    def listing(title, entries, render, open_=True):
        body = "".join(f"<li>{render(c)}</li>" for c in entries) or "<li class=muted>none</li>"
        return f"<details class=chg{' open' if open_ and entries else ''}><summary>{e(title)} ({len(entries)})</summary><ul>{body}</ul></details>"

    counts = [("New", len(dl["new"])), ("Reappeared", len(dl["reappeared"])), ("Removed", len(dl["removed"])),
              ("Posting changed", len(dl["posting_changed"])), ("Decision changed", len(dl["decision_changed"])),
              ("Unchanged", dl["unchanged"])]
    chips = "".join(f"<span class=chip>{e(k)} <b>{v}</b></span>" for k, v in counts)
    return (f"<p class=muted>Compared with {e(dl['archive_id'])} (as of {e(dl['as_of'])}). {e(' '.join(dl['notes']))}</p>"
            f"<div class=chips>{chips}</div>"
            + listing("New / Reappeared", [dict(c, kind="new") for c in dl["new"]]
                      + [dict(c, kind="reappeared") for c in dl["reappeared"]],
                      lambda c: f"<b>{'New' if c['kind'] == 'new' else 'Reappeared'}</b> {job(c)}")
            + listing("Removed", dl["removed"], job)
            + listing("Posting changed", dl["posting_changed"],
                      lambda c: f"{job(c)} — posting {e(str(c['before']))} → {e(str(c['after']))}")
            + listing("Decision changed", dl["decision_changed"],
                      lambda c: f"{job(c)} — " + "; ".join(f"{e(f)}: {e(str(b))} → {e(str(a))}" for f, b, a in c["changes"]))
            + listing("Unchanged", dl["unchanged_jobs"], job, open_=False))


def _path_text(path: list[tuple[str, str]]) -> str:
    """Placements in run order; consecutive runs with the same placement are one span."""
    spans: list[list[str]] = []
    for as_of, section in path:
        if spans and spans[-1][2] == section:
            spans[-1][1] = as_of
        else:
            spans.append([as_of, as_of, section])
    return " → ".join(f"{a}{'–' + b if b != a else ''} {s}" for a, b, s in spans)


# Browser filter script (display-only). It runs inside the page's single
# <script> block and does nothing unless the job browser table is present.
BROWSER_JS = """{const hb=document.getElementById('hb');
if(hb&&hb.querySelectorAll){
const hrows=[...hb.querySelectorAll('tr.hrow')],boxes=[...document.querySelectorAll('input[data-bfilter]')],
sets=[...document.querySelectorAll('input[name=hset]')],hdate=document.getElementById('hdate'),
hfrom=document.getElementById('hfrom'),hto=document.getElementById('hto');
function happly(){
const set=(sets.find(s=>s.checked)||{value:'recent'}).value,want={};
boxes.forEach(b=>{if(b.checked)(want[b.dataset.bfilter]=want[b.dataset.bfilter]||[]).push(b.value);});
const lo=hdate.value==='custom'?hfrom.value:hdate.value,hi=hdate.value==='custom'?hto.value:'';
let n=0,t=0;
hrows.forEach(r=>{const inSet=r.dataset.set===set;if(inSet)t++;
const ok=inSet&&Object.entries(want).every(([f,v])=>f==='ever'?r.dataset.ever.split(' ').some(x=>v.includes(x)):v.includes(r.dataset[f]))
&&(!lo||r.dataset.seen>=lo)&&(!hi||r.dataset.seen<=hi);
r.hidden=!ok;if(ok)n++;});
document.querySelectorAll('[data-bsum]').forEach(s=>{const k=(want[s.dataset.bsum]||[]).length;s.textContent=k?k+' selected':'ALL';});
document.getElementById('hbcount').textContent=`Showing ${n} of ${t} jobs`;}
[...boxes,...sets,hdate,hfrom,hto].forEach(e=>e.addEventListener('change',happly));happly();}}"""


def _posting_label(identity: str) -> str:
    """A MyCareersFuture posting ID shortened to its last 12 characters; any other
    posting identity (a URL) shown as its last path segment."""
    return identity[-12:] if _POSTING_ID.fullmatch(identity) else urlparse(identity).path.rstrip("/").rsplit("/", 1)[-1]


def _browser_html(dashboard: dict) -> str:
    """Job browser: Recent / Historical datasets with view-only filters. Every
    value shown is a stored decision value from the run it belongs to."""
    e = html.escape
    b = dashboard["browser"]
    rows = b["rows"]
    counts = Counter(r["set"] for r in rows)
    sets = (f"<label><input type=radio name=hset value=recent checked> Recent — in a run within the last {b['recent_days']} days "
            f"({counts['recent']})</label><label><input type=radio name=hset value=historical> Historical — "
            f"latest run earlier ({counts['historical']})</label>")
    groups = []
    for key, label in BROWSER_FILTERS:
        values = sorted({v for r in rows for v in (r["ever"] if key == "ever" else [r[key]])})
        boxes = "".join(f"<label><input type=checkbox data-bfilter=\"{key}\" value=\"{e(v)}\"> {e(v)}</label>" for v in values)
        groups.append(f"<details class=bf><summary>{e(label)}: <span data-bsum=\"{key}\">ALL</span></summary>"
                      f"<div class=bfv>{boxes}</div></details>")
    presets = "".join(f"<option value=\"{e(b['cutoffs'][n])}\">Last {n} days</option>" for n in DATE_PRESETS)
    dates = (f"<label>Latest run<select id=hdate><option value=\"\">ALL</option>{presets}<option value=custom>Custom</option>"
             f"</select></label><label>From<input type=date id=hfrom></label><label>To<input type=date id=hto></label>")
    body = "".join(
        f"<tr class=hrow data-job=\"{e(r['job_id'])}\" data-set=\"{r['set']}\" data-decision=\"{e(r['decision'])}\" "
        f"data-jobtype=\"{e(r['jobtype'])}\" data-employer=\"{e(r['employer'])}\" data-resume=\"{e(r['resume'])}\" "
        f"data-source=\"{e(r['source'])}\" data-ever=\"{e(' '.join(r['ever']))}\" data-seen=\"{e(r['latest_run'])}\">"
        f"<td><a href=\"{e(r['url'])}\">{e(r['company'])} · {e(r['title'])}</a> <span class=muted>({e(r['market'])})</span></td>"
        f"<td><b>{e(r['decision'])}</b><div class=muted>{e(r['priority'])} · {e(r['next_action'])}"
        f"{'' if r['in_current_run'] else ' · as of the ' + e(r['latest_run']) + ' run'}</div></td>"
        f"<td>{e(r['employer'])}</td><td>{e(r['jobtype'])}</td><td>{e(r['resume'])}</td><td>{e(r['source'])}</td>"
        f"<td>{e(r['first_run'])}</td><td>{e(r['latest_run'])}"
        f"{'' if r['in_current_run'] else '<div class=muted>not in this run</div>'}</td>"
        f"<td class=why>{e(_path_text(r['path']))}</td>"
        f"<td class=why>{len(r['postings'])}: {e(', '.join(_posting_label(p) for p in r['postings']))}</td></tr>" for r in rows)
    runs = ", ".join(f"{a} ({i})" for a, i in b["runs"])
    history = (f"Runs: {e(runs)}." if b["available"] else f"Only this run is shown. {e(b['reason'])}")
    notes = f" {e(' '.join(b['notes']))}" if b["notes"] else ""
    return (f"<p class=muted>One row per Coach job across the runs below; each row shows the job's latest stored "
            f"decision. Filters change only which rows are shown. {history}{notes} Postings: the representative posting "
            f"each run recorded for the job (other postings of a job are not part of the run output).</p>"
            f"<div class=bsets>{sets}</div><div class=filters>{''.join(groups)}{dates}<span id=hbcount></span></div>"
            f"<div class=wrap><table><thead><tr><th>Job</th><th>Latest decision</th><th>Employer</th><th>Job type</th>"
            f"<th>Resume effort</th><th>Source</th><th>First run</th><th>Latest run</th><th>Placements by run</th>"
            f"<th>Postings</th></tr></thead><tbody id=hb>{body}</tbody></table></div>")


def _status_html(dashboard: dict) -> str:
    h, live = dashboard["health"], dashboard["live"]
    archive = h["archive"]
    esc = lambda v: html.escape(str(v))                                    # noqa: E731
    health = _dl([("As of", esc(h["as_of"])), ("Coach version", esc(h["coach_version"])),
                  ("Raw corpus", esc((h["raw_corpus"] or "")[:12])), ("Jobs evaluated", esc(h["jobs"])),
                  ("Archive", esc(archive["status"] + (f" · {archive['archive_id']}" if archive.get("archive_id") else ""))),
                  ("SG passed", esc(live["sg_passed"])), ("Current SG live coverage", esc(f"{live['covered']} / {live['sg_passed']}")),
                  ("Latest live observation", esc(live["latest_observation"] or "—")),
                  ("Evidence valid through", esc(live["valid_through"] or "—"))])
    evidence = _dl([("Current SG MCF passed jobs", esc(live["sg_passed"])),
                    ("Current postings with current check", esc(live["covered"])),
                    ("Coverage", esc(f"{live['covered']} / {live['sg_passed']}")), ("Missing current evidence", esc(live["missing"])),
                    ("Latest observation", esc(live["latest_observation"] or "—")),
                    ("Evidence valid through", esc(live["valid_through"] or "—")),
                    ("Historical eligible live records (not coverage)", esc(live["historical_eligible_records"]))])
    return (f"<div class=stats><section class=stat><h3>Run health</h3>{health}</section>"
            f"<section class=stat><h3>Live evidence</h3>{evidence}</section></div>")


def render_html(dashboard: dict) -> str:
    e = html.escape
    md_sections = render_markdown(dashboard)
    s, run, c, w, live = dashboard["sections"], dashboard["run"], dashboard["counts"], dashboard["workload"], dashboard["live"]
    rows = []
    for i in dashboard["all"]:
        attrs = " ".join(f'data-{f}="{e(str(i.get(f) or "—"))}"' for f in FILTERS)
        rows.append(f"<tr {attrs} data-search=\"{e(search_text(i))}\"><td>{e(i['section'])}</td>"
                    f"<td><a href=\"{e(i['url'])}\">{e(i['company'])} · {e(i['title'])}</a>"
                    f"<div class=why>{e(i['why'])}{' · Hold because: ' + e(i['hold_reason']) if i['hold_reason'] else ''}</div>"
                    f"{_detail_html(i)}</td><td>{e(i['market'])}</td><td>{e(_cv(i))}</td>"
                    f"<td>{e(i['priority'])}</td><td>{e(i['next_action'])}</td><td>{e(i['resume_effort'])} / {e(i['research_effort'])}</td>"
                    f"<td>{e(VERIFY_LABEL.get(i['verification_code'], '—'))}</td><td>{e(i['posted_date'] or '—')}</td></tr>")
    selects = []
    for f in FILTERS:
        values = sorted({str(i.get(f) or "—") for i in dashboard["all"]})
        options = "".join(f"<option>{e(v)}</option>" for v in values)
        selects.append(f"<label>{e(f.replace('_', ' '))}<select data-filter=\"{f}\"><option value=\"\">all</option>{options}</select></label>")
    load = [("Apply / prepare now", w["apply_prepare"]), ("Verification work", w["verify"]),
            ("Resume work required", w["resume_work"]), ("Research work required", w["research_work"]),
            ("On hold", w["on_hold"]), ("Stop", f"{w['stop']} (+ {w['rejected']} rejected)")]
    workload_html = "".join(f"<span class=chip>{e(k)} <b>{e(str(v))}</b></span>" for k, v in load)
    archive = dashboard["health"]["archive"]
    status_line = (f"As of {run.get('as_of_date', '')} · archive {archive['status']} · current SG live coverage "
                   f"{live['covered']} / {live['sg_passed']} · evidence valid through {live['valid_through'] or '—'}")

    def section(n, title, body, open_):
        return f"<details class=sec id=s{n}{' open' if open_ else ''}><summary><h2>{n}. {e(title)}</h2></summary>{body}</details>"

    dni_body = "".join(
        f"<h3>{e(rule)} ({len(group)})</h3><ul>" + "".join(
            f"<li data-job=\"{e(i['job_id'])}\">{e(i['company'])} · {e(i['title'])} <span class=muted>({e(i['market'])}) — "
            f"{e(i['why'])}</span></li>" for i in group) + "</ul>"
        for rule, group in sorted(_group(s["DO_NOT_INVEST"], "why_rule").items())) or "<p class=muted>None.</p>"
    reasons = Counter(_collapse(r) for i in s["HARD_FILTER_REJECTED"] for r in i["blocking_text"].split("; "))
    rejected_body = ("<table><thead><tr><th>Reason</th><th>Jobs</th></tr></thead><tbody>"
                     + "".join(f"<tr><td>{e(r)}</td><td>{n}</td></tr>" for r, n in reasons.most_common()) + "</tbody></table><ul>"
                     + "".join(f"<li data-job=\"{e(i['job_id'])}\">{e(i['company'])} · {e(i['title'])} "
                               f"<span class=muted>({e(i['market'])})</span></li>" for i in s["HARD_FILTER_REJECTED"]) + "</ul>")
    full_body = (f"<div class=searchbar><input id=\"q\" type=\"search\" placeholder=\"Search company, title, priority, why…\" "
                 f"autocomplete=\"off\" aria-label=\"Search jobs\"><span id=\"count\"></span></div>"
                 f"<div class=\"filters\">{''.join(selects)}</div>"
                 f"<div class=\"wrap\"><table><thead><tr><th>Section</th><th>Job · why here</th><th>Market</th><th>Career value · tier</th>"
                 f"<th>Priority</th><th>Next action</th><th>Resume / research</th><th>Verify</th><th>Posted</th></tr></thead>"
                 f"<tbody id=full>{''.join(rows)}</tbody></table></div>")
    sections = [
        section(1, f"Today ({c['TODAY']})", f"<div class=cards>{''.join(_card_html(i) for i in s['TODAY']) or '<p class=muted>None.</p>'}</div>", True),
        section(2, f"Verify First ({c['VERIFY_FIRST']})", _verify_html(s["VERIFY_FIRST"]), True),
        section(3, f"On Hold ({c['ON_HOLD']})", f"<div class=cards>{''.join(_card_html(i) for i in s['ON_HOLD']) or '<p class=muted>None.</p>'}</div>", True),
        section(4, "Changes Since Previous Run", _changes_html(dashboard), True),
        section(5, f"Full Opportunity View ({len(dashboard['all'])})", full_body, True),
        section(6, f"Do Not Invest ({c['DO_NOT_INVEST']})", dni_body, False),
        section(7, f"Rejected by the hard filter ({c['HARD_FILTER_REJECTED']})", rejected_body, False),
        section(8, f"Job browser — recent and historical ({len(dashboard['browser']['rows'])})", _browser_html(dashboard), True),
    ]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Career Coach Dashboard</title><style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e3e0d8;--accent:#2f5d50;--card:#ffffff;--warn:#8a5a00;
--a-apply:#2f6b45;--a-resume:#2d5b8a;--a-verify:#8a5a00;--a-hold:#6b4f8a;--a-stop:#8a3b3b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#171715;--fg:#ecebe6;--muted:#a3a19a;--line:#33322e;--accent:#7fbfa9;--card:#1f1f1c;--warn:#e0b25c;
--a-apply:#7fcf98;--a-resume:#8fb8e8;--a-verify:#e0b25c;--a-hold:#c0a6e6;--a-stop:#e69a9a}}}}
*{{box-sizing:border-box}}body{{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1400px;padding:16px}}
h1{{font-size:20px;margin:0 0 4px}}h2{{font-size:17px;margin:0;display:inline}}h3{{font-size:14px;margin:8px 0 6px}}
h4{{font-size:12px;margin:0 0 4px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}}
pre{{white-space:pre-wrap;font:13px/1.5 ui-monospace,monospace;border:1px solid var(--line);padding:12px;border-radius:8px}}
.muted,.why{{color:var(--muted)}}.why{{font-size:12px}}a{{color:var(--accent)}}code{{font-size:12px}}
.sec{{border-top:1px solid var(--line);padding:12px 0}}.sec>summary{{cursor:pointer;margin-bottom:10px}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}}.chip{{border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:12px}}
.stats,.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}}
.stat,.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0}}
dl{{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;margin:0}}dt{{color:var(--muted)}}dd{{margin:0;overflow-wrap:anywhere}}
.card p{{margin:4px 0}}.top{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px}}
.act,.ev{{border:1px solid currentColor;border-radius:999px;padding:1px 8px;font-size:11px;font-weight:700;letter-spacing:.02em;white-space:nowrap}}
.act-apply-prepare{{color:var(--a-apply)}}.act-resume-work{{color:var(--a-resume)}}.act-verify{{color:var(--a-verify)}}
.act-hold{{color:var(--a-hold)}}.act-stop{{color:var(--a-stop)}}.ev{{color:var(--muted);font-weight:500}}.ev-current{{color:var(--a-apply)}}.ev-missing{{color:var(--a-stop)}}
.meta{{color:var(--muted);font-size:12px}}.block{{color:var(--warn)}}.effort{{font-weight:600}}
.detail summary{{cursor:pointer;color:var(--accent);font-size:12px;margin-top:4px}}
.grps{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;margin-top:6px;font-size:12px}}
.searchbar{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:4px 0 8px}}#q{{flex:1 1 320px;font-size:15px;padding:8px 10px}}
.filters{{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 12px}}label{{font-size:12px;color:var(--muted);display:flex;flex-direction:column}}
select,input{{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:4px}}
.wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:var(--bg)}}
.verdict{{border:1px solid var(--line);border-radius:8px;padding:6px 10px;margin:6px 0}}.verdict-PASS{{border-color:var(--a-apply)}}
.verdict-INVESTIGATE,.verdict-FAIL{{border-color:var(--warn)}}.warn{{color:var(--warn)}}
#count,#hbcount{{color:var(--muted);font-weight:600}}.chg summary{{cursor:pointer;font-weight:600;margin:6px 0}}
.bsets{{display:flex;flex-wrap:wrap;gap:12px;margin:6px 0}}.bsets label{{flex-direction:row;gap:4px;color:var(--fg);font-size:13px}}
.bf summary{{cursor:pointer;font-size:12px;color:var(--fg)}}.bfv{{display:flex;flex-direction:column;gap:2px;padding:4px 0 0 8px}}
.bfv label{{flex-direction:row;gap:4px;color:var(--fg)}}
@media (max-width:700px){{body{{padding:12px}}#q{{flex-basis:100%}}.filters label{{flex:1 1 140px}}
.vtable thead{{display:none}}.vtable tr{{display:block;border-bottom:1px solid var(--line);padding:6px 0}}
.vtable td{{display:block;border:0;padding:2px 0}}.vtable td[data-label]::before{{content:attr(data-label) ": ";color:var(--muted)}}}}
</style></head><body>
<header><h1>Career Coach — {e(str(run.get('run_kind', '')))} as of {e(str(run.get('as_of_date', '')))}</h1>
<div class=chips aria-label="Workload today">{workload_html}</div>
<p class=muted>{e(status_line)} · <a href="#s8">Job browser</a></p>
{_verdict_html(dashboard)}
<details><summary>Run health &amp; live evidence</summary>{_status_html(dashboard)}</details></header>
{''.join(sections)}
<details class=sec><summary><h2>Full text dashboard (same content as dashboard.md)</h2></summary><pre>{e(md_sections)}</pre></details>
<script>
const sel=[...document.querySelectorAll('select[data-filter]')],rows=[...document.querySelectorAll('#full tr')],q=document.getElementById('q');
const key=f=>f.replace(/_(\\w)/g,(m,c)=>c.toUpperCase());
function apply(){{const terms=q.value.toLowerCase().split(/\\s+/).filter(Boolean);let n=0;rows.forEach(r=>{{
const ok=sel.every(s=>!s.value||r.dataset[key(s.dataset.filter)]===s.value)&&terms.every(t=>r.dataset.search.includes(t));
r.hidden=!ok;if(ok)n++;}});document.getElementById('count').textContent=`Showing ${{n}} of ${{rows.length}} jobs`;}}
sel.forEach(s=>s.addEventListener('change',apply));q.addEventListener('input',apply);apply();
{BROWSER_JS}
</script></body></html>
"""
