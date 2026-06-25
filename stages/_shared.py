"""Helpers shared between stage modules. Internal: keep imports stage->shared,
never the other direction (no stage-to-stage imports)."""

from __future__ import annotations

import base64
from pathlib import Path

TEXT_SUFFIXES = {".md", ".txt", ".markdown"}


def load_resume_as_content_block(resume_path: str | None) -> dict | None:
    """Read a résumé file and return an Anthropic content block.

    - PDFs  -> a base64 'document' block (media_type application/pdf)
    - .md/.txt/.markdown -> a 'text' block with the file contents
    Returns None if the path is empty, missing, or unreadable; the caller
    decides how to surface that (typically: triage_score=1, fail).
    """
    if not resume_path or not isinstance(resume_path, str):
        return None
    path = Path(resume_path)
    if not path.exists():
        return None

    suffix = path.suffix.lower()
    try:
        if suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                return None
            return {"type": "text", "text": f"RÉSUMÉ:\n\n{text}"}
        # Default to PDF handling for .pdf and anything else binary.
        pdf_bytes = path.read_bytes()
        b64_data = base64.standard_b64encode(pdf_bytes).decode("ascii")
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": b64_data,
            },
        }
    except OSError as exc:
        print(f"  WARNING: Failed to read résumé {resume_path}: {exc}")
        return None


def load_pdf_as_document_block(pdf_path: str) -> dict | None:
    """Backward-compatible alias. Prefer load_resume_as_content_block."""
    return load_resume_as_content_block(pdf_path)


SHORTLIST_TIER_PREFIXES = ("Tier 1", "Tier 2")


def shortlist_rows(synth_dicts: list[dict], role_title: str = "") -> list[dict]:
    """Filter synthesis candidates to the top two tiers, flatten to rows.

    Returns the "worth a conversation" set — Tier 1 and Tier 2 candidates —
    as flat dicts with stable keys, sorted by score descending. Shared by the
    combined report's priority section and the shortlist exporter.
    """
    rows: list[dict] = []
    for c in synth_dicts:
        tier = str(c.get("final_tier", ""))
        if not tier.startswith(SHORTLIST_TIER_PREFIXES):
            continue
        ev = c.get("evaluated", {}) or {}
        cand = ev.get("candidate", {}) or {}
        rows.append(
            {
                "role": role_title,
                "name": cand.get("name", ""),
                "tier": tier,
                "score": c.get("final_score", 0),
                "recommendation": ev.get("recommendation", ""),
                "current_role": ev.get("current_role", ""),
                "years_experience": ev.get("years_experience", ""),
                "email": cand.get("email", ""),
                "profile_url": cand.get("profile_url", ""),
            }
        )
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows
