from pathlib import Path

from stages.demo import run_demo


def test_demo_produces_report(tmp_path: Path):
    report_path = run_demo(slug="staff-backend-engineer", data_root=tmp_path)
    assert report_path.exists()
    html = report_path.read_text()
    assert "Candidate Report" in html
    # Mock data renders ranked candidates
    assert "Tier 1" in html
