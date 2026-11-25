"""Selected store, honest daily statistics and read-only reviews; no platform calls."""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from providers.google_business import GoogleBusinessProvider
from providers.pinterest import PinterestProvider


def provider(cls, body):
    p = cls({"account_id": "accounts/1", "location_id": "accounts/1/locations/9"})
    p._request = Mock(return_value=Mock(json=Mock(return_value=body)))
    return p


def test_google_profile_uses_selected_location_and_city():
    p = provider(
        GoogleBusinessProvider,
        {
            "name": "locations/9",
            "title": "Willy",
            "storefrontAddress": {"locality": "Pisa", "addressLines": ["Via Roma 1"]},
        },
    )
    profile = p.get_profile("fake")
    assert profile.platform_id == "accounts/1/locations/9"
    assert "Pisa" in profile.name and "Via Roma 1" in profile.name
    assert p._request.call_count == 1
    assert p._request.call_args.args[1].endswith("/locations/9")


def window():
    return datetime(2026, 11, 14, tzinfo=UTC), datetime(2026, 11, 14, 23, 59, tzinfo=UTC)


def test_google_daily_metrics_selected_store_and_real_zero():
    series = [
        {
            "dailyMetric": key,
            "timeSeries": {"datedValues": [{"date": {"year": 2026, "month": 11, "day": 14}, "value": value}]},
        }
        for key, value in [
            ("BUSINESS_IMPRESSIONS_DESKTOP_MAPS", "7"),
            ("BUSINESS_IMPRESSIONS_DESKTOP_SEARCH", "0"),
            ("BUSINESS_IMPRESSIONS_MOBILE_MAPS", "0"),
            ("BUSINESS_IMPRESSIONS_MOBILE_SEARCH", "3"),
            ("WEBSITE_CLICKS", "0"),
        ]
    ]
    p = provider(GoogleBusinessProvider, {"multiDailyMetricTimeSeries": [{"dailyMetricTimeSeries": series}]})
    m = p.get_account_metrics("fake", window())
    assert m.impressions == 10 and m.extra["metrics"]["clicks"] == 0
    assert p._request.call_args.args[0] == "GET"
    assert "/locations/9:" in p._request.call_args.args[1]
    assert p._request.call_args.kwargs["params"]["dailyRange.start_date.day"] == 14


@pytest.mark.parametrize("cls", [GoogleBusinessProvider, PinterestProvider])
def test_empty_response_is_unknown_not_zero(cls):
    p = provider(cls, {})
    with pytest.raises(Exception, match="(?i)(data|metric)"):
        p.get_account_metrics("fake", window())


def test_pinterest_account_reads_summary_with_real_zero():
    p = provider(
        PinterestProvider,
        {"all": {"summary_metrics": {"IMPRESSION": 12, "PIN_CLICK": 0, "SAVE": 2, "OUTBOUND_CLICK": 1}}},
    )
    m = p.get_account_metrics("fake", window())
    assert m.impressions == 12 and m.extra["metrics"] == {"impressions": 12, "clicks": 0, "saves": 2, "outbound": 1}
    assert p._request.call_args.args[1].endswith("/user_account/analytics")


def test_reviews_are_bounded_and_use_selected_location():
    p = provider(
        GoogleBusinessProvider,
        {"averageRating": 4.6, "totalReviewCount": 60, "reviews": [{"reviewId": "r1"}], "nextPageToken": "page2"},
    )
    r = p.get_reviews("fake", limite=1)
    assert r["average_rating"] == 4.6 and r["total_reviews"] == 60 and r["troncato"]
    assert len(r["reviews"]) == 1
    assert p._request.call_args.args[0] == "GET"
    assert p._request.call_args.args[1].endswith("/accounts/1/locations/9/reviews")
    assert p._request.call_args.kwargs["params"]["pageSize"] == 1
