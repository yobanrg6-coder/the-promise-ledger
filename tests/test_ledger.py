"""
Tests for the falsifiable prediction ledger. Uses an in-memory fake backend
(store.InMemoryBackend) with the exact same query semantics as the real
Firestore backend - no network calls, so this runs fast and deterministically
in any environment.
"""

import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ledger"))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agents"))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp_server"))

import store
import predictor
from trends_service import TrendsCheckUnavailable


def test_record_and_fetch_prediction_roundtrip():
    backend = store.InMemoryBackend()
    pid = store.record_prediction(
        topic="Test Topic", baseline_geo="US", target_geo="MX",
        baseline_rank=1, baseline_search_volume="1000+", baseline_velocity_score=15,
        baseline_saturation_level="LOW", baseline_lifecycle_stage="ACCELERATING",
        evaluation_window_hours=0.0,  # due immediately
        backend=backend,
    )
    assert pid

    due = store.get_due_predictions(backend=backend)
    assert len(due) == 1
    assert due[0]["topic"] == "Test Topic"
    assert due[0]["status"] == "PENDING"


def test_not_due_prediction_is_excluded():
    backend = store.InMemoryBackend()
    store.record_prediction(
        topic="Future Topic", baseline_geo="US", target_geo="MX",
        baseline_rank=1, baseline_search_volume="1000+", baseline_velocity_score=15,
        baseline_saturation_level="LOW", baseline_lifecycle_stage="ACCELERATING",
        evaluation_window_hours=999,  # far in the future
        backend=backend,
    )
    due = store.get_due_predictions(backend=backend)
    assert due == []


def test_resolve_prediction_and_accuracy_stats():
    backend = store.InMemoryBackend()
    pid1 = store.record_prediction(
        "A", "US", "MX", 1, "1000+", 15, "LOW", "ACCELERATING", 0.0, backend=backend
    )
    pid2 = store.record_prediction(
        "B", "US", "MX", 2, "500+", 10, "LOW", "EMERGING", 0.0, backend=backend
    )
    store.resolve_prediction(pid1, "CORRECT", "appeared in MX", backend=backend)
    store.resolve_prediction(pid2, "INCORRECT", "did not appear", backend=backend)

    stats = store.get_accuracy_stats(backend=backend)
    assert stats["evaluated"] == 2
    assert stats["correct"] == 1
    assert stats["incorrect"] == 1
    assert stats["accuracy_pct"] == 50.0
    assert stats["pending"] == 0


def test_resolve_prediction_rejects_invalid_status():
    backend = store.InMemoryBackend()
    pid = store.record_prediction(
        "A", "US", "MX", 1, "1000+", 15, "LOW", "ACCELERATING", 0.0, backend=backend
    )
    try:
        store.resolve_prediction(pid, "MAYBE", "unclear", backend=backend)
        assert False, "should have raised"
    except ValueError:
        pass


def test_accuracy_stats_empty_ledger_has_no_accuracy_percentage():
    backend = store.InMemoryBackend()
    stats = store.get_accuracy_stats(backend=backend)
    assert stats["total_predictions"] == 0
    assert stats["accuracy_pct"] is None


def test_predictor_issues_one_prediction_per_horizon(monkeypatch):
    backend = store.InMemoryBackend()
    fixed_trends = [
        {"topic": "Breaking Story", "search_volume": "2000+", "related_news": [{"headline": "Real coverage", "url": "https://example.com"}]},
    ]
    monkeypatch.setattr(
        predictor.GoogleTrendsService, "fetch_daily_trending_topics",
        staticmethod(lambda geo: fixed_trends if geo == "US" else [])
    )

    ids = predictor.make_predictions_for_market_pair("US", "MX", backend=backend)
    assert len(ids) == len(predictor.FORECAST_HORIZONS_HOURS)

    horizons = {p["evaluation_window_hours"] for p in backend.query_by_status("PENDING")}
    assert horizons == {float(h) for h in predictor.FORECAST_HORIZONS_HOURS}


def test_predictor_skips_duplicate_pending_topic_per_horizon(monkeypatch):
    backend = store.InMemoryBackend()
    fixed_trends = [
        {"topic": "Breaking Story", "search_volume": "2000+", "related_news": [{"headline": "Real coverage", "url": "https://example.com"}]},
    ]
    monkeypatch.setattr(
        predictor.GoogleTrendsService, "fetch_daily_trending_topics",
        staticmethod(lambda geo: fixed_trends if geo == "US" else [])
    )

    first_ids = predictor.make_predictions_for_market_pair("US", "MX", backend=backend)
    assert len(first_ids) == len(predictor.FORECAST_HORIZONS_HOURS)

    second_ids = predictor.make_predictions_for_market_pair("US", "MX", backend=backend)
    assert second_ids == []  # every horizon already has a PENDING prediction for this topic/pair


def test_predictor_resolves_correct_when_topic_now_visible(monkeypatch):
    backend = store.InMemoryBackend()
    store.record_prediction(
        "Now Trending Here Too", "US", "MX", 1, "2000+", 15, "LOW", "ACCELERATING", 0.0, backend=backend
    )

    def fake_fetch(geo):
        if geo == "MX":
            return [{"topic": "Now Trending Here Too", "search_volume": "500+", "related_news": []}]
        return []

    monkeypatch.setattr(
        predictor.GoogleTrendsService, "fetch_daily_trending_topics", staticmethod(fake_fetch)
    )

    result = predictor.resolve_due_predictions(backend=backend)
    assert result["resolved_correct"] == 1
    assert result["resolved_incorrect"] == 0

    stats = store.get_accuracy_stats(backend=backend)
    assert stats["correct"] == 1


def test_predictor_sends_to_graded_check_when_topic_absent_everywhere(monkeypatch):
    """
    Cloud Run's half of resolution never calls pytrends (Google blocks it
    from Cloud Run's egress - see TrendsCheckUnavailable's docstring), so an
    exact-match miss across every tracked target isn't resolved INCORRECT
    outright - it's handed to NEEDS_GRADED_CHECK for the local relay
    (run_graded_check_relay.py) to finish.
    """
    backend = store.InMemoryBackend()
    store.record_prediction(
        "Never Arrives", "US", "MX", 1, "2000+", 15, "LOW", "ACCELERATING", 0.0, backend=backend
    )

    # Non-empty target trending list that genuinely doesn't contain the
    # predicted topic - a real, checkable absence.
    monkeypatch.setattr(
        predictor.GoogleTrendsService, "fetch_daily_trending_topics",
        staticmethod(lambda geo: [{"topic": "Something Unrelated", "search_volume": "500+", "related_news": []}])
    )

    result = predictor.resolve_due_predictions(backend=backend)
    assert result["resolved_correct"] == 0
    assert result["resolved_incorrect"] == 0
    assert result["sent_to_graded_check"] == 1
    assert result["skipped_fetch_failed"] == 0

    needs_check = store.get_needs_graded_check(backend=backend)
    assert len(needs_check) == 1
    assert needs_check[0]["topic"] == "Never Arrives"


def _make_needs_graded_check_prediction(backend, topic="Rising Elsewhere"):
    """Shared setup: get a real prediction into NEEDS_GRADED_CHECK status via
    the same path production code uses, instead of poking the field directly."""
    store.record_prediction(
        topic, "US", "MX", 1, "2000+", 15, "LOW", "ACCELERATING", 0.0, backend=backend
    )
    predictor.resolve_due_predictions(backend=backend)  # uses fetch_daily_trending_topics as currently monkeypatched
    [prediction] = store.get_needs_graded_check(backend=backend)
    return prediction


def test_graded_check_resolves_correct_via_relative_signal(monkeypatch):
    """
    A topic can genuinely gain real search traction in a target market
    without literally out-ranking that market's own top-10 obsessions. The
    comparative check (see fetch_relative_search_ratio) is what catches that
    - as long as it clears the real, non-trivial calibrated bar. This is the
    local relay's job, never Cloud Run's - see resolve_needs_graded_check.
    """
    backend = store.InMemoryBackend()
    monkeypatch.setattr(
        predictor.GoogleTrendsService, "fetch_daily_trending_topics",
        staticmethod(lambda geo: [{"topic": "Weakest Real Trend", "search_volume": "500+", "related_news": []}])
    )
    _make_needs_graded_check_prediction(backend)

    monkeypatch.setattr(
        predictor.GoogleTrendsService, "fetch_relative_search_ratio",
        staticmethod(lambda candidate, anchor, geo: 0.4)  # above RELATIVE_SIGNAL_THRESHOLD (0.25)
    )
    monkeypatch.setattr(predictor.time, "sleep", lambda seconds: None)

    result = predictor.resolve_needs_graded_check(backend=backend)
    assert result["resolved_correct"] == 1
    assert result["resolved_incorrect"] == 0
    assert result["still_unavailable"] == 0


def test_graded_check_resolves_incorrect_when_relative_signal_too_weak(monkeypatch):
    backend = store.InMemoryBackend()
    monkeypatch.setattr(
        predictor.GoogleTrendsService, "fetch_daily_trending_topics",
        staticmethod(lambda geo: [{"topic": "Weakest Real Trend", "search_volume": "500+", "related_news": []}])
    )
    _make_needs_graded_check_prediction(backend, topic="Barely There")

    monkeypatch.setattr(
        predictor.GoogleTrendsService, "fetch_relative_search_ratio",
        staticmethod(lambda candidate, anchor, geo: 0.05)  # below RELATIVE_SIGNAL_THRESHOLD (0.25)
    )
    monkeypatch.setattr(predictor.time, "sleep", lambda seconds: None)

    result = predictor.resolve_needs_graded_check(backend=backend)
    assert result["resolved_correct"] == 0
    assert result["resolved_incorrect"] == 1


def test_graded_check_leaves_item_unresolved_when_signal_check_unavailable(monkeypatch):
    """
    A rate-limited or otherwise failed comparative check is a "couldn't
    check" - it must not be resolved as INCORRECT. The item stays
    NEEDS_GRADED_CHECK and gets retried on the relay's next run.
    """
    backend = store.InMemoryBackend()
    monkeypatch.setattr(
        predictor.GoogleTrendsService, "fetch_daily_trending_topics",
        staticmethod(lambda geo: [{"topic": "Weakest Real Trend", "search_volume": "500+", "related_news": []}])
    )
    _make_needs_graded_check_prediction(backend, topic="Rate Limited Check")

    def raise_unavailable(candidate, anchor, geo):
        raise TrendsCheckUnavailable("simulated 429 exhausted retries")

    monkeypatch.setattr(
        predictor.GoogleTrendsService, "fetch_relative_search_ratio",
        staticmethod(raise_unavailable)
    )
    monkeypatch.setattr(predictor.time, "sleep", lambda seconds: None)

    result = predictor.resolve_needs_graded_check(backend=backend)
    assert result["resolved_correct"] == 0
    assert result["resolved_incorrect"] == 0
    assert result["still_unavailable"] == 1

    still_there = store.get_needs_graded_check(backend=backend)
    assert len(still_there) == 1  # still awaiting a graded check, will be retried


def test_predictor_skips_resolution_when_target_fetch_fails(monkeypatch):
    """
    An empty result from fetch_daily_trending_topics means the real feed was
    unreachable (see trends_service.py's "no silent fabrication" fix), not
    that the target market genuinely has zero trending topics. Resolving as
    INCORRECT in that case would poison the ledger's accuracy stat on every
    transient network failure during the unattended cycle.
    """
    backend = store.InMemoryBackend()
    store.record_prediction(
        "Unresolved Due To Outage", "US", "MX", 1, "2000+", 15, "LOW", "ACCELERATING", 0.0, backend=backend
    )

    monkeypatch.setattr(
        predictor.GoogleTrendsService, "fetch_daily_trending_topics",
        staticmethod(lambda geo: [])
    )

    result = predictor.resolve_due_predictions(backend=backend)
    assert result["resolved_correct"] == 0
    assert result["resolved_incorrect"] == 0
    assert result["skipped_fetch_failed"] == 1

    due_again = store.get_due_predictions(backend=backend)
    assert len(due_again) == 1  # still PENDING, will be retried next cycle


def test_predictor_excludes_candidates_with_no_real_news_backing(monkeypatch):
    """
    A high-velocity candidate with zero related_news items is usually a bare
    name spike (a specific athlete, a local fixture) with no realistic path
    to another country's own top-10 trending list within hours. Added after
    the first live batch (90 predictions, all such candidates) resolved at a
    genuine 0% accuracy - see BITACORA_PROYECTO.md.
    """
    backend = store.InMemoryBackend()
    fixed_trends = [
        {"topic": "Bare Name Spike", "search_volume": "5000+", "related_news": []},
    ]
    monkeypatch.setattr(
        predictor.GoogleTrendsService, "fetch_daily_trending_topics",
        staticmethod(lambda geo: fixed_trends if geo == "US" else [])
    )

    ids = predictor.make_predictions_for_market_pair("US", "MX", backend=backend)
    assert ids == []


def test_make_predictions_for_baseline_issues_one_prediction_per_horizon(monkeypatch):
    backend = store.InMemoryBackend()
    fixed_trends = [
        {"topic": "Breaking Story", "search_volume": "2000+", "related_news": [{"headline": "Real coverage", "url": "https://example.com"}]},
    ]
    monkeypatch.setattr(
        predictor.GoogleTrendsService, "fetch_daily_trending_topics",
        staticmethod(lambda geo: fixed_trends if geo == "US" else [])
    )

    ids = predictor.make_predictions_for_baseline("US", ["MX", "ES", "GB"], backend=backend)
    assert len(ids) == len(predictor.FORECAST_HORIZONS_HOURS)

    pending = backend.query_by_status("PENDING")
    assert {p["evaluation_window_hours"] for p in pending} == {float(h) for h in predictor.FORECAST_HORIZONS_HOURS}
    assert all(p["target_geos"] == ["MX", "ES", "GB"] for p in pending)


def test_make_predictions_for_baseline_skips_topic_present_in_any_target(monkeypatch):
    fixed_trends = {
        "US": [{"topic": "Already Global", "search_volume": "2000+", "related_news": [{"headline": "x", "url": "y"}]}],
        "MX": [{"topic": "Already Global", "search_volume": "500+", "related_news": []}],
        "ES": [],
        "GB": [],
    }
    backend = store.InMemoryBackend()
    monkeypatch.setattr(
        predictor.GoogleTrendsService, "fetch_daily_trending_topics",
        staticmethod(lambda geo: fixed_trends.get(geo, []))
    )

    ids = predictor.make_predictions_for_baseline("US", ["MX", "ES", "GB"], backend=backend)
    assert ids == []  # already visible in MX - not a real gap, no bet placed


def test_resolve_due_predictions_resolves_correct_if_match_in_any_tracked_target(monkeypatch):
    """
    The whole point of the multi-target widening: a hit in ANY tracked
    target resolves CORRECT, not just one fixed pair partner.
    """
    backend = store.InMemoryBackend()
    store.record_multi_target_prediction(
        "Crossed Into GB", "US", ["MX", "ES", "GB"], 1, "2000+", 15, "LOW", "ACCELERATING", 0.0, backend=backend
    )

    fixed_trends = {
        "MX": [{"topic": "Something Else", "search_volume": "500+"}],
        "ES": [{"topic": "Another Thing", "search_volume": "300+"}],
        "GB": [{"topic": "Crossed Into GB", "search_volume": "1000+"}],
    }
    monkeypatch.setattr(
        predictor.GoogleTrendsService, "fetch_daily_trending_topics",
        staticmethod(lambda geo: fixed_trends.get(geo, []))
    )

    result = predictor.resolve_due_predictions(backend=backend)
    assert result["resolved_correct"] == 1
    assert result["sent_to_graded_check"] == 0
