# Offline demo data (synthetic)

**Everything in this folder is synthetic.** Every company, job title, description, salary, URL (`example.test`),
email address and live-check record was written for the demo. None of it comes from a real job posting, and no
website was visited to produce it.

| File | Contents |
|---|---|
| `raw/mycareersfuture_latest.jsonl` | 5 raw records in the format of the Singapore source (one posting appears twice, as if returned by two search queries) |
| `raw/hiredly_latest.jsonl` | 9 raw records in the format of the Malaysian source (one is a recruiter repost of another job; one is 228 days old) |
| `live_checks.jsonl` | 2 synthetic live-check observations for Singapore postings (one open, one closed) |

Run it from the repository root:

```bash
python scripts/run_demo.py
```

This copies the two raw files into `data/raw/` and runs the unchanged pipeline offline:

```bash
python scripts/run_all.py --skip-scrape --as-of 2026-01-15 --live-checks demo/live_checks.jsonl
```

It decides each job against the fictional example profile in `config/profile.yaml`.

## What the demo produces

Measured on 2026-11-18:

- **Raw records:** 14, which become **12 jobs** after deduplication.
  - Pass 1 merged the posting returned by two queries.
  - Pass 3 merged the recruiter repost by text similarity.
- **Hard filters:** 9 jobs passed and 3 were rejected.
- **Dashboard placements:** Today 4, Verify first 3, Do not invest 2, rejected by the hard filter 3.

| Job (fictional) | Country | Scope | Viability | Priority | Next action | Deciding rule |
|---|---|---|---|---|---|---|
| Data Scientist, Forecasting | Malaysia | FULL | VIABLE | HIGH | RESEARCH_EMPLOYER | PR-9: primary target (APPLIED_DS), no readiness gap |
| Data Scientist, Experimentation | Singapore | FULL | VIABLE | HIGH | TAILOR_RESUME | PR-9: primary target (EXPERIMENTATION_CAUSAL), no readiness gap |
| Machine Learning Engineer | Malaysia | FULL | VIABLE | PRIORITY | TAILOR_RESUME | PR-11: secondary target (ML_ENGINEER) |
| Senior Data Scientist | Malaysia | FULL | VIABLE | SELECTIVE | TAILOR_RESUME | PR-6: asks for 7 years; declared relevant years 1 |
| Data Scientist | Malaysia | FULL | UNKNOWN | VERIFY_FIRST | VERIFY_COMPENSATION | PR-3: salary not disclosed (hard floor applies) |
| Product Data Scientist | Singapore | FULL | UNKNOWN | VERIFY_FIRST | VERIFY_APPLICATION_CHANNEL | PR-3: stored description lists an email; no current live check |
| Decision Scientist | Singapore | FULL | UNKNOWN | VERIFY_FIRST | VERIFY_POSTING_OPEN | PR-3: live check recorded the posting as closed |
| Risk Data Scientist | Singapore | FULL | BLOCKED | DO_NOT_INVEST | DO_NOT_INVEST | PR-1: description has no email address (V-4a′) |
| Junior Data Scientist | Malaysia | FULL | BLOCKED | DO_NOT_INVEST | DO_NOT_INVEST | PR-1: maximum MYR 5,500 below the hard floor MYR 7,500 (V-3) |
| MIS Reporting Analyst | Malaysia | HARD_FILTER_REJECTED | BLOCKED | DO_NOT_INVEST | DO_NOT_INVEST | HC-1: no Data Science component |
| Data Scientist | Malaysia | HARD_FILTER_REJECTED | BLOCKED | DO_NOT_INVEST | DO_NOT_INVEST | HC-2: "citizens only" |
| Data Scientist | Malaysia | HARD_FILTER_REJECTED | BLOCKED | DO_NOT_INVEST | DO_NOT_INVEST | posting older than 120 days |

**The audit verdict is INVESTIGATE (not cleared), by design of the audit.** The run's audit flags two things:

- **No scrape report.** No collector produced these raw files, so there is no scrape report (`scrape_report`).
- **No posting IDs to match.** The synthetic postings use `example.test` URLs. The audit accepts live evidence as
  current only when it matches a real MyCareersFuture posting ID in the job's URL, so it reports the Singapore jobs
  as lacking current live evidence (`live_coverage`). The Career Coach still used the two synthetic live checks in
  its decisions.

The run is archived under `data/archive/runs/`. The dashboard is written to
`data/output/career_coach_operational/` as `dashboard.md`, `opportunities.csv` and `opportunities.html`.

**Re-running the demo:**
- **Unchanged inputs:** the run verifies the existing archive and adds nothing.
- **A fresh start:** delete `data/output/`, `data/archive/` and the two `data/raw/*_latest.jsonl` files.
- **Your own data is safe:** the demo refuses to overwrite raw files that are not the demo's own.
