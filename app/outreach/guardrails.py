"""Send guardrails — HARD-CODED per spec §7.3, not just configurable.

 - No email is sent unless status='approved' (approval only via dashboard).
 - Daily send cap default 15, hard max 30 regardless of config.
 - Minimum 3-minute spacing between sends.
 - One email per contact per film, max 1 follow-up after 7 days, never more.
 - Never email the same address twice for the same film (across contacts).
 - Never email anyone in DoNotContact.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Contact, DoNotContact, OutreachEmail

HARD_MAX_DAILY = 30
MIN_SPACING = timedelta(minutes=3)
FOLLOWUP_MIN_AGE = timedelta(days=7)
MAX_EMAILS_PER_CONTACT_FILM = 2  # original + 1 follow-up, never more

# 'sending' counts: it is an in-flight claim that will become 'sent'.
SENT_LIKE_STATUSES = ("sending", "sent", "replied", "bounced", "opted_out")


class GuardrailViolation(Exception):
    """Raised when a send must be blocked. Message is shown loudly in UI/logs."""


def effective_daily_cap(db: Session | None = None) -> int:
    cap = get_settings().daily_send_cap
    if db is not None:
        from app.outreach.drafts import get_setting

        try:
            cap = int(get_setting(db, "daily_send_cap", str(cap)))
        except ValueError:
            pass
    return min(cap, HARD_MAX_DAILY)


def is_do_not_contact(db: Session, email_address: str) -> bool:
    return (
        db.query(DoNotContact)
        .filter(func.lower(DoNotContact.email) == email_address.strip().lower())
        .one_or_none()
        is not None
    )


def _account_filter(q, account_id: int | None, use_sentinel: bool):
    """Scope a query to one sender account. Caps and spacing are per
    connected Gmail account — each sender has their own daily budget."""
    if not use_sentinel:
        return q
    if account_id is None:
        return q.filter(OutreachEmail.sender_account_id.is_(None))
    return q.filter(OutreachEmail.sender_account_id == account_id)


def sent_today_count(
    db: Session,
    now: datetime | None = None,
    exclude_id: int | None = None,
    account_id: int | None = None,
    per_account: bool = False,
) -> int:
    """Sends today plus in-flight 'sending' claims (so concurrent claims can
    never overshoot the cap). Query params are naive UTC to match how SQLite
    stores the column."""
    now = now or datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    q = db.query(OutreachEmail).filter(
        or_(
            OutreachEmail.sent_at >= day_start,
            OutreachEmail.status == "sending",
        )
    )
    q = _account_filter(q, account_id, per_account)
    if exclude_id is not None:
        q = q.filter(OutreachEmail.id != exclude_id)
    return q.count()


def last_send_at(
    db: Session, account_id: int | None = None, per_account: bool = False
) -> datetime | None:
    q = db.query(OutreachEmail.sent_at).filter(OutreachEmail.sent_at.isnot(None))
    q = _account_filter(q, account_id, per_account)
    row = q.order_by(OutreachEmail.sent_at.desc()).first()
    return row[0] if row else None


def _as_utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo; treat naive timestamps as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def assert_can_send(
    db: Session, email_obj: OutreachEmail, now: datetime | None = None
) -> None:
    """Raise GuardrailViolation unless every rule passes. Called immediately
    before any Gmail send."""
    now = now or datetime.now(timezone.utc)

    # 'sending' is the atomic claim the send route takes on an approved email
    # immediately before calling this — both states prove dashboard approval.
    if email_obj.status not in ("approved", "sending"):
        raise GuardrailViolation(
            f"email #{email_obj.id} has status {email_obj.status!r}; "
            "only 'approved' emails can be sent (approve it in the dashboard)"
        )

    contact = db.get(Contact, email_obj.contact_id)
    if contact is None:
        raise GuardrailViolation("contact no longer exists")
    address = contact.email.strip().lower()

    if is_do_not_contact(db, address):
        raise GuardrailViolation(f"{address} is on the do-not-contact list")

    cap = effective_daily_cap(db)
    sent = sent_today_count(db, now, exclude_id=email_obj.id,
                            account_id=email_obj.sender_account_id, per_account=True)
    if sent >= cap:
        raise GuardrailViolation(f"daily send cap reached for this account ({sent}/{cap})")

    last = last_send_at(db, account_id=email_obj.sender_account_id, per_account=True)
    if last is not None and now - _as_utc(last) < MIN_SPACING:
        wait_s = int((MIN_SPACING - (now - _as_utc(last))).total_seconds())
        raise GuardrailViolation(
            f"minimum 3-minute spacing between sends; wait {wait_s}s"
        )

    # Same address + same film, across any contact row with that address.
    prior = (
        db.query(OutreachEmail)
        .join(Contact, OutreachEmail.contact_id == Contact.id)
        .filter(
            OutreachEmail.film_id == email_obj.film_id,
            func.lower(Contact.email) == address,
            OutreachEmail.status.in_(SENT_LIKE_STATUSES),
            OutreachEmail.id != email_obj.id,
        )
        .order_by(OutreachEmail.sent_at)
        .all()
    )
    if len(prior) >= MAX_EMAILS_PER_CONTACT_FILM:
        raise GuardrailViolation(
            f"already sent {len(prior)} emails to {address} for this film; "
            "max is 1 original + 1 follow-up"
        )
    if prior:  # this would be the single allowed follow-up
        first = prior[0]
        if any(p.status == "replied" for p in prior):
            raise GuardrailViolation(
                f"{address} already replied for this film; no follow-up needed"
            )
        if first.sent_at and now - _as_utc(first.sent_at) < FOLLOWUP_MIN_AGE:
            raise GuardrailViolation(
                "follow-up allowed only 7+ days after the original send"
            )
