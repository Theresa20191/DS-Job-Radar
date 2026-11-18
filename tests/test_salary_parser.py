import json

from src.parsers.salary_parser import parse_salary, to_myr


def test_mycareersfuture_salary_known():
    raw = json.dumps({"minimum": 7500, "maximum": 14000, "type": {"salaryType": "Monthly"}})
    result = parse_salary(raw, "mycareersfuture")
    assert result["salary_min"] == 7500
    assert result["salary_max"] == 14000
    assert result["salary_currency"] == "SGD"
    assert result["salary_period"] == "Monthly"
    assert result["salary_unknown"] is False


def test_mycareersfuture_salary_missing():
    raw = json.dumps({"minimum": None, "maximum": None, "type": {}})
    result = parse_salary(raw, "mycareersfuture")
    assert result["salary_unknown"] is True
    assert result["salary_min"] is None


def test_hiredly_salary_range():
    result = parse_salary("12000 - 16000", "hiredly")
    assert result["salary_min"] == 12000
    assert result["salary_max"] == 16000
    assert result["salary_currency"] == "MYR"
    assert result["salary_unknown"] is False


def test_hiredly_salary_undisclosed():
    result = parse_salary("Undisclosed", "hiredly")
    assert result["salary_unknown"] is True
    assert result["salary_min"] is None
    assert result["salary_max"] is None


def test_hiredly_salary_single_number():
    result = parse_salary("RM 5000", "hiredly")
    assert result["salary_min"] == 5000
    assert result["salary_max"] == 5000


def test_empty_salary_raw():
    result = parse_salary("", "hiredly")
    assert result["salary_unknown"] is True


def test_to_myr_conversion():
    lo, hi = to_myr(1000, 2000, "SGD")
    assert lo > 1000  # SGD -> MYR should scale up given rate > 1
    assert hi > 2000


def test_to_myr_already_myr():
    lo, hi = to_myr(3000, 4000, "MYR")
    assert (lo, hi) == (3000, 4000)


def test_to_myr_unknown():
    lo, hi = to_myr(None, None, None)
    assert (lo, hi) == (None, None)
