"""SourceAdapter interface — every platform (YouTube, X, Instagram) implements
this so the rest of the system needs zero changes per source."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DiscoveredVideo:
    source: str
    source_id: str
    url: str
    title: str
    description: str
    duration_seconds: int
    published_at: datetime | None
    thumbnail_url: str
    channel_source_id: str
    # initial statistics when the listing included them (free first snapshot)
    views: int = 0
    likes: int = 0
    comments: int = 0


@dataclass
class VideoStats:
    source_id: str
    views: int
    likes: int
    comments: int


@dataclass
class CreatorProfile:
    source: str
    source_channel_id: str
    name: str
    url: str
    subscriber_count: int
    description: str
    country: str | None = None
    links: list[str] = field(default_factory=list)


class SourceAdapter(ABC):
    source: str

    @abstractmethod
    def discover(self, queries: list[str]) -> list[DiscoveredVideo]:
        """Search for candidate short films."""

    @abstractmethod
    def snapshot_stats(self, source_ids: list[str]) -> list[VideoStats]:
        """Fetch current view/like/comment counts (batched)."""

    @abstractmethod
    def get_creator_profile(self, channel_source_id: str) -> CreatorProfile | None:
        """Fetch creator metadata used for contact extraction."""
