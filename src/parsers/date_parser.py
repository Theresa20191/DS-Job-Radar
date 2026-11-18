"""Parses posted_date_raw (ISO date or ISO datetime, depending on source)
into a normalized posted_date and job_age_days. Never guesses a date.

job_age_days is a CALENDAR-day difference against an explicit `as_of_date`
(the scoring-run date), not a wall-clock timedelta:
  - explicit: nothing downstream reads the current date, so replaying the
    same corpus with the same as_of_date is reproducible;
  - calendar: a posting's age no longer depends on the time of day a run
    happens to start (a job posted at 17:54 used to age one day later than
    a job posted at 09:00 on the same date).
The thresholds that consume job_age_days are unchanged: hard_filters'
max_job_age_days and job_risk's >60-day staleness flag.
"""

from datetime import date, datetime, timezone

from dateutil import parser as dateutil_parser


def parse_posted_date(posted_date_raw: str, as_of_date: date) -> dict:
    if not posted_date_raw:
        return {"posted_date": None, "job_age_days": None}
    try:
        dt = dateutil_parser.isoparse(posted_date_raw)
    except (ValueError, TypeError):
        return {"posted_date": None, "job_age_days": None}

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if isinstance(as_of_date, datetime):
        as_of_date = as_of_date.astimezone(timezone.utc).date()

    return {"posted_date": dt.date().isoformat(), "job_age_days": (as_of_date - dt.date()).days}
