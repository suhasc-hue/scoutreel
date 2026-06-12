"""APScheduler job definitions. Run with: python -m app.jobs  (or `make worker`).

Every job is wrapped so one bad video / API hiccup never kills a batch, and a
quota error stops the job gracefully until the next scheduled run (YouTube
quota resets at midnight PT).
"""
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy.orm import Session

from app.classify import classify
from app.config import get_settings
from app.db import init_db, session_scope
from app.models import (
    DEFAULT_SEED_QUERIES,
    Channel,
    ClassifierLog,
    Contact,
    DoNotContact,
    Film,
    FilmStat,
    OutreachEmail,
    ScoreSnapshot,
    SeedQuery,
)
from app.scoring import ScoringConstants, compute_score_from_points
from app.sources.youtube import QuotaExceeded, YouTubeAdapter

# Alternate search profile between runs (spec §5.1). 'long' is included
# because short films span 1-45 min and 'medium' tops out at 20.
_SEARCH_PROFILES = [
    ("viewCount", "medium"),
    ("date", "medium"),
    ("viewCount", "short"),
    ("date", "short"),
    ("viewCount", "long"),
    ("date", "long"),
]
_run_counter = {"discovery": 0}

VELOCITY_WINDOW_SNAPSHOTS = 4  # fit views/hr over up to this many snapshots


def ensure_seed_queries(db: Session) -> None:
    existing = {q.query for q in db.query(SeedQuery).all()}
    for q in DEFAULT_SEED_QUERIES:
        if q not in existing:
            db.add(SeedQuery(query=q))
    db.commit()


def scoring_constants_from_settings(db: Session) -> ScoringConstants:
    from app.outreach.drafts import get_setting

    s = get_settings()

    def f(key: str, default: float) -> float:
        try:
            return float(get_setting(db, key, str(default)))
        except ValueError:
            return default

    return ScoringConstants(
        velocity_weight=f("score_velocity_weight", s.score_velocity_weight),
        engagement_weight=f("score_engagement_weight", s.score_engagement_weight),
        recency_weight=f("score_recency_weight", s.score_recency_weight),
        comment_weight=f("score_comment_weight", s.score_comment_weight),
        recency_window_days=f("recency_window_days", s.recency_window_days),
    )


def _aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo; stored values are UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------- discovery ----------

def discovery_job() -> None:
    settings = get_settings()
    if not settings.youtube_api_key:
        logger.warning("discovery skipped: YOUTUBE_API_KEY not set")
        return
    order, duration = _SEARCH_PROFILES[_run_counter["discovery"] % len(_SEARCH_PROFILES)]
    _run_counter["discovery"] += 1
    with session_scope() as db:
        ensure_seed_queries(db)
        queries = [q.query for q in db.query(SeedQuery).filter_by(enabled=True).all()]
        adapter = YouTubeAdapter()
        try:
            videos = adapter.discover(queries, order=order, duration=duration)
        except QuotaExceeded as exc:
            logger.warning("discovery stopped, quota exhausted: {}", exc)
            return

        # Classify first, then batch-fetch profiles only for channels of
        # accepted films that we don't know yet (50 channels per quota unit).
        accepted = []
        for v in videos:
            exists = (
                db.query(Film.id)
                .filter_by(source=v.source, source_id=v.source_id)
                .one_or_none()
            )
            if exists:
                continue
            result = classify(
                v.title, v.description, v.duration_seconds,
                use_llm=settings.use_llm_classifier,
                api_key=settings.anthropic_api_key,
                model=settings.anthropic_model,
            )
            db.add(
                ClassifierLog(
                    source=v.source, source_id=v.source_id, title=v.title[:500],
                    duration_seconds=v.duration_seconds,
                    decision=result.is_short_film, confidence=result.confidence,
                    reason=result.reason[:250],
                )
            )
            if result.is_short_film:
                accepted.append((v, result))

        known = {
            c.source_channel_id: c
            for c in db.query(Channel).filter(
                Channel.source == "youtube",
                Channel.source_channel_id.in_({v.channel_source_id for v, _ in accepted}),
            )
        }
        missing = [v.channel_source_id for v, _ in accepted if v.channel_source_id not in known]
        profiles = {}
        if missing:
            try:
                profiles = adapter.get_creator_profiles(missing)
            except QuotaExceeded as exc:
                logger.warning("profile fetch stopped, quota exhausted: {}", exc)

        new_count = 0
        for v, result in accepted:
            try:
                channel = known.get(v.channel_source_id)
                if channel is None:
                    p = profiles.get(v.channel_source_id)
                    channel = Channel(
                        source=v.source,
                        source_channel_id=v.channel_source_id,
                        name=p.name if p else "",
                        url=p.url if p else "",
                        subscriber_count=p.subscriber_count if p else 0,
                        country=p.country if p else None,
                        description=p.description if p else "",
                        last_checked_at=datetime.now(timezone.utc),
                    )
                    db.add(channel)
                    db.flush()
                    known[v.channel_source_id] = channel
                from app.enrich import (
                    assess_quality,
                    credits_to_json,
                    infer_country,
                    parse_credits,
                )

                credits = parse_credits(v.description)
                quality, is_festival, is_award = assess_quality(
                    v.title, v.description, credits, result.film_school,
                    result.genre, channel.subscriber_count,
                )
                db.add(
                    Film(
                        source=v.source, source_id=v.source_id, url=v.url,
                        title=v.title, description=v.description,
                        duration_seconds=v.duration_seconds,
                        published_at=v.published_at, thumbnail_url=v.thumbnail_url,
                        channel_id=channel.id, is_short_film=True,
                        genre=result.genre, language=result.language,
                        film_school=result.film_school, status="new",
                        country=infer_country(channel.country, result.language),
                        credits=credits_to_json(credits),
                        quality_score=quality,
                        is_festival=is_festival, is_award=is_award,
                    )
                )
                new_count += 1
            except Exception as exc:  # noqa: BLE001 — one bad video never kills the batch
                logger.error("failed to ingest {}: {}", v.source_id, exc)
        db.commit()
        logger.info("discovery ({}/{}) done: {} candidates, {} new films",
                    order, duration, len(videos), new_count)


# ---------- stats snapshots + scoring ----------

def snapshot_job() -> None:
    settings = get_settings()
    if not settings.youtube_api_key:
        logger.warning("snapshot skipped: YOUTUBE_API_KEY not set")
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.tracking_window_days)
    with session_scope() as db:
        films = (
            db.query(Film)
            .filter(Film.status.in_(("new", "shortlisted")), Film.discovered_at >= cutoff)
            .all()
        )
        if not films:
            logger.info("snapshot: nothing to track")
            return
        by_source_id = {f.source_id: f for f in films if f.source == "youtube"}
        adapter = YouTubeAdapter()
        try:
            stats = adapter.snapshot_stats(list(by_source_id.keys()))
        except QuotaExceeded as exc:
            logger.warning("snapshot stopped, quota exhausted: {}", exc)
            return
        now = datetime.now(timezone.utc)
        for st in stats:
            film = by_source_id.get(st.source_id)
            if film is None:
                continue
            db.add(
                FilmStat(
                    film_id=film.id, captured_at=now,
                    views=st.views, likes=st.likes, comments=st.comments,
                )
            )
        db.commit()
        refresh_channels(db, adapter, films)
        score_films(db, [f.id for f in films])
        logger.info("snapshot: {} films updated", len(stats))


def refresh_channels(db: Session, adapter: YouTubeAdapter, films: list[Film]) -> None:
    """Keep subscriber counts fresh — the score normalizer depends on them.
    Batched: 50 channels per quota unit, refreshed at most every 24h."""
    stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    channels = {f.channel.source_channel_id: f.channel for f in films if f.channel}
    stale = [
        c for c in channels.values()
        if c.last_checked_at is None or _aware(c.last_checked_at) < stale_cutoff
    ]
    if not stale:
        return
    try:
        profiles = adapter.get_creator_profiles([c.source_channel_id for c in stale])
    except QuotaExceeded:
        logger.warning("channel refresh skipped, quota exhausted")
        return
    now = datetime.now(timezone.utc)
    for c in stale:
        p = profiles.get(c.source_channel_id)
        if p:
            c.subscriber_count = p.subscriber_count
            c.name = p.name or c.name
            c.description = p.description or c.description
        c.last_checked_at = now
    db.commit()
    logger.info("refreshed {} channels", len(stale))


def score_films(db: Session, film_ids: list[int]) -> int:
    """Store a ScoreSnapshot for every film with ≥2 stat rows. Velocity is a
    least-squares fit over the last few snapshots, not a single noisy delta."""
    constants = scoring_constants_from_settings(db)
    scored = 0
    for film_id in film_ids:
        film = db.get(Film, film_id)
        if film is None or film.published_at is None:
            continue
        stats = (
            db.query(FilmStat)
            .filter_by(film_id=film_id)
            .order_by(FilmStat.captured_at.desc())
            .limit(VELOCITY_WINDOW_SNAPSHOTS)
            .all()
        )
        if len(stats) < 2:
            continue
        latest = stats[0]
        latest_at = _aware(latest.captured_at)
        points = [(s.views, _aware(s.captured_at)) for s in stats]
        result = compute_score_from_points(
            points=points,
            likes=latest.likes, comments=latest.comments,
            subscriber_count=film.channel.subscriber_count if film.channel else 0,
            published_at=_aware(film.published_at), now=latest_at,
            constants=constants,
        )
        db.add(
            ScoreSnapshot(
                film_id=film_id, captured_at=latest_at,
                velocity_score=result.velocity_score,
                engagement_score=result.engagement_score,
                total_score=result.total_score,
            )
        )
        scored += 1
    db.commit()
    return scored


# ---------- contact extraction (Phase 2) ----------

def extract_contacts_for_channel(db: Session, channel: Channel, crawl_links: bool = True) -> list[Contact]:
    """Contact pipeline in priority order:
    1. channel description  2. video descriptions  3. bio links (polite crawl)
    4. web search -> crawl results (only if BRAVE_API_KEY is set).
    Only stores addresses not already present and not on the DNC list."""
    from app.contacts.crawl import PoliteCrawler
    from app.contacts.extract import extract_emails, extract_links
    from app.outreach.guardrails import is_do_not_contact

    found: list[tuple[str, str, str]] = []  # (email, source_of_email, confidence)
    for e in extract_emails(channel.description or ""):
        found.append((e.email, "channel_about", e.confidence))
    for film in channel.films:
        for e in extract_emails(film.description or ""):
            found.append((e.email, "video_description", e.confidence))

    if crawl_links and not found:
        links = extract_links(channel.description or "")
        for film in channel.films:
            links.extend(extract_links(film.description or ""))
        links = list(dict.fromkeys(links))[:10]
        if links:
            crawler = PoliteCrawler()
            try:
                for c in crawler.find_contacts(links):
                    src = "bio_link" if any(h in c.source_url for h in
                          ("linktr.ee", "beacons.ai", "bio.link", "carrd.co")) else "website"
                    found.append((c.email, src, c.confidence))
            finally:
                crawler.close()

    # Last resort: official search API + polite crawl of the results.
    if crawl_links and not found and get_settings().brave_api_key:
        from app.contacts.websearch import find_contacts_via_search
        from app.enrich import credits_from_json
        from app.models import ContactLead

        director = None
        title = ""
        for film in channel.films:
            credits = credits_from_json(film.credits)
            if credits.get("Director"):
                director = credits["Director"][0]
                title = film.title
                break
        if not title and channel.films:
            title = channel.films[0].title
        contacts, leads = find_contacts_via_search(channel.name, title, director)
        for c in contacts:
            found.append((c.email, "website", c.confidence))
        existing_leads = {l.url for l in db.query(ContactLead).filter_by(channel_id=channel.id)}
        for lead in leads:
            if lead["url"] not in existing_leads:
                db.add(ContactLead(channel_id=channel.id,
                                   title=lead["title"][:250], url=lead["url"][:500]))

    stored: list[Contact] = []
    existing = {c.email.lower() for c in channel.contacts}
    for email, source_of_email, confidence in found:
        if email.lower() in existing or is_do_not_contact(db, email):
            continue
        contact = Contact(
            channel_id=channel.id, email=email,
            source_of_email=source_of_email, confidence=confidence,
            verified_at=datetime.now(timezone.utc),
        )
        db.add(contact)
        stored.append(contact)
        existing.add(email.lower())
    db.commit()
    return stored


def contact_extraction_job() -> None:
    """Find contacts for shortlisted films whose channels have none yet."""
    with session_scope() as db:
        channels = (
            db.query(Channel)
            .join(Film, Film.channel_id == Channel.id)
            .filter(Film.status == "shortlisted")
            .all()
        )
        for channel in {c.id: c for c in channels}.values():
            if channel.contacts:
                continue
            try:
                stored = extract_contacts_for_channel(db, channel)
                logger.info("contacts for {!r}: {} found", channel.name, len(stored))
            except Exception as exc:  # noqa: BLE001
                logger.error("contact extraction failed for channel {}: {}", channel.id, exc)


# ---------- reply polling (Phase 3) ----------

def reply_poll_job() -> None:
    from app.outreach.gmail_client import GmailClient, reply_requests_unsubscribe

    with session_scope() as db:
        # 'replied' threads are re-checked too: an unsubscribe can arrive in a
        # second reply after the first one marked the thread replied.
        watched = (
            db.query(OutreachEmail)
            .filter(
                OutreachEmail.status.in_(("sent", "replied")),
                OutreachEmail.gmail_thread_id.isnot(None),
            )
            .all()
        )
        if not watched:
            return
        try:
            client = GmailClient(interactive=False)
            me = client.my_address()
        except Exception as exc:  # noqa: BLE001
            logger.warning("reply poll skipped (Gmail not authorized — run `make gmail-auth`): {}", exc)
            return
        for email_obj in watched:
            try:
                replies = client.get_thread_replies(email_obj.gmail_thread_id, me)
            except Exception as exc:  # noqa: BLE001
                logger.error("thread check failed for #{}: {}", email_obj.id, exc)
                continue
            if not replies:
                continue
            if email_obj.status == "sent":
                email_obj.status = "replied"
            if any(reply_requests_unsubscribe(r) for r in replies):
                email_obj.status = "opted_out"
                contact = db.get(Contact, email_obj.contact_id)
                if contact and not db.query(DoNotContact).filter_by(email=contact.email.lower()).one_or_none():
                    db.add(DoNotContact(email=contact.email.lower(), reason="replied unsubscribe"))
                    logger.info("{} added to do-not-contact (unsubscribe reply)", contact.email)
        db.commit()


# ---------- housekeeping ----------

def prune_job() -> None:
    """Time-series tables grow forever otherwise. Keep 90 days of history;
    films/contacts/outreach are never pruned."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    with session_scope() as db:
        stats = db.query(FilmStat).filter(FilmStat.captured_at < cutoff).delete()
        scores = db.query(ScoreSnapshot).filter(ScoreSnapshot.captured_at < cutoff).delete()
        if stats or scores:
            logger.info("pruned {} stat rows, {} score rows", stats, scores)


# ---------- scheduler entrypoint ----------

def main() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    init_db()
    with session_scope() as db:
        ensure_seed_queries(db)

    sched = BlockingScheduler(timezone="UTC")
    sched.add_job(discovery_job, "interval", hours=6, id="discovery",
                  next_run_time=datetime.now(timezone.utc))
    sched.add_job(snapshot_job, "interval", hours=6, id="snapshot",
                  next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2))
    sched.add_job(contact_extraction_job, "interval", hours=12, id="contacts",
                  next_run_time=datetime.now(timezone.utc) + timedelta(minutes=5))
    sched.add_job(reply_poll_job, "interval", hours=2, id="reply_poll",
                  next_run_time=datetime.now(timezone.utc) + timedelta(minutes=10))
    sched.add_job(prune_job, "interval", days=7, id="prune",
                  next_run_time=datetime.now(timezone.utc) + timedelta(minutes=20))
    logger.info("worker started — discovery/snapshot 6h, contacts 12h, replies 2h, prune 7d")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("worker stopped")


if __name__ == "__main__":
    main()
