"""Pipeline stages, star ratings, bulk draft rendering."""
from app.crm import (
    PIPELINE_STAGES,
    STAR_LABELS,
    auto_advance,
    set_stage,
    stars_for_match,
    stars_string,
)
from app.models import Channel, Contact, Film
from app.outreach.drafts import render_draft


def make_channel(db, **kw):
    ch = Channel(source="youtube", source_channel_id=kw.pop("cid", "UC1"),
                 name=kw.pop("name", "Studio"), **kw)
    db.add(ch)
    db.flush()
    return ch


def test_auto_advance_moves_forward_only(db):
    ch = make_channel(db)
    assert ch.pipeline_stage == "discovered"
    assert auto_advance(ch, "shortlisted")
    assert ch.pipeline_stage == "shortlisted"
    assert auto_advance(ch, "contacted")
    # never backwards
    assert not auto_advance(ch, "shortlisted")
    assert ch.pipeline_stage == "contacted"


def test_auto_advance_respects_manual_stages(db):
    ch = make_channel(db)
    set_stage(ch, "collaborating")
    # automatic events must not move someone the human placed
    assert not auto_advance(ch, "contacted")
    assert ch.pipeline_stage == "collaborating"
    set_stage(ch, "rejected")
    assert not auto_advance(ch, "replied")
    assert ch.pipeline_stage == "rejected"


def test_set_stage_any_direction(db):
    ch = make_channel(db)
    set_stage(ch, "collaborating")
    assert set_stage(ch, "shortlisted")  # human can move backwards
    assert ch.pipeline_stage == "shortlisted"
    assert not set_stage(ch, "bogus")
    assert "bogus" not in PIPELINE_STAGES


def test_stars_mapping():
    assert stars_for_match(None) == 0
    assert stars_for_match(99) == 5
    assert stars_for_match(95) == 5
    assert stars_for_match(88) == 4
    assert stars_for_match(78) == 3
    assert stars_for_match(65) == 2
    assert stars_for_match(56) == 1
    assert stars_string(4) == "★★★★☆"
    assert STAR_LABELS[5] == "Outstanding"


def test_bulk_draft_uses_director_and_needs_no_edit(db):
    ch = make_channel(db, name="Luna Frame Films")
    film = Film(
        source="youtube", source_id="v1", title="STATIC", channel_id=ch.id,
        credits='{"Director": ["M. Costa"]}',
    )
    db.add(film)
    contact = Contact(channel_id=ch.id, email="hello@luna.example",
                      source_of_email="channel_about", confidence="listed_business")
    db.add(contact)
    db.flush()
    film.channel = ch
    subject, body = render_draft(db, film, contact, bulk=True)
    assert "M. Costa" in body          # [Director Name] personalization
    assert "STATIC" in body            # [Film Name] personalization
    assert "[ADD A SPECIFIC COMPLIMENT" not in body  # approvable immediately
    assert "unsubscribe" in body.lower()  # footer still mandatory
    assert "STATIC" in subject


def test_bulk_draft_falls_back_to_channel_name(db):
    ch = make_channel(db, name="Backyard Pictures", cid="UC2")
    film = Film(source="youtube", source_id="v2", title="The Last Delivery",
                channel_id=ch.id)
    db.add(film)
    contact = Contact(channel_id=ch.id, email="team@byp.example",
                      source_of_email="channel_about", confidence="listed_business")
    db.add(contact)
    db.flush()
    film.channel = ch
    _, body = render_draft(db, film, contact, bulk=True)
    assert "Backyard Pictures" in body
