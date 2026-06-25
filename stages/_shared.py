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
