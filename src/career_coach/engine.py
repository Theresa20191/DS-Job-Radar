"""Career Coach engine V1.2: turns the complete evaluated population into
CareerCoachDecision records. Deterministic: no clock, network or randomness;
every field carries provenance (O.1)."""

from dataclasses import dataclass, replace
from datetime import date

from src.career_coach import rules as R
from src.career_coach.career_value import career_value, match_tracks, order_candidates
from src.career_coach.inputs import eligible_live_checks
from src.career_coach.setup import content_hash, parse_config, parse_declarations, validate_setup
from src.career_coach.substance import Sentence, analyse
from src.models.career_coach import (
    COACH_VERSION,
    CareerCoachDecision,
    CoachInput,
    LiveCheckRecord,
)

QUEUE_CODE = {"V-5": "VQ-ELIGIBILITY", "V-3b": "VQ-COMPENSATION", "V-6": "VQ-POSTING_OPEN",
              "V-4b": "VQ-APPLICATION_CHANNEL"}
QUEUE_ORDER = ("VQ-ELIGIBILITY", "VQ-COMPENSATION", "VQ-POSTING_OPEN", "VQ-APPLICATION_CHANNEL",
               "VQ-JOB_SCOPE", "VQ-ROLE_LEVEL")                                          # Q-3
TRACK_EVIDENCE_CAP = 3                                                                  # EV-1


@dataclass(frozen=True)
class State:
    """The categorical inputs to priority / investment / next action — the
    unit the Q-1 / Q-2 counterfactuals vary."""
    factors: tuple
    cv: str
    exp: str
    sen: str
    sen_direction: str | None
    edu: str
    constraint: str
    resume: str
    research: str
    contact: bool

    def outcome(self) -> tuple[str, str, str, str]:
        viability = R.viability_state(list(self.factors))
        ready, _ = R.readiness(self.exp, self.sen, self.sen_direction, self.edu)
        rule = R.priority(viability, self.cv, ready, self.constraint)
        prio = R.PRIORITY_OF_RULE[rule]
        _, _, fit = R.investment(prio, self.resume, self.research)
        unknown = [f.rule_id for f in self.factors if f.result == "UNKNOWN"]
        action, _ = R.next_action(rule, unknown, fit, self.contact, self.research, self.resume)
        return prio, action, rule, fit


class Coach:
    def __init__(self, profile: dict, raw_config: dict, contacts: set[str]):
        self.profile = profile
        self.config = parse_config(raw_config)
        self.declarations = parse_declarations(profile)
        problems = validate_setup(self.config, self.declarations, profile)
        if problems:
            raise ValueError("Career Coach setup invalid: " + "; ".join(problems))
        self.contacts = contacts
        self.config_hash = content_hash(raw_config)
        self.profile_hash = content_hash(profile)

    # ------------------------------------------------------------------ run

    def run(self, population: list[CoachInput], as_of_date: date, run_kind: str,
            live_records: list[LiveCheckRecord], raw_corpus_hash: str) -> tuple[list[CareerCoachDecision], dict]:
        used, ignored = eligible_live_checks(live_records, as_of_date, self.config.thresholds.live_check_window_days)
        run = {"as_of_date": as_of_date.isoformat(), "run_kind": run_kind, "coach_version": COACH_VERSION,
               "input_hashes": {"raw_corpus": raw_corpus_hash, "config": self.config_hash, "profile": self.profile_hash,
                                "eligible_live_checks": content_hash(sorted(
                                    (r.model_dump(mode="json") for r in used.values()), key=lambda r: r["record_id"]))}}
        decisions = [self.decide(c, used.get(c.job.job_id), run) for c in sorted(population, key=lambda c: c.job.job_id)]
        decl = self.declarations
        return decisions, {
            "live_checks_used": sorted(r.record_id for r in used.values()), "live_checks_ignored": ignored,
            "live_check_window_days": self.config.thresholds.live_check_window_days,
            # Q-2: the declaration state behind every PD-* item, as declared (never inferred).
            "declarations": {
                "work_authorization": dict(decl.work_authorization),
                "experience": decl.experience.model_dump(mode="json"),
                "seniority": decl.seniority.model_dump(mode="json"),
                "resume_variants_ready": decl.resume_variants_ready,
            },
            "profile_items_affected_jobs": {item: sum(item in d.profile_items_affecting_job for d in decisions)
                                            for item in ("PD-WORK_AUTH", "PD-RELEVANT_YEARS", "PD-TARGET_LEVELS",
                                                         "PD-RESUME_VARIANTS")},
            "positionings": {p: spec.label for p, spec in self.config.positionings.items()},
        }

    # --------------------------------------------------------------- decide

    def decide(self, item: CoachInput, live: LiveCheckRecord | None, run: dict) -> CareerCoachDecision:
        job = item.job
        job_ref = {k: getattr(job, k) for k in ("job_id", "company", "job_title", "country", "canonical_url", "posted_date")}
        if not item.hard_filter.passed:                                            # IN-4
            return self._rejected(item, job_ref, run)

        cfg, decl, profile = self.config, self.declarations, self.profile
        factors = R.viability_factors(job, decl, live)
        viability = R.viability_state(factors)

        sub = analyse(job, cfg)
        matches = match_tracks(job, sub, cfg, decl)
        cv_state, cv_rule, deciding = career_value(sub, matches, cfg)
        ordered = order_candidates(matches, cfg)

        tech_state, required, evidenced, gaps = R.technical(job, profile)
        dom_state, dom_terms = R.domain(sub.domain_placements, profile)
        exp_state, measure, cand_value = R.experience(job, decl, deciding.track if deciding else None,
                                                      cfg.thresholds.k_stretch_years)
        sen_state, sen_dir, sen_rule = R.seniority(job, decl, cfg)
        edu_state = R.education(job, profile, cfg)
        ready, ready_rule = R.readiness(exp_state, sen_state, sen_dir, edu_state)
        constraint, components, p_rule = R.practical(job, decl, profile, self.contacts)

        resume_track, rt_rules = self._resume_track(cv_state, deciding, ordered)
        positioning = resume_track["positioning"] if isinstance(resume_track, dict) else None
        resume, e_r = R.resume_effort(tech_state, len(gaps), ready, positioning, decl, cfg.thresholds.heavy_gap_min)
        research, e_s = R.research_effort(job, sub.status, dom_state, cv_state, sub.central_work, decl)

        state = State(tuple(factors), cv_state, exp_state, sen_state, sen_dir, edu_state, constraint,
                      resume, research, components["network"] == "CONTACT_AVAILABLE")
        prio, action, pr_rule, fit = state.outcome()
        justified, required_inv, _ = R.investment(prio, resume, research)
        _, na_rule = R.next_action(pr_rule, [f.rule_id for f in factors if f.result == "UNKNOWN"], fit,
                                   state.contact, research, resume)

        # ---- evidence registry (EV-1): central work, deciding domain, years, then track evidence
        registry = _Registry()
        central_hits = sub.hits.get(sub.central_work, []) if sub.central_work else \
            sorted((h for hs in sub.hits.values() for h in hs), key=lambda h: h.sentence.index)
        central_ids = [registry.sentence(h.sentence, "work_statement", h.pattern_id) for h in central_hits[:TRACK_EVIDENCE_CAP]]
        if deciding:
            for s in deciding.evidence[:TRACK_EVIDENCE_CAP]:
                registry.sentence(s, "track_evidence")
        years_text = R.years_sentence(job)
        years_id = registry.text(years_text, "years_requirement", "requirements_section_raw|description_raw") if years_text else None
        matched = []
        for m in matches:
            ids = [registry.sentence(s, "track_evidence") for s in m.evidence[:TRACK_EVIDENCE_CAP]]
            matched.append({"track": m.track, "declared_tier": m.declared_tier, "specificity": m.specificity,
                            "defining": m.defining, "clause": "; ".join(m.clauses), "evidence_ids": ids,
                            "evidence_count": len(m.evidence)})

        blocking = next((f for f in factors if f.result == "FAIL"), None)
        queue = self._verification_queue(state, prio, action, job, ready_rule, exp_state, measure, cand_value)
        profile_items = self._profile_items(state, (prio, action), job, sen_rule, positioning, ordered)
        uncertainty = ("MIXED" if queue and profile_items else "JOB_SIDE_UNKNOWN" if queue
                       else "PROFILE_DECLARATION_UNKNOWN" if profile_items else "RESOLVED")      # U-1

        cv_block = {"state": cv_state, "deciding_rule": cv_rule, "deciding_track": deciding.track if deciding else None,
                    "matched_tracks": matched,
                    "explanation": self._cv_explanation(cv_state, cv_rule, deciding, sub)}
        decision = {
            "job": job_ref, "run": run, "evaluation_scope": "FULL",
            "application_viability": {"state": viability, "factors": [
                {"rule_id": f.rule_id, "factor": f.factor, "result": f.result, "detail": f.detail,
                 "live_record_id": f.live_record_id} for f in factors]},
            "blocking_reason": {"rule_id": blocking.rule_id, "text": blocking.detail} if blocking else None,
            "work_substance": {"status": sub.status, "basis": sub.basis, "central_work": sub.central_work,
                               "activities": {a: sub.count(a) for a in cfg.activity_precedence if sub.count(a)},
                               "title_family": job.role_family, "evidence_ids": central_ids},
            "career_value": cv_block,
            "resume_track": resume_track,
            "candidate_alignment": {
                "technical": {"state": tech_state, "required": required, "evidenced": evidenced, "gaps": list(gaps)},
                "domain": {"state": dom_state, "terms": dom_terms},
                "experience": {"state": exp_state, "job_required_years": job.years_experience_required,
                               "job_evidence_id": years_id, "candidate_measure": measure, "candidate_value": cand_value},
                "seniority": {"state": sen_state, "job_level": job.role_level,
                              "candidate_aligned_levels": decl.seniority.aligned_levels,
                              "candidate_stretch_levels": decl.seniority.stretch_levels, "direction": sen_dir},
                "education": {"state": edu_state}},
            "readiness": {"state": ready, "deciding_rule": ready_rule,
                          "components": {"experience": exp_state, "seniority": sen_state, "education": edu_state}},
            "practical_constraint": {"state": constraint, "components": components},
            "resume_effort": resume, "research_effort": research,
            "application_priority": prio,
            "application_investment": {"justified": justified, "required": required_inv, "fit": fit},
            "next_action": action,
            "primary_reason": {"rule_id": pr_rule, "text": self._reason(pr_rule, blocking, factors, cv_state, deciding,
                                                                         ready, ready_rule, job, exp_state, measure,
                                                                         cand_value, edu_state, constraint)},
            "verification_queue": queue,
            "profile_items_affecting_job": profile_items,
            "uncertainty_state": uncertainty,
            "key_evidence": registry.items,
            "evidence_gaps": [{"kind": "TOOL" if kind == "tool" else "TECHNIQUE", "name": name,
                               "text": f"No evidence in your profile for {name} (required by this job)"}
                              for name, kind in gaps.items()] +
                             ([{"kind": "DOMAIN", "name": t, "text": f"Your profile declares no {t} domain (this job's domain)"}
                               for t in dom_terms] if dom_state == "NO_OVERLAP" else []),
        }
        live_input = [f"live_checks[{live.record_id}]"] if live else []
        decision["provenance"] = {
            "evaluation_scope": _p(["IN-1"], ["hard_filter.passed"]),
            "application_viability": _p([f.rule_id for f in factors] + ["V-AGG"],
                                        ["country", "sources", "description_raw", "salary_*", "work_authorization_restriction_type",
                                         "career_coach.work_authorization", "career_coach.compensation_floor"] + live_input),
            "blocking_reason": _p(([blocking.rule_id] if blocking else []) + ["V-AGG"], []),
            "work_substance": _p(["S-1", "S-2", "S-3", "S-4", "S-5", "S-6"],
                                 ["description_raw", "role_family", "config.work_text", "config.activities",
                                  "config.activity_precedence"], central_ids),
            "career_value": _p([cv_rule, "CV-8"] + (["RT-1", "RT-2", "RT-3", "RT-4"] if deciding else []),
                               ["config.tracks", "config.domains", "job_title", "role_family",
                                f"career_coach.target_tracks.{deciding.declared_tier}" if deciding else "career_coach.target_tracks"],
                               [i for m in matched for i in m["evidence_ids"]]),
            "resume_track": _p(rt_rules, ["config.positionings", "career_value.deciding_track"]),
            "candidate_alignment": _p(["A-T", "A-D", "EX-1", _ex_rule(exp_state), sen_rule, "ED-1"],
                                      ["skill_evidence", "profile.skills", "profile.techniques", "profile.domain",
                                       "years_experience_required", f"career_coach.experience.{measure}", "role_level",
                                       "career_coach.seniority", "education_required", "profile.education_level_rank",
                                       "config.education_ranks", "config.level_rungs", "config.thresholds.k_stretch_years"],
                                      [years_id] if years_id else []),
            "readiness": _p([ready_rule], ["candidate_alignment"]),
            "practical_constraint": _p([p_rule, "P-2", "P-3", "P-4", "P-5", "P-AGG"],
                                       ["salary_*", "career_coach.compensation_floor", "profile.target_locations",
                                        "remote_type", "company_normalized", "contacts.company", "employer_clarity"]),
            "resume_effort": _p([e_r], ["candidate_alignment.technical", "readiness", "resume_track",
                                        "career_coach.resume_variants_ready", "config.thresholds.heavy_gap_min"]),
            "research_effort": _p([e_s], ["employer_clarity", "work_substance", "candidate_alignment.domain",
                                          "career_value", "role_family", "salary_unknown"]),
            "application_priority": _p([pr_rule], ["application_viability", "career_value", "readiness",
                                                   "practical_constraint"]),
            "application_investment": _p(["I-1", "I-2", "I-3"], ["application_priority", "resume_effort", "research_effort"]),
            "next_action": _p([na_rule], ["application_priority", "application_viability", "application_investment",
                                          "practical_constraint.components.network", "research_effort", "resume_effort"]),
            "primary_reason": _p([pr_rule], []),
            "verification_queue": _p(["Q-1", "Q-3"], []),
            "profile_items_affecting_job": _p(["Q-2"], []),
            "uncertainty_state": _p(["U-1"], ["verification_queue", "profile_items_affecting_job"]),
            "key_evidence": _p(["EV-1"], []),
            "evidence_gaps": _p(["EV-2"], ["candidate_alignment"]),
        }
        return CareerCoachDecision.model_validate(decision)

    # -------------------------------------------------------------- helpers

    def _rejected(self, item: CoachInput, job_ref: dict, run: dict) -> CareerCoachDecision:
        reasons = "; ".join(item.hard_filter.reasons)
        decision = {
            "job": job_ref, "run": run, "evaluation_scope": "HARD_FILTER_REJECTED",
            "application_viability": {"state": "BLOCKED", "factors": [
                {"rule_id": "V-1", "factor": "hard_filter", "result": "FAIL", "detail": reasons}]},
            "blocking_reason": {"rule_id": "V-1", "text": reasons},
            "work_substance": None, "career_value": None, "resume_track": None, "candidate_alignment": None,
            "readiness": None, "practical_constraint": None, "resume_effort": None, "research_effort": None,
            "application_priority": "DO_NOT_INVEST",
            "application_investment": {"justified": "NONE", "required": None, "fit": "NOT_APPLICABLE"},
            "next_action": "DO_NOT_INVEST",
            "primary_reason": {"rule_id": "PR-1", "text": f"Rejected by the hard filter: {reasons}"},
            "uncertainty_state": "RESOLVED",
        }
        prov = {name: _p(["IN-4"], []) for name in ("work_substance", "career_value", "resume_track",
                                                     "candidate_alignment", "readiness", "practical_constraint",
                                                     "resume_effort", "research_effort", "verification_queue",
                                                     "profile_items_affecting_job", "key_evidence", "evidence_gaps")}
        prov.update({
            "evaluation_scope": _p(["IN-4"], ["hard_filter.passed"]),
            "application_viability": _p(["V-1", "V-AGG"], ["hard_filter.reasons"]),
            "blocking_reason": _p(["V-1"], ["hard_filter.reasons"]),
            "application_priority": _p(["PR-1"], ["application_viability"]),
            "application_investment": _p(["I-1"], ["application_priority"]),
            "next_action": _p(["NA-1"], ["application_priority"]),
            "primary_reason": _p(["PR-1"], []),
            "uncertainty_state": _p(["U-1"], []),
        })
        decision["provenance"] = prov
        return CareerCoachDecision.model_validate(decision)

    def _resume_track(self, cv_state, deciding, ordered):
        if cv_state not in ("PRIMARY", "SECONDARY", "ADJACENT") or deciding is None:
            return "NONE", ["RT-6"]
        position = next(i for i, (m, _) in enumerate(ordered) if m.track == deciding.track)
        key = ordered[position][1]
        runner_up = ordered[position + 1][0].track if position + 1 < len(ordered) else None
        positioning = next(p for p, spec in self.config.positionings.items() if deciding.track in spec.tracks)
        return ({"positioning": positioning, "source_track": deciding.track, "declared_tier": deciding.declared_tier,
                 "runner_up": runner_up, "deciding_key": key}, [key, "RT-5"])

    def _verification_queue(self, state: State, prio, action, job, ready_rule, exp_state, measure, cand_value):
        current = (prio, action)
        items = []
        for f in state.factors:                                                     # Q-1 viability unknowns
            if f.result != "UNKNOWN":
                continue
            fav = _swap(state, f, "PASS").outcome()[:2]
            unfav = _swap(state, f, "FAIL").outcome()[:2]
            if fav != current or unfav != current:
                items.append(_item(QUEUE_CODE[f.rule_id], _question(f.rule_id, job), fav, unfav))
        if state.cv == "UNKNOWN":
            fav = replace(state, cv="PRIMARY").outcome()[:2]
            unfav = replace(state, cv="NON_TARGET").outcome()[:2]
            if fav != current or unfav != current:
                items.append(_item("VQ-JOB_SCOPE", "What does the role actually involve? No work statement in the "
                                                   "posting established its central work.", fav, unfav))
        if ready_rule == "R-3":
            fav = replace(state, sen="ALIGNED", sen_direction=None).outcome()[:2]
            unfav = replace(state, sen="MATERIAL_GAP", sen_direction="OTHER", exp="MATERIAL_GAP").outcome()[:2]
            if fav != current or unfav != current:
                items.append(_item("VQ-ROLE_LEVEL", f"Is this really a {job.role_level}-level role? It asks for "
                                                    f"{job.years_experience_required:g} years, which your declared "
                                                    f"{measure} ({cand_value:g}) meets.", fav, unfav))
        return sorted(items, key=lambda i: QUEUE_ORDER.index(i["code"]))

    def _profile_items(self, state: State, current, job, sen_rule, positioning, ordered) -> list[str]:
        items = []
        decl = self.declarations
        if decl.work_authorization.get(job.country) not in ("PASS", "NOT_ELIGIBLE"):   # PD-WORK_AUTH
            blocked = replace(state, factors=state.factors + (R.Factor("V-2", "work_authorization", "FAIL", "cf"),))
            cleared = replace(state, factors=tuple(f for f in state.factors if f.rule_id != "V-5"))
            if blocked.outcome()[:2] != current or cleared.outcome()[:2] != current:
                items.append("PD-WORK_AUTH")
        if state.exp == "UNASSESSED":                                                 # PD-RELEVANT_YEARS
            if replace(state, exp="MEETS").outcome()[:2] != current or \
                    replace(state, exp="MATERIAL_GAP").outcome()[:2] != current:
                items.append("PD-RELEVANT_YEARS")
        if sen_rule == "SN-2":                                                        # PD-TARGET_LEVELS
            if replace(state, sen="ALIGNED").outcome()[:2] != current or \
                    replace(state, sen="MATERIAL_GAP", sen_direction="ABOVE").outcome()[:2] != current:
                items.append("PD-TARGET_LEVELS")
        if decl.resume_variants_ready is None and positioning is not None:           # PD-RESUME_VARIANTS
            ready_decl = decl.model_copy(update={"resume_variants_ready": [positioning]})
            ready_resume, _ = R.resume_effort(*self._resume_inputs(job, state, positioning, ready_decl))
            if replace(state, resume=ready_resume).outcome()[:2] != current:
                items.append("PD-RESUME_VARIANTS")
        return items

    def _resume_inputs(self, job, state: State, positioning, decl):
        tech_state, _, _, gaps = R.technical(job, self.profile)
        ready, _ = R.readiness(state.exp, state.sen, state.sen_direction, state.edu)
        return tech_state, len(gaps), ready, positioning, decl, self.config.thresholds.heavy_gap_min

    @staticmethod
    def _cv_explanation(state, rule, deciding, sub) -> str:
        if rule == "CV-1":
            return "No work statement in the posting established what the job involves."
        if rule == "CV-7":
            return f"Work evidenced ({', '.join(sorted(sub.hits))}) but no configured track matched."
        tier = {"primary": "a primary", "secondary": "a secondary", "adjacent_ok": "an adjacent",
                "non_target": "a non-target"}[deciding.declared_tier]
        return f"{deciding.track} matched ({'; '.join(deciding.clauses)}) — {tier} track in your declarations."

    def _reason(self, rule, blocking, factors, cv, deciding, ready, ready_rule, job, exp_state, measure, value,
                edu_state, constraint) -> str:
        track = deciding.track if deciding else ""
        if rule == "PR-1":
            return f"Blocked: {blocking.detail}"
        if rule == "PR-2":
            return f"Non-target work: {track}"
        if rule == "PR-3":
            first = next(f for f in factors if f.result == "UNKNOWN")
            return f"Verify before investing: {first.detail}"
        if rule == "PR-4":
            return "Job scope not established from the posting's work statements"
        if rule == "PR-5":
            return "Posted pay is below your soft floor"
        if rule in ("PR-6", "PR-8") or ready in ("MATERIAL_GAP", "LEVEL_CONFLICT", "STRETCH"):
            gap = self._readiness_text(ready_rule, job, exp_state, measure, value, edu_state)
            if rule in ("PR-6", "PR-8"):
                return gap
        if rule == "PR-7":
            return f"{'Adjacent to' if cv == 'ADJACENT' else 'Outside'} your target tracks" + (f" ({track})" if track else "")
        tier = "Primary" if cv == "PRIMARY" else "Secondary"
        if rule in ("PR-9", "PR-11"):
            suffix = " · readiness unassessed" if ready == "UNASSESSED" else " · no readiness gap found"
            return f"{tier} target ({track}){suffix}"
        detail = gap if ready == "STRETCH" else "pay not confirmed against your soft floor"
        return f"{tier} target ({track}) · {detail}"

    def _readiness_text(self, ready_rule, job, exp_state, measure, value, edu_state) -> str:
        decl = self.declarations.seniority
        levels = f"aligned {decl.aligned_levels}, stretch {decl.stretch_levels}"
        if ready_rule == "R-1":
            return f"Asks for {job.education_required}, above your declared education"
        if ready_rule == "R-2":
            return (f"Asks for {job.years_experience_required:g} years; your declared {measure} is {value:g} "
                    f"(more than {self.config.thresholds.k_stretch_years:g} years short)")
        if ready_rule == "R-3":
            return (f"Title level {job.role_level} is outside your declared levels ({levels}), but your declared "
                    f"{measure} meets the stated {job.years_experience_required:g} years — confirm the level")
        if ready_rule == "R-4":
            return f"Level {job.role_level} is outside your declared levels ({levels})"
        if exp_state == "WITHIN_STRETCH":
            return f"stretch: asks for {job.years_experience_required:g} years, your declared {measure} is {value:g}"
        return f"stretch: {job.role_level} is a declared stretch level"


class _Registry:
    def __init__(self):
        self.items: list[dict] = []
        self._by_key: dict = {}

    def _add(self, key, item) -> str:
        if key not in self._by_key:
            item["id"] = f"E{len(self.items) + 1}"
            self.items.append(item)
            self._by_key[key] = item["id"]
        return self._by_key[key]

    def sentence(self, s: Sentence, kind: str, pattern_id: str | None = None) -> str:
        return self._add(("s", s.index), {"kind": kind, "text": s.text, "source_field": "description_raw",
                                          "section": s.heading or s.zone, "pattern_id": pattern_id})

    def text(self, text: str, kind: str, source: str) -> str:
        return self._add(("t", text), {"kind": kind, "text": text, "source_field": source, "section": "",
                                       "pattern_id": None})


def _p(rules, inputs, evidence=None) -> dict:
    return {"rules": list(dict.fromkeys(rules)), "inputs": inputs, "evidence": list(evidence or [])}


def _ex_rule(state: str) -> str:
    return {"NOT_STATED_BY_JOB": "EX-2", "UNASSESSED": "EX-3"}.get(state, "EX-4")


def _swap(state: State, factor, result: str) -> State:
    return replace(state, factors=tuple(replace(f, result=result) if f is factor else f for f in state.factors))


def _item(code, question, fav, unfav) -> dict:
    return {"code": code, "question": question, "source": "JOB",
            "if_resolved_favourably": f"priority {fav[0]} · action {fav[1]}",
            "if_resolved_unfavourably": f"priority {unfav[0]} · action {unfav[1]}"}


def _question(rule: str, job) -> str:
    return {
        "V-5": f"Does the posting's work-authorization condition allow you to apply in {job.country}?",
        "V-3b": "Does the pay range meet your hard compensation floor?",
        "V-6": "Is the posting still open?",
        "V-4b": "Is the MyCareersFuture JD page live, and does it still list the email address?",
    }[rule]
