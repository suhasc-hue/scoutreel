from datetime import datetime, timedelta, timezone

import pytest

from app.models import Channel, Contact, DoNotContact, Film, OutreachEmail
from app.outreach.drafts import ensure_footer
from app.outreach.guardrails import (
    HARD_MAX_DAILY,
    GuardrailViolation,
    assert_can_send,
    effective_daily_cap,
)

NOW = datetime(2026, 6, 10, 15, 0, tzinfo=timezone.utc)


def make_world(db, n_films=1, email="filmmaker@studio.com"):
    channel = Channel(source="youtube", source_channel_id="UC1", name="Studio")
    db.add(channel)
    db.flush()
    contact = Contact(channel_id=channel.id, email=email,
                      source_of_email="channel_about", confidence="listed_business")
    db.add(contact)
    films = []
    for i in range(n_films):
        f = Film(source="youtube", source_id=f"vid{i}", title=f"Film {i}",
                 channel_id=channel.id)
        db.add(f)
        films.append(f)
    db.flush()
    return channel, contact, films


def make_email(db, contact, film, status="approved", sent_at=None):
    e = OutreachEmail(contact_id=contact.id, film_id=film.id,
                      subject="s", body="b", status=status, sent_at=sent_at)
    db.add(e)
    db.flush()
    return e


def test_unapproved_email_blocked(db):
    _, contact, films = make_world(db)
    e = make_email(db, contact, films[0], status="draft")
    with pytest.raises(GuardrailViolation, match="approved"):
        assert_can_send(db, e, NOW)


def test_approved_email_passes(db):
    _, contact, films = make_world(db)
    e = make_email(db, contact, films[0], status="approved")
    assert_can_send(db, e, NOW)  # must not raise


def test_do_not_contact_blocked(db):
    _, contact, films = make_world(db)
    db.add(DoNotContact(email="FILMMAKER@studio.com".lower(), reason="asked"))
    db.flush()
    e = make_email(db, contact, films[0])
    with pytest.raises(GuardrailViolation, match="do-not-contact"):
        assert_can_send(db, e, NOW)


def test_dnc_check_case_insensitive(db):
    _, contact, films = make_world(db, email="Filmmaker@Studio.COM")
    db.add(DoNotContact(email="filmmaker@studio.com", reason="asked"))
    db.flush()
    e = make_email(db, contact, films[0])
    with pytest.raises(GuardrailViolation, match="do-not-contact"):
        assert_can_send(db, e, NOW)


def test_daily_cap_enforced(db, monkeypatch):
    channel, contact, films = make_world(db, n_films=40)
    cap = effective_daily_cap()
    # cap-many already sent today (well spaced)
    for i in range(cap):
        make_email(db, contact, films[i], status="sent",
                   sent_at=NOW - timedelta(hours=10) + timedelta(minutes=5 * i))
    e = make_email(db, contact, films[cap + 1], status="approved")
    with pytest.raises(GuardrailViolation, match="daily send cap"):
        assert_can_send(db, e, NOW)


def test_hard_max_30_even_if_setting_higher(db):
    from app.outreach.drafts import set_setting

    set_setting(db, "daily_send_cap", "500")
    db.flush()
    assert effective_daily_cap(db) == HARD_MAX_DAILY


def test_sending_claims_count_toward_cap(db):
    """In-flight 'sending' rows occupy cap slots so concurrent claims can
    never overshoot."""
    from app.outreach.guardrails import sent_today_count

    _, contact, films = make_world(db, n_films=3)
    make_email(db, contact, films[0], status="sent", sent_at=NOW - timedelta(hours=2))
    make_email(db, contact, films[1], status="sending")  # claimed, not yet sent
    assert sent_today_count(db, NOW) == 2


def test_sending_status_is_sendable_after_claim(db):
    """The send route claims approved→sending before calling assert_can_send."""
    _, contact, films = make_world(db)
    e = make_email(db, contact, films[0], status="sending")
    assert_can_send(db, e, NOW)  # must not raise


def test_min_spacing_between_sends(db):
    _, contact, films = make_world(db, n_films=3)
    make_email(db, contact, films[0], status="sent", sent_at=NOW - timedelta(minutes=1))
    e = make_email(db, contact, films[1], status="approved")
    with pytest.raises(GuardrailViolation, match="spacing"):
        assert_can_send(db, e, NOW)
    # 3+ minutes later is fine
    assert_can_send(db, e, NOW + timedelta(minutes=3))


def test_dedupe_same_address_same_film(db):
    """Never email the same address twice for the same film — even via a
    different Contact row with the same address."""
    channel, contact, films = make_world(db)
    contact2 = Contact(channel_id=channel.id, email="FILMMAKER@STUDIO.COM",
                       source_of_email="website", confidence="listed_business")
    db.add(contact2)
    db.flush()
    make_email(db, contact, films[0], status="sent", sent_at=NOW - timedelta(days=1))
    e2 = OutreachEmail(contact_id=contact2.id, film_id=films[0].id,
                       subject="s", body="b", status="approved")
    db.add(e2)
    db.flush()
    with pytest.raises(GuardrailViolation, match="follow-up"):
        assert_can_send(db, e2, NOW)


def test_followup_allowed_after_7_days_only(db):
    _, contact, films = make_world(db)
    make_email(db, contact, films[0], status="sent", sent_at=NOW - timedelta(days=3))
    followup = make_email(db, contact, films[0], status="approved")
    with pytest.raises(GuardrailViolation, match="7"):
        assert_can_send(db, followup, NOW)
    # after 7 days it is allowed
    assert_can_send(db, followup, NOW + timedelta(days=5))


def test_never_more_than_one_followup(db):
    _, contact, films = make_world(db)
    make_email(db, contact, films[0], status="sent", sent_at=NOW - timedelta(days=20))
    make_email(db, contact, films[0], status="sent", sent_at=NOW - timedelta(days=10))
    third = make_email(db, contact, films[0], status="approved")
    with pytest.raises(GuardrailViolation, match="max"):
        assert_can_send(db, third, NOW)


def test_no_followup_after_reply(db):
    _, contact, films = make_world(db)
    make_email(db, contact, films[0], status="replied", sent_at=NOW - timedelta(days=10))
    followup = make_email(db, contact, films[0], status="approved")
    with pytest.raises(GuardrailViolation, match="replied"):
        assert_can_send(db, followup, NOW)


def test_footer_always_present():
    body = "Hi there,\n\nLoved your film."
    out = ensure_footer(body, "channel_about")
    assert "unsubscribe" in out.lower()
    assert "found your email" in out.lower()
    # idempotent — never doubles up
    assert ensure_footer(out, "channel_about") == out


def test_footer_not_fooled_by_unsubscribe_in_pitch():
    """Writing the word 'unsubscribe' in the body must not suppress the
    mandatory transparency footer."""
    body = "Hi,\n\nOur newsletter lets anyone unsubscribe anytime."
    out = ensure_footer(body, "website")
    assert "I found your email via" in out
