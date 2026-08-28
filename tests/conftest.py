"""Shared test setup: never let a test touch the real data/ledger.json."""

import pytest

from ledger import promises


@pytest.fixture(autouse=True)
def _isolate_ledger_backend(monkeypatch, tmp_path):
    """Force the process-wide default backend to a throwaway in-memory one for
    every test, so a call that omits `backend=` can't read or write the repo's
    committed ledger file."""
    monkeypatch.setenv("LEDGER_BACKEND", "memory")
    monkeypatch.setenv("LEDGER_JSON_PATH", str(tmp_path / "ledger.json"))
    promises.reset_default_backend()
    yield
    promises.reset_default_backend()
