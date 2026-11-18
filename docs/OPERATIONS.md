# DS Job Radar: operations and reference

The detailed operating guide: commands, run stages, live-check recording, the application log, data
boundaries and cautions. For the project overview, see [../README.md](../README.md).

> **Public release.** Live source-specific collection adapters are excluded from this public release. The
> processing and decision pipeline runs on raw files already in `data/raw/`, for example the synthetic files
> that `scripts/run_demo.py` puts there. Every run in this release therefore uses `--skip-scrape`.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m pytest -q                 # offline test suite
python scripts/run_demo.py          # offline demo: synthetic raw files -> run_all --skip-scrape
```

The Career Coach workflow is documented in full below. The legacy scoring stages remain available separately and
are **not** part of it. They read the same raw files:

```bash
python scripts/run_pipeline.py     # legacy: normalize/dedup/enrich/filter/score -> data/output/*.parquet
python scripts/generate_report.py  # legacy: -> data/output/daily_job_radar.md
```

Legacy outputs land in `data/output/`: `jobs_scored.parquet`, `daily_top_jobs.csv`,
`daily_top_jobs.parquet`, `daily_job_radar.md`. `data/processed/jobs_processed.parquet`
holds the deduped/enriched (pre-scoring) layer written by the legacy pipeline.

## Production workflow (Career Coach)

### 1. Normal daily run

```bash
python scripts/run_all.py [--as-of YYYY-MM-DD] [--skip-scrape] [--out DIR] [--archive-root DIR] [--live-checks FILE]
```

The whole run holds a single-writer pipeline lock (a second pipeline process is refused with exit 5).

Stages, in order — each one stops the run with its exit code on failure, and
no later stage runs:

| Stage | What it does | Exit on failure |
|---|---|---|
| scrape | runs `scripts/run_scraper.py`, then requires every enabled source's `data/raw/<source>_latest.jsonl` to be freshly written and non-empty. **The collector is not included in this release**, so without `--skip-scrape` this stage fails (exit 10); with it, the stage is reported as `SKIPPED` | 10 |
| pipeline | loads the raw data, deduplicates, enriches and applies the existing hard filter (nothing is scored or tiered) | 20 |
| coach | runs the Career Coach (`OPERATIONAL`) with `data/live_checks.jsonl`; refuses to replace an output folder whose run is not archived | 30 |
| audit | grades the run with 10 audit gates (`src/career_coach/run_audit.py`) and writes `run_manifest.json`; a FAIL verdict stops the run before it is archived | 35 |
| archive | archives the run immutably under `data/archive/runs/<as_of>_OPERATIONAL_<id>/` | 40 |
| dashboard | renders `dashboard.md`, `opportunities.csv`, `opportunities.html` from this run | 50 |

Defaults: `--as-of` today's date; output `data/output/career_coach_operational/`
(`decisions.json`, `run_meta.json` + the three dashboard files); archive root
`data/archive/runs/`; live checks `data/live_checks.jsonl`. `--skip-scrape`
evaluates the raw data already on disk (reported as `SKIPPED`). The summary
ends with `DAILY RUN SUCCEEDED` only when every stage passed and the audit verdict is PASS. `run_all` never
scores or tiers jobs, never writes legacy outputs, and never reads or writes
the application log.

The archive id is derived from the as-of date, the run kind and the run's
input hashes (config, profile, raw corpus, eligible live checks). Re-running
with identical inputs verifies the existing archive and adds nothing; a run
with different inputs (for example new live evidence) gets its own archive,
so earlier runs of the same day stay preserved. An existing archive is never
overwritten: if the same id already exists with different content, the
archive stage refuses (exit 40).

> **Failure rule** (applies when a collector is installed). Do not run `run_all.py --skip-scrape` after a failed
> scrape until every enabled source's `data/raw/<source>_latest.jsonl` has
> been verified non-empty and trustworthy. An interrupted scrape publication can leave
> the raw files mixed until the next scraper run rolls it back (see Known cautions), and the pipeline stage
> only fails when *all* raw data is empty — so a skip-scrape run after a
> one-source failure would silently evaluate the other source alone.

### 2. Live checks (Singapore MyCareersFuture)

Singapore MyCareersFuture jobs are screened on live evidence (rules V-4/V-6):
the posting must be reviewable and its JD must contain an email address. An
eligible live record is one with `checked_on` on or before the run's as-of
date and at most 7 days old (`live_check_window_days: 7`); the latest one per
job wins. Without one, the Coach falls back to the stored JD (V-4a′ no email
→ blocked; V-4b email → verify first).

The live-check producer, which observed postings on the website, is **not included in this public release**.
Reviewed observations are recorded with the recorder, which needs no network access:

```bash
python scripts/record_live_checks.py observations.csv
```

The CSV has one row per observed posting, with the columns
`source,source_job_id,checked_on,method,page_reviewable,email_in_jd,posting_status`
(`YES`/`NO` for the two checks, `OPEN`/`CLOSED`/`UNKNOWN` for the status). The recorder maps each posting to its
job in the raw data on disk and appends to `data/live_checks.jsonl`. It is all-or-nothing and append-only: records
that already exist are skipped, a conflicting record with the same id is refused, and nothing is ever rewritten.
Re-running it with the same CSV is a no-op. Never edit `data/live_checks.jsonl` by hand. `run_all.py --live-checks
FILE` reads another live-check file instead; the demo uses `demo/live_checks.jsonl`.

A new check is needed when:

- the raw data holds Singapore MyCareersFuture jobs that have no live record;
- a job's latest live record is more than 7 days older than the run's as-of date;
- a current job points to a different canonical posting than its latest live
  record (live evidence describes one posting; see Known cautions).

**"live checks used" is not coverage.** The run summary and dashboard header count every eligible record (the
latest per job id within the window), including records for jobs that are no longer in the corpus. Coverage is
reported separately: the audit's `live_coverage` gate and the dashboard's "Live evidence" section count a job as
covered only when its decision cites an eligible live record for the MyCareersFuture posting ID the job currently
points to.

### 3. Same-day final Coach run after a live refresh

```bash
python scripts/run_all.py --as-of YYYY-MM-DD --skip-scrape
```

Run this after recording fresh live checks against the raw data on disk, so the final Coach decisions and dashboard for that day use the
current evidence. It evaluates exactly the raw data already on disk; because
the eligible live-check input differs, it gets its own archive and the
earlier run of the same day stays archived.

### 4. Logging applications

Use only `scripts/coach_log.py`. It appends `coach_v1` decision and outcome
events and `coach_void_v1` void events to `data/applications.jsonl`; nothing
is inferred and nothing is recorded automatically. The file is created by the
first valid event — until then it does not exist, and a missing file (like an
empty one) simply means zero recorded events. Never create it by hand.

The Coach's next action `APPLY` (and the dashboard's "APPLY / PREPARE" label)
is a recommendation, not an event: running the Coach or rendering the
dashboard never writes to the application log.

> `python scripts/log_application.py` is **legacy** (it runs the legacy
> scoring pipeline and strategy code and writes `application_log_v1` events
> to the same file). Do not use it for production application logging.
> `python scripts/generate_decision_cards.py` is **legacy** too: it reads the
> same file with the legacy loader, which rejects or misreads current events.
> Do not run it against the production application log.

```text
decision APPLIED  != a submitted application   (you decided to apply)
outcome  APPLIED  =  a submitted application   (you actually submitted it)
```

Neither creates the other. `--run` is a saved OPERATIONAL run directory — the
current output folder, or the job's archive under `data/archive/runs/` once
the output folder holds a later run. Dates are `YYYY-MM-DD`, must be a real
calendar day and must not be after today's local date (past dates are fine
for events recorded late); `--notes` is stored verbatim and never interpreted.

```bash
# your decision (SAVED | APPLIED | SKIPPED | DEFERRED), with optional declared effort
python scripts/coach_log.py decision --run data/output/career_coach_operational --job-id JOB_ID \
    --decision APPLIED --date YYYY-MM-DD [--effort LOW|MEDIUM|HIGH|UNKNOWN] [--notes "..."]

# the submission itself
python scripts/coach_log.py outcome --run data/output/career_coach_operational --job-id JOB_ID \
    --outcome APPLIED --date YYYY-MM-DD

# later stages, as they happen
python scripts/coach_log.py outcome --run RUN_DIR --job-id JOB_ID --outcome INTERVIEW   --date YYYY-MM-DD
python scripts/coach_log.py outcome --run RUN_DIR --job-id JOB_ID --outcome FINAL_ROUND --date YYYY-MM-DD
python scripts/coach_log.py outcome --run RUN_DIR --job-id JOB_ID --outcome OFFER       --date YYYY-MM-DD
python scripts/coach_log.py outcome --run RUN_DIR --job-id JOB_ID --outcome REJECTED    --date YYYY-MM-DD
python scripts/coach_log.py outcome --run RUN_DIR --job-id JOB_ID --outcome WITHDRAWN   --date YYYY-MM-DD
python scripts/coach_log.py outcome --run RUN_DIR --job-id JOB_ID --outcome CLOSED      --date YYYY-MM-DD

# reconstructed status (latest decision and outcome per job) and every event with its id
python scripts/coach_log.py history [--job-id JOB_ID]

# an event entered in error: mark it void (the original line is never changed)
python scripts/coach_log.py void --event-id N --job-id JOB_ID --date YYYY-MM-DD --reason "..."
```

Rules enforced by the code (an invalid call prints `not recorded: ...` and
appends nothing):

- a **decision** needs the job in the referenced run, and the run must be
  OPERATIONAL; its snapshot of the Coach's values is copied from that run;
- an **outcome** needs the job in the referenced run or already in the log;
- a **void** names one earlier decision or outcome by its event id (as listed
  by `history`; its 0-based position in the log) and must repeat that event's
  job id; it needs a non-blank reason and a date no earlier than the voided
  event's. An event can be voided once; a void cannot be voided.

Mistakes are never edited out of `data/applications.jsonl`. A voided event
stays in the file; `history` marks it, and the learning report lists it and
leaves it out of every figure (submissions, milestones, terminal outcomes,
decision context, latency, flags). To replace a wrong value, void the event
and record the correct one as a new event. The void reason is kept for the
record only and is never analysed.

For the learning report, decision-time context is anchored at the first
recorded `outcome APPLIED`: only decisions dated on or before it count. Log
the decision on or before the submission date. OFFER, REJECTED, WITHDRAWN and
CLOSED are terminal; CLOSED means closed without an employer decision.
Duplicates are kept (and flagged by the learning report).

### 5. Learning report

```bash
python scripts/outcome_report.py --as-of YYYY-MM-DD [--log FILE] [--archive-root DIR] [--out DIR]
```

Read-only over the application log and the run archive. Writes
`outcome_report.md`, `analysis_dataset.json` and `outcome_state.json` to
`data/output/learning/` (default). All figures are descriptive counts ("k of
n") of recorded events. Sections:

1. Sources — events read, run archives available, and voided events (listed; excluded from every figure).
2. Application funnel — submitted applications (outcome APPLIED), one table
   per event schema version, never pooled.
3. OPEN applications — submitted with no terminal outcome; no timeout applies.
4. Decided to apply, submission not recorded.
5. Other histories without a submission.
6. Decision conversions — SAVED → submitted, DEFERRED → submitted.
7. Decision-time context strata, per event schema — submissions without any
   decision-time context are counted as excluded.
8. Days between recorded stages (latency); histories flagged `OUT_OF_ORDER`
   or `OUTCOME_WITHOUT_SUBMISSION` are excluded from this section only.
9. Data-quality flags — `OUT_OF_ORDER`, `DUPLICATE_STAGE`,
   `OUTCOME_WITHOUT_SUBMISSION` (derived; the log is unchanged).
10. Not available in V1 — surfaced → reviewed, salary values, career outcomes.

V2 calibration and V3 personalisation are **not active**: nothing in the
report feeds back into the Coach, its rules, priorities or the dashboard.

### 6. Production data boundaries

| Path | Semantics |
|---|---|
| `data/raw/<source>_<timestamp>.jsonl` | raw snapshot per scrape, written by a collector (not included) — never rewritten |
| `data/raw/<source>_latest.jsonl` | the input `run_all` evaluates; placed by `scripts/run_demo.py` (synthetic) or by you |
| `data/live_checks.jsonl` | **append-only** live evidence (via `record_live_checks.py` only) |
| `data/live_check_evidence/<date>/` | evidence folders written by the live-check producer (not included); read by the audit when present |
| `data/archive/runs/<as_of>_<kind>_<id>/` | **immutable** run archives (read-only files) |
| `data/output/career_coach_operational/` | generated / replaceable — the current run and dashboard; replaced only once archived |
| `data/output/learning/` | generated / replaceable — learning report outputs |
| `data/applications.jsonl` | **append-only** application log (`coach_v1` decisions/outcomes and `coach_void_v1` voids via `coach_log.py`; legacy `application_log_v1` lines are read too); created by the first recorded event, absent until then |
| `data/output/jobs_scored.parquet`, `daily_top_jobs.*`, `daily_job_radar.md`, `scoring_audit*`, `data/processed/`, `data/snapshots/` | **legacy-only** — written only by legacy commands |

All of `data/raw/`, `data/processed/`, `data/output/`, `data/archive/`,
`data/live_check_evidence/`, `data/live_checks.jsonl`, `data/applications.jsonl`
and `data/contacts.csv` are gitignored.

### 7. Operational sequence (public release)

Offline demonstration:

```text
run_demo.py        (synthetic raw files -> run_all.py --skip-scrape --as-of 2026-01-15 --live-checks demo/live_checks.jsonl)
```

With your own raw files in `data/raw/<source>_latest.jsonl` (records in the `RawJob` format, `src/models/raw_job.py`):

```text
run_all.py --as-of <date> --skip-scrape
→ record_live_checks.py <reviewed observations.csv>     (when Singapore jobs need live evidence)
→ run_all.py --as-of <date> --skip-scrape               (final Coach/dashboard state for the day)
```

Applications are logged with `coach_log.py` whenever they happen; the learning report is run on demand. Nothing
here is scheduled or automated.

### 8. Known cautions

- **Raw files are trusted as placed.** The excluded collector published raw files all-or-nothing, with staging
  files and a journal. In this release, raw files are placed by `run_demo.py` or by you. The audit still checks them
  (`raw_integrity`), and its `publication_state` gate fails a run if a publication journal or staging files are
  present in `data/raw/`.
- **`--skip-scrape` after a failed scrape** must not be used blindly (see the
  failure rule).
- **Live evidence is job- and posting-specific.** A record describes the
  posting it observed (`lc-<date>-<posting id>`), and the Coach matches it by
  job id.
- **A job can keep its job id while pointing to a new canonical posting.** Its
  older record then describes the old posting; refresh to get evidence for
  the current one. The old record stays in the file as history.
- **Historical live evidence is preserved.** Newer records supersede older
  ones for the same job; nothing is deleted.
- **Same-day runs stay separate** when their input evidence differs: each has
  its own archive id.
- **The application log is append-only.** Never edit, delete or truncate it.
  A mistaken event is voided with `coach_log.py void` (a new event); the
  current status is the latest recorded event that has not been voided.
- **Notes and void reasons are never interpreted** by the learning layer.

## Configuration — edit these, not the code

- `config/profile.yaml` — your target roles, locations, skills, domain background.
- `config/scoring.yaml` — Base component weights (only their ratios matter;
  they're renormalized over the known components), opportunity-type
  thresholds, hard-filter rules. Tier cutoffs are derived, not configured,
  and the PTF contract is versioned in code (`src/scoring/ptf_scale.py`).
- `config/skills_taxonomy.yaml` — the skill vocabulary extracted from job text.
- `config/sources.yaml` — the source names whose raw files are read (the collectors and their
  search and request settings are not included).
- `data/contacts.csv` — optional manual networking input (company, contact
  name, relationship, strength). Never scraped — you fill this in yourself.
  Gitignored by default since it's personal data.

## Data sources and the excluded collectors

The pipeline reads raw files for two sources, MyCareersFuture (Singapore) and Hiredly (Malaysia). Their live
collection adapters are **not included in this public release**:

- the two source adapters and their shared retry helper;
- the scraper command and its all-or-nothing publication of raw files;
- the live-check producer;
- a source smoke test;
- each source's search queries and request settings.

What remains is source-agnostic: the `JobSource` interface (`src/scrapers/base.py`), the raw record model
(`src/models/raw_job.py`), each source's field parsing in the normalisers (salary, location), and the rules and audit
checks that refer to a source by name.

When the collectors were written, robots.txt and bot protection were checked by hand for six candidates. The current
Terms of Use of the two sources have not been reviewed.

### Sources considered and rejected

| Source | Reason rejected |
|---|---|
| JobStreet MY/SG | robots.txt disallows `*/job/` and all query-string search (`Disallow: *?`); homepage itself returns a Cloudflare 403 challenge |
| Indeed | robots.txt disallows `/jobs/MY/`, `/jobs/SG/`, `/viewjob`, `/jobs`, `/q-` — exactly the paths needed |
| LinkedIn | ToS explicitly prohibits scraping; robots.txt disallows generic user-agents from job pages |
| Glints | Cloudflare 403 "challenge" response on job search |
| Wobb | Unreliable — TLS/origin connection errors |

We never bypass CAPTCHA, login, or bot-protection to reach a source — if a
site blocks us, it stays out of scope until it offers a public/legitimate path in.

### Collection strategy: high recall, precision downstream

Collection queried broadly (10 overlapping keyword searches) on purpose. This means:
- The same job often appears under several queries — expected, not a bug.
  Deduplication merges these and keeps every `matched_query` as provenance.
- Keyword search pads results with unrelated postings. This is why hard filtering
  and role classification exist as separate downstream stages rather than
  trying to make collection itself precise.

## Legacy pipeline stages (`scripts/run_pipeline.py`, not used by the daily run)

```
raw collection → dedup → enrichment (skills, role classification) →
hard filters → scoring → tiering → reporting
```

1. **Raw** (`data/raw/*.jsonl`) — immutable, exactly what each source
   returned. Never edited in place.
2. **Dedup** (`src/dedup/deduplicator.py`) — two passes: within-source (same
   `job_id` seen under multiple queries) and cross-source (same normalized
   company+title+location). Provenance (`sources`, `source_urls`,
   `matched_queries`) is unioned, never discarded.
3. **Enrichment** (`src/enrichment/`) — skill extraction (config-driven
   taxonomy), role family/level classification, and `role_type` — the
   content-based classifier that distinguishes `TRUE_DS` from
   `DS_TITLE_BUT_ANALYTICS_HEAVY` (a job titled "Data Scientist" that's
   actually dashboard/reporting work). A title that matches no known
   pattern (`role_family="Other"`) is judged by content too, not assumed
   to be noise or assumed to be relevant.
4. **Hard filters** (`src/pipeline/hard_filters.py`) — reject only for
   clear disqualifiers (non-DS role family/type, too short a description,
   too stale, wrong geography and not remote, or a posting that explicitly
   denies eligibility to foreign applicants — HC-2). A DS-titled job whose
   scope reads analytics-heavy stays eligible and is scored low instead.
   Never rejects for "title says Senior", "missing one preferred skill",
   "no visa sponsorship", or "local candidates preferred" — those are not
   explicit exclusions.
5. **Scoring** (`src/scoring/`) — one module per sub-score, each returning
   a score, an `is_known` flag, and human-readable reasons. See below.
6. **Reporting** (`src/reporting/`) — Parquet/CSV exports plus
   `daily_job_radar.md`, grouped by tier with full explanations.

## Legacy scoring model (not used by the daily run)

```text
Overall = clamp_0_100( 0.60 × Base + 0.40 × PTF + network_bonus − job_risk_penalty )
```

**Base** is the weighted mean of the *known* job-side components
(`config/scoring.yaml`: job_quality .30, interview_fit .10, compensation .08,
company_maturity .07, location .05), renormalized over the known ones.
Unknown components are excluded, never scored as 0.

**Personal Transition Fit (PTF)** scores technical and domain evidence once
(frozen contract `ptf_support_v1`, `src/scoring/ptf_scale.py`):

- `technical_alignment` is known iff at least one REQUIRED technique is
  evidenced in your profile's `techniques`. LIMITED (umbrella techniques
  Machine Learning / Deep Learning, or a specific technique you list as
  moderate) or SUBSTANTIVE (a specific technique you list as strong).
  Unevidenced required techniques are reported as "No evidence in your
  profile for X (required by this job)" — a gap, never a penalty.
- `domain_alignment` is known iff a qualifying overlap with your domain
  terms exists: LIMITED (peripheral), SUBSTANTIVE (the job's own work) or
  CORE (the job's or team's defining subject). See
  `src/scoring/domain_alignment.py` for the placement rules.
- Band values: LIMITED 15, SUBSTANTIVE 45, CORE 75; with both dimensions
  known, PTF = stronger + ⅓ × weaker. An unknown PTF contributes 0 — it is
  not assessed, not penalized.

`network_bonus` (up to +10) and `job_risk_penalty` (up to −20) are applied
**after** the core, as separate adjustments.

`interview_fit_score` is a heuristic fit against the posting's stated bar,
**not a probability** — there's no application-outcome data to calibrate
against (see Roadmap).

**Decision stability** (`tier_floor` / `tier_current` / `tier_ceiling`,
`STABLE` / `COULD_RISE` / `COULD_FALL` / `COULD_MOVE`) shows whether the
tier could change if information the system doesn't have yet were known:
a required technique not yet evidenced in your profile, or an undisclosed
salary. It is display-only and never changes a score. `data_completeness_pct`
is a separate, raw data-richness metric; `unknown_fields` lists what's missing.

### Tiers and opportunity types

Tier cutoffs are **derived**, not configured: each is the overall score of a
reference genuine-DS job (every other component at its module's declared
neutral value) at a given PTF state — C at PTF unknown (≈42.3), B at
SUBSTANTIVE (≈60.3), A at CORE (≈72.3). They are stored exactly; the rounded
values are display only (`src/scoring/tiers.py`).

A job also gets zero or more `opportunity_types` (`DS_SUBSTANCE` (the
frozen role_type judged the content genuine DS work — not a career-movement
claim),
`STRONG_INTERVIEW_FIT`, `STRETCH`, `DOMAIN_ADVANTAGE` (domain band ≥
SUBSTANTIVE), `HIGH_COMPENSATION`, `NETWORKING_OPPORTUNITY`), thresholded in
`config/scoring.yaml`. `recommended_action` (`APPLY` / `APPLY + NETWORK` /
`STRETCH APPLY` / `SKIP`) is derived from tier + opportunity types in
`src/scoring/overall.py`.

## Testing

```bash
python -m pytest tests/ -v
```

Tests use synthetic fixtures and make no network requests. A few tests need private
production data (run archives, raw snapshots) that is not part of this repository; they skip.
`tests/test_pipeline_integration.py` runs the full pipeline end-to-end
against a small synthetic raw dataset.

## Known limitations (by design, not oversights)

- **`company_maturity_score` is always unknown.** No company-enrichment
  source exists yet (V2). It's excluded from Base and reported once as a
  system-level limitation, not defaulted to a guess.
- **No commute score.** Would require office geocoding — deferred to V2
  rather than inventing commute times.
- **Source-side data errors happen.** E.g., a posting on MyCareersFuture may
  tag an annual salary as "Monthly," producing an absurd monthly figure.
  `job_risk.py` flags implausible salaries (> MYR 100k/month) rather than
  silently trusting or silently discarding them.
- **`interview_fit_score` is unvalidated.** It'll stay a heuristic
  until real application outcomes exist to calibrate against.

## Roadmap

_This is the original legacy-scoring roadmap. Its V2/V3 labels are not the
Career Coach's V2 calibration / V3 personalisation, neither of which is active._

- **V2**: company/Google-search enrichment (fills in `company_maturity_score`),
  salary enrichment, commute calculation, contact matching.
- **V3**: application/interview tracker (the `status` field on `ProcessedJob`
  — NEW/SEEN/SHORTLISTED/APPLIED/INTERVIEW/OFFER/REJECTED/CLOSED/EXPIRED —
  already exists for this).
- **V4**: learn from actual application outcomes which titles/companies/skills
  actually generate interviews, and use that to replace the heuristic
  `interview_fit_score` heuristic with something calibrated.
