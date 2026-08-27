"""
Tests for GoogleTrendsService.fetch_relative_search_ratio's retry/failure
handling. No real network calls - TrendReq is replaced with a fake that
raises or returns controlled data.
"""

import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp_server"))

import pandas as pd
import pytest
from pytrends.exceptions import TooManyRequestsError

import trends_service
from trends_service import GoogleTrendsService, TrendsCheckUnavailable


class _FakeResponse:
    status_code = 429


def _make_fake_trendreq(build_payload_effects):
    """
    build_payload_effects: list of callables, one per TrendReq() construction,
    each either raising or returning a DataFrame from interest_over_time().
    """
    calls = {"count": 0}

    class _FakeTrendReq:
        def __init__(self, *args, **kwargs):
            pass

        def build_payload(self, keywords, timeframe=None, geo=None):
            self._keywords = keywords

        def interest_over_time(self):
            effect = build_payload_effects[calls["count"]]
            calls["count"] += 1
            if isinstance(effect, Exception):
                raise effect
            return effect

    return _FakeTrendReq


def test_returns_ratio_on_clean_success(monkeypatch):
    df = pd.DataFrame({"candidate": [10, 20], "anchor": [40, 40]})
    monkeypatch.setattr(trends_service, "TrendReq", _make_fake_trendreq([df]))

    ratio = GoogleTrendsService.fetch_relative_search_ratio("candidate", "anchor", "US")
    assert ratio == 0.5


def test_returns_none_when_no_usable_data(monkeypatch):
    monkeypatch.setattr(trends_service, "TrendReq", _make_fake_trendreq([pd.DataFrame()]))

    ratio = GoogleTrendsService.fetch_relative_search_ratio("candidate", "anchor", "US")
    assert ratio is None


def test_retries_once_on_rate_limit_then_succeeds(monkeypatch):
    df = pd.DataFrame({"candidate": [5], "anchor": [20]})
    monkeypatch.setattr(
        trends_service, "TrendReq",
        _make_fake_trendreq([TooManyRequestsError.from_response(_FakeResponse()), df])
    )
    monkeypatch.setattr(trends_service.time, "sleep", lambda seconds: None)

    ratio = GoogleTrendsService.fetch_relative_search_ratio("candidate", "anchor", "US")
    assert ratio == 0.25


def test_raises_check_unavailable_after_retries_exhausted(monkeypatch):
    monkeypatch.setattr(
        trends_service, "TrendReq",
        _make_fake_trendreq([
            TooManyRequestsError.from_response(_FakeResponse()),
            TooManyRequestsError.from_response(_FakeResponse()),
        ])
    )
    monkeypatch.setattr(trends_service.time, "sleep", lambda seconds: None)

    with pytest.raises(TrendsCheckUnavailable):
        GoogleTrendsService.fetch_relative_search_ratio("candidate", "anchor", "US")


def test_raises_check_unavailable_on_other_errors(monkeypatch):
    monkeypatch.setattr(trends_service, "TrendReq", _make_fake_trendreq([RuntimeError("boom")]))

    with pytest.raises(TrendsCheckUnavailable):
        GoogleTrendsService.fetch_relative_search_ratio("candidate", "anchor", "US")
