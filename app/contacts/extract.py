"""Email + link extraction from channel metadata.

RULE (spec §4): only store emails explicitly published for contact/business
inquiries. When the surrounding context is ambiguous we mark
confidence='inferred' and the dashboard requires an extra confirmation click.
"""
import re
from dataclasses import dataclass

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

# Words that, near an email, indicate it is published for business contact.
BUSINESS_CONTEXT_WORDS = [
    "business",
    "contact",
    "inquiries",
    "inquiry",
    "enquiries",
    "enquiry",
    "booking",
    "bookings",
    "collab",
    "collaboration",
    "work with",
    "press",
    "professional",
    "management",
    "mgmt",
    "reach me",
    "reach out",
    "email me",
    "for work",
    "hire",
    "commissions",
]

# Never store these — placeholders, no-reply, platform addresses.
JUNK_EMAIL_PATTERNS = [
    r"^no-?reply@",
    r"@example\.",
    r"@test\.",
    r"@email\.com$",
    r"@domain\.",
    r"@yourdomain\.",
    r"\.(png|jpe?g|gif|webp|svg|mp4)$",  # image@2x.png style false positives
]

LINK_HUB_DOMAINS = ["linktr.ee", "beacons.ai", "bio.link", "linkin.bio", "carrd.co"]

CONTEXT_WINDOW = 120  # chars on each side of the email to inspect


@dataclass(frozen=True)
class ExtractedEmail:
    email: str
    confidence: str  # 'listed_business' | 'inferred'
    context: str  # snippet that justified the decision


def _clean_email(raw: str) -> str:
    return raw.strip().strip(".,;:!?<>()[]\"'").lower()


def is_junk_email(email: str) -> bool:
    return any(re.search(p, email, re.IGNORECASE) for p in JUNK_EMAIL_PATTERNS)


def extract_emails(text: str) -> list[ExtractedEmail]:
    """Find emails in free text and classify by surrounding context."""
    if not text:
        return []
    results: dict[str, ExtractedEmail] = {}
    for m in EMAIL_RE.finditer(text):
        email = _clean_email(m.group(0))
        if not email or "@" not in email or is_junk_email(email):
            continue
        start = max(0, m.start() - CONTEXT_WINDOW)
        end = min(len(text), m.end() + CONTEXT_WINDOW)
        context = text[start:end].replace("\n", " ").strip()
        context_l = context.lower()
        listed = any(w in context_l for w in BUSINESS_CONTEXT_WORDS)
        confidence = "listed_business" if listed else "inferred"
        prev = results.get(email)
        # Keep the strongest classification seen for a given address.
        if prev is None or (prev.confidence == "inferred" and confidence == "listed_business"):
            results[email] = ExtractedEmail(email=email, confidence=confidence, context=context)
    return list(results.values())


def extract_links(text: str) -> list[str]:
    """Collect URLs from a description, link hubs first (most likely to hold
    a contact page)."""
    if not text:
        return []
    seen: list[str] = []
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?")
        if url not in seen:
            seen.append(url)
    hubs = [u for u in seen if any(d in u.lower() for d in LINK_HUB_DOMAINS)]
    rest = [u for u in seen if u not in hubs]
    return hubs + rest
