"""Zero-key demo: render the bundled mock report with no API calls.

Loads a role YAML directly so this module imports neither run.py nor any
LLM stage — `python run.py --demo` works with no keys installed.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from stages.report import generate_mock_synthesis, run_report


def run_demo(slug: str = "staff-backend-engineer", data_root: Path | None = None) -> Path:
    """Generate synthetic synthesis data and render a report. Returns its path."""
    config_path = Path(f"config/roles/{slug}.yaml")
    if not config_path.exists():
        raise FileNotFoundError(f"Role config not found: {config_path}")
    config = yaml.safe_load(config_path.read_text())
    config["today"] = date.today().isoformat()
    config["role_slug"] = slug
    data_dir = (data_root or Path("data")) / "roles" / slug
    config["data_dir"] = data_dir
    config["total_screened"] = 285  # illustrative for the demo header
    (data_dir / "reports").mkdir(parents=True, exist_ok=True)

    synth_path = generate_mock_synthesis(data_dir / "results" / "stage4_synthesis.json")
    report_path = run_report(config, synth_path)
    return report_path
