"""Credits parsing, quality assessment, country/region mapping."""
from app.enrich import (
    assess_quality,
    infer_country,
    parse_credits,
    region_of,
)

PRO_DESCRIPTION = """STATIC — a sci-fi short film.

A radio operator hears tomorrow.

Official Selection — Mumbai International Film Festival 2026
Winner — Best Short Film, Indie Cine Awards

Director: M. Costa
Written by Ana Costa & J. Pereira
Cinematography: S. Kumar
Producer: Luna Frame Films
Editor - R. Gomes
Music by Arkady M.
Sound Design: P. Silva
Cast: A. Rao, T. Fernandes, M. Iyer
Production House: Luna Frame Films

Instagram: https://instagram.com/lunaframe
"""


def test_parses_full_credit_block():
    c = parse_credits(PRO_DESCRIPTION)
    assert c["Director"] == ["M. Costa"]
    assert "Ana Costa" in c["Writer"] and "J. Pereira" in c["Writer"]
    assert c["DOP"] == ["S. Kumar"]
    assert c["Producer"] == ["Luna Frame Films"]
    assert c["Editor"] == ["R. Gomes"]
    assert c["Music"] == ["Arkady M"]  # trailing punctuation is normalized away
    assert "A. Rao" in c["Cast"] and len(c["Cast"]) == 3
    assert c["Production"] == ["Luna Frame Films"]


def test_credit_block_form_cast_on_next_lines():
    desc = "A short film.\n\nCast:\nJane Doe\nRavi Kumar\n\nSubscribe for more!"
    c = parse_credits(desc)
    assert c["Cast"] == ["Jane Doe", "Ravi Kumar"]


def test_a_film_by_maps_to_director():
    c = parse_credits("A film by Wes Mendes\nshot on 16mm")
    assert c["Director"] == ["Wes Mendes"]


def test_no_credits_in_spam():
    c = parse_credits("LIKE SHARE SUBSCRIBE!!! follow on instagram @xyz #viral")
    assert c == {}


def test_urls_and_handles_stripped():
    c = parse_credits("Director: Maya Lin https://maya.example @mayalin")
    assert c["Director"] == ["Maya Lin"]


def test_quality_professional_film_scores_high():
    credits = parse_credits(PRO_DESCRIPTION)
    q, fest, award = assess_quality(
        "STATIC — a sci-fi short film", PRO_DESCRIPTION, credits,
        film_school=False, genre="sci-fi", channel_subscriber_count=2300,
    )
    assert fest and award
    assert q >= 7


def test_quality_clip_farm_scores_zero():
    q, fest, award = assess_quality(
        "Naukar Ko Malkin Ka Pyaar 😱 New Emotional Kahani",
        "moral story wait for end #viral #shortfilm",
        {}, film_school=False, genre=None, channel_subscriber_count=2_400_000,
    )
    assert q == 0.0
    assert not fest and not award


def test_quality_plain_indie_passes_floor():
    q, _, _ = assess_quality(
        "First Light — short film",
        "A short film.\nDirector: J. Park\nCast: A, B\nDOP: C\nEdited by D\n",
        {"Director": ["J. Park"], "Cast": ["A", "B"], "DOP": ["C"], "Editor": ["D"]},
        film_school=False, genre="drama", channel_subscriber_count=900,
    )
    assert q >= 2


def test_country_and_region():
    assert infer_country("IN", None) == "IN"
    assert infer_country(None, "hindi") == "IN"
    assert infer_country(None, "korean") == "KR"
    assert infer_country(None, None) is None
    assert region_of("IN") == "South Asia"
    assert region_of("FR") == "Europe"
    assert region_of("NG") == "Africa"
    assert region_of(None) is None
