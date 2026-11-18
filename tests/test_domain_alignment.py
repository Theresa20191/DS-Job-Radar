"""D2 domain placement (frozen D2-a/b/c + narrow R2 Risk rule).

The pinned expectations run on 15 real postings saved in
tests/fixtures/d2_pinned_jobs.json, so they don't depend on a re-scrape."""

# Public copy: tests that need the private real-posting fixture (d2_pinned_jobs.json) are not included;
# the tests below do not use it and are unchanged apart from fictional example-profile values.

import json
from pathlib import Path

import pytest

from src.models.processed_job import ProcessedJob
from src.scoring import domain_alignment
from src.scoring.domain_alignment import GENERIC_TERMS, SPECIFIC_TERMS, place_evidence, term_matches

PROFILE = {"domain": ["Banking", "Fraud", "AML", "Risk", "Credit Risk", "Fintech", "Financial Crime",
                      "Insurance", "Payments", "Credit"]}


def job(**kw) -> ProcessedJob:
    base = dict(job_id="j", job_title="Data Scientist", company_normalized="acme", description_raw="", requirements_raw="")
    return ProcessedJob(**{**base, **kw})


def band(**kw):
    return domain_alignment.score(job(**kw), PROFILE).band


# --- pinned corpus expectations ---------------------------------------------------


# --- D2-a classification ------------------------------------------------------------

def test_generic_and_specific_sets_are_frozen():
    assert GENERIC_TERMS == {"Risk", "Credit", "Insurance", "Payments"}
    assert SPECIFIC_TERMS == {"AML", "Fraud", "Financial Crime", "Credit Risk", "Fintech", "Banking"}


def test_generic_term_can_qualify_when_central():
    assert band(job_title="Senior Product Data Scientist, Consumer Payments") == "CORE"


def test_generic_peripheral_is_non_evidence():
    assert band(requirements_raw="Experience in e-commerce, logistics, or payments.") is None


# --- D2-b narrow R2 -----------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Ensure risk governance and enterprise risk management across the group.",
    "Support risk management processes with deal teams.",
    "Build RAG pipelines with strong emphasis on hallucination reduction and risk controls.",
    "Perform bias/risk assessment and ethical AI practices.",
    "Manage project risk, issue and dependency tracking.",
    "Maintain data integrity and risk dashboards.",
    "Our Trust & Safety team reviews content quality.",
])
def test_risk_non_qualifying_cases(text):
    assert band(description_raw=text) is None


def test_risk_with_fraud_context_in_sentence_qualifies():
    assert band(description_raw="Develop risk control models to combat account abuse and fraud.") == "SUBSTANTIVE"


def test_risk_team_identity_qualifies_via_job_level_context():
    text = ("The Business Risk Integrated Control team is missioned to protect users.\n"
            "It covers business risk areas such as account integrity, anti-spam and fraud.")
    assert band(description_raw=text) == "CORE"


def test_bare_risk_control_without_context_does_not_qualify():
    assert band(description_raw="Define risk control measurements for the platform.") is None


# --- D2-c ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Key medical and insurance benefits, maternity expenses and flexible work.",
    "We are an equal opportunity employer regardless of background.",
    "Substantial investment funding from a well known Fintech organization.",
    "Experience from any data-rich industry is welcome — e-commerce, fintech, logistics.",
    "Partnering with Legal, Risk, Compliance teams on data privacy.",
])
def test_non_evidence_cases(text):
    assert band(description_raw=text) is None


def test_client_industry_is_limited():
    assert band(description_raw="We service a client base across banking and financial services.") == "LIMITED"


def test_alternative_industry_list_is_limited():
    assert band(requirements_raw="Experience in marketplaces, e-commerce, or fintech.") == "LIMITED"


def test_count_never_raises_band():
    assert band(description_raw="We serve clients in banking, fintech and banking and fintech again.") == "LIMITED"


def test_band_is_highest_placement():
    assert band(description_raw="Develop models to detect fraud in card transactions.",
                requirements_raw="Experience in banking or fintech.") == "SUBSTANTIVE"


def test_specific_term_in_title_is_core():
    assert band(job_title="Data Scientist, Fraud") == "CORE"


def test_no_overlap_is_unknown_not_zero():
    result = domain_alignment.score(job(description_raw="We build recommendation systems for e-commerce."), PROFILE)
    assert (result.known, result.band, result.score, result.unknown_reason) == (False, None, None, "no_qualifying_overlap")


# --- term matching carried over from domain_fit ---------------------------------------

def test_aml_seamless_false_positive():
    assert not term_matches("AML", "Ensure seamless integration of data science models.")


def test_aml_applied_ml_false_positive():
    assert not term_matches("AML", "Data AML is our machine learning platform.")


def test_aml_genuine_phrase_and_kyc():
    assert term_matches("AML", "Experience with AML compliance and KYC processes.")
    assert term_matches("AML", "Senior Data Scientist - KYC")


def test_insurance_not_matched_inside_assurance():
    assert not term_matches("Insurance", "quality assurance tooling")
    assert not place_evidence(job(company_normalized="assurance tech"), ["Insurance", "Banking"])
