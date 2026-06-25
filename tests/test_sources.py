from stages.sources import resolve_source
from stages.fetch import run_fetch
from stages.local_source import run_local_fetch


def test_default_is_ashby():
    assert resolve_source({}) is run_fetch


def test_explicit_ashby():
    assert resolve_source({"source": "ashby"}) is run_fetch


def test_local():
    assert resolve_source({"source": "local"}) is run_local_fetch


def test_unknown_raises():
    try:
        resolve_source({"source": "workday"})
        assert False, "expected ValueError"
    except ValueError:
        pass
