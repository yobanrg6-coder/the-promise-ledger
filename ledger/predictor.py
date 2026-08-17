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
from contextlib import closing
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
)
from trends_service import GoogleTrendsService

MAX_NEW_CANDIDATES_PER_MARKET_PAIR = 3
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


def _normalize(topic: str) -> str:
    return topic.strip().lower()


def make_predictions_for_market_pair(baseline_geo: str, target_geo: str, db_path: str = store.DEFAULT_DB_PATH) -> list[str]:
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

    with closing(store.get_connection(db_path)) as conn:
        existing_pending = {
            (_normalize(row["topic"]), row["evaluation_window_hours"])
            for row in conn.execute(
                "SELECT topic, evaluation_window_hours FROM predictions WHERE status = 'PENDING' AND baseline_geo = ? AND target_geo = ?",
                (baseline_geo, target_geo),
            ).fetchall()
        }

    new_ids = []
    for idx, item, velocity, saturation_level, lifecycle in with_scores:
        topic = item.get("topic", "")
        for horizon_hours in FORECAST_HORIZONS_HOURS:
            if (_normalize(topic), float(horizon_hours)) in existing_pending:
                continue
            prediction_id = store.record_prediction(
                db_path=db_path,
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


def resolve_due_predictions(db_path: str = store.DEFAULT_DB_PATH) -> dict[str, int]:
    due = store.get_due_predictions(db_path)
    resolved_correct = 0
    resolved_incorrect = 0
    skipped_fetch_failed = 0
    # None = not attempted yet this run, [] would be indistinguishable from a
    # real (extremely unlikely) empty trending list, so cache the raw result
    # object (list or None-on-failure) rather than always a list.
    target_cache: dict[str, list[dict[str, Any]] | None] = {}

    for prediction in due:
        target_geo = prediction["target_geo"]
        if target_geo not in target_cache:
            fetched = GoogleTrendsService.fetch_daily_trending_topics(geo=target_geo)
            # An empty result means either the RSS feed was unreachable or
            # returned nothing parsable - see trends_service.py's own "no
            # silent fabrication" fix. Either way we cannot tell "the topic
            # genuinely never showed up" from "we failed to check", and
            # resolving as INCORRECT in that case would silently poison the
            # Forecast Ledger's accuracy stat on every transient network
            # hiccup during the unattended 6h cycle. Leave PENDING instead -
            # it gets re-checked on the next cycle.
            target_cache[target_geo] = fetched if fetched else None
        target_trends = target_cache[target_geo]

        if target_trends is None:
            skipped_fetch_failed += 1
            continue

        match = next(
            (t for t in target_trends if _normalize(t.get("topic", "")) == _normalize(prediction["topic"])),
            None,
        )
        if match:
            rank = target_trends.index(match) + 1
            store.resolve_prediction(
                db_path, prediction["id"], "CORRECT",
                f"Appeared in {target_geo} trending list at rank {rank} ({match.get('search_volume', '?')})."
            )
            resolved_correct += 1
        else:
            store.resolve_prediction(
                db_path, prediction["id"], "INCORRECT",
                f"Still not visible in {target_geo}'s trending list as of resolution time."
            )
            resolved_incorrect += 1

    return {
        "resolved_correct": resolved_correct,
        "resolved_incorrect": resolved_incorrect,
        "skipped_fetch_failed": skipped_fetch_failed,
        "checked": len(due),
    }
