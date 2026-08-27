"""
Makes and resolves falsifiable predictions using only real Google Trends data
and the deterministic scoring engine - no LLM calls, so this can run
unattended for days without an API key or any inference cost.

A prediction is simple and checkable: "topic X, currently trending in
baseline_geo and NOT YET visible in target_geo, will become visible in
target_geo's own trending list within N hours." Resolution re-pulls
target_geo's trending list at (or after) the deadline and checks for real.
"""

import os
import sys
import time
from typing import Any

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agents"))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp_server"))

import store
from scoring import (
    classify_lifecycle_stage,
    compute_saturation,
    compute_velocity_score,
    find_cross_market_gaps,
    find_multi_target_gaps,
)
from trends_service import GoogleTrendsService, TrendsCheckUnavailable

MAX_NEW_CANDIDATES_PER_MARKET_PAIR = 3
MAX_NEW_CANDIDATES_PER_BASELINE = 3
# Calibrated against real trending-topic velocities observed live (most real
# items score 2-12 on a normal day; 20+ only shows up on major spikes). A
# fixed threshold near the top of that normal range still filters the weakest
# half without requiring a rare spike day to produce any predictions at all.
MIN_VELOCITY_TO_PREDICT = 8
# A candidate with zero related_news_item entries in the RSS feed is usually a
# one-off, hyper-local name spike (a specific athlete, a local team fixture)
# that has no real reason to ever surface on another country's own top-10
# trending list. Requiring at least one real news article as backing selects
# for candidates with an actual broader story behind them, which is the only
# kind of topic a 1-24h cross-market prediction can plausibly win. Added
# 16-ago-2026 after the first live batch (90 predictions, all US-outbound,
# mostly bare name spikes like "dennis schroder") resolved at a genuine 0%.
MIN_RELATED_NEWS_TO_PREDICT = 1

# Multiple horizons per candidate instead of one: waiting weeks for enough
# evaluated predictions to show a meaningful accuracy stat isn't an option
# before the deadline. Firing 1h/4h/12h/24h bets on the same candidate turns
# every cycle into up to 4 independent, faster-resolving falsifiable claims -
# still real (each is checked against a fresh Trends pull), just more of them.
FORECAST_HORIZONS_HOURS = [1, 4, 12, 24]

# Provisional calibration from a single live comparison (26-ago-2026): two
# genuinely real, currently-trending US topics compared against each other
# landed at ~0.15-0.3, while a structurally irrelevant topic ("banco del
# bienestar" vs a real US trending item) landed at 0.0. Set below that real
# range so genuine near-misses aren't punished, well above 0 so pure noise
# still fails. Revisit once enough RELATIVE_SEARCH_SIGNAL resolutions have
# accumulated to calibrate against real correct/incorrect outcomes instead of
# one live sample - see fetch_relative_search_ratio's own docstring for why a
# single-keyword threshold isn't usable at all.
RELATIVE_SIGNAL_THRESHOLD = 0.25
# pytrends is an unofficial, rate-limited endpoint - only called for
# candidates that already missed the free exact top-10 check. Raised from an
# initial 2.0s to 12.0s after a live Cloud Run run (26-ago-2026) got HTTP 429
# on all 35 real calls in one cycle at the lower value - still not a
# guarantee (see TrendsCheckUnavailable's own docstring on the IP-reputation
# possibility), but cheap to try before concluding it can't be fixed by pacing.
RELATIVE_SIGNAL_THROTTLE_SECONDS = 12.0


def _normalize(topic: str) -> str:
    return topic.strip().lower()


def make_predictions_for_market_pair(baseline_geo: str, target_geo: str, backend: store.PredictionBackend | None = None) -> list[str]:
    baseline_trends = GoogleTrendsService.fetch_daily_trending_topics(geo=baseline_geo)
    target_trends = GoogleTrendsService.fetch_daily_trending_topics(geo=target_geo)
    gaps = find_cross_market_gaps(baseline_geo, baseline_trends, target_geo, target_trends)

    with_scores = []
    total = len(baseline_trends)
    for idx, item in enumerate(baseline_trends):
        if _normalize(item.get("topic", "")) not in {_normalize(g["topic"]) for g in gaps}:
            continue
        velocity = compute_velocity_score(item.get("search_volume", ""), idx, total)
        if velocity < MIN_VELOCITY_TO_PREDICT:
            continue
        related_news_count = len(item.get("related_news", []))
        if related_news_count < MIN_RELATED_NEWS_TO_PREDICT:
            continue
        _, saturation_level = compute_saturation(related_news_count, 0)
        lifecycle = classify_lifecycle_stage(item.get("search_volume", ""), idx, total, related_news_count, 0)
        with_scores.append((idx, item, velocity, saturation_level, lifecycle))

    with_scores.sort(key=lambda t: t[2], reverse=True)  # highest velocity first
    with_scores = with_scores[:MAX_NEW_CANDIDATES_PER_MARKET_PAIR]

    existing_pending = {
        (_normalize(row["topic"]), row["evaluation_window_hours"])
        for row in store.get_pending_for_pair(baseline_geo, target_geo, backend=backend)
    }

    new_ids = []
    for idx, item, velocity, saturation_level, lifecycle in with_scores:
        topic = item.get("topic", "")
        for horizon_hours in FORECAST_HORIZONS_HOURS:
            if (_normalize(topic), float(horizon_hours)) in existing_pending:
                continue
            prediction_id = store.record_prediction(
                backend=backend,
                topic=topic,
                baseline_geo=baseline_geo,
                target_geo=target_geo,
                baseline_rank=idx + 1,
                baseline_search_volume=item.get("search_volume", ""),
                baseline_velocity_score=velocity,
                baseline_saturation_level=saturation_level,
                baseline_lifecycle_stage=lifecycle,
                evaluation_window_hours=float(horizon_hours),
            )
            new_ids.append(prediction_id)
    return new_ids


def make_predictions_for_baseline(
    baseline_geo: str, target_geos: list[str], backend: store.PredictionBackend | None = None
) -> list[str]:
    """
    Widened version of make_predictions_for_market_pair: the falsifiable
    claim becomes "will appear in at least one of target_geos" instead of one
    fixed pair - see find_multi_target_gaps and
    ledger/store.py::record_multi_target_prediction for why. Same candidate
    filters (velocity, real-news backing) and horizon fan-out as the pair
    version, just fed by a broader gap search.
    """
    baseline_trends = GoogleTrendsService.fetch_daily_trending_topics(geo=baseline_geo)
    target_trends_by_geo = {geo: GoogleTrendsService.fetch_daily_trending_topics(geo=geo) for geo in target_geos}
    gaps = find_multi_target_gaps(baseline_geo, baseline_trends, target_trends_by_geo)
    gap_topics = {_normalize(g["topic"]) for g in gaps}

    with_scores = []
    total = len(baseline_trends)
    for idx, item in enumerate(baseline_trends):
        if _normalize(item.get("topic", "")) not in gap_topics:
            continue
        velocity = compute_velocity_score(item.get("search_volume", ""), idx, total)
        if velocity < MIN_VELOCITY_TO_PREDICT:
            continue
        related_news_count = len(item.get("related_news", []))
        if related_news_count < MIN_RELATED_NEWS_TO_PREDICT:
            continue
        _, saturation_level = compute_saturation(related_news_count, 0)
        lifecycle = classify_lifecycle_stage(item.get("search_volume", ""), idx, total, related_news_count, 0)
        with_scores.append((idx, item, velocity, saturation_level, lifecycle))

    with_scores.sort(key=lambda t: t[2], reverse=True)  # highest velocity first
    with_scores = with_scores[:MAX_NEW_CANDIDATES_PER_BASELINE]

    existing_pending = {
        (_normalize(row["topic"]), row["evaluation_window_hours"])
        for row in store.get_pending_for_baseline(baseline_geo, backend=backend)
    }

    new_ids = []
    for idx, item, velocity, saturation_level, lifecycle in with_scores:
        topic = item.get("topic", "")
        for horizon_hours in FORECAST_HORIZONS_HOURS:
            if (_normalize(topic), float(horizon_hours)) in existing_pending:
                continue
            prediction_id = store.record_multi_target_prediction(
                backend=backend,
                topic=topic,
                baseline_geo=baseline_geo,
                target_geos=target_geos,
                baseline_rank=idx + 1,
                baseline_search_volume=item.get("search_volume", ""),
                baseline_velocity_score=velocity,
                baseline_saturation_level=saturation_level,
                baseline_lifecycle_stage=lifecycle,
                evaluation_window_hours=float(horizon_hours),
            )
            new_ids.append(prediction_id)
    return new_ids


def _cached_trends_fetcher(target_cache: dict[str, list[dict[str, Any]] | None]):
    # None = not attempted yet this run, [] would be indistinguishable from a
    # real (extremely unlikely) empty trending list, so cache the raw result
    # object (list or None-on-failure) rather than always a list.
    def get_trends(geo: str) -> list[dict[str, Any]] | None:
        if geo not in target_cache:
            fetched = GoogleTrendsService.fetch_daily_trending_topics(geo=geo)
            target_cache[geo] = fetched if fetched else None
        return target_cache[geo]

    return get_trends


def resolve_due_predictions(backend: store.PredictionBackend | None = None) -> dict[str, int]:
    """
    Cloud Run's half of resolution: the free, reliable exact-match check
    only - no pytrends call, ever (Google blocks it from Cloud Run's egress,
    verified live 26-ago-2026 with a real cooldown retry still failing - see
    TrendsCheckUnavailable). Handles both the current multi-target
    predictions (`target_geos`, a list - see make_predictions_for_baseline)
    and older single-target ones (`target_geo`) recorded before that change,
    uniformly.

    A miss across every tracked target is a real, high bar, but not proof of
    "wrong" - it's hand off to NEEDS_GRADED_CHECK for
    ledger/run_graded_check_relay.py (a non-Cloud-Run, non-datacenter IP) to
    finish, rather than resolved INCORRECT from data we know is incomplete.
    """
    due = store.get_due_predictions(backend=backend)
    resolved_correct = 0
    resolved_incorrect = 0  # kept for stable return shape - Cloud Run never sets this itself anymore
    sent_to_graded_check = 0
    skipped_fetch_failed = 0
    get_trends = _cached_trends_fetcher({})

    for prediction in due:
        target_geos = prediction.get("target_geos") or [prediction["target_geo"]]
        topic = prediction["topic"]

        match_found = None
        any_fetch_failed = False
        for target_geo in target_geos:
            target_trends = get_trends(target_geo)
            if target_trends is None:
                # An empty result means the RSS feed was unreachable or
                # returned nothing parsable - see trends_service.py's own "no
                # silent fabrication" fix. Can't tell "genuinely absent" from
                # "failed to check" for this target, so don't conclude
                # anything from it - but other targets may still resolve it.
                any_fetch_failed = True
                continue
            match = next(
                (t for t in target_trends if _normalize(t.get("topic", "")) == _normalize(topic)),
                None,
            )
            if match:
                match_found = (target_geo, target_trends.index(match) + 1, match)
                break

        if match_found:
            target_geo, rank, match = match_found
            store.resolve_prediction(
                prediction["id"], "CORRECT",
                f"Appeared in {target_geo} trending list at rank {rank} ({match.get('search_volume', '?')}).",
                backend=backend,
            )
            resolved_correct += 1
            continue

        if any_fetch_failed:
            # Couldn't confirm absence everywhere this cycle - retry next time.
            skipped_fetch_failed += 1
            continue

        store.mark_needs_graded_check(
            prediction["id"],
            f"Not in the top-10 of any of {target_geos} as of {store.utcnow_iso()}.",
            backend=backend,
        )
        sent_to_graded_check += 1

    return {
        "resolved_correct": resolved_correct,
        "resolved_incorrect": resolved_incorrect,
        "sent_to_graded_check": sent_to_graded_check,
        "skipped_fetch_failed": skipped_fetch_failed,
        "checked": len(due),
    }


def resolve_needs_graded_check(backend: store.PredictionBackend | None = None) -> dict[str, int]:
    """
    Second-pass, final resolution for predictions Cloud Run could only
    confirm were absent from every tracked target's own top-10 (see
    resolve_due_predictions -> store.mark_needs_graded_check). Must be run
    from a non-datacenter IP - see ledger/run_graded_check_relay.py and
    fetch_relative_search_ratio's docstring for why Cloud Run's own egress
    cannot do this reliably.

    Two checks, in order: (1) a fresh, still-free exact top-10 re-check
    (time has passed since Cloud Run's miss, so a late real arrival is
    possible), then (2) the comparative pytrends signal against each
    target's own weakest currently-trending topic, keeping the best result
    across all tracked targets.
    """
    due = store.get_needs_graded_check(backend=backend)
    resolved_correct = 0
    resolved_incorrect = 0
    still_unavailable = 0
    get_trends = _cached_trends_fetcher({})

    for prediction in due:
        target_geos = prediction.get("target_geos") or [prediction["target_geo"]]
        topic = prediction["topic"]

        match_found = None
        any_fetch_ok = False
        for target_geo in target_geos:
            target_trends = get_trends(target_geo)
            if target_trends is None:
                continue
            any_fetch_ok = True
            match = next(
                (t for t in target_trends if _normalize(t.get("topic", "")) == _normalize(topic)),
                None,
            )
            if match:
                match_found = (target_geo, target_trends.index(match) + 1, match)
                break

        if match_found:
            target_geo, rank, match = match_found
            store.resolve_prediction(
                prediction["id"], "CORRECT",
                f"Appeared in {target_geo} trending list at rank {rank} ({match.get('search_volume', '?')}) "
                "on a later re-check.",
                backend=backend,
            )
            resolved_correct += 1
            continue

        if not any_fetch_ok:
            still_unavailable += 1
            continue

        best_ratio = None
        best_anchor = None
        best_geo = None
        any_check_succeeded = False
        for target_geo in target_geos:
            target_trends = get_trends(target_geo)
            if not target_trends:
                continue
            anchor_topic = target_trends[-1].get("topic", "")
            if not anchor_topic or _normalize(anchor_topic) == _normalize(topic):
                continue
            try:
                ratio = GoogleTrendsService.fetch_relative_search_ratio(topic, anchor_topic, target_geo)
                any_check_succeeded = True
            except TrendsCheckUnavailable as e:
                print(f"[Ledger] {e}")
                time.sleep(RELATIVE_SIGNAL_THROTTLE_SECONDS)
                continue
            time.sleep(RELATIVE_SIGNAL_THROTTLE_SECONDS)
            if ratio is not None and (best_ratio is None or ratio > best_ratio):
                best_ratio, best_anchor, best_geo = ratio, anchor_topic, target_geo

        if not any_check_succeeded:
            # pytrends itself unreachable this run - try again next relay run
            # rather than concluding INCORRECT from an incomplete check.
            still_unavailable += 1
            continue

        if best_ratio is not None and best_ratio >= RELATIVE_SIGNAL_THRESHOLD:
            store.resolve_prediction(
                prediction["id"], "CORRECT",
                f"Not in any tracked target's own top-10, but reached {best_ratio:.0%} of the real "
                f"search interest of {best_anchor!r} ({best_geo}'s own weakest currently-trending topic).",
                backend=backend,
            )
            resolved_correct += 1
        else:
            detail = (
                f" (best reached {best_ratio:.0%} of {best_anchor!r} in {best_geo}, "
                f"below the {RELATIVE_SIGNAL_THRESHOLD:.0%} bar)"
            ) if best_ratio is not None else ""
            store.resolve_prediction(
                prediction["id"], "INCORRECT",
                f"Still not visible in any of {target_geos}'s trending lists, and no comparable "
                f"real search interest found{detail}.",
                backend=backend,
            )
            resolved_incorrect += 1

    return {
        "resolved_correct": resolved_correct,
        "resolved_incorrect": resolved_incorrect,
        "still_unavailable": still_unavailable,
        "checked": len(due),
    }
