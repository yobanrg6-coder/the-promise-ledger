"""
Unit tests for the deterministic Opportunity Radar scoring in agents/scoring.py.
No network, no LLM, no ADK - just the math that has to be right for the
"transparent score breakdown" claim in the pitch to actually be true.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.scoring import (
    classify_lifecycle_stage,
    classify_niche_lifecycle_stage,
    compute_saturation,
    compute_velocity_score,
    compute_viral_window_hours,
    derive_recommended_action,
    find_cross_market_gaps,
    find_multi_target_gaps,
    ground_niche_candidates,
    parse_traffic_estimate,
    rank_and_ground_candidates,
    recompute_virality_verdict,
)


def test_parse_traffic_estimate_thousands():
    assert parse_traffic_estimate("500K+") == 500_000


def test_parse_traffic_estimate_millions():
    assert parse_traffic_estimate("1.2M+") == 1_200_000


def test_parse_traffic_estimate_plain_number():
    assert parse_traffic_estimate("2000") == 2000


def test_parse_traffic_estimate_empty_or_garbage():
    assert parse_traffic_estimate("") == 0
    assert parse_traffic_estimate("not a number") == 0


def test_velocity_score_top_ranked_high_traffic_is_near_max():
    score = compute_velocity_score("500K+", rank_index=0, total_items=10)
    assert score == 25


def test_velocity_score_bottom_ranked_low_traffic_is_low():
    score = compute_velocity_score("1K+", rank_index=9, total_items=10)
    assert score < 5


def test_velocity_score_always_within_bounds():
    for traffic in ["0", "10K+", "500K+", "5M+", "garbage"]:
        for rank in range(0, 10):
            score = compute_velocity_score(traffic, rank, 10)
            assert 0 <= score <= 25


def test_saturation_no_coverage_is_low_saturation_high_score():
    score, level = compute_saturation(related_news_count=0, breakout_suggestion_count=1)
    assert level == "LOW"
    assert score >= 10


def test_saturation_heavy_coverage_is_saturated_low_score():
    score, level = compute_saturation(related_news_count=2, breakout_suggestion_count=8)
    assert level == "SATURATED"
    assert score <= 4


def test_saturation_score_always_within_bounds():
    for news in range(0, 5):
        for suggestions in range(0, 12):
            score, level = compute_saturation(news, suggestions)
            assert 0 <= score <= 15
            assert level in ("LOW", "MEDIUM", "SATURATED")


def test_viral_window_is_shorter_when_more_saturated():
    low_sat_window = compute_viral_window_hours(0, 1, "10K+")
    high_sat_window = compute_viral_window_hours(2, 8, "10K+")

    def upper_bound(window: str) -> int:
        return int(window.split("-")[1].split(" ")[0])

    assert upper_bound(high_sat_window) < upper_bound(low_sat_window)


def test_recompute_virality_verdict_matches_weighted_formula():
    result = recompute_virality_verdict(90, 90, 90, "a perfectly normal script with no banned phrases")
    assert result["overall_virality_score"] == 90
    assert result["status"] == "APPROVED"


def test_recompute_virality_verdict_overrides_llm_status_below_80():
    # Same sub-scores an LLM might report while mistakenly self-labeling APPROVED.
    result = recompute_virality_verdict(75, 75, 75, "clean script text")
    assert result["overall_virality_score"] == 75
    assert result["status"] == "NEEDS_REVISION"


def test_recompute_virality_verdict_enforces_anti_cliche_cap_even_if_llm_missed_it():
    # High sub-scores but the script text contains a banned phrase the LLM
    # itself listed as an automatic 65-point cap - Python must catch it even
    # if the LLM's own sub-scores/status didn't.
    result = recompute_virality_verdict(95, 95, 95, "In today's fast-paced world, agents win.")
    assert result["overall_virality_score"] == 65
    assert result["status"] == "NEEDS_REVISION"
    assert result["anti_cliche_triggered"] is True


def test_recommended_action_thresholds():
    assert derive_recommended_action(95) == "ACT_NOW"
    assert derive_recommended_action(80) == "ACT_NOW"
    assert derive_recommended_action(79) == "MONITOR"
    assert derive_recommended_action(55) == "MONITOR"
    assert derive_recommended_action(54) == "IGNORE"
    assert derive_recommended_action(0) == "IGNORE"


def test_recommended_action_blocks_act_now_when_niche_is_not_actually_relevant():
    """
    Reproduces the real bug: niche 'perritos' winning on 'tenis cincinnati'
    with a high total score but near-zero real connection to the niche.
    A high total score alone must not be enough to trigger autonomous
    content generation.
    """
    assert derive_recommended_action(90, audience_relevance_score=3) == "MONITOR"
    assert derive_recommended_action(90, audience_relevance_score=9) == "MONITOR"


def test_recommended_action_allows_act_now_at_relevance_threshold():
    assert derive_recommended_action(90, audience_relevance_score=10) == "ACT_NOW"
    assert derive_recommended_action(90, audience_relevance_score=20) == "ACT_NOW"


def test_rank_and_ground_candidates_attaches_grounded_fields():
    daily_trends = [
        {"topic": "A", "search_volume": "500K+", "related_news": [{"headline": "x"}]},
        {"topic": "B", "search_volume": "10K+", "related_news": []},
    ]
    breakout = ["a tutorial", "a vs b", "a 2026"]

    grounded = rank_and_ground_candidates(daily_trends, breakout)

    assert len(grounded) == 2
    for item in grounded:
        assert "grounded_velocity_score" in item
        assert "grounded_saturation_score" in item
        assert "grounded_saturation_level" in item
        assert "grounded_viral_window_hours" in item
        assert 0 <= item["grounded_velocity_score"] <= 25
        assert 0 <= item["grounded_saturation_score"] <= 15

    # higher-traffic, top-ranked item must score at least as high on velocity
    assert grounded[0]["grounded_velocity_score"] >= grounded[1]["grounded_velocity_score"]
    assert grounded[0]["grounded_lifecycle_stage"] in (
        "EMERGING", "ACCELERATING", "BREAKOUT", "SATURATED"
    )


def test_lifecycle_stage_heavy_coverage_is_saturated():
    stage = classify_lifecycle_stage("500K+", rank_index=0, total_items=10,
                                      related_news_count=2, breakout_suggestion_count=8)
    assert stage == "SATURATED"


def test_lifecycle_stage_top_traffic_low_coverage_is_breakout():
    stage = classify_lifecycle_stage("500K+", rank_index=0, total_items=10,
                                      related_news_count=0, breakout_suggestion_count=0)
    assert stage == "BREAKOUT"


def test_lifecycle_stage_low_traffic_bottom_rank_is_emerging():
    stage = classify_lifecycle_stage("1K+", rank_index=9, total_items=10,
                                      related_news_count=0, breakout_suggestion_count=0)
    assert stage == "EMERGING"


def test_lifecycle_stage_always_one_of_four_valid_values():
    for traffic in ["0", "10K+", "500K+", "5M+"]:
        for rank in range(0, 10):
            for news in range(0, 3):
                stage = classify_lifecycle_stage(traffic, rank, 10, news, 4)
                assert stage in ("EMERGING", "ACCELERATING", "BREAKOUT", "SATURATED")


def test_ground_niche_candidates_empty_signal_returns_empty_list():
    assert ground_niche_candidates({"current_interest": None, "rising_queries": [], "top_queries": []}) == []


def test_ground_niche_candidates_ignores_top_queries():
    """top_queries are established baseline context, not opportunity candidates -
    only rising_queries should ever become a candidate."""
    signal = {
        "current_interest": 60,
        "rising_queries": [],
        "top_queries": [{"query": "already popular", "value": 90}],
    }
    assert ground_niche_candidates(signal) == []


def test_ground_niche_candidates_breakout_scores_max_velocity():
    signal = {"current_interest": 40, "rising_queries": [{"query": "x", "growth_pct": None}], "top_queries": []}
    grounded = ground_niche_candidates(signal)
    assert len(grounded) == 1
    assert grounded[0]["topic"] == "x"
    assert grounded[0]["search_volume"] == "Breakout"
    assert grounded[0]["grounded_velocity_score"] == 25
    assert grounded[0]["niche_specific"] is True


def test_ground_niche_candidates_low_growth_scores_low_velocity():
    signal = {"current_interest": 40, "rising_queries": [{"query": "y", "growth_pct": 20}], "top_queries": []}
    grounded = ground_niche_candidates(signal)
    assert grounded[0]["grounded_velocity_score"] < 5
    assert grounded[0]["search_volume"] == "+20%"


def test_ground_niche_candidates_scores_always_within_bounds():
    for growth in [None, 0, 20, 150, 300, 5000]:
        signal = {"current_interest": 50, "rising_queries": [{"query": "z", "growth_pct": growth}], "top_queries": []}
        grounded = ground_niche_candidates(signal)
        assert 0 <= grounded[0]["grounded_velocity_score"] <= 25
        assert 0 <= grounded[0]["grounded_saturation_score"] <= 15
        assert grounded[0]["grounded_lifecycle_stage"] in ("EMERGING", "ACCELERATING", "BREAKOUT", "SATURATED")


def test_classify_niche_lifecycle_stage_breakout():
    assert classify_niche_lifecycle_stage(velocity_score=25, saturation_level="LOW") == "BREAKOUT"


def test_classify_niche_lifecycle_stage_saturated_overrides_high_velocity():
    assert classify_niche_lifecycle_stage(velocity_score=25, saturation_level="SATURATED") == "SATURATED"


def test_classify_niche_lifecycle_stage_low_velocity_is_emerging():
    assert classify_niche_lifecycle_stage(velocity_score=2, saturation_level="LOW") == "EMERGING"


def test_cross_market_gap_detects_topic_missing_in_target():
    baseline = [
        {"topic": "Autonomous Agent Workflows", "search_volume": "500K+"},
        {"topic": "Shared Topic", "search_volume": "100K+"},
    ]
    target = [
        {"topic": "Shared Topic", "search_volume": "80K+"},
    ]
    gaps = find_cross_market_gaps("US", baseline, "MX", target)
    assert len(gaps) == 1
    assert gaps[0]["topic"] == "Autonomous Agent Workflows"
    assert gaps[0]["status"] == "NOT_YET_VISIBLE"
    assert gaps[0]["baseline_rank"] == 1


def test_cross_market_gap_is_case_and_whitespace_insensitive():
    baseline = [{"topic": "  Gemini Agents  ", "search_volume": "200K+"}]
    target = [{"topic": "gemini agents", "search_volume": "50K+"}]
    gaps = find_cross_market_gaps("US", baseline, "MX", target)
    assert gaps == []


def test_cross_market_gap_empty_target_returns_all_baseline_topics():
    baseline = [
        {"topic": "A", "search_volume": "10K+"},
        {"topic": "B", "search_volume": "20K+"},
    ]
    gaps = find_cross_market_gaps("US", baseline, "MX", [])
    assert len(gaps) == 2


def test_cross_market_gap_deduplicates_repeated_baseline_topic():
    """
    Reproduces a real live pull: the RSS feed listed "christopher nolan"
    twice at different ranks (#1 and #4) in the same BR trending list.
    """
    baseline = [
        {"topic": "christopher nolan", "search_volume": "200+"},
        {"topic": "neymar", "search_volume": "20000+"},
        {"topic": "Christopher Nolan", "search_volume": "500+"},
    ]
    gaps = find_cross_market_gaps("BR", baseline, "US", [])
    topics = [g["topic"] for g in gaps]
    assert topics.count("christopher nolan") + topics.count("Christopher Nolan") == 1
    assert len(gaps) == 2


def test_multi_target_gap_absent_from_all_targets_is_a_gap():
    baseline = [{"topic": "Autonomous Agent Workflows", "search_volume": "200K+"}]
    targets = {
        "MX": [{"topic": "Something Else", "search_volume": "50K+"}],
        "ES": [{"topic": "Another Thing", "search_volume": "30K+"}],
        "GB": [{"topic": "Unrelated Topic", "search_volume": "10K+"}],
    }
    gaps = find_multi_target_gaps("US", baseline, targets)
    assert len(gaps) == 1
    assert gaps[0]["topic"] == "Autonomous Agent Workflows"
    assert gaps[0]["target_geos"] == ["ES", "GB", "MX"]
    assert gaps[0]["status"] == "NOT_YET_VISIBLE_ANYWHERE"


def test_multi_target_gap_present_in_just_one_target_is_not_a_gap():
    """
    The whole point of widening the ledger's claim to "at least one of
    several targets": a topic only needs to be genuinely absent everywhere to
    count as a real gap - already showing up in a single tracked market is
    enough to disqualify it, same as find_cross_market_gaps' single-pair rule.
    """
    baseline = [{"topic": "Gemini Agents", "search_volume": "200K+"}]
    targets = {
        "MX": [{"topic": "gemini agents", "search_volume": "50K+"}],
        "ES": [{"topic": "Something Else", "search_volume": "30K+"}],
    }
    gaps = find_multi_target_gaps("US", baseline, targets)
    assert gaps == []


def test_multi_target_gap_deduplicates_repeated_baseline_topic():
    baseline = [
        {"topic": "christopher nolan", "search_volume": "200+"},
        {"topic": "neymar", "search_volume": "20000+"},
        {"topic": "Christopher Nolan", "search_volume": "500+"},
    ]
    gaps = find_multi_target_gaps("BR", baseline, {"US": [], "MX": []})
    topics = [g["topic"] for g in gaps]
    assert topics.count("christopher nolan") + topics.count("Christopher Nolan") == 1
    assert len(gaps) == 2


def test_multi_target_gap_empty_targets_returns_all_baseline_topics():
    baseline = [
        {"topic": "A", "search_volume": "10K+"},
        {"topic": "B", "search_volume": "20K+"},
    ]
    gaps = find_multi_target_gaps("US", baseline, {"MX": [], "ES": [], "GB": []})
    assert len(gaps) == 2
