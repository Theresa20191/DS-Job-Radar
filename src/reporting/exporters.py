from pathlib import Path

import pandas as pd

from src.models.processed_job import ProcessedJob
from src.models.scored_job import ScoredJob


def _to_dataframe(jobs: list) -> pd.DataFrame:
    return pd.DataFrame([j.model_dump(mode="json") for j in jobs])


def export_processed(jobs: list[ProcessedJob], out_dir: Path) -> None:
    df = _to_dataframe(jobs)
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_dir / "jobs_processed.parquet", index=False)


def export_scored(jobs: list[ScoredJob], out_dir: Path) -> None:
    df = _to_dataframe(jobs)
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_dir / "jobs_scored.parquet", index=False)


def export_top_jobs(jobs: list[ScoredJob], out_dir: Path, top_n: int = 10) -> None:
    top = sorted(jobs, key=lambda j: j.overall_score, reverse=True)[:top_n]
    df = _to_dataframe(top)
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "daily_top_jobs.csv", index=False)
    df.to_parquet(out_dir / "daily_top_jobs.parquet", index=False)
