import json

from src.parsers.location_parser import parse_location


def test_mycareersfuture_location():
    raw = json.dumps({"districts": [{"region": "Central"}]})
    result = parse_location(raw, "mycareersfuture")
    assert result["country"] == "Singapore"
    assert result["location_normalized"] == "Central"


def test_mycareersfuture_location_no_districts():
    raw = json.dumps({"districts": []})
    result = parse_location(raw, "mycareersfuture")
    assert result["country"] == "Singapore"
    assert result["location_normalized"] == "Singapore"


def test_hiredly_location():
    result = parse_location("Sepang | Kuala Lumpur", "hiredly")
    assert result["country"] == "Malaysia"
    assert result["city"] == "Kuala Lumpur"
    assert result["location_normalized"] == "Kuala Lumpur"


def test_remote_detection():
    result = parse_location("Remote | Malaysia", "hiredly")
    assert result["remote_type"] == "Remote"


def test_unknown_source():
    result = parse_location("somewhere", "jobstreet")
    assert result["country"] == "unknown"
