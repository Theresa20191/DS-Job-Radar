from src.normalizers.company_normalizer import normalize_company
from src.normalizers.title_normalizer import normalize_title


def test_normalize_title_strips_parens():
    assert normalize_title("Data Scientist (GenAI), Experienced Hire") == "data scientist experienced hire"


def test_normalize_title_lowercases():
    assert normalize_title("Senior Data Scientist") == "senior data scientist"


def test_normalize_company_strips_sdn_bhd():
    assert normalize_company("Example Systems Sdn Bhd") == "example systems"


def test_normalize_company_strips_pte_ltd():
    assert normalize_company("Allegis Group Singapore Pte Ltd") == "allegis group singapore"


def test_normalize_company_empty():
    assert normalize_company("") == ""
