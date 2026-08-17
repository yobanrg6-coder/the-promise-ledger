"""
Google Trends & Real-Time Search Intelligence Service
Provides live trending queries, breakout topics, search volume estimates, and viral benchmarks.
"""

import json
import urllib.parse
from typing import Any

# defusedxml instead of stdlib xml.etree.ElementTree: this parses a real,
# third-party network response (Google Trends RSS) on every call, and stdlib
# ElementTree is known-vulnerable to XML bomb / entity-expansion attacks on
# untrusted input. Drop-in compatible API.
import defusedxml.ElementTree as ET
import httpx
from pytrends.request import TrendReq


class GoogleTrendsService:
    """Service to fetch real-time Google Trends and rising search queries."""

    DAILY_TRENDS_RSS = "https://trends.google.com/trending/rss"
    AUTOCOMPLETE_API = "https://suggestqueries.google.com/complete/search"

    @classmethod
    def fetch_daily_trending_topics(cls, geo: str = "ES") -> list[dict[str, Any]]:
        """
        Fetch real-time daily trending topics from Google Trends RSS.
        """
        url = f"{cls.DAILY_TRENDS_RSS}?geo={geo.upper()}"
        trends = []
        try:
            response = httpx.get(url, timeout=8.0, headers={"User-Agent": "Mozilla/5.0"})
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                for item in root.findall(".//item")[:10]:
                    title = item.find("title").text if item.find("title") is not None else ""
                    approx_traffic = item.find("{https://trends.google.com/trending/rss}approx_traffic")
                    traffic = approx_traffic.text if approx_traffic is not None else "50K+"
                    pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                    
                    news_items = []
                    for news in item.findall("{https://trends.google.com/trending/rss}news_item")[:2]:
                        news_title = news.find("{https://trends.google.com/trending/rss}news_item_title")
                        news_url = news.find("{https://trends.google.com/trending/rss}news_item_url")
                        if news_title is not None:
                            news_items.append({
                                "headline": news_title.text,
                                "url": news_url.text if news_url is not None else ""
                            })

                    trends.append({
                        "topic": title,
                        "search_volume": traffic,
                        "published_at": pub_date,
                        "related_news": news_items
                    })
        except Exception as e:  # noqa: BLE001 - third-party feed: network, HTTP, and XML-parse failures all fold into the same "no live data" fallback below
            print(f"[TrendsService] RSS fetch warning: {e}")

        # No silent fabrication: if the real feed is empty or unreachable for
        # this geo, callers must see an empty list and fail fast, never a
        # made-up topic dressed up with a fake search_volume. See
        # agents/orchestrator.py's pre-flight check.
        if not trends:
            print(f"[TrendsService] No live trends returned for geo={geo.upper()} - returning empty, no fallback data.")
        return trends

    @classmethod
    def fetch_breakout_queries(cls, keyword: str, language: str = "es") -> list[str]:
        """
        Fetch real-time autocomplete suggestions and related breakout search intents from Google.
        """
        params = {
            "client": "chrome",
            "q": keyword,
            "hl": language
        }
        suggestions = []
        try:
            url = f"{cls.AUTOCOMPLETE_API}?{urllib.parse.urlencode(params)}"
            response = httpx.get(url, timeout=5.0, headers={"User-Agent": "Mozilla/5.0"})
            if response.status_code == 200:
                data = json.loads(response.text)
                if len(data) > 1 and isinstance(data[1], list):
                    suggestions = data[1][:8]
        except Exception as e:  # noqa: BLE001 - third-party endpoint: network/HTTP/JSON failures all fold into the same empty-suggestions fallback
            print(f"[TrendsService] Suggestions fetch warning: {e}")

        # Same rule as fetch_daily_trending_topics: no invented suggestions
        # standing in for real Google Autocomplete data.
        if not suggestions:
            print(f"[TrendsService] No live suggestions returned for keyword='{keyword}' - returning empty, no fallback data.")
        return suggestions

    @classmethod
    def fetch_niche_signal(cls, keyword: str, geo: str = "MX", language: str = "es") -> dict[str, Any]:
        """
        Real, quantified Google Trends data for the user's exact niche keyword.
        fetch_daily_trending_topics only covers a country's generic top-10 news/
        sports/pop-culture list, which rarely intersects with a narrow niche
        (pets, hobbies, a specific B2B vertical) - a niche can have real,
        accelerating search interest that never once cracks the national
        top-10. This queries Trends' interest-over-time and related-queries
        widgets directly for the niche itself, via the unofficial but
        real-data pytrends client (no API key, same underlying Google data the
        public trends.google.com website shows).
        Returns:
            current_interest: latest 0-100 relative interest value for the
                keyword itself over the last 7 days (real, Google-computed).
            rising_queries: real queries actually accelerating around this
                keyword right now - [{"query": str, "growth_pct": int | None}],
                None means Google's own "Breakout" label (essentially infinite
                growth from a near-zero base), never estimated by us.
            top_queries: established, already-popular queries for this niche -
                [{"query": str, "value": int}], real 0-100 relative values.
        Empty dict on any failure (network, no data for this geo/keyword,
        Google rate-limiting this unofficial endpoint) - never fabricated.
        """
        empty = {"current_interest": None, "rising_queries": [], "top_queries": []}
        if not keyword.strip():
            return empty
        try:
            pytrends = TrendReq(hl=language, tz=360, timeout=(5, 10))
            pytrends.build_payload([keyword], timeframe="now 7-d", geo=geo.upper())

            interest_df = pytrends.interest_over_time()
            current_interest = None
            if not interest_df.empty:
                current_interest = int(interest_df[keyword].iloc[-1])

            related = pytrends.related_queries().get(keyword, {})
            rising_queries = []
            rising_df = related.get("rising")
            if rising_df is not None and not rising_df.empty:
                for _, row in rising_df.head(8).iterrows():
                    try:
                        growth_pct = int(row["value"])
                    except (ValueError, TypeError):
                        # Google's own literal "Breakout" label for extreme
                        # growth from a near-zero base - not a number we invent.
                        growth_pct = None
                    rising_queries.append({"query": str(row["query"]), "growth_pct": growth_pct})

            top_queries = []
            top_df = related.get("top")
            if top_df is not None and not top_df.empty:
                for _, row in top_df.head(8).iterrows():
                    top_queries.append({"query": str(row["query"]), "value": int(row["value"])})

            return {
                "current_interest": current_interest,
                "rising_queries": rising_queries,
                "top_queries": top_queries,
            }
        except Exception as e:  # noqa: BLE001 - third-party endpoint (unofficial Trends API): network/parsing/rate-limit failures all fold into the same empty-signal fallback
            print(f"[TrendsService] Niche signal fetch warning for keyword='{keyword}': {e}")
            return empty

    @classmethod
    def get_platform_virality_benchmarks(cls, platform: str) -> dict[str, Any]:
        """
        Get algorithmic retention metrics and optimal structure based on target platform.
        """
        benchmarks = {
            "tiktok": {
                "ideal_duration_seconds": "30-45s",
                "hook_window_seconds": 2.5,
                "ideal_slide_count": None,
                "primary_retention_trigger": "Pattern interrupt in first 3 seconds + open loop at second 15",
                "recommended_hashtags_count": 4
            },
            "instagram_reels": {
                "ideal_duration_seconds": "20-35s",
                "hook_window_seconds": 3.0,
                "ideal_slide_count": None,
                "primary_retention_trigger": "High visual contrast text hook + audio sync + saveable value in caption",
                "recommended_hashtags_count": 6
            },
            "instagram_carousel": {
                "ideal_duration_seconds": None,
                "hook_window_seconds": None,
                "ideal_slide_count": 7,
                "primary_retention_trigger": "Slide 1 bold curiosity statement + Slide 2-6 actionable micro-steps + Slide 7 CTA",
                "recommended_hashtags_count": 8
            },
            "linkedin": {
                "ideal_duration_seconds": None,
                "hook_window_seconds": None,
                "ideal_slide_count": 6,
                "primary_retention_trigger": "First 2 lines above 'see more' with clear business impact + clean bulleted takeaway",
                "recommended_hashtags_count": 4
            },
            "youtube_shorts": {
                "ideal_duration_seconds": "45-55s",
                "hook_window_seconds": 2.0,
                "ideal_slide_count": None,
                "primary_retention_trigger": "Immediate problem statement in frame 1 + visual proof + seamless loop ending",
                "recommended_hashtags_count": 3
            }
        }
        return benchmarks.get(platform.lower().replace(" ", "_"), benchmarks["instagram_reels"])
