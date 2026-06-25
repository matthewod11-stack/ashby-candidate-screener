from pathlib import Path

from models import RawCandidate
from stages.local_source import run_local_fetch


def _make_inputs(tmp_path: Path) -> Path:
    indir = tmp_path / "candidates"
    indir.mkdir()
    (indir / "jane.md").write_text("# Jane Doe\nStaff Engineer, 8y APIs.")
    (indir / "bob.txt").write_text("Bob Lee — Backend Engineer, 5y.")
    (indir / "candidates.csv").write_text(
        "name,email,application_date,resume_file\n"
        "Jane Doe,jane@example.com,2026-02-01,jane.md\n"
        "Bob Lee,bob@example.com,2026-02-02,bob.txt\n"
    )
    return indir


def test_local_fetch_writes_stage1(tmp_path: Path):
    indir = _make_inputs(tmp_path)
    config = {
        "input_dir": str(indir),
        "data_dir": tmp_path / "data" / "roles" / "demo",
    }
    out = run_local_fetch(config)
    assert out.exists()

    import json
    rows = json.loads(out.read_text())
    assert len(rows) == 2
    cands = [RawCandidate.from_dict(r) for r in rows]
    by_name = {c.name: c for c in cands}
    assert by_name["Jane Doe"].email == "jane@example.com"
    # resume_path must resolve to a real file on disk
    assert Path(by_name["Jane Doe"].resume_path).exists()
    assert by_name["Jane Doe"].application_date == "2026-02-01"


def test_local_fetch_missing_csv_raises(tmp_path: Path):
    indir = tmp_path / "empty"
    indir.mkdir()
    config = {"input_dir": str(indir), "data_dir": tmp_path / "d"}
    try:
        run_local_fetch(config)
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass
