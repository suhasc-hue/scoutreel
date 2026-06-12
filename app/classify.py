"""\"Is this actually a short film?\" — weighted heuristics first, optional LLM pass.

The heuristic never needs an API key; the LLM pass is feature-flagged via
USE_LLM_CLASSIFIER and refines heuristic-positive candidates only.

Also detects genre, language (via unicode script + keywords) and whether the
film looks like a film-school / student production — used for the curated
rows on the dashboard.
"""
import json
import re
from dataclasses import dataclass, replace

from loguru import logger

MIN_DURATION_S = 60
MAX_DURATION_S = 45 * 60

# A negative title keyword rejects outright — these are clip/meme/series
# formats, not narrative short films.
NEGATIVE_TITLE_PATTERNS = [
    r"\btrailer\b", r"\bteaser\b", r"\breaction\b", r"\breview\b",
    r"\bgameplay\b", r"\bpodcast\b", r"\bbts\b", r"behind the scenes",
    r"\bepisode\b", r"\bep\.?\s*\d", r"web\s*series", r"full movie",
    r"\bsong\b", r"\blyrical\b", r"\bstatus\b", r"\bpromo\b", r"\bvlog\b",
    r"\bnews\b", r"\bunboxing\b", r"\bcompilation\b", r"\btutorial\b",
    r"\binterview\b", r"comedy video", r"funny video", r"\bprank\b",
    r"\broast(?:ing)?\b", r"stand\s*-?\s*up", r"\bhighlights\b", r"\blive\b",
    r"#shorts\b", r"\bcrime patrol\b", r"\brecap\b", r"\bexplained\b",
    # formulaic melodrama / moral-story clip farms
    r"\bmalkin\b", r"\bnaukar(?:ani)?\b", r"\bsaas\b", r"\bbahu\b",
    r"moral story", r"\bkahani\b", r"emotional story", r"\bsasur\b",
    r"new (whatsapp|viral) video", r"wait for (the )?end",
]

SHORT_FILM_PHRASE = r"short\s+film|short\s+movie|court\s+m[ée]trage|cortometraje"

FILM_SCHOOL_PATTERNS = [
    r"film school", r"student film", r"student short", r"thesis film",
    r"graduation film", r"diploma film", r"degree film", r"\bftii\b",
    r"\bnfts\b", r"\bcalarts\b", r"\bafi\b", r"film institute",
    r"film academy", r"student academy award", r"\bsrfti\b",
]

GENRE_KEYWORDS = [
    ("horror", ["horror", "haunted", "ghost", "paranormal", "bhoot", "exorcis"]),
    ("thriller", ["thriller", "suspense", "crime", "mystery", "murder", "heist", "kidnap"]),
    ("sci-fi", ["sci-fi", "scifi", "science fiction", "time travel", "dystopi", "robot", "cyborg", "alien"]),
    ("romance", ["romance", "romantic", "love story", "valentine"]),
    ("comedy", ["comedy", "comedic", "satire", "funny"]),
    ("animation", ["animation", "animated", "stop motion", "stop-motion", "cgi short", "pixar"]),
    ("documentary", ["documentary", "docu-", "true story of"]),
    ("drama", ["drama", "emotional", "tragedy", "social message"]),
]

_SCRIPT_RANGES = [
    ("hindi", 0x0900, 0x097F),       # Devanagari (hindi/marathi)
    ("bengali", 0x0980, 0x09FF),
    ("punjabi", 0x0A00, 0x0A7F),
    ("gujarati", 0x0A80, 0x0AFF),
    ("tamil", 0x0B80, 0x0BFF),
    ("telugu", 0x0C00, 0x0C7F),
    ("kannada", 0x0C80, 0x0CFF),
    ("malayalam", 0x0D00, 0x0D7F),
    ("thai", 0x0E00, 0x0E7F),
    ("korean", 0xAC00, 0xD7AF),
    ("japanese", 0x3040, 0x30FF),
    ("chinese", 0x4E00, 0x9FFF),
    ("russian", 0x0400, 0x04FF),
    ("arabic", 0x0600, 0x06FF),
]

_LANGUAGE_KEYWORDS = [
    "hindi", "tamil", "telugu", "kannada", "malayalam", "marathi", "bengali",
    "punjabi", "gujarati", "urdu", "nepali", "spanish", "french", "german",
    "italian", "portuguese", "korean", "japanese", "indonesian", "turkish",
]


@dataclass(frozen=True)
class Classification:
    is_short_film: bool
    confidence: float
    genre: str | None = None
    language: str | None = None
    film_school: bool = False
    reason: str = ""


def detect_language(title: str, description: str) -> str:
    """Unicode-script counting first, then explicit language words, else english."""
    counts: dict[str, int] = {}
    for ch in title + " " + description[:300]:
        cp = ord(ch)
        for lang, lo, hi in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                counts[lang] = counts.get(lang, 0) + 1
                break
    if counts:
        lang, n = max(counts.items(), key=lambda kv: kv[1])
        if n >= 3:
            return lang
    text = f"{title} {description[:500]}".lower()
    for lang in _LANGUAGE_KEYWORDS:
        if re.search(rf"\b{lang}\b", text):
            return lang
    return "english"


def detect_genre(title: str, description: str) -> str | None:
    for source in (title.lower(), description[:800].lower()):
        for genre, words in GENRE_KEYWORDS:
            if any(w in source for w in words):
                return genre
    return None


def detect_film_school(title: str, description: str) -> bool:
    text = f"{title}\n{description[:1500]}".lower()
    return any(re.search(p, text) for p in FILM_SCHOOL_PATTERNS)


def _strip_hashtags(s: str) -> str:
    return re.sub(r"#\w+", " ", s)


def heuristic_classify(title: str, description: str, duration_seconds: int) -> Classification:
    title_l = title.lower()
    desc_l = description.lower()
    language = detect_language(title, description)
    genre = detect_genre(title, description)
    film_school = detect_film_school(title, description)

    def result(ok: bool, conf: float, reason: str) -> Classification:
        return Classification(ok, conf, genre=genre, language=language,
                              film_school=film_school, reason=reason)

    if not (MIN_DURATION_S <= duration_seconds <= MAX_DURATION_S):
        return result(False, 0.9, f"duration {duration_seconds}s out of 60s-45min")

    for pat in NEGATIVE_TITLE_PATTERNS:
        if re.search(pat, title_l):
            return result(False, 0.85, f"negative keyword in title: {pat}")

    # --- weighted positive evidence ---
    pts = 0.0
    reasons: list[str] = []
    title_nohash = _strip_hashtags(title_l)
    hashtag_count = title_l.count("#")

    if re.search(SHORT_FILM_PHRASE, title_nohash):
        pts += 3.0
        reasons.append("'short film' in title")
    elif re.search(r"#?\s*shortfilm", title_l) or re.search(SHORT_FILM_PHRASE, title_l):
        pts += 1.0  # hashtag-only mention is weak evidence (meme/skit channels abuse it)
        reasons.append("shortfilm tag")
    if re.search(SHORT_FILM_PHRASE, _strip_hashtags(desc_l)):
        pts += 1.5
        reasons.append("'short film' in description")
    if re.search(r"award|festival|official selection|selected at|laurel", f"{title_l}\n{desc_l}"):
        pts += 1.5
        reasons.append("festival/award")
    if re.search(r"directed by|director\s*[:\-–]|a film by", desc_l) or "directed by" in title_l:
        pts += 1.0
        reasons.append("director credit")
    if re.search(r"\bcast\b|starring|\bactors?\s*[:\-–]", desc_l):
        pts += 1.0
        reasons.append("cast credit")
    if re.search(r"screenplay|cinematograph|director of photography|\bdop\b|sound design|edited by", desc_l):
        pts += 1.0
        reasons.append("crew credits")
    if film_school:
        pts += 1.0
        reasons.append("film-school markers")
    if 240 <= duration_seconds <= 2400:
        pts += 0.5  # 4-40 min is the short-film sweet spot
    if hashtag_count >= 4:
        pts -= 2.0
        reasons.append("hashtag spam")
    elif hashtag_count >= 2:
        pts -= 1.0
        reasons.append("hashtag heavy")
    if duration_seconds < 240 and not re.search(SHORT_FILM_PHRASE, title_nohash):
        pts -= 1.5
        reasons.append("very short without explicit title")

    if pts >= 3.0:
        return result(True, min(0.5 + pts * 0.07, 0.95), "; ".join(reasons))
    return result(False, min(0.5 + (3.0 - pts) * 0.1, 0.9),
                  f"insufficient evidence ({pts:.1f} pts: {'; '.join(reasons) or 'none'})")


LLM_PROMPT = """You are classifying YouTube videos for a short-film discovery tool.
Given the metadata below, decide if this is an actual narrative short film
(fiction/animation/documentary short), not a trailer, reaction, review, vlog,
comedy skit, gameplay video or podcast. Be strict: hashtag-spam skit channels
often tag clips #shortfilm.

Title: {title}
Duration: {duration_seconds} seconds
Description:
{description}

Respond with ONLY a JSON object: {{"is_short_film": true/false, "confidence": 0.0-1.0, "genre": "drama|comedy|thriller|horror|sci-fi|romance|animation|documentary|other"}}"""


def llm_classify(
    title: str, description: str, duration_seconds: int, api_key: str, model: str
) -> Classification | None:
    """Returns None on any failure so callers fall back to the heuristic."""
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=200,
            messages=[
                {
                    "role": "user",
                    "content": LLM_PROMPT.format(
                        title=title[:300],
                        duration_seconds=duration_seconds,
                        description=description[:2000],
                    ),
                }
            ],
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
        data = json.loads(raw)
        return Classification(
            is_short_film=bool(data["is_short_film"]),
            confidence=float(data.get("confidence", 0.5)),
            genre=data.get("genre"),
            reason="llm",
        )
    except Exception as exc:  # noqa: BLE001 — never let the LLM kill a batch
        logger.warning("LLM classify failed, falling back to heuristic: {}", exc)
        return None


def classify(
    title: str,
    description: str,
    duration_seconds: int,
    use_llm: bool = False,
    api_key: str = "",
    model: str = "claude-sonnet-4-20250514",
) -> Classification:
    result = heuristic_classify(title, description, duration_seconds)
    if use_llm and api_key and result.is_short_film:
        refined = llm_classify(title, description, duration_seconds, api_key, model)
        if refined is not None:
            # language/film-school detection stays heuristic — LLM only judges
            # the is-it-a-short-film question and genre.
            return replace(
                refined,
                genre=refined.genre or result.genre,
                language=result.language,
                film_school=result.film_school,
            )
    return result
