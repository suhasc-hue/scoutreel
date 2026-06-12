"""Outreach draft generation — template rendering + personalization.

Every body MUST end with the transparency footer: how the sender found them
and a one-line opt-out. The footer is appended programmatically so editing the
template can never remove it.
"""
import re

from jinja2 import Template
from loguru import logger
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Contact, Film, Setting

COMPLIMENT_PLACEHOLDER = "[ADD A SPECIFIC COMPLIMENT — edit before approving]"

DEFAULT_SUBJECT = "Loved your short film “{{ film_title }}”"

DEFAULT_TEMPLATE = """Hi {{ filmmaker_name }},

I just watched “{{ film_title }}” and wanted to reach out. {{ specific_compliment }}

{{ user_pitch }}

If you're open to it, I'd love to chat — no obligation, of course.

{{ signature }}"""

SOURCE_DESCRIPTIONS = {
    "channel_about": "the contact email listed publicly on your channel",
    "bio_link": "the contact info in your public bio links",
    "website": "the contact page on your website",
}

FOOTER_MARKER = "I found your email via"

FOOTER_TEMPLATE = (
    "\n\n--\n"
    "I found your email via {source_description}. "
    "Reply 'unsubscribe' and I won't contact you again."
)


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row and row.value else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def generate_compliment(film: Film) -> str:
    """LLM-generated one-liner when enabled; otherwise a placeholder that
    forces a manual edit before approval."""
    settings = get_settings()
    if not (settings.use_llm_classifier and settings.anthropic_api_key):
        return COMPLIMENT_PLACEHOLDER
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        msg = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=120,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Write ONE sincere, specific compliment sentence (max 30 words) "
                        "about this short film, suitable for a professional outreach "
                        "email. No flattery clichés, no quotes around the output.\n\n"
                        f"Title: {film.title}\nDescription: {film.description[:1500]}"
                    ),
                }
            ],
        )
        line = msg.content[0].text.strip().strip('"')
        return line or COMPLIMENT_PLACEHOLDER
    except Exception as exc:  # noqa: BLE001
        logger.warning("compliment generation failed: {}", exc)
        return COMPLIMENT_PLACEHOLDER


def ensure_footer(body: str, source_of_email: str) -> str:
    """Append the mandatory transparency + opt-out footer if missing.
    Checks the footer's own marker phrase — merely writing the word
    'unsubscribe' elsewhere in the body does not satisfy the requirement."""
    if FOOTER_MARKER in body and "unsubscribe" in body.lower():
        return body
    desc = SOURCE_DESCRIPTIONS.get(source_of_email, "your publicly listed contact info")
    return body.rstrip() + FOOTER_TEMPLATE.format(source_description=desc)


def has_unedited_placeholder(body: str) -> bool:
    return bool(re.search(r"\[ADD A SPECIFIC COMPLIMENT", body))


def render_draft(db: Session, film: Film, contact: Contact) -> tuple[str, str]:
    """Returns (subject, body) with the footer guaranteed present."""
    settings = get_settings()
    subject_tpl = get_setting(db, "email_subject_template", DEFAULT_SUBJECT)
    body_tpl = get_setting(db, "email_body_template", DEFAULT_TEMPLATE)
    signature = get_setting(db, "signature", settings.signature) or settings.user_name
    user_pitch = get_setting(db, "user_pitch", settings.user_pitch)

    variables = {
        "filmmaker_name": (film.channel.name if film.channel else "") or "there",
        "film_title": film.title,
        "specific_compliment": generate_compliment(film),
        "user_pitch": user_pitch,
        "signature": signature,
    }
    subject = Template(subject_tpl).render(**variables).strip()
    body = Template(body_tpl).render(**variables).strip()
    body = ensure_footer(body, contact.source_of_email)
    return subject, body
