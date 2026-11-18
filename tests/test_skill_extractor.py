from src.enrichment.skill_extractor import extract_required_and_preferred, extract_skills, split_required_preferred


def test_extract_skills_basic():
    text = "We use Python, SQL, and XGBoost for our machine learning models."
    skills = extract_skills(text)
    assert "Python" in skills
    assert "SQL" in skills
    assert "XGBoost" in skills
    assert "Machine Learning" in skills


def test_extract_skills_empty():
    assert extract_skills("") == []


def test_split_required_preferred_with_marker():
    text = "Must know Python and SQL. Nice to have: AWS and Docker experience."
    required, preferred = split_required_preferred(text)
    assert "Python" in required
    assert "AWS" in preferred


def test_split_required_preferred_no_marker():
    text = "Must know Python and SQL."
    required, preferred = split_required_preferred(text)
    assert required == text
    assert preferred == ""


def test_extract_required_and_preferred_distinct():
    description = "Great team building forecasting models with Tableau dashboards."
    requirements = "Required: Python, SQL, scikit-learn. Preferred: AWS, Docker."
    required, preferred = extract_required_and_preferred(description, requirements)
    assert "Python" in required
    assert "Scikit-learn" in required
    assert "AWS" in preferred
    assert "Docker" in preferred
    assert "AWS" not in required
