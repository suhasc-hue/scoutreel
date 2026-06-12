"""Virality scoring — pure functions, unit-tested.

total_score = (log10(views_per_hour + 1) * 2
               + engagement_rate * 100
               + recency_boost * 2) / size_normalizer
"""
import math
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ScoringConstants:
    velocity_weight: float = 2.0
    engagement_weight: float = 100.0
    recency_weight: float = 2.0
    comment_weight: float = 3.0
    recency_window_days: float = 14.0


DEFAULT_CONSTANTS = ScoringConstants()


@dataclass(frozen=True)
class ScoreResult:
    velocity_score: float  # views per hour
    engagement_score: float  # engagement rate (0..~1)
    total_score: float


def views_per_hour(
    prev_views: int, prev_at: datetime, curr_views: int, curr_at: datetime
) -> float:
    """Δviews between last two snapshots / Δhours. Never negative, never div-by-zero."""
    delta_hours = (curr_at - prev_at).total_seconds() / 3600.0
    if delta_hours <= 0:
        return 0.0
    return max(0.0, (curr_views - prev_views) / delta_hours)


def views_per_hour_trend(points: list[tuple[int, datetime]]) -> float:
    """Least-squares slope (views/hour) over several snapshots — robust to a
    single noisy snapshot, unlike a simple last-two delta. Points are
    (views, captured_at) in any order; result floors at 0."""
    if len(points) < 2:
        return 0.0
    pts = sorted(points, key=lambda p: p[1])
    t0 = pts[0][1]
    xs = [(p[1] - t0).total_seconds() / 3600.0 for p in pts]
    ys = [float(p[0]) for p in pts]
    n = len(pts)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom <= 0:
        return 0.0
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    return max(0.0, slope)


def engagement_rate(
    views: int, likes: int, comments: int, comment_weight: float = 3.0
) -> float:
    """(likes + 3*comments) / max(views, 1)."""
    return (likes + comment_weight * comments) / max(views, 1)


def size_normalizer(subscriber_count: int) -> float:
    """log10(subs + 100) — small channels going viral rank higher."""
    return math.log10(max(subscriber_count, 0) + 100)


def recency_boost(
    published_at: datetime, now: datetime, window_days: float = 14.0
) -> float:
    """max(0, 1 - days_since_publish/window). 1.0 brand new, 0 past the window."""
    days = (now - published_at).total_seconds() / 86400.0
    return max(0.0, 1.0 - days / window_days)


def total_score(
    vph: float,
    er: float,
    boost: float,
    normalizer: float,
    constants: ScoringConstants = DEFAULT_CONSTANTS,
) -> float:
    raw = (
        math.log10(vph + 1) * constants.velocity_weight
        + er * constants.engagement_weight
        + boost * constants.recency_weight
    )
    return raw / max(normalizer, 1e-9)


def compute_score(
    prev_views: int,
    prev_at: datetime,
    curr_views: int,
    curr_at: datetime,
    likes: int,
    comments: int,
    subscriber_count: int,
    published_at: datetime,
    now: datetime | None = None,
    constants: ScoringConstants = DEFAULT_CONSTANTS,
) -> ScoreResult:
    """Convenience wrapper used by the snapshot job. All inputs are plain values
    so this stays pure and trivially testable."""
    now = now or curr_at
    vph = views_per_hour(prev_views, prev_at, curr_views, curr_at)
    er = engagement_rate(curr_views, likes, comments, constants.comment_weight)
    boost = recency_boost(published_at, now, constants.recency_window_days)
    norm = size_normalizer(subscriber_count)
    return ScoreResult(
        velocity_score=vph,
        engagement_score=er,
        total_score=total_score(vph, er, boost, norm, constants),
    )


def compute_score_from_points(
    points: list[tuple[int, datetime]],
    likes: int,
    comments: int,
    subscriber_count: int,
    published_at: datetime,
    now: datetime,
    constants: ScoringConstants = DEFAULT_CONSTANTS,
) -> ScoreResult:
    """Like compute_score but fits velocity over all given snapshots."""
    vph = views_per_hour_trend(points)
    latest_views = max((p[0] for p in points), default=0)
    er = engagement_rate(latest_views, likes, comments, constants.comment_weight)
    boost = recency_boost(published_at, now, constants.recency_window_days)
    norm = size_normalizer(subscriber_count)
    return ScoreResult(
        velocity_score=vph,
        engagement_score=er,
        total_score=total_score(vph, er, boost, norm, constants),
    )
