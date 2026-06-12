"""YouTube Data API v3 adapter (Phase 1).

Quota notes: search.list = 100 units, videos.list / channels.list = 1 unit.
Daily budget is tracked in the QuotaUsage table (Pacific-time day, matching
Google's reset). Discovery stops gracefully when the budget is hit.
"""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy.orm import Session
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.models import QuotaUsage
from app.sources.base import CreatorProfile, DiscoveredVideo, SourceAdapter, VideoStats

SEARCH_COST = 100
LIST_COST = 1
PACIFIC = ZoneInfo("America/Los_Angeles")

_ISO_DURATION_RE = re.compile(
    r"PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?"
)


class QuotaExceeded(Exception):
    """Raised when the daily YouTube quota budget is exhausted."""


def parse_iso8601_duration(value: str) -> int:
    m = _ISO_DURATION_RE.fullmatch(value or "")
    if not m:
        return 0
    h = int(m.group("h") or 0)
    mi = int(m.group("m") or 0)
    s = int(m.group("s") or 0)
    return h * 3600 + mi * 60 + s


def _pt_day(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(PACIFIC).strftime("%Y-%m-%d")


def get_quota_row(db: Session) -> QuotaUsage:
    day = _pt_day()
    row = db.query(QuotaUsage).filter_by(day=day).one_or_none()
    if row is None:
        row = QuotaUsage(day=day, units_used=0, searches_run=0)
        db.add(row)
        db.flush()
    return row


def _charge(db: Session, units: int, is_search: bool) -> None:
    settings = get_settings()
    row = get_quota_row(db)
    if row.units_used + units > settings.daily_yt_quota_budget:
        raise QuotaExceeded(
            f"quota budget {settings.daily_yt_quota_budget} would be exceeded "
            f"({row.units_used} used, {units} requested)"
        )
    if is_search and row.searches_run >= settings.max_searches_per_day:
        raise QuotaExceeded(f"daily search cap {settings.max_searches_per_day} reached")
    row.units_used += units
    if is_search:
        row.searches_run += 1


def charge_quota(units: int, is_search: bool = False, db: Session | None = None) -> None:
    """Track quota in its own short transaction so accounting never commits a
    caller's half-finished work. Pass db only in tests."""
    if db is not None:
        _charge(db, units, is_search)
        db.commit()
        return
    from app.db import session_scope

    with session_scope() as session:
        _charge(session, units, is_search)


def _is_quota_error(exc: BaseException) -> bool:
    try:
        from googleapiclient.errors import HttpError

        if isinstance(exc, HttpError) and exc.resp.status == 403:
            return b"quota" in (exc.content or b"").lower()
    except ImportError:
        pass
    return False


def _is_retryable(exc: BaseException) -> bool:
    """Retry transient server errors / rate limits, but never quota exhaustion."""
    try:
        from googleapiclient.errors import HttpError

        if isinstance(exc, HttpError):
            return exc.resp.status in (429, 500, 502, 503, 504)
    except ImportError:
        pass
    return isinstance(exc, (ConnectionError, TimeoutError))


api_retry = retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(4),
    reraise=True,
)


class YouTubeAdapter(SourceAdapter):
    source = "youtube"

    def __init__(self, db: Session | None = None, api_key: str | None = None):
        self.db = db  # kept for back-compat; quota tracking has its own session
        settings = get_settings()
        self.api_key = api_key or settings.youtube_api_key
        if not self.api_key:
            raise RuntimeError("YOUTUBE_API_KEY is not set")
        from googleapiclient.discovery import build

        self.yt = build("youtube", "v3", developerKey=self.api_key, cache_discovery=False)
        self.window_days = settings.discovery_window_days

    # ---- discovery ----

    def discover(
        self, queries: list[str], order: str = "viewCount", duration: str = "medium"
    ) -> list[DiscoveredVideo]:
        """One search.list pass per query; caller alternates order/duration
        between runs (viewCount/date, medium/short)."""
        published_after = (
            datetime.now(timezone.utc) - timedelta(days=self.window_days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        video_ids: list[str] = []
        for q in queries:
            try:
                charge_quota(SEARCH_COST, is_search=True)
            except QuotaExceeded:
                logger.warning("Quota budget hit — stopping discovery for today")
                break
            try:
                resp = self._search(q, published_after, order, duration)
            except Exception as exc:
                if _is_quota_error(exc):
                    raise QuotaExceeded(str(exc)) from exc
                logger.error("search.list failed for {!r}: {} — skipping query", q, exc)
                continue
            for item in resp.get("items", []):
                vid = item.get("id", {}).get("videoId")
                if vid:
                    video_ids.append(vid)

        # De-dupe preserving order, then hydrate with videos.list (1 unit / 50 ids).
        video_ids = list(dict.fromkeys(video_ids))
        return self._hydrate(video_ids)

    @api_retry
    def _search(self, q: str, published_after: str, order: str, duration: str) -> dict:
        return (
            self.yt.search()
            .list(
                part="id",
                q=q,
                type="video",
                videoDuration=duration,
                publishedAfter=published_after,
                order=order,
                maxResults=50,
                safeSearch="none",
            )
            .execute()
        )

    def _hydrate(self, video_ids: list[str]) -> list[DiscoveredVideo]:
        out: list[DiscoveredVideo] = []
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i : i + 50]
            try:
                charge_quota(LIST_COST)
                resp = self._videos_list(chunk, "snippet,contentDetails")
            except QuotaExceeded:
                break
            except Exception as exc:
                if _is_quota_error(exc):
                    raise QuotaExceeded(str(exc)) from exc
                logger.error("videos.list failed: {} — skipping chunk", exc)
                continue
            for item in resp.get("items", []):
                try:
                    out.append(self._to_video(item))
                except Exception as exc:  # one bad video never kills a batch
                    logger.warning("skipping malformed video {}: {}", item.get("id"), exc)
        return out

    @api_retry
    def _videos_list(self, ids: list[str], part: str) -> dict:
        return self.yt.videos().list(part=part, id=",".join(ids), maxResults=50).execute()

    @staticmethod
    def _to_video(item: dict) -> DiscoveredVideo:
        sn = item["snippet"]
        published = None
        if sn.get("publishedAt"):
            published = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
        thumbs = sn.get("thumbnails", {})
        thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
        return DiscoveredVideo(
            source="youtube",
            source_id=item["id"],
            url=f"https://www.youtube.com/watch?v={item['id']}",
            title=sn.get("title", ""),
            description=sn.get("description", ""),
            duration_seconds=parse_iso8601_duration(
                item.get("contentDetails", {}).get("duration", "")
            ),
            published_at=published,
            thumbnail_url=thumb,
            channel_source_id=sn.get("channelId", ""),
        )

    # ---- stats ----

    def snapshot_stats(self, source_ids: list[str]) -> list[VideoStats]:
        out: list[VideoStats] = []
        for i in range(0, len(source_ids), 50):
            chunk = source_ids[i : i + 50]
            try:
                charge_quota(LIST_COST)
                resp = self._videos_list(chunk, "statistics")
            except QuotaExceeded:
                logger.warning("Quota budget hit — partial stats snapshot")
                break
            except Exception as exc:
                if _is_quota_error(exc):
                    raise QuotaExceeded(str(exc)) from exc
                logger.error("stats videos.list failed: {} — skipping chunk", exc)
                continue
            for item in resp.get("items", []):
                st = item.get("statistics", {})
                out.append(
                    VideoStats(
                        source_id=item["id"],
                        views=int(st.get("viewCount", 0)),
                        likes=int(st.get("likeCount", 0)),
                        comments=int(st.get("commentCount", 0)),
                    )
                )
        return out

    # ---- creator profile ----

    def get_creator_profile(self, channel_source_id: str) -> CreatorProfile | None:
        return self.get_creator_profiles([channel_source_id]).get(channel_source_id)

    def get_creator_profiles(self, channel_ids: list[str]) -> dict[str, CreatorProfile]:
        """Batched channels.list — 50 channels per 1-unit call."""
        out: dict[str, CreatorProfile] = {}
        ids = list(dict.fromkeys(i for i in channel_ids if i))
        for i in range(0, len(ids), 50):
            chunk = ids[i : i + 50]
            try:
                charge_quota(LIST_COST)
                resp = self._channels_list(chunk)
            except QuotaExceeded:
                raise
            except Exception as exc:
                if _is_quota_error(exc):
                    raise QuotaExceeded(str(exc)) from exc
                logger.error("channels.list failed: {} — skipping chunk", exc)
                continue
            for ch in resp.get("items", []):
                try:
                    out[ch["id"]] = self._to_profile(ch)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("skipping malformed channel {}: {}", ch.get("id"), exc)
        return out

    @staticmethod
    def _to_profile(ch: dict) -> CreatorProfile:
        sn = ch.get("snippet", {})
        stats = ch.get("statistics", {})
        return CreatorProfile(
            source="youtube",
            source_channel_id=ch["id"],
            name=sn.get("title", ""),
            url=f"https://www.youtube.com/channel/{ch['id']}",
            subscriber_count=int(stats.get("subscriberCount", 0)),
            description=sn.get("description", ""),
            country=sn.get("country"),
        )

    @api_retry
    def _channels_list(self, channel_ids: list[str]) -> dict:
        return (
            self.yt.channels()
            .list(part="snippet,statistics", id=",".join(channel_ids), maxResults=50)
            .execute()
        )
