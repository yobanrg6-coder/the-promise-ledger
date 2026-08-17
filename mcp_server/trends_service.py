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
