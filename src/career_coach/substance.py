"""Work substance (S-1..S-6) and domain placement (D-1) from the job's own
text. Requirements / preferred text never creates an activity (S-3, M16);
the title is context only (S-5)."""

import re
from dataclasses import dataclass, field

from src.enrichment.requirements_sections import segment
from src.models.career_coach import CoachConfig, JobProjection


@dataclass(frozen=True)
class Sentence:
    index: int            # position among the description's units (provenance)
    text: str
    zone: str             # requirements_sections zone
    heading: str


@dataclass
class ActivityHit:
    sentence: Sentence
    pattern_id: str


@dataclass
class SubstanceResult:
    basis: str
    work: list[Sentence]
    other: list[Sentence]                       # non-work description sentences (D-1 CONTEXT)
    hits: dict[str, list[ActivityHit]]          # activity -> evidence (one per sentence)
    central_work: str | None
    status: str
    domain_placements: dict[str, set[str]] = field(default_factory=dict)   # term -> placements
    domain_hits: dict[str, list[Sentence]] = field(default_factory=dict)   # term -> OWN_WORK sentences

    def count(self, activity: str) -> int:
        return len(self.hits.get(activity, []))


def _any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def verb_object(verbs: str, objects: str, text: str) -> bool:
    """S-2: a verb and an object in the same sentence, as two distinct,
    non-overlapping spans (one word can never be both)."""
    object_spans = [m.span() for m in re.finditer(objects, text, re.I)]
    return any(v_end <= o_start or o_end <= v_start
               for v_start, v_end in (m.span() for m in re.finditer(verbs, text, re.I))
               for o_start, o_end in object_spans)


def work_sentences(job: JobProjection, config: CoachConfig) -> tuple[str, list[Sentence], list[Sentence]]:
    """S-1: (basis, work sentences, other description sentences)."""
    units, _ = segment(job.description_raw, "description_raw", default_zone="unsectioned")
    sentences: list[Sentence] = []
    for i, u in enumerate(units):
        prev = sentences[-1] if sentences else None
        # A line that starts lower-case right after an unterminated line in the
        # same section is the same sentence wrapped across lines (S-2).
        if prev and prev.zone == u.zone and prev.heading == u.heading and u.text[:1].islower() \
                and not prev.text.rstrip().endswith((".", "!", "?", ";", ":")):
            sentences[-1] = Sentence(prev.index, f"{prev.text} {u.text}", prev.zone, prev.heading)
        else:
            sentences.append(Sentence(i, u.text, u.zone, u.heading))
    wt = config.work_text

    def boilerplate(s: Sentence) -> bool:
        return s.heading == "closing boilerplate" or (s.heading and _any(wt.boilerplate_headings, s.heading))

    # "mixed" headings ("Responsibilities & Requirements") cannot be told apart
    # line by line; they are excluded so requirement text never creates work.
    candidates = [s for s in sentences if s.zone in ("other", "unsectioned") and not boilerplate(s)]
    responsibility = [s for s in candidates if s.zone == "other" and s.heading
                      and _any(wt.responsibility_headings, s.heading)]
    if responsibility:
        basis, work = "RESPONSIBILITY_SECTION", responsibility
    elif candidates:
        basis, work = "DESCRIPTION_SENTENCES", candidates
    else:
        basis, work = "NONE", []
    work_ids = {s.index for s in work}
    return basis, work, [s for s in sentences if s.index not in work_ids]


def analyse(job: JobProjection, config: CoachConfig) -> SubstanceResult:
    basis, work, other = work_sentences(job, config)

    hits: dict[str, list[ActivityHit]] = {}
    for activity, spec in config.activities.items():                           # S-2
        for s in work:
            if _any(spec.exclusions, s.text):
                continue
            pattern = next((p for p in spec.patterns if verb_object(p.verbs, p.objects, s.text)), None)
            if pattern:
                hits.setdefault(activity, []).append(ActivityHit(s, pattern.id))

    counts = {a: len(h) for a, h in hits.items()}
    central = None
    if counts:                                                                  # S-4
        top = max(counts.values())
        leaders = [a for a in config.activity_precedence if counts.get(a) == top]
        if top >= 2 or len(counts) == 1:
            central = leaders[0]
    status = "ESTABLISHED" if central else ("PARTIAL" if counts else "UNKNOWN")  # S-6

    placements: dict[str, set[str]] = {}
    domain_hits: dict[str, list[Sentence]] = {}
    for term, patterns in config.domains.items():                               # D-1
        places = set()
        if _any(patterns, job.job_title):
            places.add("DEFINING")
        own = [s for s in work if _any(patterns, s.text)]
        if own:
            places.add("OWN_WORK")
            domain_hits[term] = own
        if not places and any(_any(patterns, s.text) for s in other):
            places.add("CONTEXT")
        if places:
            placements[term] = places
    return SubstanceResult(basis, work, other, hits, central, status, placements, domain_hits)
