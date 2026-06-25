from pathlib import Path

from stages._shared import load_resume_as_content_block


def test_text_resume_returns_text_block(tmp_path: Path):
    p = tmp_path / "jane.md"
    p.write_text("# Jane Doe\nStaff Engineer with 8 years building APIs.")
    block = load_resume_as_content_block(str(p))
    assert block is not None
    assert block["type"] == "text"
    assert "Jane Doe" in block["text"]


def test_pdf_resume_returns_document_block(tmp_path: Path):
    # Minimal valid-enough PDF header; loader only base64-encodes bytes.
    p = tmp_path / "cv.pdf"
    p.write_bytes(b"%PDF-1.4\n%fake\n")
    block = load_resume_as_content_block(str(p))
    assert block is not None
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"


def test_missing_file_returns_none():
    assert load_resume_as_content_block("/no/such/file.pdf") is None


def test_empty_path_returns_none():
    assert load_resume_as_content_block("") is None
