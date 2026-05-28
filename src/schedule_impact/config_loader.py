"""Load YAML configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def load_p6_schema() -> dict[str, Any]:
    path = _REPO_ROOT / "config" / "p6_schema.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_settings(path: Path | None = None) -> dict[str, Any]:
    if path and path.is_file():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for candidate in (_REPO_ROOT / "config" / "settings.yaml", _REPO_ROOT / "config" / "settings.example.yaml"):
        if candidate.is_file():
            return yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    return {}
