import json
from pathlib import Path

from stages.local_source import run_local_fetch


def test_bundled_examples_parse(tmp_path: Path):
    config = {
        "input_dir": "examples/sample-candidates",
        "data_dir": tmp_path / "data" / "roles" / "demo",
    }
    out = run_local_fetch(config)
    rows = json.loads(out.read_text())
    assert len(rows) == 4
    for r in rows:
        assert r["name"] and r["email"]
        assert Path(r["resume_path"]).exists()
