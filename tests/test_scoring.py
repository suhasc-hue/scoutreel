from datetime import datetime, timedelta, timezone

from app.scoring import (
    DEFAULT_CONSTANTS,
    compute_score,
    compute_score_from_points,
    engagement_rate,
    recency_boost,
    size_normalizer,
    total_score,
    views_per_hour,
    views_per_hour_trend,
)

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def test_views_per_hour_basic():
    assert views_per_hour(1000, T0, 1600, T0 + timedelta(hours=6)) == 100.0


def test_views_per_hour_never_negative():
    # view count corrections happen; velocity floors at 0
    assert views_per_hour(2000, T0, 1500, T0 + timedelta(hours=1)) == 0.0


def test_views_per_hour_zero_time_delta():
    assert views_per_hour(100, T0, 200, T0) == 0.0


def test_engagement_rate_formula():
    # (likes + 3*comments) / views
    assert engagement_rate(1000, 50, 10) == (50 + 30) / 1000


def test_engagement_rate_zero_views():
    assert engagement_rate(0, 5, 5) == 20.0  # max(views, 1) guards div-by-zero


def test_size_normalizer_small_channel_ranks_higher():
    # same raw signal, smaller channel -> bigger score
    assert size_normalizer(100) < size_normalizer(1_000_000)
    small = total_score(100, 0.05, 0.5, size_normalizer(100))
    big = total_score(100, 0.05, 0.5, size_normalizer(1_000_000))
    assert small > big


def test_recency_boost_bounds():
    assert recency_boost(T0, T0) == 1.0
    assert recency_boost(T0, T0 + timedelta(days=7)) == 0.5
    assert recency_boost(T0, T0 + timedelta(days=30)) == 0.0  # never negative


def test_total_score_monotonic_in_velocity():
    norm = size_normalizer(10_000)
    assert total_score(500, 0.02, 0.5, norm) > total_score(50, 0.02, 0.5, norm)


def test_compute_score_end_to_end():
    result = compute_score(
        prev_views=1000,
        prev_at=T0,
        curr_views=2200,
        curr_at=T0 + timedelta(hours=6),
        likes=150,
        comments=40,
        subscriber_count=5000,
        published_at=T0 - timedelta(days=2),
        constants=DEFAULT_CONSTANTS,
    )
    assert result.velocity_score == 200.0
    assert abs(result.engagement_score - (150 + 120) / 2200) < 1e-9
    assert result.total_score > 0


def test_trend_exact_on_linear_data():
    pts = [(1000 + 100 * h, T0 + timedelta(hours=h)) for h in range(4)]
    assert abs(views_per_hour_trend(pts) - 100.0) < 1e-9


def test_trend_order_independent():
    pts = [(1300, T0 + timedelta(hours=3)), (1000, T0), (1200, T0 + timedelta(hours=2))]
    assert abs(views_per_hour_trend(pts) - 100.0) < 1e-9


def test_trend_resists_single_noisy_snapshot():
    """One spiked snapshot moves a fitted slope far less than a last-two delta."""
    clean = [(1000, T0), (1600, T0 + timedelta(hours=6)),
             (2200, T0 + timedelta(hours=12)), (2800, T0 + timedelta(hours=18))]
    spiked = clean[:3] + [(9000, T0 + timedelta(hours=18))]
    fitted = views_per_hour_trend(spiked)
    last_two = views_per_hour(2200, T0 + timedelta(hours=12), 9000, T0 + timedelta(hours=18))
    assert abs(fitted - 100) < abs(last_two - 100)


def test_trend_edge_cases():
    assert views_per_hour_trend([]) == 0.0
    assert views_per_hour_trend([(100, T0)]) == 0.0
    # all same timestamp -> no slope
    assert views_per_hour_trend([(100, T0), (200, T0)]) == 0.0
    # declining views floor at 0
    assert views_per_hour_trend([(2000, T0), (1000, T0 + timedelta(hours=5))]) == 0.0


def test_compute_score_from_points_matches_formula():
    pts = [(1000 + 200 * h, T0 + timedelta(hours=h)) for h in range(3)]
    r = compute_score_from_points(
        points=pts, likes=100, comments=20, subscriber_count=5000,
        published_at=T0 - timedelta(days=2), now=T0 + timedelta(hours=2),
    )
    assert abs(r.velocity_score - 200.0) < 1e-9
    assert r.engagement_score == (100 + 60) / 1400
    assert r.total_score > 0


def test_compute_score_stale_video_gets_no_recency():
    fresh = compute_score(1000, T0, 2000, T0 + timedelta(hours=6), 10, 1, 1000,
                          published_at=T0 - timedelta(days=1))
    stale = compute_score(1000, T0, 2000, T0 + timedelta(hours=6), 10, 1, 1000,
                          published_at=T0 - timedelta(days=60))
    assert fresh.total_score > stale.total_score
