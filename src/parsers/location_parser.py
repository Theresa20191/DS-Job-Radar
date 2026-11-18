"""Parses location_raw into structured country/city/location fields.
Country is inferred from source (MCF=Singapore-only, Hiredly=Malaysia-only
by construction, per the source feasibility check) rather than parsed from
free text, which is far more reliable than string-matching addresses."""

import json

SOURCE_COUNTRY = {
    "mycareersfuture": "Singapore",
    "hiredly": "Malaysia",
}


def parse_location(location_raw: str, source: str) -> dict:
    country = SOURCE_COUNTRY.get(source, "unknown")
    city = "unknown"
    location_normalized = ""

    if source == "mycareersfuture":
        try:
            data = json.loads(location_raw) if location_raw else {}
        except (json.JSONDecodeError, TypeError):
            data = {}
        districts = data.get("districts") or []
        region = districts[0].get("region") if districts else None
        city = "Singapore"
        location_normalized = region or "Singapore"

    elif source == "hiredly":
        parts = [p.strip() for p in (location_raw or "").split("|")]
        addr = parts[0] if parts and parts[0] else ""
        state = parts[1] if len(parts) > 1 and parts[1] else ""
        city = state or "unknown"
        location_normalized = state or addr

    text_l = (location_raw or "").lower()
    if "remote" in text_l:
        remote_type = "Remote"
    elif "hybrid" in text_l:
        remote_type = "Hybrid"
    elif location_normalized:
        remote_type = "Onsite"
    else:
        remote_type = "unknown"

    return {
        "country": country,
        "city": city,
        "location_normalized": location_normalized,
        "remote_type": remote_type,
        "office_location": location_raw or "",
    }
