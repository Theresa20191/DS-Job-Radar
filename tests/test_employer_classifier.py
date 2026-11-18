import pytest

from src.enrichment.employer_classifier import classify_employer
from src.models.processed_job import ProcessedJob

RECRUITERS_CFG = {
    "known_recruiter_names": ["People Profilers", "Evolution Recruitment Solutions", "Allegis Group"],
    "generic_keywords": ["recruit", "staffing", "talent acquisition", "manpower"],
}


def make_job(**overrides) -> ProcessedJob:
    defaults = dict(job_id="job_1", company="Acme Bank Sdn Bhd")
    defaults.update(overrides)
    return ProcessedJob(**defaults)


def test_known_recruiter_name_detected():
    job = make_job(company="People Profilers Pte Ltd")
    clarity, reason = classify_employer(job, RECRUITERS_CFG)
    assert clarity == "RECRUITER"
    assert "People Profilers" in reason


def test_generic_recruiter_keyword_detected():
    job = make_job(company="Bright Staffing Solutions")
    clarity, reason = classify_employer(job, RECRUITERS_CFG)
    assert clarity == "RECRUITER"


def test_direct_employer_default():
    job = make_job(company="AIA Malaysia")
    clarity, reason = classify_employer(job, RECRUITERS_CFG)
    assert clarity == "DIRECT_EMPLOYER"


def test_unknown_when_no_company():
    job = make_job(company="")
    clarity, reason = classify_employer(job, RECRUITERS_CFG)
    assert clarity == "UNKNOWN"


def test_duplicate_companies_signal_takes_priority():
    job = make_job(company="Jeet Analytics Pte Ltd", duplicate_companies=["Unison Consulting Pte Ltd"])
    clarity, reason = classify_employer(job, RECRUITERS_CFG)
    assert clarity == "RECRUITER"
    assert "Unison Consulting" in reason


def test_no_false_positive_on_unrelated_company():
    # "Consulting" alone is not in generic_keywords — shouldn't trigger.
    job = make_job(company="Avensys Consulting Pte Ltd")
    clarity, reason = classify_employer(job, RECRUITERS_CFG)
    assert clarity == "DIRECT_EMPLOYER"


# Regulatory agency markers in the job text. Markers are quoted from real
# postings (Talentsis, RN Care, Cygnify, Achieve Career Consultant, Avensys);
# the JTKSM wording is illustrative — no Malaysian licence appears in the corpus.
@pytest.mark.parametrize("company, description", [
    ("TALENTSIS PTE. LTD.", "Work Location: East / West (EA Reg No: 20C0312) Please email a copy of your resume"),
    ("RN CARE PTE. LTD.", "Only shortlisted candidates will be contacted. RN Care Pte. Ltd EA License No: 17C8900"),
    ("CYGNIFY PTE. LTD.", "UEN: 202329526G EA Licence: 23S2061"),
    ("Acme Pte Ltd", "Apply to our consultant (EA Personnel Reg No: R1234567)."),
    ("ACHIEVE CAREER CONSULTANT PTE LTD", "send your resume to David (EA Personnel Reg. No. R1800004, Achieve Career Consultant Pte Ltd"),
    ("Avensys Consulting Pte Ltd", "Avensys Consulting Pte Ltd EA Licence 12C5759 Privacy Statement"),
    ("Acme Sdn Bhd", "Private Employment Agency Licence No: JTKSM 1172"),
])
def test_regulatory_agency_marker_in_description(company, description):
    clarity, reason = classify_employer(make_job(company=company, description_raw=description), RECRUITERS_CFG)
    assert clarity == "RECRUITER"
    assert "employment-agency registration" in reason


@pytest.mark.parametrize("description", [
    "We partner with our client, a leading bank, to deliver analytics for our clients across the region.",
    "Join our Talent Acquisition team; we work with staffing and recruitment partners.",
    "Our recruitment process has three rounds. Reference R1234567. Version 20C0312.",
])
def test_noisy_agency_wording_is_not_a_marker(description):
    clarity, _ = classify_employer(make_job(company="Acme Bank Sdn Bhd", description_raw=description), RECRUITERS_CFG)
    assert clarity == "DIRECT_EMPLOYER"


def test_earlier_signals_keep_precedence_over_the_description_marker():
    job = make_job(company="People Profilers Pte Ltd", description_raw="EA License Number: 02C4944")
    assert "People Profilers" in classify_employer(job, RECRUITERS_CFG)[1]
    job = make_job(company="Jeet Analytics Pte Ltd", duplicate_companies=["Unison Consulting Pte Ltd"],
                   description_raw="(EA Reg No: 20C0312)")
    assert "Unison Consulting" in classify_employer(job, RECRUITERS_CFG)[1]


# P0-2: registration / licence numbers without the "EA" prefix count only next
# to the posting company's own name.
EPS_TEXT = ("<p>Interested candidates may apply directly to recruiter@example.test (Reg ID: R1234567) for immediate processing"
            "<br /></p><p>EPS Consultants Pte Ltd |95C5630</p>")     # verbatim from the 10 Oct 2026 EPS posting


@pytest.mark.parametrize("description, marker", [
    (EPS_TEXT, "Reg ID: R1234567"),                                                       # real wording, both markers
    ("<p>EPS Consultants Pte Ltd |95C5630</p>", "EPS Consultants Pte Ltd |95C5630"),      # real wording, licence only
    ("Apply to the recruiter (Reg ID: R1234567) for immediate processing. EPS Consultants Pte Ltd", "Reg ID: R1234567"),
    ("EPS CONSULTANTS PTE. LTD. Licence No. 95C5630", "Licence No. 95C5630"),
    ("eps consultants pte ltd / 95C5630", "eps consultants pte ltd / 95C5630"),           # case and separator
])
def test_unprefixed_marker_next_to_the_company_name_is_an_agency_marker(description, marker):
    clarity, reason = classify_employer(make_job(company="EPS CONSULTANTS PTE LTD", description_raw=description), RECRUITERS_CFG)
    assert clarity == "RECRUITER"
    assert reason == f"Job description states an employment-agency registration ('{marker}')"


@pytest.mark.parametrize("company, description", [
    # the same numbers away from the company name (no company identity nearby)
    ("EPS CONSULTANTS PTE LTD", "Licence No. 95C5630. " + "Our team builds credit models across many markets. " * 4
     + "Contact EPS Consultants for details."),
    ("EPS CONSULTANTS PTE LTD", "Interested applicants please send your resume. Reg ID: R1234567"),
    # the number sits next to ANOTHER company's name
    ("Acme Analytics Pte Ltd", "Our partner EPS Consultants Pte Ltd |95C5630 handles payroll."),
    # direct employers with unrelated numbers next to their own name
    ("Acme Analytics Pte Ltd", "Acme Analytics Pte Ltd (UEN 201912345K), 20 Cecil Street #12-01 S049705"),
    ("Acme Analytics Pte Ltd", "Acme Analytics Pte Ltd | Reg. No. 199601303W | 12 months contract"),
    ("Acme Analytics Pte Ltd", "Acme Analytics Pte Ltd Ref 22S1234 Version 20C0312"),
    ("Acme Analytics Pte Ltd", "Acme Analytics registered 120 patents and holds licence MAS-2024-17."),
    ("Acme Analytics Pte Ltd", "Acme Analytics Pte Ltd - 2024 Registration of 1200 users, R12345 reference"),
    # a company name too short to locate reliably in free text
    ("ABC Pte Ltd", "ABC Pte Ltd |95C5630"),
])
def test_unrelated_numbers_or_other_names_do_not_reclassify_a_direct_employer(company, description):
    clarity, _ = classify_employer(make_job(company=company, description_raw=description), RECRUITERS_CFG)
    assert clarity == "DIRECT_EMPLOYER"


def test_prefixed_markers_keep_their_reason_and_precedence_over_unprefixed_ones():
    text = "Avensys Consulting Pte Ltd EA Licence 12C5759 | Reg ID: R1234567"
    _, reason = classify_employer(make_job(company="Avensys Consulting Pte Ltd", description_raw=text), RECRUITERS_CFG)
    assert reason == "Job description states an employment-agency registration ('EA Licence 12C5759')"


@pytest.mark.parametrize("company, core", [
    ("EPS CONSULTANTS PTE LTD", "EPS CONSULTANTS"), ("TALENTSIS PTE. LTD.", "TALENTSIS"),
    ("ZENITH INFOTECH (S) PTE LTD.", "ZENITH INFOTECH"), ("RANDSTAD PTE. LIMITED", "RANDSTAD"),
    ("Acme Sdn Bhd", "Acme"), ("Limited Edition Games Pte Ltd", "Limited Edition Games"), ("ABC Pte Ltd", None), ("", None),
])
def test_company_core_strips_only_trailing_legal_suffixes(company, core):
    from src.enrichment.employer_classifier import _company_core
    assert _company_core(company) == core
