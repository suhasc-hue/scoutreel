"""X (Twitter) adapter — Phase 4, feature-flagged via ENABLE_X_SOURCE.

Uses ONLY the official X API v2 (paid tier required). No scraping — hard
constraint from the spec. Skeleton is in place so enabling it later requires
no changes elsewhere in the system.
"""
from loguru import logger

from app.config import get_settings
from app.sources.base import CreatorProfile, DiscoveredVideo, SourceAdapter, VideoStats


class XAdapter(SourceAdapter):
    source = "x"

    def __init__(self):
        settings = get_settings()
        if not settings.enable_x_source:
            raise RuntimeError("X source is disabled (set ENABLE_X_SOURCE=true)")
        if not settings.x_bearer_token:
            raise RuntimeError("X_BEARER_TOKEN is not set")
        self.bearer = settings.x_bearer_token

    def discover(self, queries: list[str]) -> list[DiscoveredVideo]:
        # Planned: GET /2/tweets/search/recent with `"short film" has:video`,
        # ranked by public_metrics velocity; respect rate-limit response headers.
        logger.warning("XAdapter.discover not yet implemented (Phase 4)")
        return []

    def snapshot_stats(self, source_ids: list[str]) -> list[VideoStats]:
        logger.warning("XAdapter.snapshot_stats not yet implemented (Phase 4)")
        return []

    def get_creator_profile(self, channel_source_id: str) -> CreatorProfile | None:
        # Planned: GET /2/users/:id — bio URL feeds the same Phase 2 crawler.
        logger.warning("XAdapter.get_creator_profile not yet implemented (Phase 4)")
        return None
