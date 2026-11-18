"""location_score. commute_score is deliberately not computed in the MVP —
no office-geocoding/commute source exists yet (section 18: don't invent
commute times); that's a V2 enrichment module."""


# Declared value for a job inside the candidate's target locations. Also
# used to derive the tier reference job (scoring/tiers.py).
IN_TARGET_LOCATION_SCORE = 90.0


def score(job, profile: dict, config: dict) -> dict:
    targets = [t.lower() for t in profile["target_locations"]]

    if job.remote_type == "Remote":
        return {"score": 95.0, "known": True, "reasons": ["Fully remote — no location constraint"]}

    loc_text = f"{job.city} {job.location_normalized} {job.country}".lower()
    matched = [t for t in targets if t and t in loc_text]

    if matched:
        return {
            "score": IN_TARGET_LOCATION_SCORE,
            "known": True,
            "reasons": [f"Located in {job.location_normalized}, {job.country} — within target geography"],
        }
    if job.country in ("Malaysia", "Singapore"):
        return {
            "score": 70.0,
            "known": True,
            "reasons": [f"Within target country ({job.country}) but specific area not in your target list"],
        }
    return {
        "score": 20.0,
        "known": True,
        "reasons": [f"'{job.country}' is outside your primary target geography"],
    }
