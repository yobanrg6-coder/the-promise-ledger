"""
Deterministic Opportunity Radar Scoring
Pure functions with no I/O and no LLM calls. These compute the two Radar
sub-scores that have a real data source (search velocity and content
saturation) so the "transparent math" the UI promises is actually math,
not an LLM guessing a number inside a Pydantic range.

The remaining sub-scores (recency, audience relevance, hook potential,
brand safety) stay LLM-judged in orchestrator.py because they require
semantic reasoning the raw signals don't carry. total_opportunity_score
is always summed here in Python, never trusted from the LLM, so the
number on screen can never silently disagree with its own breakdown.
"""

import re
from typing import Any

_TRAFFIC_PATTERN = re.compile(r"([\d.]+)\s*([KM]?)\+?", re.IGNORECASE)
_TRAFFIC_MULTIPLIERS = {"": 1, "K": 1_000, "M": 1_000_000}

VELOCITY_MAX = 25
SATURATION_MAX = 15
# Calibrated against real values observed from trends.google.com/trending/rss:
# most items report raw approx_traffic in the 100-2000 range, with rare spikes
# to ~50000 for major global stories. 5000 keeps the scale spread out instead
# of clustering every real-world item near zero (which a 500K ceiling did).
TRAFFIC_CEILING = 5_000


def parse_traffic_estimate(raw: str) -> int:
    """Parse Google Trends style traffic strings ('500K+', '1.2M+') into an int."""
    if not raw:
        return 0
    match = _TRAFFIC_PATTERN.search(raw.strip())
    if not match:
        return 0
    number_part, unit = match.group(1), match.group(2).upper()
    try:
        value = float(number_part)
    except ValueError:
        return 0
    return int(value * _TRAFFIC_MULTIPLIERS.get(unit, 1))


def compute_velocity_score(search_volume: str, rank_index: int, total_items: int) -> int:
    """
    Velocity = how hard this topic is currently accelerating.
    65% traffic magnitude (parsed from the RSS approx_traffic field),
    35% list-rank recency (Google Trends RSS is ordered by trending strength,
    so an earlier rank is itself a real, if coarse, freshness signal).
    """
    traffic = parse_traffic_estimate(search_volume)
    traffic_component = min(traffic / TRAFFIC_CEILING, 1.0)
    total_items = max(total_items, 1)
    rank_component = 1.0 - (rank_index / total_items)
    raw = (0.65 * traffic_component + 0.35 * rank_component) * VELOCITY_MAX
    return max(0, min(VELOCITY_MAX, round(raw)))


def compute_saturation(related_news_count: int, breakout_suggestion_count: int) -> tuple[int, str]:
    """
    Saturation pressure rises with how much coverage already exists (related_news)
    and how many "already established" autocomplete suggestions surround the topic
    (breakout_suggestion_count). Score is inverted: high score = low saturation = good.
    """
    news_pressure = min(related_news_count / 2, 1.0)
    suggestion_pressure = min(breakout_suggestion_count / 8, 1.0)
    pressure = 0.6 * news_pressure + 0.4 * suggestion_pressure
    score = max(0, min(SATURATION_MAX, round((1 - pressure) * SATURATION_MAX)))
    if score >= 10:
        level = "LOW"
    elif score >= 5:
        level = "MEDIUM"
    else:
        level = "SATURATED"
    return score, level


def compute_viral_window_hours(related_news_count: int, breakout_suggestion_count: int, search_volume: str) -> str:
    """Estimate the hours before a breakout topic saturates, from the same real signals."""
    _, level = compute_saturation(related_news_count, breakout_suggestion_count)
    traffic = parse_traffic_estimate(search_volume)
    traffic_component = min(traffic / TRAFFIC_CEILING, 1.0)
    base_hours = {"LOW": 18, "MEDIUM": 10, "SATURATED": 3}[level]
    # high current traffic burns the window faster even at low saturation
    adjusted = max(2, round(base_hours * (1 - 0.4 * traffic_component)))
    low = max(2, adjusted - 4)
    return f"{low}-{adjusted} hours"


LIFECYCLE_STAGES = ("EMERGING", "ACCELERATING", "BREAKOUT", "SATURATED")


def classify_lifecycle_stage(
    search_volume: str,
    rank_index: int,
    total_items: int,
    related_news_count: int,
    breakout_suggestion_count: int,
) -> str:
    """
    Single-snapshot lifecycle classification from real signals only. This is
    honest about what one RSS pull can and cannot tell you: it CANNOT detect
    DECAYING (that requires knowing a topic was higher yesterday, which needs
    historical data we don't persist). Four stages only:

    EMERGING     - present but low traffic/rank, little coverage yet.
    ACCELERATING - rising traffic/rank, coverage still thin (the sweet spot).
    BREAKOUT     - top traffic/rank, coverage still catching up.
    SATURATED    - heavy related-news coverage and/or many breakout
                   suggestions already established - the story is old news.
    """
    velocity = compute_velocity_score(search_volume, rank_index, total_items)
    _, saturation_level = compute_saturation(related_news_count, breakout_suggestion_count)

    if saturation_level == "SATURATED":
        return "SATURATED"
    if velocity >= 20 and saturation_level == "LOW":
        return "BREAKOUT"
    if velocity >= 10:
        return "ACCELERATING"
    return "EMERGING"


# audience_relevance_score is 0-20 (see OpportunityRadar). A topic can score
# high on total_opportunity_score purely from velocity/saturation/hook
# potential while having near-zero real connection to the niche the user
# asked about (e.g. niche "perritos" winning on "tenis cincinnati" because
# tennis was simply the strongest generic trend that day). Below half marks
# means the LLM's own judgment doesn't believe this genuinely fits the
# niche, so autonomous script generation shouldn't fire even at a high total
# score - added 16-ago-2026 after finding exactly this failure live.
MIN_RELEVANCE_FOR_ACT_NOW = 10


def derive_recommended_action(total_score: int, audience_relevance_score: int = 20) -> str:
    """
    Single source of truth for the ACT_NOW / MONITOR / IGNORE threshold.
    ACT_NOW additionally requires real niche relevance, not just raw trend
    strength - see MIN_RELEVANCE_FOR_ACT_NOW above.
    """
    if total_score >= 80 and audience_relevance_score >= MIN_RELEVANCE_FOR_ACT_NOW:
        return "ACT_NOW"
    if total_score >= 55:
        return "MONITOR"
    return "IGNORE"


# Same literal phrases the Critic's own system prompt (virality_auditor.py)
# lists as an automatic 65-point cap. Re-checked here in Python so a right-
# sub-scores-wrong-status LLM inconsistency can't silently let a draft through
# as APPROVED - the Radar score already gets this treatment (never trust LLM
# arithmetic), the Critic's score didn't until this fix.
ANTI_CLICHE_PHRASES = [
    "in today's fast-paced world",
    "let's dive in",
    "look no further",
    "game-changer",
    "unlock the power of",
]


def recompute_virality_verdict(
    hook_strength: int,
    retention_pacing: int,
    value_density: int,
    script_text: str,
) -> dict[str, Any]:
    """
    Recompute overall_virality_score (40% hook + 40% pacing + 20% value, per
    the Critic's own stated formula) and the APPROVED/NEEDS_REVISION status
    in Python, instead of trusting the LLM's self-reported total and gate
    decision verbatim.
    """
    weighted = round(0.4 * hook_strength + 0.4 * retention_pacing + 0.2 * value_density)
    anti_cliche_triggered = any(phrase in script_text.lower() for phrase in ANTI_CLICHE_PHRASES)
    score = min(65, weighted) if anti_cliche_triggered else weighted
    score = max(0, min(100, score))
    status = "APPROVED" if (score >= 80 and not anti_cliche_triggered) else "NEEDS_REVISION"
    return {"overall_virality_score": score, "status": status, "anti_cliche_triggered": anti_cliche_triggered}


def rank_and_ground_candidates(
    daily_trends: list[dict[str, Any]],
    breakout_queries: list[str],
) -> list[dict[str, Any]]:
    """
    Attach grounded velocity_score/saturation_score/estimated_viral_window_hours to
    every raw candidate topic before the LLM ever sees them, so the model is
    ranking against real numbers instead of inventing its own.
    """
    total = len(daily_trends)
    grounded = []
    for idx, item in enumerate(daily_trends):
        related_news_count = len(item.get("related_news", []))
        velocity = compute_velocity_score(item.get("search_volume", ""), idx, total)
        saturation_score, saturation_level = compute_saturation(related_news_count, len(breakout_queries))
        window = compute_viral_window_hours(related_news_count, len(breakout_queries), item.get("search_volume", ""))
        lifecycle_stage = classify_lifecycle_stage(
            item.get("search_volume", ""), idx, total, related_news_count, len(breakout_queries)
        )
        grounded.append({
            **item,
            "grounded_velocity_score": velocity,
            "grounded_saturation_score": saturation_score,
            "grounded_saturation_level": saturation_level,
            "grounded_viral_window_hours": window,
            "grounded_lifecycle_stage": lifecycle_stage,
        })
    return grounded


# Niche-specific rising-query growth is a percentage, not a traffic count, so
# it needs its own ceiling separate from TRAFFIC_CEILING. 300% is already a
# strong real breakout for a specific search query (most rising queries land
# in the 50-150% range); a literal Google "Breakout" label (growth_pct=None)
# is treated as hitting this ceiling, since it represents an even larger,
# unquantified jump from a near-zero base.
RISING_GROWTH_CEILING = 300


def classify_niche_lifecycle_stage(velocity_score: int, saturation_level: str) -> str:
    """Same four-stage rule as classify_lifecycle_stage, applied to scores
    already computed from niche-specific rising-query data instead of the
    national daily-trends RSS feed."""
    if saturation_level == "SATURATED":
        return "SATURATED"
    if velocity_score >= 20 and saturation_level == "LOW":
        return "BREAKOUT"
    if velocity_score >= 10:
        return "ACCELERATING"
    return "EMERGING"


def ground_niche_candidates(niche_signal: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Convert real, niche-specific Google Trends data (GoogleTrendsService.fetch_niche_signal)
    into the same grounded-candidate shape rank_and_ground_candidates produces from the
    generic national daily-trends feed, so both sources can be merged into one candidate
    list the TrendScoutAgent reasons over identically.

    Only the 'rising_queries' - real queries Google itself flags as currently accelerating
    around this exact niche keyword - become candidates. 'top_queries' (already-established,
    high-value related terms) are informational context, not opportunity candidates: a
    query already popular today isn't a timing opportunity, it's the baseline the rising
    queries are breaking out from.

    A query that is, by Google's own methodology, "rising" or "breakout" is by definition
    not yet saturated - so saturation here is derived from the same growth_pct that drives
    velocity (the faster and newer the rise, the further it is from being old news), not a
    second independent signal we don't have for a single search query.
    """
    candidates = []
    for item in niche_signal.get("rising_queries", []):
        growth_pct = item.get("growth_pct")
        is_breakout = growth_pct is None
        effective_growth = RISING_GROWTH_CEILING if is_breakout else growth_pct

        velocity_score = max(0, min(VELOCITY_MAX, round(VELOCITY_MAX * min(effective_growth / RISING_GROWTH_CEILING, 1.0))))
        saturation_score = max(0, min(SATURATION_MAX, round(SATURATION_MAX * min(effective_growth / RISING_GROWTH_CEILING, 1.0))))
        if saturation_score >= 10:
            saturation_level = "LOW"
        elif saturation_score >= 5:
            saturation_level = "MEDIUM"
        else:
            saturation_level = "SATURATED"

        base_hours = {"LOW": 18, "MEDIUM": 10, "SATURATED": 3}[saturation_level]
        low = max(2, base_hours - 4)

        candidates.append({
            "topic": item["query"],
            "search_volume": "Breakout" if is_breakout else f"+{growth_pct}%",
            "published_at": "",
            "related_news": [],
            "grounded_velocity_score": velocity_score,
            "grounded_saturation_score": saturation_score,
            "grounded_saturation_level": saturation_level,
            "grounded_viral_window_hours": f"{low}-{base_hours} hours",
            "grounded_lifecycle_stage": classify_niche_lifecycle_stage(velocity_score, saturation_level),
            "niche_specific": True,
        })
    return candidates


def find_cross_market_gaps(
    baseline_geo: str,
    baseline_trends: list[dict[str, Any]],
    target_geo: str,
    target_trends: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Real, observed fact: which topics are trending in baseline_geo right now
    but do NOT appear in target_geo's current trending list. This is a
    verifiable absence, not a predicted arrival time - we never claim to know
    WHEN it will reach the target market, only that it hasn't yet.
    """
    def normalize(topic: str) -> str:
        return topic.strip().lower()

    target_topics = {normalize(t.get("topic", "")) for t in target_trends}
    gaps = []
    seen_baseline_topics = set()
    for idx, item in enumerate(baseline_trends):
        topic = item.get("topic", "")
        normalized = normalize(topic)
        if normalized in target_topics:
            continue
        # The RSS feed itself can list the same story twice at different
        # ranks (observed live: "christopher nolan" at #1 and #4 in the same
        # BR pull) - keep only the first, strongest-ranked occurrence.
        if normalized in seen_baseline_topics:
            continue
        seen_baseline_topics.add(normalized)
        gaps.append({
            "topic": topic,
            "baseline_geo": baseline_geo,
            "baseline_rank": idx + 1,
            "baseline_search_volume": item.get("search_volume", ""),
            "target_geo": target_geo,
            "status": "NOT_YET_VISIBLE",
        })
    return gaps
