"""
Tests for the falsifiable prediction ledger. Uses a temp SQLite file and
fixture trend data - no network calls, so this runs fast and deterministically
in any environment.
"""

import os
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ledger"))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agents"))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp_server"))

import store
import predictor


def temp_db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # let get_connection create it fresh
    return path


def test_record_and_fetch_prediction_roundtrip():
    db_path = temp_db_path()
    try:
        pid = store.record_prediction(
            db_path, topic="Test Topic", baseline_geo="US", target_geo="MX",
            baseline_rank=1, baseline_search_volume="1000+", baseline_velocity_score=15,
            baseline_saturation_level="LOW", baseline_lifecycle_stage="ACCELERATING",
            evaluation_window_hours=0.0,  # due immediately
        )
        assert pid

        due = store.get_due_predictions(db_path)
        assert len(due) == 1
        assert due[0]["topic"] == "Test Topic"
        assert due[0]["status"] == "PENDING"
    finally:
        os.remove(db_path)


def test_not_due_prediction_is_excluded():
    db_path = temp_db_path()
    try:
        store.record_prediction(
            db_path, topic="Future Topic", baseline_geo="US", target_geo="MX",
            baseline_rank=1, baseline_search_volume="1000+", baseline_velocity_score=15,
            baseline_saturation_level="LOW", baseline_lifecycle_stage="ACCELERATING",
            evaluation_window_hours=999,  # far in the future
        )
        due = store.get_due_predictions(db_path)
        assert due == []
    finally:
        os.remove(db_path)


def test_resolve_prediction_and_accuracy_stats():
    db_path = temp_db_path()
    try:
        pid1 = store.record_prediction(
            db_path, "A", "US", "MX", 1, "1000+", 15, "LOW", "ACCELERATING", 0.0
        )
        pid2 = store.record_prediction(
            db_path, "B", "US", "MX", 2, "500+", 10, "LOW", "EMERGING", 0.0
        )
        store.resolve_prediction(db_path, pid1, "CORRECT", "appeared in MX")
        store.resolve_prediction(db_path, pid2, "INCORRECT", "did not appear")

        stats = store.get_accuracy_stats(db_path)
        assert stats["evaluated"] == 2
        assert stats["correct"] == 1
        assert stats["incorrect"] == 1
        assert stats["accuracy_pct"] == 50.0
        assert stats["pending"] == 0
    finally:
        os.remove(db_path)


def test_resolve_prediction_rejects_invalid_status():
    db_path = temp_db_path()
    try:
        pid = store.record_prediction(
            db_path, "A", "US", "MX", 1, "1000+", 15, "LOW", "ACCELERATING", 0.0
        )
        try:
            store.resolve_prediction(db_path, pid, "MAYBE", "unclear")
            assert False, "should have raised"
        except ValueError:
            pass
    finally:
        os.remove(db_path)


def test_accuracy_stats_empty_ledger_has_no_accuracy_percentage():
    db_path = temp_db_path()
    try:
        stats = store.get_accuracy_stats(db_path)
        assert stats["total_predictions"] == 0
        assert stats["accuracy_pct"] is None
    finally:
        os.remove(db_path)


def test_get_connection_creates_missing_parent_directory():
    """
    A fresh clone, a fresh Docker build, or the ledger.db being intentionally
    dropped before submission all start with no data/ directory at all - and
    sqlite3.connect() never creates missing parent directories on its own.
    """
    tmp_root = tempfile.mkdtemp()
    try:
        nested_db_path = os.path.join(tmp_root, "does", "not", "exist", "ledger.db")
        assert not os.path.exists(os.path.dirname(nested_db_path))

        stats = store.get_accuracy_stats(nested_db_path)  # must not raise

        assert os.path.exists(nested_db_path)
        assert stats["total_predictions"] == 0
    finally:
        import shutil
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_predictor_issues_one_prediction_per_horizon(monkeypatch):
    db_path = temp_db_path()
    try:
        fixed_trends = [
            {"topic": "Breaking Story", "search_volume": "2000+", "related_news": [{"headline": "Real coverage", "url": "https://example.com"}]},
        ]
        monkeypatch.setattr(
            predictor.GoogleTrendsService, "fetch_daily_trending_topics",
            staticmethod(lambda geo: fixed_trends if geo == "US" else [])
        )

        ids = predictor.make_predictions_for_market_pair("US", "MX", db_path=db_path)
        assert len(ids) == len(predictor.FORECAST_HORIZONS_HOURS)

        with closing(store.get_connection(db_path)) as conn:
            horizons = {row["evaluation_window_hours"] for row in conn.execute("SELECT evaluation_window_hours FROM predictions")}
        assert horizons == {float(h) for h in predictor.FORECAST_HORIZONS_HOURS}
    finally:
        os.remove(db_path)


def test_predictor_skips_duplicate_pending_topic_per_horizon(monkeypatch):
    db_path = temp_db_path()
    try:
        fixed_trends = [
            {"topic": "Breaking Story", "search_volume": "2000+", "related_news": [{"headline": "Real coverage", "url": "https://example.com"}]},
        ]
        monkeypatch.setattr(
            predictor.GoogleTrendsService, "fetch_daily_trending_topics",
            staticmethod(lambda geo: fixed_trends if geo == "US" else [])
        )

        first_ids = predictor.make_predictions_for_market_pair("US", "MX", db_path=db_path)
        assert len(first_ids) == len(predictor.FORECAST_HORIZONS_HOURS)

        second_ids = predictor.make_predictions_for_market_pair("US", "MX", db_path=db_path)
        assert second_ids == []  # every horizon already has a PENDING prediction for this topic/pair
    finally:
        os.remove(db_path)


def test_predictor_resolves_correct_when_topic_now_visible(monkeypatch):
    db_path = temp_db_path()
    try:
        pid = store.record_prediction(
            db_path, "Now Trending Here Too", "US", "MX", 1, "2000+", 15, "LOW", "ACCELERATING", 0.0
        )

        def fake_fetch(geo):
            if geo == "MX":
                return [{"topic": "Now Trending Here Too", "search_volume": "500+", "related_news": []}]
            return []

        monkeypatch.setattr(
            predictor.GoogleTrendsService, "fetch_daily_trending_topics", staticmethod(fake_fetch)
        )

        result = predictor.resolve_due_predictions(db_path=db_path)
        assert result["resolved_correct"] == 1
        assert result["resolved_incorrect"] == 0

        stats = store.get_accuracy_stats(db_path)
        assert stats["correct"] == 1
    finally:
        os.remove(db_path)


def test_predictor_resolves_incorrect_when_topic_still_absent(monkeypatch):
    db_path = temp_db_path()
    try:
        store.record_prediction(
            db_path, "Never Arrives", "US", "MX", 1, "2000+", 15, "LOW", "ACCELERATING", 0.0
        )

        # Non-empty target trending list that genuinely doesn't contain the
        # predicted topic - a real, checkable absence.
        monkeypatch.setattr(
            predictor.GoogleTrendsService, "fetch_daily_trending_topics",
            staticmethod(lambda geo: [{"topic": "Something Unrelated", "search_volume": "500+", "related_news": []}])
        )

        result = predictor.resolve_due_predictions(db_path=db_path)
        assert result["resolved_correct"] == 0
        assert result["resolved_incorrect"] == 1
        assert result["skipped_fetch_failed"] == 0
    finally:
        os.remove(db_path)


def test_predictor_skips_resolution_when_target_fetch_fails(monkeypatch):
    """
    An empty result from fetch_daily_trending_topics means the real feed was
    unreachable (see trends_service.py's "no silent fabrication" fix), not
    that the target market genuinely has zero trending topics. Resolving as
    INCORRECT in that case would poison the ledger's accuracy stat on every
    transient network failure during the unattended cycle.
    """
    db_path = temp_db_path()
    try:
        store.record_prediction(
            db_path, "Unresolved Due To Outage", "US", "MX", 1, "2000+", 15, "LOW", "ACCELERATING", 0.0
        )

        monkeypatch.setattr(
            predictor.GoogleTrendsService, "fetch_daily_trending_topics",
            staticmethod(lambda geo: [])
        )

        result = predictor.resolve_due_predictions(db_path=db_path)
        assert result["resolved_correct"] == 0
        assert result["resolved_incorrect"] == 0
        assert result["skipped_fetch_failed"] == 1

        due_again = store.get_due_predictions(db_path)
        assert len(due_again) == 1  # still PENDING, will be retried next cycle
    finally:
        os.remove(db_path)


def test_predictor_excludes_candidates_with_no_real_news_backing(monkeypatch):
    """
    A high-velocity candidate with zero related_news items is usually a bare
    name spike (a specific athlete, a local fixture) with no realistic path
    to another country's own top-10 trending list within hours. Added after
    the first live batch (90 predictions, all such candidates) resolved at a
    genuine 0% accuracy - see BITACORA_PROYECTO.md.
    """
    db_path = temp_db_path()
    try:
        fixed_trends = [
            {"topic": "Bare Name Spike", "search_volume": "5000+", "related_news": []},
        ]
        monkeypatch.setattr(
            predictor.GoogleTrendsService, "fetch_daily_trending_topics",
            staticmethod(lambda geo: fixed_trends if geo == "US" else [])
        )

        ids = predictor.make_predictions_for_market_pair("US", "MX", db_path=db_path)
        assert ids == []
    finally:
        os.remove(db_path)
