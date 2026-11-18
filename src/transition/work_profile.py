"""Work Profile — what the job's own work IS (job-intrinsic, multi-label,
no score). Transition Lens V1, annotation-only.

Evidence source: job RESPONSIBILITY / TASK statements, selected by what a
sentence says rather than by which parser zone it landed in. The existing
section detector is reused for sentence segmentation and headings, but its
zones are not a reliable work boundary on this corpus:
  - real duties land in qualification zones when a sub-heading is missed
    (PhysicsX: "This Role ... you'll build ... by:" sits under "Who We're
    Looking For"; Dell lists "Develop, evaluate, and deploy ..." under
    "Essential Requirements");
  - the `other` zone also holds About-us, Why-join and benefits text.

Selection rules:
  - skip sentences under non-work headings (about us, why join, benefits,
    closing boilerplate, ...);
  - skip candidate-attribute statements (years, degree, experience with,
    proficiency, "you are/have", "is a plus", ...) — qualification text is
    never turned into work evidence;
  - skip career-progression text ("where you'll go", "you'll grow into")
    and everything after it under the same heading;
  - skip company / programme voice ("we believe", "our teams ...", "you will
    get opportunities", "holds significant potential") and bare
    sub-headings ("Model Development:");
  - outside qualification zones: accept task statements (imperative or
    gerund task verb, "you will / you'll / you serve ..." statements, or
    "the role / position spans / involves / requires ..." statements —
    Chevron states its work as what "the position requires" it to do);
  - inside qualification zones: accept only imperative duties (base-form
    task verb) or sentences after an explicit responsibility cue ("This
    Role", "Your responsibilities include", "you'll ... by:"). Gerund /
    noun-phrase / past-tense items there are experience asks, not duties.

Known limitation: the frozen role_type / job_quality classifier is NOT
changed. It can say TRUE_DS (job_quality 75) where this profile shows
ML_PLATFORM, AI_APPLICATION_DEV or ANALYTICS_INSIGHT work; both are shown,
and the frozen result stands.
"""

import re

from src.enrichment.requirements_sections import analyze
from src.models.transition import EvidenceRef, WorkLabel, WorkProfile
from src.scoring.domain_alignment import _clean

# --- sentence selection ---------------------------------------------------------

_VERBS = [
    "develop", "build", "design", "train", "deploy", "own", "lead", "drive", "define", "analyse", "analyze",
    "apply", "create", "partner", "work", "research", "monitor", "maintain", "implement", "translate",
    "use", "utilise", "utilize", "evaluate", "validate", "conduct", "collaborate", "support", "perform",
    "optimise", "optimize", "measure", "mentor", "help", "improve", "serve", "shape", "deliver", "architect",
    "integrate", "automate", "handle", "identify", "combat", "quantify", "align", "contribute", "participate",
    "prototype", "explore", "prepare", "establish", "run", "manage", "oversee", "produce", "communicate",
    "present", "write", "query", "process", "investigate", "detect", "forecast", "model", "engineer",
    "scale", "operate", "ensure", "provide", "guide", "coach", "productionise", "productionize", "refine",
    "represent", "mine", "embed", "leverage", "coordinate", "review", "debug", "set", "assist", "select",
    "tune", "fine-tune", "innovate", "illuminate", "influence", "execute", "iterate", "own", "enable",
    "promote", "frame", "solve", "take", "drive", "champion", "extract", "clean", "transform",
    "generate", "share", "recommend", "uncover", "surface", "track", "propose", "formulate", "construct", "fit",
]
_IRREGULAR_ING = {"run": "running", "set": "setting", "model": "modelling", "debug": "debugging"}


def _ing(verb: str) -> str:
    if verb in _IRREGULAR_ING:
        return _IRREGULAR_ING[verb]
    return (verb[:-1] if verb.endswith("e") and not verb.endswith("ee") else verb) + "ing"


_BASE = "|".join(sorted({re.escape(v) for v in _VERBS}, key=len, reverse=True))
_GERUND = "|".join(sorted({re.escape(_ing(v)) for v in _VERBS} | {"modeling"}, key=len, reverse=True))
_PREFIX = r"^(?:[-•*·]\s*)?(?:[A-Z][\w&/,'() -]{0,50}:\s*)?"           # optional "Label: " lead-in
_IMPERATIVE = re.compile(_PREFIX + rf"(?:{_BASE})\b", re.I)
_GERUND_START = re.compile(_PREFIX + rf"(?:{_GERUND})\b", re.I)
_YOU_TASK = re.compile(r"\byou(?:'ll| will| would| serve| are responsible| own| lead| design| build| partner| help)\b"
                       r"|^(?:the|this) (?:role|position) (?:spans|involves|includes|covers|focuses|will|requires)\b", re.I)

_ATTRIBUTE = re.compile(
    r"\b\d+\+?\s*(?:-\s*\d+\s*)?(?:years?|yrs)\b|experience (?:in|with|of|using|building|working|developing)\b"
    r"|proven (?:experience|track)|hands-on experience|\b(?:bachelor|master|phd|degree)\b|proficien"
    r"|familiar(?:ity)? with|knowledge of|understanding of|background in|ability to|\bable to\b"
    r"|\byou are (?:a|an|someone|comfortable|passionate|excited)\b|\byou have\b|\byou've\b|\byou're\b"
    r"|excellent (?:communication|problem)|strong (?:communication|programming|proficiency|foundation|background"
    r"|understanding|analytical|hands-on|skills|technical)|is a plus|nice to have|\bpreferred\b|desirable"
    r"|good to have|skills? in\b", re.I)
_CAREER_PROGRESSION = re.compile(r"where you'?ll go|career (?:path|progression)|you'?ll grow into|grow into becoming", re.I)
_RESPONSIBILITY_CUE = re.compile(
    r"^(?:this|the|your) role\b|responsibilit|what you(?:'ll| will) do|key accountabilit|\bduties\b"
    r"|you(?:'ll| will)[^.]{0,90}\bby:?$", re.I)
_NON_WORK_HEADING = re.compile(
    r"\babout (?:us|our|the company)\b|who we are|why join|benefit|what we offer|perks|our (?:company|culture|values)"
    r"|closing boilerplate|equal opportunit|life at|company (?:overview|profile)|diversity|what you'?ll (?:enjoy|get)", re.I)
# Company / programme voice ("we believe", "our teams ...", "read our blog",
# "holds significant potential", "you will get opportunities") describes the
# employer, not the job's work.
_COMPANY_VOICE = re.compile(
    r"^(?:our|we)\b|\bwe believe\b|\bread our blog\b|\bholds? (?:significant|great|huge|enormous|tremendous) potential\b"
    r"|\bget (?:the )?opportunit(?:y|ies)\b", re.I)
_QUALIFICATION_ZONES = ("required", "preferred")


def _logical_lines(units) -> list[tuple]:
    """Rejoin sentences the source split across lines (bold/links rendered
    as separate lines: "Design and implement" / "data quality metrics ...").
    A unit continues the previous one, under the same heading and zone,
    when it starts lower-case or with punctuation, or when the previous one
    is a short lead-in that did not end a sentence."""
    lines: list[list] = []
    for unit in units:
        text = _clean(unit.text)
        if not text:
            continue
        if lines:
            prev_unit, prev_text = lines[-1]
            same_block = (prev_unit.heading, prev_unit.zone, prev_unit.source_field) == (unit.heading, unit.zone, unit.source_field)
            continues = re.match(r"^[a-z,;:)(]", text) or (len(prev_text.split()) <= 4 and not re.search(r"[.;:!?]$", prev_text))
            if same_block and continues and not re.search(r"[.!?]$", prev_text):
                lines[-1][1] = f"{prev_text} {text}".replace(" ,", ",").replace(" .", ".")
                continue
        lines.append([unit, text])
    return [(unit, text) for unit, text in lines]


def work_sentences(job) -> list[EvidenceRef]:
    """Task statements describing the job's own work, zone-independent."""
    selected: list[EvidenceRef] = []
    heading, work_mode, stopped = None, False, False
    for unit, text in _logical_lines(analyze(job.description_raw or "", job.requirements_raw or "").units):
        if unit.heading != heading:                       # new section: reset local state
            heading, work_mode, stopped = unit.heading, False, False
        if not text or _NON_WORK_HEADING.search(unit.heading or ""):
            continue
        if _CAREER_PROGRESSION.search(text):
            stopped = True
        if stopped:
            continue
        if _RESPONSIBILITY_CUE.search(text):
            work_mode = True
        if _ATTRIBUTE.search(text) or _COMPANY_VOICE.search(text):
            continue
        if text.endswith(":") and len(text.split()) <= 4:     # bare sub-heading ("Model Development:")
            continue
        is_task = bool(_IMPERATIVE.search(text) or _GERUND_START.search(text) or _YOU_TASK.search(text))
        if unit.zone in _QUALIFICATION_ZONES:
            accepted = bool(_IMPERATIVE.search(text) or _YOU_TASK.search(text)) or (work_mode and is_task)
            rule = "duty_in_qualification_zone"
        else:
            accepted, rule = is_task, "task_statement"
        if accepted:
            selected.append(EvidenceRef(field=unit.source_field, text=text[:240], rule_id=rule, heading=unit.heading or ""))
    return selected


# --- labels -----------------------------------------------------------------------
#
# Labels describe the work a sentence evidences. Each rule names an activity,
# not a topic: a sentence that merely mentions models, ML solutions or a
# model's lifecycle is not modelling work (it may be engineering, platform,
# operations or collaboration work, which have their own labels).

# APPLIED_MODELLING: a model-building verb whose object is a model. A model
# noun used as a modifier ("model performance / logic / pipelines /
# lifecycle / serving") names something ABOUT a model, not a model.
_MODEL_BUILD_VERBS = (r"(?:build|built|develop|design|train|fine-tun|fit|implement|construct|propos|formulat|creat"
                      r"|architect|validat)\w*")
_MODEL_OBJECT = (r"(?:models\b(?!\s+(?:registr\w*|serving|infrastructure|pipelines?)\b)"
                 r"|model\b(?=\s*(?:$|[.,;:)!?/]|(?:for|to|that|which|and|or|in|on|with|using|across|of|by|from|at|as|into"
                 r"|where|including|such|trained|built)\b))"
                 r"|algorithms?\b|classifiers?\b|estimators?\b|recommenders?\b)")
# Sentences whose modelling words describe foundation-model / LLM / agentic
# application work are AI_APPLICATION_DEV, not applied DS modelling.
_GENAI = re.compile(r"\b(?:llms?|large language|foundation(?:al)? models?|genai|gen ai|generative ai|rag|retrieval[- ]augmented"
                    r"|agents?|agentic|multi-agent|chatbots?|copilots?|prompt\w*)\b", re.I)
# Building or operating the systems AROUND models is platform / operations
# work. Platforms or infrastructure only as a business setting ("risks in our
# products/platforms", "infrastructure operations") do not veto.
_PLATFORM_CONTEXT = re.compile(
    r"\b(?:build|develop|design|operate|maintain|scale|architect)\w*\b[^.;]{0,40}\b(?:infrastructure|platforms?|tooling)\b"
    r"|\b(?:infrastructure|platforms?|tooling|systems) (?:for|behind|that power|powering|around)\b|\bserving\b"
    r"|\bpipelines?\b[^.;]{0,60}\b(?:training|model release)\b|\b(?:for|behind|around) (?:the )?models?\b", re.I)
# Mentoring statements name topics of guidance, not the work itself.
_MENTORING = re.compile(r"^(?:[-•*·]\s*)?(?:mentor|coach)\w*\b", re.I)
# Model evaluation / tuning / LLM evaluation is not decision experimentation.
_EXPERIMENT_ON_MODELS = re.compile(
    r"\bexperiments?\b[^.;]{0,40}\b(?:to|that) (?:test|evaluate|assess|benchmark|measure)\b[^.;]{0,40}"
    r"\b(?:performance|accuracy|models?|llms?)\b|\bexperiments? (?:on|with) (?:\w+ ){0,2}(?:models?|llms?)\b", re.I)
_EXPERIMENT_GENERIC = re.compile(r"\b(?:design|run|set up|debug|size|analy[sz]e|execut\w*)\b[^.;]{0,40}\bexperiments?\b", re.I)

LABEL_RULES: dict[str, re.Pattern] = {
    "APPLIED_MODELLING": re.compile(
        rf"\b{_MODEL_BUILD_VERBS}\b[^.;]{{0,100}}?\b{_MODEL_OBJECT}"
        r"|\b(?:predictive|statistical|behaviou?r(?:al)?|propensity|risk|credit|fraud|churn|demand|time[- ]series|econometric)"
        r" modell?ing\b|\b(?:own|lead)\w*\b[^.;]{0,40}\bmodell?ing\b(?! (?:team|lifecycle|platform))"
        r"|feature engineering|hyperparameter tuning|\bbacktest\w*|\bcalibration\b|\bmodel (?:development|building)\b"
        r"|\b(?:apply|applies|applying|use|using|utili[sz]\w*) (?:machine learning|\bml\b|deep learning|predictive)"
        r" (?:techniques|methods|models)|\bforecasting (?:using|with)\b|time[- ]series (?:forecasting|model\w*)"
        r"|\b(?:ranking|recommend(?:er|ation)) (?:systems?|models?|algorithms?)\b|relevance (?:matching|models?|ranking)"
        r"|collaborative filtering", re.I),
    # Decision experimentation / causal work. Generic "design / run
    # experiments" counts only when the experiment is not a model test
    # (see _EXPERIMENT_ON_MODELS); hypothesis testing alone is not enough.
    "EXPERIMENTATION_DECISION": re.compile(
        r"\ba/b\b|\bab test\w*|split test|switchback|geo[- ]tests?|controlled experiments?|randomi[sz]ed (?:controlled )?(?:trials?|experiments?)"
        r"|experiment(?:s|ation|al)? (?:design|framework|platform|strateg\w*|methodolog\w*)|design of experiments"
        r"|causal (?:inference|analysis|impact|effects?|model\w*|measurement)|\buplift\b|incrementality|treatment effects?"
        r"|counterfactual|quasi-experiment\w*|difference-in-differences|diff-in-diff|synthetic control", re.I),
    "OPTIMISATION": re.compile(
        r"optimi[sz]ation (?:models?|solutions?|algorithms?|capabilit\w+|services?|problems?|engines?)"
        r"|operations research|mathematical (?:programming|optimi[sz]ation)|linear programming|integer programming"
        r"|\bmilp\b|\bsolvers?\b|(?:crew|aircraft|fleet|workforce|vehicle) (?:schedul|assign|rout)\w*"
        r"|route optimi[sz]ation", re.I),
    "ANALYTICS_INSIGHT": re.compile(
        r"\binsights?\b|analytics expert|business questions|deep[- ]dives?|quantitative support|\bkpis?\b|\bokrs?\b"
        r"|performance metrics|key (?:performance )?metrics|storytell\w*|stories with|data-driven recommendations"
        r"|recommendations? (?:to|for)\b|exploratory (?:data )?analys[ie]s|root[- ]causes?"
        r"|analy[sz]\w* [^.;]{0,40}\b(?:trends|patterns|behaviou?r|datasets?)\b|strategic (?:recommendations|perspective)", re.I),
    # BI / MIS-style reporting only — not project/status reporting, grant
    # reports or research documentation.
    "REPORTING": re.compile(
        r"\bdashboards?\b|\bmis\b|visuali[sz]ations?|\bexcel\b|control towers?"
        r"|ad[- ]hoc (?:analys[ie]s|requests|reports?|reporting)"
        r"|\b(?:business|management|regular|weekly|monthly|daily|operational|performance|sales|kpi|executive) report(?:s|ing)?\b"
        r"|\breports?,? (?:and|&) (?:dashboards|visuali[sz]ations)|\bdashboards?,? (?:and |& )?reports?\b", re.I),
    "ML_ENGINEERING": re.compile(
        r"production (?:ml|machine learning|models?|systems?|environments?|services?)|productioni[sz]\w*"
        r"|\bdeploy\w*\b[^.;]{0,50}\b(?:models?|ml|algorithms?)\b|model deployment|(?:real[- ]time|online) inference"
        r"|inference (?:services?|pipelines?)|operationali[sz]\w*|into production|mlops pipelines?|model serving", re.I),
    # Platform / model-operations work. Working WITH DevOps teams or in a
    # DevOps environment is collaboration context, not platform work.
    "ML_PLATFORM": re.compile(
        r"\bml (?:platforms?|infrastructure|stack)\b|machine learning (?:platforms?|infrastructure)|\bmlops\b|\bllmops\b"
        r"|(?:ai|model|training|serving|inference|gpu) infrastructure|platforms?,? (?:and )?tooling"
        r"|tooling,? and infrastructure|infrastructure and systems|feature stores?|model registr\w+|\bci/cd\b"
        r"|\bkubernetes\b|systems behind the ai stack|training framework|framework efficiency"
        r"|(?:cpu|gpu)\b[^.;]{0,40}\bresources?\b|resource (?:allocation|scheduling|operation)"
        r"|model lifecycle (?:management|operations|controls)|\bversioning\b|\blineage\b|\b(?:data|concept) drift\b", re.I),
    "RESEARCH": re.compile(
        r"\bresearch (?:and develop\w*|projects|on|into)\b|conduct\w* [^.;]{0,20}research"
        r"|\bpublications?\b|\bpublish\w*|novel (?:methods?|algorithms?|approaches|measures)", re.I),
    "AI_APPLICATION_DEV": re.compile(
        r"\b(?:build|develop|design|creat|implement|embed|architect|integrat|deliver|prototyp|optimi[sz]|fine-tun|train)\w*\b"
        r"[^.;]{0,80}\b(?:llms?|genai|gen ai|generative ai|rag|retrieval[- ]augmented|agents?|agentic|chatbots?"
        r"|copilots?|prompt pipelines?|ai capabilities|ai applications?|ai-driven use cases|q&a"
        r"|large language(?: [\w/-]+){0,4} models?|foundation(?:al)? models?)\b"
        r"|agentic ai development|ai-supported development|prompt engineering", re.I),
    # Delivery / project management of AI or data initiatives. No track maps
    # this label: a job whose only evidenced work is delivery management is
    # OFF_VOCABULARY (work outside the DS track vocabulary), not UNKNOWN.
    "PROJECT_DELIVERY": re.compile(
        r"\bproject (?:plans?|timelines?|delivery|governance|controls|closure|documentation|objectives|status|updates)\b"
        r"|\bmilestones\b|\buat\b|user acceptance|go-live|production readiness|stakeholder (?:sign-offs?|acceptance)"
        r"|delivery progress|\bhandover\b", re.I),
}


def _labels_for(sentence: str) -> list[str]:
    if _MENTORING.search(sentence):
        return []
    labels = [name for name, rx in LABEL_RULES.items() if rx.search(sentence)]
    if "APPLIED_MODELLING" in labels and (_PLATFORM_CONTEXT.search(sentence) or _GENAI.search(sentence)):
        # Building the systems behind model training is platform work, and
        # fine-tuning LLMs / foundation models or building agents is AI
        # application work — neither is applied DS modelling in this vocabulary.
        labels.remove("APPLIED_MODELLING")
    if (_EXPERIMENT_GENERIC.search(sentence) and not _EXPERIMENT_ON_MODELS.search(sentence)
            and "EXPERIMENTATION_DECISION" not in labels):
        labels.append("EXPERIMENTATION_DECISION")
    return labels


def build_work_profile(job) -> WorkProfile:
    sentences = work_sentences(job)
    evidence: dict[str, list[EvidenceRef]] = {}
    for ref in sentences:
        for label in _labels_for(ref.text):
            evidence.setdefault(label, []).append(ref.model_copy(update={"rule_id": f"{label}:{ref.rule_id}"}))
    labels = [WorkLabel(label=name, evidence=refs) for name, refs in evidence.items()]
    if labels:
        return WorkProfile(labels=labels, work_sentence_count=len(sentences))
    return WorkProfile(work_sentence_count=len(sentences),
                       unknown_reason="no_task_statements" if not sentences else "no_labelled_work")
