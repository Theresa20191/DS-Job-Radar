"""Trajectory — which DS identities (tracks) the job's work builds toward,
and in which direction relative to the candidate's configured targets
(Transition Lens V1, annotation-only).

Track rules are declarative (config/tracks.yaml). They read only Work
Profile labels, the title, role_family and D2 domain placements; never
technical/domain bands or scores, PTF, job_quality or role_type.
"""

import re

from src.enrichment.role_classifier import DS_TITLED_FAMILIES
from src.models.transition import (
    EvidenceRef, GuardFailure, TARGET_TIERS, TrackMatch, Trajectory, WorkProfile,
)

_DIRECTION_BY_TIER = {"primary": "TOWARD_PRIMARY", "secondary": "TOWARD_SECONDARY",
                      "adjacent_ok": "ADJACENT", "non_target": "NON_TARGET"}
_CONDITION_KEYS = {"labels_any", "labels_all", "labels_none", "label_dominates", "domain", "title",
                   "not_title", "role_family_any", "ds_titled", "work_context", "label_evidence", "any_of"}


def _list_mention(text: str, match: re.Match) -> bool:
    """True when the matched term is one item of a comma-separated list
    ("exploratory analysis, forecasting, segmentation, ..."): a technique
    named in an enumeration, not the subject of the work statement."""
    after, before = text[match.end():], text[:match.start()]
    adjacent = re.match(r"\w*\s*,", after) or re.search(r",\s*(?:and\s+|or\s+)?(?:[\w-]+\s+)?$", before)
    window = text[max(0, match.start() - 50):match.end() + 50]
    return bool(adjacent) and window.count(",") >= 2


def _statement_matches(text: str, spec: dict) -> bool:
    if "not_matching" in spec and re.search(spec["not_matching"], text, re.I):
        return False
    if "matching" not in spec:
        return True
    hits = list(re.finditer(spec["matching"], text, re.I))
    if spec.get("not_as_list_item"):
        hits = [m for m in hits if not _list_mention(text, m)]
    return bool(hits)


def validate_vocabulary(vocab: dict) -> None:
    def check(cond: dict, where: str):
        unknown = set(cond) - _CONDITION_KEYS
        if unknown:
            raise ValueError(f"{where}: unknown condition keys {sorted(unknown)}")
        for sub in cond.get("any_of", []):
            check(sub, where)
    for track_id, rule in vocab["tracks"].items():
        if "context" not in rule:
            raise ValueError(f"track {track_id} has no context")
        check(rule["context"], f"{track_id}.context")
        if "guard" in rule:
            check(rule["guard"], f"{track_id}.guard")
        for other in rule.get("excludes", []):
            if other not in vocab["tracks"]:
                raise ValueError(f"track {track_id} excludes unknown track {other}")


def _eval(cond: dict, job, profile: WorkProfile, sentences: list[EvidenceRef]) -> list[EvidenceRef] | None:
    """Evidence for a satisfied condition, or None if it does not hold."""
    evidence: list[EvidenceRef] = []

    def label_ev(label):
        return next((l.evidence[0] for l in profile.labels if l.label == label), None)

    if "labels_any" in cond:
        hits = [label_ev(l) for l in cond["labels_any"] if profile.has(l)]
        if not hits:
            return None
        evidence.append(hits[0])
    if "labels_all" in cond:
        if not all(profile.has(l) for l in cond["labels_all"]):
            return None
        evidence.extend(label_ev(l) for l in cond["labels_all"])
    if "labels_none" in cond and any(profile.has(l) for l in cond["labels_none"]):
        return None
    if "label_dominates" in cond:
        spec = cond["label_dominates"]
        top = profile.count(spec["label"])
        if not (top > 0 and top > sum(profile.count(o) for o in spec["over"])):
            return None
        evidence.append(label_ev(spec["label"]))
    if "domain" in cond:
        spec = cond["domain"]
        items = [p for p in (job.domain_alignment.provenance if job.domain_alignment else [])
                 if p["term"] in spec["terms"] and p["placement"] in spec["placements"]]
        if not items:
            return None
        evidence.append(EvidenceRef(field=items[0]["field"], text=items[0]["sentence"], rule_id=f"D2:{items[0]['rule']}"))
    if "title" in cond:
        if not re.search(cond["title"], job.job_title or "", re.I):
            return None
        evidence.append(EvidenceRef(field="job_title", text=job.job_title, rule_id="title"))
    if "not_title" in cond and re.search(cond["not_title"], job.job_title or "", re.I):
        return None
    if "role_family_any" in cond:
        if job.role_family not in cond["role_family_any"]:
            return None
        evidence.append(EvidenceRef(field="role_family", text=job.role_family, rule_id="role_family"))
    if cond.get("ds_titled") and job.role_family not in DS_TITLED_FAMILIES:
        return None
    if "work_context" in cond:
        hit = next((s for s in sentences if re.search(cond["work_context"], s.text, re.I)), None)
        if hit is None:
            return None
        evidence.append(hit.model_copy(update={"rule_id": "work_context"}))
    if "label_evidence" in cond:
        # Same-sentence linking: ONE evidence sentence of one of the labels
        # must itself carry the context (never a label from one sentence and
        # the context from another).
        spec = cond["label_evidence"]
        hit = next((e for l in profile.labels if l.label in spec["labels"] for e in l.evidence
                    if _statement_matches(e.text, spec)), None)
        if hit is None:
            return None
        evidence.append(hit.model_copy(update={"rule_id": "label_evidence"}))
    if "any_of" in cond:
        alternatives = [ev for ev in (_eval(sub, job, profile, sentences) for sub in cond["any_of"]) if ev is not None]
        if not alternatives:
            return None
        evidence.extend(alternatives[0])
    return [e for e in evidence if e is not None]


def _target_tier(track_id: str, targets: dict | None) -> str:
    if not targets:
        return "UNTARGETED"
    return next((tier for tier in TARGET_TIERS if track_id in (targets.get(tier) or [])), "UNTARGETED")


def build_trajectory(job, profile: WorkProfile, sentences: list[EvidenceRef], vocab: dict,
                     targets: dict | None) -> Trajectory:
    if profile.unknown_reason:
        return Trajectory(direction="UNKNOWN", unknown_reason="work_profile_unknown")

    matched: dict[str, list[EvidenceRef]] = {}
    guard_failures: list[GuardFailure] = []
    for track_id, rule in vocab["tracks"].items():
        context_ev = _eval(rule["context"], job, profile, sentences)
        if context_ev is None:
            continue
        if "guard" in rule:
            guard_ev = _eval(rule["guard"], job, profile, sentences)
            if guard_ev is None:
                guard_failures.append(GuardFailure(track_id=track_id, failed_guard=str(rule["guard"])))
                continue
            context_ev = context_ev + guard_ev
        matched[track_id] = context_ev
    for track_id in list(matched):
        if any(other in matched for other in vocab["tracks"][track_id].get("excludes", [])):
            del matched[track_id]

    tracks = [TrackMatch(track_id=t, target_tier=_target_tier(t, targets), rule_id=f"{vocab['version']}:{t}", evidence=ev)
              for t, ev in matched.items()]
    tiers = {m.target_tier for m in tracks}
    mixed = bool(tiers & {"primary", "secondary"}) and "non_target" in tiers
    if not targets:
        return Trajectory(tracks=tracks, guard_failures=guard_failures, mixed=mixed,
                          direction="UNKNOWN", unknown_reason="not_configured")
    if not tracks:
        direction = "OFF_VOCABULARY"
    else:
        direction = next((_DIRECTION_BY_TIER[t] for t in TARGET_TIERS if t in tiers), "UNTARGETED")
    return Trajectory(tracks=tracks, direction=direction, mixed=mixed, guard_failures=guard_failures)
