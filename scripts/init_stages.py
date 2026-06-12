"""One-time pipeline-stage backfill: derive each channel's CRM stage from its
existing film statuses and outreach history."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.crm import auto_advance  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.models import Channel, Contact, OutreachEmail  # noqa: E402


def main() -> None:
    init_db()
    moved = 0
    with session_scope() as db:
        for channel in db.query(Channel).all():
            if not channel.pipeline_stage:
                channel.pipeline_stage = "discovered"
            if any(f.status == "shortlisted" for f in channel.films):
                moved += auto_advance(channel, "shortlisted")
            emails = (
                db.query(OutreachEmail)
                .join(Contact, OutreachEmail.contact_id == Contact.id)
                .filter(Contact.channel_id == channel.id)
                .all()
            )
            sent = [e for e in emails if e.sent_at is not None]
            if sent:
                moved += auto_advance(channel, "contacted")
                channel.last_contacted_at = max(e.sent_at for e in sent)
            if any(e.status in ("replied", "opted_out") for e in emails):
                moved += auto_advance(channel, "replied")
            if channel.stage_changed_at is None and channel.pipeline_stage != "discovered":
                channel.stage_changed_at = datetime.now(timezone.utc)
        db.commit()
        total = db.query(Channel).count()
    print(f"initialized stages for {total} channels ({moved} advanced beyond discovered)")


if __name__ == "__main__":
    main()
