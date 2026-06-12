"""Instagram adapter — Phase 4, feature-flagged via ENABLE_INSTAGRAM_SOURCE.

Two compliant paths only (NO browser-automation scraping — hard constraint):
 (a) Graph API hashtag search (requires Business/Creator account + app review)
 (b) a licensed commercial social-data provider behind the same interface.
"""
from loguru import logger

from app.config import get_settings
from app.sources.base import CreatorProfile, DiscoveredVideo, SourceAdapter, VideoStats


class InstagramAdapter(SourceAdapter):
    source = "instagram"

    def __init__(self):
        settings = get_settings()
        if not settings.enable_instagram_source:
            raise RuntimeError(
                "Instagram source is disabled (set ENABLE_INSTAGRAM_SOURCE=true)"
            )
        if not settings.ig_provider_key:
            raise RuntimeError("IG_PROVIDER_KEY is not set")
        self.provider_key = settings.ig_provider_key

    def discover(self, queries: list[str]) -> list[DiscoveredVideo]:
        logger.warning("InstagramAdapter.discover not yet implemented (Phase 4)")
        return []

    def snapshot_stats(self, source_ids: list[str]) -> list[VideoStats]:
        logger.warning("InstagramAdapter.snapshot_stats not yet implemented (Phase 4)")
        return []

    def get_creator_profile(self, channel_source_id: str) -> CreatorProfile | None:
        logger.warning("InstagramAdapter.get_creator_profile not yet implemented (Phase 4)")
        return None
