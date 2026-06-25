"""Stage 1 (local): build candidates from a folder of résumés + a CSV.

Mirrors stages/fetch.py's output contract exactly — a JSON list of
RawCandidate dicts at data/roles/<slug>/results/stage1_candidates.json —
so every downstream stage is unchanged. No ATS, no network.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from models import RawCandidate, save_json


def _slugify(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def run_local_fetch(config: dict) -> Path:
    """Read input_dir/candidates.csv + résumé files, write stage1 JSON.

    Args:
        config: must contain "input_dir" and "data_dir".

    Returns:
        Path to stage1_candidates.json.
    """
    input_dir = Path(config["input_dir"]).expanduser()
    csv_path = input_dir / "candidates.csv"
    if not csv_path.exists():
        raise FileNotFoundError(
            f"No candidates.csv found in {input_dir}. "
            "Expected columns: name,email,application_date,resume_file"
        )

    data_dir = Path(config.get("data_dir", "data"))
    output_path = data_dir / "results" / "stage1_candidates.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    candidates: list[RawCandidate] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, 1):
            name = (row.get("name") or "").strip() or f"Candidate {i}"
            email = (row.get("email") or "").strip()
            resume_file = (row.get("resume_file") or "").strip()
            resume_path = None
            if resume_file:
                candidate_resume = input_dir / resume_file
                if candidate_resume.exists():
                    resume_path = str(candidate_resume)
                else:
                    print(f"  WARNING: résumé not found for {name}: {candidate_resume}")
            candidate_id = (row.get("candidate_id") or "").strip() or _slugify(
                email or name, fallback=f"candidate-{i}"
            )
            profile_url = (row.get("profile_url") or "").strip() or ""
            candidates.append(
                RawCandidate(
                    candidate_id=candidate_id,
                    name=name,
                    email=email,
                    application_date=(row.get("application_date") or "").strip(),
                    profile_url=profile_url,
                    source="local",
                    resume_path=resume_path,
                    location_summary=(row.get("location_summary") or "").strip() or None,
                )
            )

    print(f"  Local source: {len(candidates)} candidates from {csv_path}")
    save_json([c.__dict__ for c in candidates], output_path)
    print(f"  Output saved to {output_path}")
    return output_path
