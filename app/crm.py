"""Filmmaker pipeline (CRM) — stages, auto-advancement, star ratings.

The atomic unit of the platform is the *filmmaker* (channel). Films are
evidence; the pipeline tracks the relationship.
"""
from datetime import datetime, timezone

PIPELINE_STAGES = [
    "discovered",
    "shortlisted",
    "contacted",
    "replied",
    "in_discussion",
    "collaborating",
    "rejected",
]

STAGE_LABELS = {
    "discovered": "Discovered",
    "shortlisted": "Shortlisted",
    "contacted": "Contacted",
    "replied": "Replied",
    "in_discussion": "In Discussion",
    "collaborating": "Collaborating",
    "rejected": "Rejected",
}

STAGE_COLORS = {
    "discovered": "#54a3ff",
    "shortlisted": "#46d369",
    "contacted": "#f5c542",
    "replied": "#e87c2e",
    "in_discussion": "#b965e0",
    "collaborating": "#e50914",
    "rejected": "#6d6d6e",
}

# Stages a filmmaker reaches only by an explicit human decision — automatic
# events (shortlist/send/reply) never move someone out of these.
MANUAL_STAGES = {"in_discussion", "collaborating", "rejected"}


def auto_advance(channel, target: str) -> bool:
    """Move a channel forward in the pipeline because of an automatic event
    (film shortlisted -> shortlisted, email sent -> contacted, reply ->
    replied). Never moves backwards and never overrides a manual stage.
    Returns True if the stage changed."""
    current = channel.pipeline_stage or "discovered"
    if current in MANUAL_STAGES:
        return False
    order = PIPELINE_STAGES.index
    try:
        if order(target) > order(current):
            channel.pipeline_stage = target
            return True
    except ValueError:
        return False
    return False


def set_stage(channel, target: str) -> bool:
    """Explicit human stage change — any direction, any stage."""
    if target not in PIPELINE_STAGES:
        return False
    channel.pipeline_stage = target
    channel.stage_changed_at = datetime.now(timezone.utc)
    return True


# ----------------------------------------------------------------- stars ---

STAR_LABELS = {
    5: "Outstanding",
    4: "Very Strong",
    3: "Good",
    2: "Average",
    1: "Weak",
    0: "Unrated",
}


def stars_for_match(match: int | None) -> int:
    """Map the match percentile (55-99) to a 1-5 star recommendation.
    0 = not enough data yet."""
    if match is None:
        return 0
    pct = (match - 55) / 44  # back to 0..1 percentile
    if pct >= 0.90:
        return 5
    if pct >= 0.70:
        return 4
    if pct >= 0.45:
        return 3
    if pct >= 0.20:
        return 2
    return 1


def stars_string(n: int) -> str:
    n = max(0, min(n, 5))
    return "★" * n + "☆" * (5 - n)
