"""
FastMCP Server - TopicAhead
Exposes live Google Trends, Breakout Queries, and Platform Virality Benchmarks to ADK Agents.
"""

import os
import sys

from dotenv import load_dotenv
from fastmcp import FastMCP

# Windows consoles/subprocess pipes default to the system codepage (cp1252),
# which cannot encode the emoji in this file's print() calls and crashes the
# server before it ever binds a port. Force UTF-8 on stdout/stderr so this
# runs the same whether launched interactively, via run.py's subprocess, or
# inside a Docker/Cloud Run container.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure local imports work properly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from trends_service import GoogleTrendsService

from agents.scoring import (
    find_cross_market_gaps,
    ground_niche_candidates,
    rank_and_ground_candidates,
)
from ledger import store as ledger_store

load_dotenv()

# Initialize FastMCP Server
mcp = FastMCP("TopicAhead Intelligence Server")

@mcp.tool()
def get_daily_trends(geo: str = "ES") -> list:
    """
    Get top real-time daily trending topics from Google Trends with estimated search volume.
    Args:
        geo: Two-letter country code (e.g. 'ES', 'MX', 'US', 'GB')
    """
    return GoogleTrendsService.fetch_daily_trending_topics(geo=geo)

@mcp.tool()
def get_rising_search_intent(keyword: str, language: str = "es") -> list:
    """
    Get real-time rising search queries and high-intent autocomplete suggestions from Google.
    Args:
        keyword: The main topic or seed keyword to investigate
        language: Two-letter language code (e.g. 'es', 'en')
    """
    return GoogleTrendsService.fetch_breakout_queries(keyword=keyword, language=language)

@mcp.tool()
def get_grounded_opportunity_candidates(geo: str = "ES", keyword: str = "") -> list:
    """
    Fetch daily trending topics AND attach a deterministic, auditable velocity/saturation
    score to each one computed from real signals (approx traffic, related news volume,
    breakout suggestion density) - not an LLM guess. Callers MUST copy
    grounded_velocity_score / grounded_saturation_score / grounded_viral_window_hours
    verbatim into their final scoring output; only the qualitative sub-scores
    (recency, audience relevance, hook potential, brand safety) are left to judgment.
    Args:
        geo: Two-letter country code (e.g. 'ES', 'MX', 'US', 'GB')
        keyword: Optional seed keyword used to also pull breakout search intent
    """
    daily_trends = GoogleTrendsService.fetch_daily_trending_topics(geo=geo)
    breakout_queries = GoogleTrendsService.fetch_breakout_queries(keyword=keyword or geo) if keyword else []
    return rank_and_ground_candidates(daily_trends, breakout_queries)

@mcp.tool()
def get_niche_trend_signals(keyword: str, geo: str = "ES") -> list:
    """
    Real, quantified rising-search-query candidates for a SHORT, ACTUAL search term
    (e.g. 'perros', 'B2B SaaS pricing', 'home espresso') - not a full niche/persona
    description. get_grounded_opportunity_candidates only covers a country's generic
    top-10 news/sports/pop-culture list, which rarely intersects with a narrow niche
    even when that niche has real search interest of its own. Use this to check the
    niche directly instead of only relying on the generic list.
    IMPORTANT: pass a genuine short search term, not the user's full niche sentence -
    "creador de contenido organico sobre perros" returns nothing because nobody
    searches that exact phrase; "perros" does. If your first attempt returns an empty
    list, try a shorter or more literal keyword before concluding there's no niche
    signal - do not silently give up after one attempt.
    Args:
        keyword: A short, real search term someone would actually type
        geo: Two-letter country code (e.g. 'ES', 'MX', 'US', 'GB')
    """
    niche_signal = GoogleTrendsService.fetch_niche_signal(keyword=keyword, geo=geo)
    return ground_niche_candidates(niche_signal)

@mcp.tool()
def get_cross_market_gaps(baseline_geo: str = "US", target_geo: str = "MX") -> list:
    """
    Compare two markets' current trending lists and report topics trending in
    baseline_geo that are NOT YET visible in target_geo's trending list. This is
    a real, observed absence right now - it is NOT a predicted arrival time.
    Never claim a specific number of hours until the topic reaches target_geo;
    only report that it has not appeared there yet.
    Args:
        baseline_geo: Market where the topic is already trending (e.g. 'US')
        target_geo: Market to check for the same topic's absence (e.g. 'MX')
    """
    baseline_trends = GoogleTrendsService.fetch_daily_trending_topics(geo=baseline_geo)
    target_trends = GoogleTrendsService.fetch_daily_trending_topics(geo=target_geo)
    return find_cross_market_gaps(baseline_geo, baseline_trends, target_geo, target_trends)

@mcp.tool()
def get_forecast_accuracy() -> dict:
    """
    Real, accumulated accuracy of this system's own past predictions - never an
    LLM-invented number. Each prediction was logged with a timestamp and later
    checked against a real re-pull of Google Trends data (see ledger/README.md).
    Returns total_predictions, pending, evaluated, correct, incorrect, accuracy_pct
    (null until at least one prediction has been evaluated).
    """
    return ledger_store.get_accuracy_stats()

@mcp.tool()
def get_virality_benchmarks(platform: str = "instagram_reels") -> dict:
    """
    Get algorithmic retention rules, hook timing, and structural benchmarks for a specific platform.
    Args:
        platform: Target social platform ('tiktok', 'instagram_reels', 'instagram_carousel', 'linkedin', 'youtube_shorts')
    """
    return GoogleTrendsService.get_platform_virality_benchmarks(platform=platform)

if __name__ == "__main__":
    host = os.getenv("MCP_SERVER_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_SERVER_PORT", "8080"))
    print(f"Starting TopicAhead FastMCP Server on http://{host}:{port}/mcp ...")
    mcp.run(transport="streamable-http", host=host, port=port)
