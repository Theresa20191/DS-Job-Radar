"""Loads config/*.yaml. Every module reads config through here rather than
opening YAML files itself, so config location only needs to change in one
place."""

from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


@lru_cache(maxsize=None)
def load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    with open(path) as f:
        return yaml.safe_load(f)


def load_profile() -> dict:
    return load_yaml("profile.yaml")


def load_scoring() -> dict:
    return load_yaml("scoring.yaml")


def load_sources() -> dict:
    return load_yaml("sources.yaml")


def load_skills_taxonomy() -> dict:
    return load_yaml("skills_taxonomy.yaml")


def load_recruiters() -> dict:
    return load_yaml("recruiters.yaml")


def load_tracks() -> dict:
    return load_yaml("tracks.yaml")


def load_career_coach() -> dict:
    return load_yaml("career_coach.yaml")
