"""Years-of-experience extraction. Cases quoted from real postings in the
2026-10-05 corpus audit are marked; the extractor returns the minimum years a
posting asks for, or None when it states none."""

import pytest

from src.enrichment.requirement_extractor import extract_years_experience, years_experience_candidates


@pytest.mark.parametrize("text, years", [
    # corpus wording (2026-10-05 audit)
    ("Shall have at least three (3) years of implementation and deployment experience in machine learning", 3.0),  # Talentsis
    ("Five or more years of experience in data science or proven experience.", 5.0),                             # Zurich
    ("Minimum of five years of work experience in professional services environment", 5.0),                      # EY
    ("2–5 years of experience in manufacturing, process, or industrial engineering", 2.0),                         # Renesas
    ("A minimum of 4-7 years of professional experience in Data Science or related technical fields", 4.0),      # Maxis
    ("Typically, 10+ years of relevant experience across data science", 10.0),                                    # AIA
    ("3 or more years of relevant industry experience as a hands-on data scientist", 3.0),                        # SIA
    ("One to two years of relevant work, internship, research, or project experience", 1.0),                     # Agilent
    ("Core ML3-5 yrs delivering ML projects end-to-end", 3.0),                                                    # Thales
    # written numbers, with and without a numeric echo
    ("three years", 3.0), ("three (3) years", 3.0), ("five years", 5.0), ("five (5) years", 5.0),
    # minimum wording
    ("at least 3 years", 3.0), ("at least three (3) years", 3.0), ("minimum of 5 years", 5.0),
    ("five or more years", 5.0), ("5+ years", 5.0), ("minimum of five years", 5.0),
    # ranges use the lower bound
    ("2-5 years", 2.0), ("2–5 years", 2.0), ("4-7 years", 4.0), ("1 - 5 years relevant experience", 1.0),
    # behaviour that was already correct stays correct
    ("Minimum 3 years of hands-on experience in machine learning implementation", 3.0),
    ("3+ years of experience", 3.0), ("5+ years in data science", 5.0),
    ("3 years + of experience in AI engineering or 5+ years of experience in software engineering", 3.0),
])
def test_minimum_years_are_extracted(text, years):
    assert extract_years_experience(text) == years


@pytest.mark.parametrize("text", [
    "Open to fresh graduates or candidates with up to 2 years of experience",                                    # Evolution
    "up to 2 years", "a maximum of 3 years of experience", "no more than 2 years of experience",
    "1 year contract", "2 year project", "3 years remaining", "3 years remaining on the programme", "a 2-year project",
    "This is a 1 year contract role with the possibility of extension.",
    "You will support a 2 year project to migrate our forecasting models to the cloud.",
    "There are 3 years remaining on the current transformation programme.",
    "As pioneering innovators for over 100 years, we're transforming our organisation",
    "", None,
])
def test_non_minimum_year_mentions_are_not_requirements(text):
    assert extract_years_experience(text) is None


def test_the_smallest_stated_requirement_is_kept():
    text = ("1 year contract, renewable. After more than 60 years of growth we are hiring. "
            "At least 1 - 3 years of experience with implementing data science solutions.")
    assert extract_years_experience(text) == 1.0
    assert extract_years_experience("up to 2 years for juniors; seniors need 5+ years of experience") == 5.0
    assert extract_years_experience("Minimum 5 years of experience in software engineering, "
                                    "including at least 3 years in machine learning.") == 3.0


# Above MAX_PLAUSIBLE_YEARS (30) a "<number> years" phrase is read as company
# age, not a requirement: 30 itself still counts; a genuine 35-year requirement
# becomes None under this cap (none exists in the corpus).
@pytest.mark.parametrize("text, years", [
    ("Candidates should bring 30 years of experience in actuarial modelling.", 30.0),
    ("We are looking for 30+ years of experience leading analytics functions.", 30.0),
    ("Requires 35 years of experience in the insurance industry.", None),
])
def test_the_30_year_cap_boundary(text, years):
    assert extract_years_experience(text) == years


# Company-age statements from the corpus (AIA, Nippon Paint, Capgemini).
@pytest.mark.parametrize("text", [
    "As pioneering innovators for over 100 years, we’re now transforming our organisation to be faster, simpler.",
    "After more than 60 years of growth, NIPSEA Group has over 28,000 employees across 15 markets.",
    "With our strong heritage of nearly 60 years, we are a responsible and diverse group of team members.",
])
def test_company_age_statements_are_not_requirements(text):
    assert extract_years_experience(text) is None
    assert extract_years_experience(text + " Minimum 3 years of experience in data science.") == 3.0


def test_candidates_are_the_requirements_the_minimum_is_taken_from():
    text = "1 year contract. Up to 2 years for juniors. Seniors need 5+ years or 3-6 years of ML experience."
    assert years_experience_candidates(text) == [5.0, 3.0]
    assert extract_years_experience(text) == min(years_experience_candidates(text))
    assert years_experience_candidates("") == [] and years_experience_candidates(None) == []
