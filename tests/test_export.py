import csv
from pathlib import Path

from models import load_json
from stages.report import generate_mock_synthesis
from stages._shared import shortlist_rows
from stages.export import write_shortlist_csv, write_shortlist_md


def _mock_rows(tmp_path: Path):
    synth = generate_mock_synthesis(tmp_path / "s.json")
    data = load_json(synth)
    return shortlist_rows(data, role_title="Staff Backend Engineer")


def test_shortlist_rows_filters_top_two_tiers(tmp_path: Path):
    rows = _mock_rows(tmp_path)
    # mock data: 4 Tier 1 + 4 Tier 2 = 8
    assert len(rows) == 8
    assert all(r["tier"].startswith(("Tier 1", "Tier 2")) for r in rows)
    # sorted by score descending
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
    assert all(r["role"] == "Staff Backend Engineer" for r in rows)
    assert all(r["name"] for r in rows)


def test_write_shortlist_csv(tmp_path: Path):
    rows = _mock_rows(tmp_path)
    out = tmp_path / "shortlist.csv"
    write_shortlist_csv(rows, out)
    assert out.exists()
    parsed = list(csv.DictReader(out.open()))
    assert len(parsed) == 8
    assert "name" in parsed[0] and "score" in parsed[0]


def test_write_shortlist_md(tmp_path: Path):
    rows = _mock_rows(tmp_path)
    out = tmp_path / "shortlist.md"
    write_shortlist_md(rows, out, title="Test Shortlist")
    text = out.read_text()
    assert "Test Shortlist" in text
    assert "| Name |" in text  # header row
    assert rows[0]["name"] in text


def test_combined_shortlist_rows_merges_roles(tmp_path: Path):
    from stages.export import combined_shortlist_rows

    s1 = generate_mock_synthesis(tmp_path / "a.json")
    s2 = generate_mock_synthesis(tmp_path / "b.json")
    results = [
        ({"role": {"title": "Backend Engineer"}}, s1),
        ({"role": {"title": "BDR"}}, s2),
    ]
    rows = combined_shortlist_rows(results)
    assert len(rows) == 16  # 8 top-tier per role
    assert {r["role"] for r in rows} == {"Backend Engineer", "BDR"}
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
