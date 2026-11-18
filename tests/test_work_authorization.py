import pytest

from src.enrichment.work_authorization import classify_work_authorization
from src.models.processed_job import ProcessedJob


def make_job(description_raw="", requirements_raw="", job_title="Data Scientist") -> ProcessedJob:
    return ProcessedJob(job_id="job_1", job_title=job_title, description_raw=description_raw, requirements_raw=requirements_raw)


# --- FAIL: explicit denial of eligibility to foreign applicants -------------

@pytest.mark.parametrize("phrase, restriction_type", [
    ("Malaysian citizens only", "NATIONALITY_SPECIFIC"),
    ("Only Malaysian citizens", "NATIONALITY_SPECIFIC"),
    ("Malaysian nationality required", "NATIONALITY_SPECIFIC"),
    ("Applicants must be Malaysian citizens", "NATIONALITY_SPECIFIC"),
    ("Must be a Malaysian citizen", "NATIONALITY_SPECIFIC"),
    ("Local Malaysian citizens only", "NATIONALITY_SPECIFIC"),
    ("Local citizens only", "NATIONALITY_SPECIFIC"),
    ("Malaysians only", "NATIONALITY_SPECIFIC"),
    ("Malaysian candidates only", "NATIONALITY_SPECIFIC"),
    ("No foreigners", "FOREIGNERS_NOT_ACCEPTED"),
    ("Foreigners are not accepted", "FOREIGNERS_NOT_ACCEPTED"),
    ("Foreign nationals are not eligible", "FOREIGNERS_NOT_ACCEPTED"),
    ("Foreign applicants are not eligible", "FOREIGNERS_NOT_ACCEPTED"),
])
def test_explicit_exclusion_fails_with_evidence(phrase, restriction_type):
    result = classify_work_authorization(make_job(requirements_raw=f"Eligibility: {phrase}. Strong Python required."))
    assert result.status == "FAIL"
    assert result.restriction_type == restriction_type
    ev = next(e for e in result.evidence if e.restriction_type == restriction_type)
    assert ev.matched_text.lower() in phrase.lower() or phrase.lower() in ev.matched_text.lower()
    assert ev.source_field == "requirements_raw"
    assert phrase in ev.context
    assert ev.matched_text in result.reason


def test_exclusion_in_html_description():
    result = classify_work_authorization(make_job(description_raw="<p>Other Requirements</p><ul><li>Malaysian citizen only</li></ul>"))
    assert result.status == "FAIL"
    assert result.evidence[0].source_field == "description_raw"
    assert result.evidence[0].matched_text == "Malaysian citizen only"


def test_exclusion_in_title():
    result = classify_work_authorization(make_job(job_title="Data Scientist (Malaysians only)"))
    assert result.status == "FAIL"
    assert result.evidence[0].source_field == "job_title"


# --- must NOT fail -----------------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "Malaysian candidates preferred",
    "Local candidates preferred",
    "Malaysian PR preferred",
    "Local candidates only",
    "Singaporeans preferred",
    "Malaysian citizens preferred",
    "No visa sponsorship",
    "Visa sponsorship unavailable",
    "Existing work authorization required",
    "Must have a valid work permit",
    "Must have a valid work pass",
    "Open to Singaporeans and PR",
    "SC/PR only",
    "This role is open to Singapore Citizens or PR only",
    "Open to Malaysian citizens and non-Malaysians with a valid work permit",
    "Not limited to Malaysians",
    "Non-Malaysians are welcome",
    "Foreigners encouraged to apply",
    "Regardless of nationality",
    "We hire regardless of national origin",
    "Not only Malaysian citizens are eligible",
])
def test_non_exclusion_language_never_fails(phrase):
    result = classify_work_authorization(make_job(requirements_raw=f"{phrase}. Strong Python required."))
    assert result.status != "FAIL"
    assert result.restriction_type not in ("FOREIGNERS_NOT_ACCEPTED", "NATIONALITY_SPECIFIC")


@pytest.mark.parametrize("phrase", [
    "No visa sponsorship", "Must have a valid work permit", "SC/PR only", "Local candidates only",
    "Open to Malaysian citizens and non-Malaysians with a valid work permit",
])
def test_restrictions_are_unknown_other_explicit_restriction_with_evidence(phrase):
    result = classify_work_authorization(make_job(requirements_raw=phrase))
    assert (result.status, result.restriction_type) == ("UNKNOWN", "OTHER_EXPLICIT_RESTRICTION")
    assert result.evidence and result.evidence[0].source_field == "requirements_raw"


@pytest.mark.parametrize("phrase", ["Not limited to Malaysians", "Non-Malaysians are welcome",
                                    "Foreigners encouraged to apply", "Open to all nationalities; visa sponsorship available."])
def test_explicit_openness_is_pass(phrase):
    result = classify_work_authorization(make_job(description_raw=phrase))
    assert (result.status, result.restriction_type) == ("PASS", "NONE")


@pytest.mark.parametrize("phrase", ["Malaysian candidates preferred", "Local candidates preferred",
                                    "Malaysian PR preferred", "Singaporeans preferred", "Malaysian citizens preferred"])
def test_preference_is_unknown_with_evidence(phrase):
    result = classify_work_authorization(make_job(requirements_raw=phrase))
    assert (result.status, result.restriction_type) == ("UNKNOWN", "UNKNOWN")
    assert result.evidence[0].matched_text == phrase


@pytest.mark.parametrize("text", [
    "We are committed to equal employment opportunity regardless of sex, race, religion, nationality, disability.",
    "Our team of 100+ innovators from 18 nationalities.",
    "We sponsor bright women from disadvantaged backgrounds through university.",
    "Based in our Singapore office, working on fraud analytics.",
    # Real corpus false positive (Pfizer MSL): a language requirement, not work authorization.
    "Local language proficiency is preferred.",
    "Ability to communicate in English and local office language(s).",
    "Ensure compliance with local employment regulations.",
])
def test_boilerplate_and_location_are_not_evidence(text):
    result = classify_work_authorization(make_job(description_raw=text))
    assert (result.status, result.restriction_type, result.evidence) == ("UNKNOWN", "UNKNOWN", [])


# --- contradiction -------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Malaysian citizens only. Non-Malaysians with valid work permits are also eligible.",
    "Malaysian citizens only, but non-Malaysians with valid work permits may apply.",
])
def test_contradiction_is_unknown_and_keeps_both_evidence(text):
    result = classify_work_authorization(make_job(requirements_raw=text))
    assert result.status == "UNKNOWN"
    types = {e.restriction_type for e in result.evidence}
    assert "NATIONALITY_SPECIFIC" in types
    assert "OTHER_EXPLICIT_RESTRICTION" in types or "NONE" in types
    assert "contradictory" in result.reason


def test_no_mention_is_unknown_without_evidence():
    result = classify_work_authorization(make_job(description_raw="We build data pipelines and dashboards."))
    assert (result.status, result.restriction_type, result.evidence) == ("UNKNOWN", "UNKNOWN", [])
