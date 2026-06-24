"""Helpers shared between stage modules. Internal: keep imports stage->shared,
never the other direction (no stage-to-stage imports)."""

from __future__ import annotations

import base64
from pathlib import Path


def load_pdf_as_document_block(pdf_path: str) -> dict | None:
    """Read a PDF file and return an Anthropic document content block.

    Returns None if the file is missing or unreadable; caller decides how to
    surface that to the candidate's score (typically: triage_score=1, fail).
    """
    path = Path(pdf_path)
    if not path.exists():
        return None
    try:
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
        print(f"  WARNING: Failed to read PDF {pdf_path}: {exc}")
        return None
