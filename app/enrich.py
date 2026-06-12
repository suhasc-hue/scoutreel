"""Film enrichment — credits parsing, quality assessment, country/region mapping.

Everything here is pure-Python over title/description text so it runs at
ingest time and in backfills with zero API cost.
"""
import json
import re

# ---------------------------------------------------------------- credits ---

# role -> patterns that introduce the credit on a line
CREDIT_PATTERNS: list[tuple[str, list[str]]] = [
    ("Director", [r"directed\s+by", r"^director'?s?\b", r"\ba\s+film\s+by\b", r"^dir\.?\b"]),
    ("Writer", [r"written\s+by", r"^writers?\b", r"^screenplay\b", r"^story\b", r"^script\b"]),
    ("DOP", [r"^cinematograph(?:y|er)\b", r"director\s+of\s+photography", r"^dop\b", r"^d\.o\.p\.?\b", r"shot\s+by", r"^camera\b"]),
    ("Producer", [r"produced\s+by", r"^producers?\b", r"^executive\s+producer\b"]),
    ("Editor", [r"edited\s+by", r"^editors?\b", r"^editing\b", r"^edit\b"]),
    ("Music", [r"^music\b", r"^score\b", r"^composer\b", r"music\s+by", r"^background\s+score\b"]),
    ("Sound", [r"^sound\s+design(?:er)?\b", r"^sound\b", r"^mix(?:ing)?\b", r"^foley\b"]),
    ("Cast", [r"^cast\b", r"^starring\b", r"^featuring\b", r"^actors?\b", r"^stars\b"]),
    ("Production", [r"^production\s+(?:house|company)\b", r"^production\b", r"^a\s+presentation\s+of\b", r"^presented\s+by\b", r"^studio\b"]),
    ("VFX", [r"^vfx\b", r"^visual\s+effects\b", r"^animation\s+by\b", r"^animator\b"]),
    ("Colorist", [r"^colou?r(?:ist|\s+grading|\s+grade)\b", r"^di\b"]),
    ("Costume", [r"^costumes?\b", r"^wardrobe\b"]),
    ("Art Director", [r"^art\s+direct(?:or|ion)\b", r"^production\s+design(?:er)?\b"]),
]

_SEP_RE = re.compile(r"\s*[:\-–—|]\s*")
_URL_RE = re.compile(r"https?://\S+|www\.\S+|@\w+")
_NOISE_RE = re.compile(
    r"subscribe|follow|instagram|facebook|twitter|youtube|whatsapp|linktree|copyright|all rights",
    re.IGNORECASE,
)


def _clean_names(raw: str) -> list[str]:
    raw = _URL_RE.sub("", raw)
    raw = re.sub(r"[#*_\"“”()\[\]]", " ", raw)
    names = re.split(r",|&| and |/|•|;", raw)
    out = []
    for n in names:
        n = " ".join(n.split()).strip(" .:-–—|")
        # plausible person/company name: 2-60 chars, not noise, not mostly digits
        if 2 <= len(n) <= 60 and not _NOISE_RE.search(n) and not re.fullmatch(r"[\d\W]+", n):
            out.append(n)
    return out[:8]


def _looks_like_name_line(line: str) -> bool:
    line = line.strip()
    if not (2 <= len(line) <= 60) or _NOISE_RE.search(line) or _URL_RE.search(line):
        return False
    words = line.split()
    return 1 <= len(words) <= 5 and not line.endswith(":")


def parse_credits(description: str) -> dict[str, list[str]]:
    """Extract film credits from a YouTube description, line by line.
    Handles both 'Director: Name' lines and block headers ('Cast:' followed
    by name lines)."""
    if not description:
        return {}
    credits: dict[str, list[str]] = {}
    lines = description.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or len(line) > 160:
            continue
        low = line.lower()
        for role, patterns in CREDIT_PATTERNS:
            if not any(re.search(p, low) for p in patterns):
                continue
            # remainder after the role label
            parts = _SEP_RE.split(line, maxsplit=1)
            remainder = parts[1] if len(parts) > 1 else ""
            if not remainder:
                m = re.search(r"(?:by)\s+(.+)$", line, re.IGNORECASE)
                remainder = m.group(1) if m else ""
            names = _clean_names(remainder) if remainder else []
            # block form: 'Cast:' on its own line, names on the next lines
            if not names:
                block = []
                j = i
                while j < len(lines) and len(block) < 6:
                    nxt = lines[j].strip()
                    if not nxt or not _looks_like_name_line(nxt):
                        break
                    block.extend(_clean_names(nxt))
                    j += 1
                if block:
                    names = block[:8]
                    i = j
            if names:
                existing = credits.setdefault(role, [])
                for n in names:
                    if n not in existing:
                        existing.append(n)
            break
    return credits


def credits_to_json(credits: dict[str, list[str]]) -> str | None:
    return json.dumps(credits, ensure_ascii=False) if credits else None


def credits_from_json(raw: str | None) -> dict[str, list[str]]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


# ---------------------------------------------------------------- quality ---

FESTIVAL_RE = re.compile(
    r"official selection|film festival|festival premiere|selected at|laurels?|"
    r"world premiere|festival run|cannes|sundance|tiff\b|berlinale|sxsw|clermont",
    re.IGNORECASE,
)
AWARD_RE = re.compile(
    r"award[- ]?winn|winner|won (the|best)|best (short|film|director|actor|cinematograph)|"
    r"jury (prize|award)|grand prix|oscar|national award",
    re.IGNORECASE,
)
# Formulaic clip-farm content (melodrama serials, moral-story mills, roasting).
JUNK_RE = re.compile(
    r"\bmalkin\b|\bnaukar(?:ani)?\b|\bsaas\b|\bbahu\b|moral story|emotional kahani|"
    r"\bkahani\b|crime stories?\b|\broast(?:ing)?\b|new (whatsapp|viral) video|"
    r"\bgarib ki\b|\bsasur\b|\bdevrani\b|\bjethani\b|sas bahu|\bperiod\b|\bkittens?\b|"
    r"\bemi\b|wait for (the )?end|don'?t miss the end",
    re.IGNORECASE,
)


def assess_quality(
    title: str,
    description: str,
    credits: dict[str, list[str]],
    film_school: bool,
    genre: str | None,
    channel_subscriber_count: int = 0,
    curated_source: bool = False,
) -> tuple[float, bool, bool]:
    """Returns (quality_score 0-10, is_festival, is_award).

    Professional markers raise the score; clip-farm markers sink it. The home
    page only surfaces films above a quality floor. curated_source marks
    films harvested from festival/aggregator/film-school channels — already
    human-curated upstream, so they start with a strong prior."""
    text = f"{title}\n{description[:2500]}"
    is_festival = bool(FESTIVAL_RE.search(text))
    is_award = bool(AWARD_RE.search(text))

    q = 1.0  # passed the classifier at all
    if curated_source:
        q += 3.0  # picked by a human curator upstream
    if is_festival:
        q += 3.0
    if is_award:
        q += 2.5
    q += min(len(credits) * 0.8, 4.0)  # structured credits = professional production
    if film_school:
        q += 2.0
    if "Production" in credits:
        q += 1.0
    if genre == "animation":
        q += 1.0
    if len(description) > 400 and description.count("\n") >= 4:
        q += 0.5  # a real synopsis + credit block, not one spam line

    if JUNK_RE.search(title) or JUNK_RE.search(description[:500]):
        q -= 5.0
    if title.count("#") >= 3:
        q -= 1.5
    if "🔥" in title or "😂" in title or "😱" in title:
        q -= 1.0
    if channel_subscriber_count > 1_000_000 and q < 4 and not curated_source:
        q -= 2.0  # huge channel with zero professional markers = content farm

    return max(0.0, min(q, 10.0)), is_festival, is_award


# ---------------------------------------------------------------- regions ---

COUNTRY_NAMES = {
    "IN": "India", "PK": "Pakistan", "BD": "Bangladesh", "LK": "Sri Lanka", "NP": "Nepal",
    "US": "United States", "CA": "Canada", "MX": "Mexico",
    "GB": "United Kingdom", "IE": "Ireland", "FR": "France", "DE": "Germany",
    "ES": "Spain", "IT": "Italy", "PT": "Portugal", "NL": "Netherlands", "BE": "Belgium",
    "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland", "PL": "Poland",
    "CZ": "Czechia", "GR": "Greece", "RO": "Romania", "HU": "Hungary", "AT": "Austria",
    "CH": "Switzerland", "UA": "Ukraine", "RU": "Russia",
    "BR": "Brazil", "AR": "Argentina", "CO": "Colombia", "CL": "Chile", "PE": "Peru",
    "NG": "Nigeria", "KE": "Kenya", "ZA": "South Africa", "GH": "Ghana", "ET": "Ethiopia",
    "EG": "Egypt", "MA": "Morocco", "TN": "Tunisia",
    "AE": "UAE", "SA": "Saudi Arabia", "IR": "Iran", "IQ": "Iraq", "IL": "Israel",
    "TR": "Turkey", "LB": "Lebanon", "JO": "Jordan",
    "KR": "South Korea", "JP": "Japan", "CN": "China", "TW": "Taiwan", "HK": "Hong Kong",
    "TH": "Thailand", "VN": "Vietnam", "PH": "Philippines", "ID": "Indonesia", "MY": "Malaysia",
    "SG": "Singapore", "AU": "Australia", "NZ": "New Zealand",
}

REGIONS: dict[str, list[str]] = {
    "South Asia": ["IN", "PK", "BD", "LK", "NP"],
    "North America": ["US", "CA"],
    "Europe": ["GB", "IE", "FR", "DE", "ES", "IT", "PT", "NL", "BE", "SE", "NO", "DK",
               "FI", "PL", "CZ", "GR", "RO", "HU", "AT", "CH", "UA", "RU"],
    "Latin America": ["BR", "AR", "CO", "CL", "PE", "MX"],
    "Africa": ["NG", "KE", "ZA", "GH", "ET", "EG", "MA", "TN"],
    "Middle East": ["AE", "SA", "IR", "IQ", "IL", "TR", "LB", "JO"],
    "East & Southeast Asia": ["KR", "JP", "CN", "TW", "HK", "TH", "VN", "PH", "ID", "MY", "SG"],
    "Oceania": ["AU", "NZ"],
}

# Language -> assumed country when the channel doesn't declare one.
LANGUAGE_COUNTRY = {
    "hindi": "IN", "tamil": "IN", "telugu": "IN", "kannada": "IN", "malayalam": "IN",
    "marathi": "IN", "bengali": "IN", "punjabi": "IN", "gujarati": "IN", "urdu": "PK",
    "nepali": "NP", "korean": "KR", "japanese": "JP", "chinese": "CN", "thai": "TH",
    "russian": "RU", "arabic": "AE", "spanish": "MX", "portuguese": "BR",
    "french": "FR", "german": "DE", "italian": "IT", "indonesian": "ID", "turkish": "TR",
}


def infer_country(channel_country: str | None, language: str | None) -> str | None:
    if channel_country and channel_country.upper() in COUNTRY_NAMES:
        return channel_country.upper()
    if language:
        return LANGUAGE_COUNTRY.get(language.lower())
    return None


def region_of(country_code: str | None) -> str | None:
    if not country_code:
        return None
    for region, codes in REGIONS.items():
        if country_code.upper() in codes:
            return region
    return None
