from pathlib import Path

import yaml

from stages.report import generate_mock_synthesis, generate_combined_report


def _cfg(slug: str, title: str) -> dict:
    cfg = yaml.safe_load(Path(f"config/roles/{slug}.yaml").read_text())
    cfg["role_slug"] = slug
    cfg["role"]["title"] = title
    cfg["total_screened"] = 100
    return cfg


def test_combined_report_renders_priority_and_roles(tmp_path: Path):
    s1 = generate_mock_synthesis(tmp_path / "a.json")
    s2 = generate_mock_synthesis(tmp_path / "b.json")
    out = generate_combined_report(
        [
            (_cfg("staff-backend-engineer", "Staff Backend Engineer"), s1),
            (_cfg("bdr", "Business Development Representative"), s2),
        ]
    )
    assert out.exists()
    html = out.read_text()
    # consolidated priority section present
    assert "Priority — Who to Meet This Week" in html
    # both roles present
    assert "Staff Backend Engineer" in html
    assert "Business Development Representative" in html
    # existing tabbed combined report still intact
    assert "role-tab" in html
