"""Input-source seam: pick the Stage 1 fetcher by config["source"].

Each fetcher has the signature (config: dict) -> Path and writes the same
stage1_candidates.json contract. Add a new ATS here and nothing downstream
changes.
"""

from __future__ import annotations

from typing import Callable
from pathlib import Path

from stages.fetch import run_fetch
from stages.local_source import run_local_fetch

SOURCES: dict[str, Callable[[dict], Path]] = {
    "ashby": run_fetch,
    "local": run_local_fetch,
}


def resolve_source(config: dict) -> Callable[[dict], Path]:
    """Return the Stage 1 fetcher for config["source"] (default: 'ashby')."""
    name = (config.get("source") or "ashby").lower()
    if name not in SOURCES:
        raise ValueError(
            f"Unknown source {name!r}. Available: {', '.join(sorted(SOURCES))}"
        )
    return SOURCES[name]
