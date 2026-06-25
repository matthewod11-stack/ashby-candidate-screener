from pathlib import Path

from state import (
    filter_new_candidates,
    get_cached_evals,
    get_candidate_first_seen,
    load_state,
    save_state,
    update_state,
)


def _empty() -> dict:
    return {"candidates": {}, "last_run": None}


def test_filter_new_candidates_dedups_by_email():
    state = _empty()
    state["candidates"]["seen@example.com"] = {"name": "Seen", "cached_eval": {}}
    candidates = [
        {"email": "seen@example.com", "name": "Seen"},
        {"email": "fresh@example.com", "name": "Fresh"},
    ]
    new = filter_new_candidates(candidates, state)
    assert [c["email"] for c in new] == ["fresh@example.com"]


def test_filter_new_candidates_is_case_insensitive():
    # state keys are stored lowercased; an upper-cased duplicate must dedup.
    state = _empty()
    state["candidates"]["ada@example.com"] = {"name": "Ada"}
    new = filter_new_candidates([{"email": "Ada@Example.com"}], state)
    assert new == []


def test_filter_new_candidates_missing_email_treated_as_new():
    # No email means we can't dedup it — never silently drop a candidate.
    state = _empty()
    new = filter_new_candidates([{"name": "No Email"}], state)
    assert len(new) == 1


def test_update_state_caches_new_eval_and_synthesis():
    state = _empty()
    deep_evals = [
        {"candidate": {"email": "ada@example.com", "name": "Ada", "candidate_id": "c1"}}
    ]
    synthesis = [
        {"evaluated": {"candidate": {"email": "ada@example.com"}}, "tier": "Tier 1"}
    ]
    update_state(state, synthesis, deep_evals, today="2026-06-25")

    entry = state["candidates"]["ada@example.com"]
    assert state["last_run"] == "2026-06-25"
    assert entry["first_seen_date"] == "2026-06-25"
    assert entry["cached_eval"]["candidate"]["name"] == "Ada"
    assert entry["cached_synthesis"]["tier"] == "Tier 1"


def test_update_state_preserves_first_seen_for_returning_candidate():
    state = _empty()
    state["candidates"]["ada@example.com"] = {
        "name": "Ada",
        "candidate_id": "c1",
        "first_seen_date": "2026-01-01",
        "last_report_date": "2026-01-01",
        "cached_eval": None,
        "cached_synthesis": None,
    }
    deep_evals = [{"candidate": {"email": "ada@example.com", "name": "Ada"}}]
    update_state(state, [], deep_evals, today="2026-06-25")

    entry = state["candidates"]["ada@example.com"]
    assert entry["first_seen_date"] == "2026-01-01"  # unchanged
    assert entry["last_report_date"] == "2026-06-25"  # refreshed


def test_get_cached_evals_skips_null_entries():
    state = _empty()
    state["candidates"]["a@x.com"] = {"cached_eval": {"score": 9}}
    state["candidates"]["b@x.com"] = {"cached_eval": None}
    assert get_cached_evals(state) == [{"score": 9}]


def test_get_candidate_first_seen_handles_case_and_misses():
    state = _empty()
    state["candidates"]["ada@example.com"] = {"first_seen_date": "2026-01-01"}
    assert get_candidate_first_seen(state, "Ada@Example.com") == "2026-01-01"
    assert get_candidate_first_seen(state, "ghost@example.com") is None


def test_load_state_recovers_from_corrupt_file(tmp_path: Path):
    (tmp_path / "state.json").write_text("{not valid json")
    state = load_state(tmp_path)
    assert state == {"candidates": {}, "last_run": None}


def test_save_then_load_state_roundtrips(tmp_path: Path):
    state = _empty()
    state["candidates"]["ada@example.com"] = {"name": "Ada"}
    state["last_run"] = "2026-06-25"
    save_state(state, tmp_path)
    assert load_state(tmp_path) == state


def test_load_state_missing_file_returns_empty(tmp_path: Path):
    assert load_state(tmp_path) == {"candidates": {}, "last_run": None}
