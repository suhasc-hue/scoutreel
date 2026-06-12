"""SQLAlchemy models — see SCOUTREEL_SPEC.md section 4."""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("source", "source_channel_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))  # youtube|x|instagram
    source_channel_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256), default="")
    url: Mapped[str] = mapped_column(String(512), default="")
    subscriber_count: Mapped[int] = mapped_column(Integer, default=0)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- filmmaker pipeline (CRM) ---
    # discovered|shortlisted|contacted|replied|in_discussion|collaborating|rejected
    pipeline_stage: Mapped[str] = mapped_column(String(24), default="discovered", index=True)
    stage_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(String(256), default="")  # comma-separated
    priority: Mapped[int] = mapped_column(Integer, default=0)  # 0 none .. 3 high
    followup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    films: Mapped[list["Film"]] = relationship(back_populates="channel")
    contacts: Mapped[list["Contact"]] = relationship(back_populates="channel")


class Film(Base):
    __tablename__ = "films"
    __table_args__ = (UniqueConstraint("source", "source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))  # youtube|x|instagram
    source_id: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(String(512), default="")
    title: Mapped[str] = mapped_column(String(512), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    thumbnail_url: Mapped[str] = mapped_column(String(512), default="")
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    is_short_film: Mapped[bool] = mapped_column(Boolean, default=False)
    genre: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    language: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    film_school: Mapped[bool] = mapped_column(Boolean, default=False)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    credits: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON role->names
    quality_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    is_festival: Mapped[bool] = mapped_column(Boolean, default=False)
    is_award: Mapped[bool] = mapped_column(Boolean, default=False)
    # new|shortlisted|rejected|contacted
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)

    channel: Mapped[Channel] = relationship(back_populates="films")
    stats: Mapped[list["FilmStat"]] = relationship(
        back_populates="film", order_by="FilmStat.captured_at"
    )
    scores: Mapped[list["ScoreSnapshot"]] = relationship(
        back_populates="film", order_by="ScoreSnapshot.captured_at"
    )


class FilmStat(Base):
    """Time-series snapshots for velocity."""

    __tablename__ = "film_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    film_id: Mapped[int] = mapped_column(ForeignKey("films.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)

    film: Mapped[Film] = relationship(back_populates="stats")


class Contact(Base):
    """RULE: only store emails explicitly published for contact/business inquiries."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    source_of_email: Mapped[str] = mapped_column(String(32))  # channel_about|bio_link|website
    confidence: Mapped[str] = mapped_column(String(32), default="inferred")  # listed_business|inferred
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    channel: Mapped[Channel] = relationship(back_populates="contacts")


class MailAccount(Base):
    """A connected Gmail sender. Several people can each connect their own
    account; every outreach email records which account sends it."""

    __tablename__ = "mail_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(320), unique=True)
    token_file: Mapped[str] = mapped_column(String(256))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContactLead(Base):
    """Web pages that likely hold contact info but are never auto-crawled
    (LinkedIn, IMDb, ...) — surfaced to the user as manual leads."""

    __tablename__ = "contact_leads"
    __table_args__ = (UniqueConstraint("channel_id", "url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    url: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutreachEmail(Base):
    __tablename__ = "outreach_emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    film_id: Mapped[int] = mapped_column(ForeignKey("films.id"), index=True)
    subject: Mapped[str] = mapped_column(String(512), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    # draft|approved|sending|sent|bounced|replied|opted_out
    # 'sending' is a transient claim taken atomically right before the Gmail
    # call so two concurrent requests can never both send the same email.
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_followup: Mapped[bool] = mapped_column(Boolean, default=False)
    # inbox fields — populated by the reply-poll job
    unread: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_reply_snippet: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # which connected Gmail account sends/sent this (None = default account)
    sender_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("mail_accounts.id"), nullable=True, index=True
    )

    contact: Mapped[Contact] = relationship()
    film: Mapped[Film] = relationship()


class DoNotContact(Base):
    __tablename__ = "do_not_contact"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    reason: Mapped[str] = mapped_column(String(256), default="")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    film_id: Mapped[int] = mapped_column(ForeignKey("films.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    velocity_score: Mapped[float] = mapped_column(Float, default=0.0)
    engagement_score: Mapped[float] = mapped_column(Float, default=0.0)
    total_score: Mapped[float] = mapped_column(Float, default=0.0)

    film: Mapped[Film] = relationship(back_populates="scores")


class SeedQuery(Base):
    """Configurable discovery queries, editable from the dashboard."""

    __tablename__ = "seed_queries"

    id: Mapped[int] = mapped_column(primary_key=True)
    query: Mapped[str] = mapped_column(String(256), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SeedChannel(Base):
    """Curated source channels (festival aggregators, film schools) whose
    uploads are harvested via playlist reads — 1 quota unit per 50 videos,
    vs 100 units per search. Editable from the dashboard."""

    __tablename__ = "seed_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    handle: Mapped[str] = mapped_column(String(128), unique=True)  # @handle or UC… id
    label: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    channel_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)  # resolved UC id
    uploads_playlist: Mapped[str | None] = mapped_column(String(128), nullable=True)
    next_page_token: Mapped[str | None] = mapped_column(String(128), nullable=True)  # deep-walk resume
    last_harvested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QuotaUsage(Base):
    """Daily YouTube API quota tracking (units, Pacific-time day like Google's)."""

    __tablename__ = "quota_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[str] = mapped_column(String(10), unique=True)  # YYYY-MM-DD (PT)
    units_used: Mapped[int] = mapped_column(Integer, default=0)
    searches_run: Mapped[int] = mapped_column(Integer, default=0)


class ClassifierLog(Base):
    """Audit trail of classifier decisions — lets us inspect false negatives
    later and build a labeled set; rejected candidates no longer vanish."""

    __tablename__ = "classifier_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16), default="youtube")
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    decision: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    """Dashboard-editable overrides (scoring constants, send cap, signature, template...)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


# Public, well-known curated short-film sources (aggregators, festival
# channels, animation schools). Stored in the DB on first run; the user can
# add/remove/disable any of them from Settings.
DEFAULT_SEED_CHANNELS = [
    ("@Omeleto", "Omeleto — festival shorts"),
    ("@watchdust", "DUST — sci-fi shorts"),
    ("@WatchALTER", "ALTER — horror shorts"),
    ("@shortoftheweek", "Short of the Week"),
    ("@filmshortage", "Film Shortage — daily picks"),
    ("@nobudge", "NoBudge — indie shorts"),
    ("@PocketFilms", "Pocket Films — Indian shorts"),
    ("@largeshortfilms", "Large Short Films — Indian prestige"),
    ("@humaramovie", "Humara Movie — Indian shorts"),
    ("@Viddsee", "Viddsee — Asian shorts"),
    ("@TheCGBros", "TheCGBros — CG animation"),
    ("@CGMeetup", "CGMeetup — animation"),
    ("@GOBELINS", "Gobelins — animation school"),
    ("@ESMAmovies", "ESMA — animation school"),
    ("@TheAnimationWorkshopVIA", "The Animation Workshop"),
    ("@filmakademie", "Filmakademie — film school"),
]

DEFAULT_SEED_QUERIES = [
    "short film",
    "award winning short film",
    '"short film" hindi',
    "film school short",
    "thriller short film",
    "drama short film",
    "sci-fi short film",
    "comedy short film",
]
