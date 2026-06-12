"""Quick schema sanity check used after auto-migration."""
import sqlite3
import sys
from pathlib import Path

db_path = Path(__file__).resolve().parents[1] / "scoutreel.db"
c = sqlite3.connect(db_path)
cols = [r[1] for r in c.execute("PRAGMA table_info(outreach_emails)")]
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
indexes = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%'")]
print("claimed_at column:", "claimed_at" in cols)
print("classifier_log table:", "classifier_log" in tables)
print("ix_* indexes:", len(indexes))
sys.exit(0 if ("claimed_at" in cols and "classifier_log" in tables and indexes) else 1)
