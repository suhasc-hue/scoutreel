from app.classify import (
    detect_film_school,
    detect_genre,
    detect_language,
    heuristic_classify,
)
from app.sources.youtube import parse_iso8601_duration


def test_accepts_obvious_short_film():
    r = heuristic_classify(
        "MIDNIGHT — Award Winning Short Film",
        "A short film about loss. Directed by A. Kumar. Cast: ... Official selection, Mumbai Film Festival.",
        14 * 60,
    )
    assert r.is_short_film
    assert r.confidence > 0.5


def test_rejects_trailer():
    r = heuristic_classify("MIDNIGHT — Official Trailer", "Short film coming soon", 120)
    assert not r.is_short_film
    assert "trailer" in r.reason


def test_rejects_reaction_and_review_and_gameplay_and_podcast():
    for word in ("reaction", "review", "gameplay", "podcast"):
        r = heuristic_classify(f"Short film {word}", "some description short film", 600)
        assert not r.is_short_film, word


def test_rejects_too_short_duration():
    r = heuristic_classify("Great Short Film", "short film", 45)
    assert not r.is_short_film
    assert "duration" in r.reason


def test_rejects_too_long_duration():
    r = heuristic_classify("Feature Film", "short film director cast", 2 * 3600)
    assert not r.is_short_film


def test_rejects_no_keywords():
    r = heuristic_classify("My vlog from Bali", "daily life stuff", 600)
    assert not r.is_short_film


def test_word_boundary_negative_keywords():
    # 'bts' must not match inside 'doubts'
    r = heuristic_classify("Doubts — a short film", "fiction, directed by X, festival run", 900)
    assert r.is_short_film


def test_rejects_hashtag_spam_skits():
    """Meme/skit channels tag clips #shortfilm — hashtag-only mention plus
    hashtag spam must not pass."""
    r = heuristic_classify(
        "देसी कॉमेडी #shortfilm #comedy #viral #funny",
        "like share subscribe #comedy #viral",
        9 * 60,
    )
    assert not r.is_short_film


def test_rejects_comedy_video_format():
    r = heuristic_classify(
        "Funniest Fun New Comedy Video 2025",
        "best comedy compilation",
        16 * 60,
    )
    assert not r.is_short_film


def test_genuine_film_without_title_phrase_passes_on_credits():
    """Regional titles often lack the English phrase — crew/festival credits
    in the description carry it over the threshold."""
    r = heuristic_classify(
        "ಮಂಜಿನ ನಾಡು",
        "A Kannada short film. Directed by R. Gowda. Cast: ... "
        "Official selection, Bengaluru International Film Festival. DOP: S. Kumar",
        18 * 60,
    )
    assert r.is_short_film
    assert r.language == "kannada"


def test_language_detection():
    assert detect_language("बॉस मेरे पति", "") == "hindi"
    assert detect_language("STATIC", "An English-language sci-fi short") == "english"
    assert detect_language("Chai Break", "a hindi short film about office life") == "hindi"
    assert detect_language("비 오는 날", "") == "korean"


def test_genre_detection():
    assert detect_genre("MIDNIGHT — a horror short film", "") == "horror"
    assert detect_genre("The Last Delivery", "a suspense thriller about a courier") == "thriller"
    assert detect_genre("Untitled", "no hints here") is None


def test_film_school_detection():
    assert detect_film_school("JUST MATES", "Our graduation film from the National Film School")
    assert detect_film_school("Thesis Film 2026", "")
    assert not detect_film_school("STATIC", "an indie short film")


def test_classification_carries_curation_fields():
    r = heuristic_classify(
        "First Light — student short film",
        "Thesis film, directed by J. Park. Cast: ... Festival run 2026.",
        12 * 60,
    )
    assert r.is_short_film
    assert r.film_school
    assert r.language == "english"


def test_duration_parsing():
    assert parse_iso8601_duration("PT14M33S") == 14 * 60 + 33
    assert parse_iso8601_duration("PT1H2M3S") == 3723
    assert parse_iso8601_duration("PT45S") == 45
    assert parse_iso8601_duration("") == 0
    assert parse_iso8601_duration("garbage") == 0
