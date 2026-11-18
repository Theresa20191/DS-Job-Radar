"""Three-pass deduplication.

Pass 1 (within-source): the same job_id can appear multiple times because
the collection stage runs many overlapping keyword queries against the
same source (this is expected — see smoke test, Hiredly duplicate_rate
62%). Group by raw.job_id, union matched_queries, keep the richest record.

Pass 2 (cross-source, exact identity): group the pass-1 records by
(company_normalized, job_title_normalized, location_normalized) so the
same real-world job posted on two different sources merges into one
ProcessedJob.

Pass 3 (content-similarity, recruiter reposts): the same job is often
posted by two different staffing agencies under two different company
names — pass 2 can't catch this since company_normalized genuinely
differs. Within jobs sharing (job_title_normalized, country), pairs whose
description+requirements text has a rapidfuzz.fuzz.ratio() >= 90 are
merged. How 90 was chosen: real
recruiter reposts scored 100.0 and 99.4 in testing, while a genuinely
similar-but-different posting (same title, same industry, different
responsibilities) scored 79.7 and a same-title-different-job pair scored
42.2. 90 sits in the gap between "duplicate" and "merely similar."

Provenance is never discarded: sources, source_urls, source_job_ids, and
matched_queries all accumulate across all three passes. Pass-3 merges
additionally record which companies were folded in, in `duplicate_companies`.
"""

import hashlib
from datetime import date, datetime, timezone

from rapidfuzz import fuzz

from src.models.processed_job import ProcessedJob
from src.models.raw_job import RawJob
from src.normalizers.build_processed_job import extract_fields

CONTENT_SIMILARITY_THRESHOLD = 90.0


def _canonical_job_id(company_normalized: str, title_normalized: str, location_normalized: str) -> str:
    key = f"{company_normalized}|{title_normalized}|{location_normalized}"
    return "job_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _completeness_key(job: ProcessedJob) -> tuple:
    """Higher is better. Used to pick which record's metadata 'wins' when
    merging duplicates, rather than arbitrarily keeping the first one."""
    return (not job.salary_unknown, bool(job.requirements_raw.strip()), len(job.description_raw))


def _content_text(job: ProcessedJob) -> str:
    return f"{job.description_raw} {job.requirements_raw}"


def _merge_content_duplicates(jobs: list[ProcessedJob], threshold: float = CONTENT_SIMILARITY_THRESHOLD) -> list[ProcessedJob]:
    buckets: dict[tuple, list[int]] = {}
    for i, job in enumerate(jobs):
        buckets.setdefault((job.job_title_normalized, job.country), []).append(i)

    # union-find over job indices
    parent = list(range(len(jobs)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for indices in buckets.values():
        if len(indices) < 2:
            continue
        texts = {i: _content_text(jobs[i]) for i in indices}
        for a_idx in range(len(indices)):
            for b_idx in range(a_idx + 1, len(indices)):
                i, j = indices[a_idx], indices[b_idx]
                if not texts[i].strip() or not texts[j].strip():
                    continue
                similarity = fuzz.ratio(texts[i], texts[j])
                if similarity >= threshold:
                    union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(len(jobs)):
        clusters.setdefault(find(i), []).append(i)

    merged: list[ProcessedJob] = []
    for indices in clusters.values():
        group = [jobs[i] for i in indices]
        if len(group) == 1:
            merged.append(group[0])
            continue

        representative = max(group, key=_completeness_key)
        other_companies = sorted({j.company for j in group if j.company != representative.company})

        merged_sources = sorted(set().union(*(j.sources for j in group)))
        merged_source_job_ids = sorted(set().union(*(j.source_job_ids for j in group)))
        merged_source_urls = sorted(set().union(*(j.source_urls for j in group)))
        merged_matched_queries = sorted(set().union(*(j.matched_queries for j in group)))
        merged_duplicate_companies = sorted(set(other_companies) | set().union(*(j.duplicate_companies for j in group)))

        merged_job = representative.model_copy(
            update={
                "sources": merged_sources,
                "source_job_ids": merged_source_job_ids,
                "source_urls": merged_source_urls,
                "matched_queries": merged_matched_queries,
                "duplicate_companies": merged_duplicate_companies,
                "first_seen_at": min(j.first_seen_at for j in group),
                "last_seen_at": max(j.last_seen_at for j in group),
            }
        )
        merged.append(merged_job)

    return merged


def deduplicate(raw_jobs: list[RawJob], as_of_date: date) -> list[ProcessedJob]:
    # --- pass 1: within-source, by raw.job_id ---
    by_job_id: dict[str, dict] = {}
    for raw in raw_jobs:
        entry = by_job_id.setdefault(
            raw.job_id,
            {
                "raws": [],
                "matched_queries": set(),
                "sources": set(),
                "source_urls": set(),
                "source_job_ids": set(),
            },
        )
        entry["raws"].append(raw)
        if raw.query_used:
            entry["matched_queries"].add(raw.query_used)
        entry["sources"].add(raw.source)
        entry["source_urls"].add(raw.source_url)
        entry["source_job_ids"].add(raw.source_job_id)

    pass1_records = []
    for job_id, entry in by_job_id.items():
        raws = entry["raws"]
        primary = max(raws, key=lambda r: len(r.job_description or ""))
        fields = extract_fields(primary, as_of_date)
        pass1_records.append(
            {
                "primary": primary,
                "fields": fields,
                "matched_queries": entry["matched_queries"],
                "sources": entry["sources"],
                "source_urls": entry["source_urls"],
                "source_job_ids": entry["source_job_ids"],
                "first_seen_at": min(r.scraped_at for r in raws),
                "last_seen_at": max(r.scraped_at for r in raws),
            }
        )

    # --- pass 2: cross-source, by normalized (company, title, location) ---
    by_identity: dict[tuple, list[dict]] = {}
    for rec in pass1_records:
        key = (
            rec["fields"]["company_normalized"],
            rec["fields"]["job_title_normalized"],
            rec["fields"]["location_normalized"],
        )
        by_identity.setdefault(key, []).append(rec)

    processed_jobs = []
    for (company_norm, title_norm, location_norm), recs in by_identity.items():
        merged_matched_queries: set = set()
        merged_sources: set = set()
        merged_source_urls: set = set()
        merged_source_job_ids: set = set()
        first_seen = min(r["first_seen_at"] for r in recs)
        last_seen = max(r["last_seen_at"] for r in recs)
        for r in recs:
            merged_matched_queries |= r["matched_queries"]
            merged_sources |= r["sources"]
            merged_source_urls |= r["source_urls"]
            merged_source_job_ids |= r["source_job_ids"]

        best = max(recs, key=lambda r: len(r["primary"].job_description or ""))
        fields = best["fields"]
        primary = best["primary"]

        job_id = _canonical_job_id(company_norm, title_norm, location_norm)

        processed_jobs.append(
            ProcessedJob(
                job_id=job_id,
                sources=sorted(merged_sources),
                source_job_ids=sorted(merged_source_job_ids),
                source_urls=sorted(merged_source_urls),
                matched_queries=sorted(merged_matched_queries),
                canonical_url=primary.source_url,
                company=primary.company,
                company_normalized=company_norm,
                job_title=primary.job_title,
                job_title_normalized=title_norm,
                employment_type=fields["employment_type"],
                country=fields["country"],
                city=fields["city"],
                location_normalized=fields["location_normalized"],
                remote_type=fields["remote_type"],
                office_location=fields["office_location"],
                salary_min=fields["salary_min"],
                salary_max=fields["salary_max"],
                salary_currency=fields["salary_currency"],
                salary_period=fields["salary_period"],
                salary_raw=primary.salary_raw,
                salary_unknown=fields["salary_unknown"],
                salary_min_myr=fields["salary_min_myr"],
                salary_max_myr=fields["salary_max_myr"],
                posted_date=fields["posted_date"],
                scraped_at=primary.scraped_at,
                first_seen_at=first_seen,
                last_seen_at=last_seen,
                job_age_days=fields["job_age_days"],
                as_of_date=fields["as_of_date"],
                status="NEW",
                description_raw=fields["description_raw"],
                requirements_raw=fields["requirements_raw"],
                responsibilities_raw=fields["responsibilities_raw"],
            )
        )

    # --- pass 3: content-similarity, catches recruiter reposts pass 2 misses ---
    return _merge_content_duplicates(processed_jobs)
