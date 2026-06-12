"""One-off: backfill NULLs left by column adds that predate default-carrying
auto-migration."""
import sqlite3
import sys
from pathlib import Path

db_path = Path(__file__).resolve().parents[1] / "scoutreel.db"
c = sqlite3.connect(db_path)
c.execute("UPDATE channels SET priority = 0 WHERE priority IS NULL")
c.execute("UPDATE channels SET notes = '' WHERE notes IS NULL")
c.execute("UPDATE channels SET tags = '' WHERE tags IS NULL")
c.execute("UPDATE channels SET pipeline_stage = 'discovered' WHERE pipeline_stage IS NULL")
c.execute("UPDATE outreach_emails SET unread = 0 WHERE unread IS NULL")
c.execute("UPDATE films SET quality_score = 0 WHERE quality_score IS NULL")
c.execute("UPDATE films SET is_festival = 0 WHERE is_festival IS NULL")
c.execute("UPDATE films SET is_award = 0 WHERE is_award IS NULL")
c.execute("UPDATE films SET film_school = 0 WHERE film_school IS NULL")
c.commit()
print("null backfill done")
sys.exit(0)
