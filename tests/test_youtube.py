"""YouTube adapter parsing + quota accounting (no network)."""
import pytest

from app.sources.youtube import (
    QuotaExceeded,
    YouTubeAdapter,
    charge_quota,
    get_quota_row,
)

REAL_VIDEO_PAYLOAD = {
    "id": "abc123XYZ",
    "snippet": {
        "publishedAt": "2026-06-01T10:30:00Z",
        "channelId": "UCxyz",
        "title": "MIDNIGHT — Short Film",
        "description": "Directed by A. Kumar.\nContact: films@studio.example",
        "thumbnails": {
            "default": {"url": "https://i.ytimg.com/vi/abc123XYZ/default.jpg"},
            "medium": {"url": "https://i.ytimg.com/vi/abc123XYZ/mqdefault.jpg"},
        },
    },
    "contentDetails": {"duration": "PT14M33S"},
}


def test_to_video_parses_realistic_payload():
    v = YouTubeAdapter._to_video(REAL_VIDEO_PAYLOAD)
    assert v.source_id == "abc123XYZ"
    assert v.duration_seconds == 14 * 60 + 33
    assert v.published_at is not None and v.published_at.year == 2026
    assert v.thumbnail_url.endswith("mqdefault.jpg")
    assert v.channel_source_id == "UCxyz"
    assert v.url == "https://www.youtube.com/watch?v=abc123XYZ"


def test_to_video_handles_missing_fields():
    v = YouTubeAdapter._to_video({"id": "x", "snippet": {}})
    assert v.duration_seconds == 0
    assert v.published_at is None
    assert v.thumbnail_url == ""


def test_to_profile_parses_channel():
    p = YouTubeAdapter._to_profile(
        {
            "id": "UCxyz",
            "snippet": {"title": "Studio", "description": "biz: a@b.example", "country": "IN"},
            "statistics": {"subscriberCount": "4200"},
        }
    )
    assert p.subscriber_count == 4200
    assert p.country == "IN"
    assert p.url.endswith("UCxyz")


def test_quota_budget_enforced(db, monkeypatch):
    from app import config

    monkeypatch.setattr(config.get_settings(), "daily_yt_quota_budget", 250)
    try:
        charge_quota(100, is_search=True, db=db)
        charge_quota(100, is_search=True, db=db)
        with pytest.raises(QuotaExceeded, match="budget"):
            charge_quota(100, is_search=True, db=db)
        row = get_quota_row(db)
        assert row.units_used == 200  # failed charge not recorded
        assert row.searches_run == 2
    finally:
        monkeypatch.setattr(config.get_settings(), "daily_yt_quota_budget", 9000)


def test_search_cap_enforced(db, monkeypatch):
    from app import config

    monkeypatch.setattr(config.get_settings(), "max_searches_per_day", 2)
    try:
        charge_quota(100, is_search=True, db=db)
        charge_quota(100, is_search=True, db=db)
        with pytest.raises(QuotaExceeded, match="search cap"):
            charge_quota(100, is_search=True, db=db)
        # non-search calls are still allowed
        charge_quota(1, db=db)
    finally:
        monkeypatch.setattr(config.get_settings(), "max_searches_per_day", 60)
