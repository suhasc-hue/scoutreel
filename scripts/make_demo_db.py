"""Build a sanitized, slimmed library DB for public deployment.

Copies the live scoutreel.db to data/library.db, then:
  - strips ALL private CRM / outreach data (scraped contact emails, drafts,
    connected mail accounts, do-not-contact list) so a public URL never
    exposes filmmaker contact info,
  - keeps only the latest stat + score snapshot per film (ordering, view
    counts and match % still render) — dropping ~90% of the snapshot rows,
  - VACUUMs to reclaim space.

The live scoutreel.db is never modified. Run from the project root:
    .venv/Scripts/python.exe scripts/make_demo_db.py
"""
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "scoutreel.db"
OUT_DIR = ROOT / "data"
OUT = OUT_DIR / "library.db"

PRIVATE_TABLES = (
    "contacts", "contact_leads", "outreach_emails",
    "mail_accounts", "do_not_contact", "classifier_log",
)


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"{SRC} not found")
    OUT_DIR.mkdir(exist_ok=True)

    # fold any WAL into the main file, then take a clean copy
    src = sqlite3.connect(SRC)
    src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    src.close()
    shutil.copy2(SRC, OUT)

    db = sqlite3.connect(OUT)
    cur = db.cursor()
    for t in PRIVATE_TABLES:
        try:
            cur.execute(f"DELETE FROM {t}")
        except sqlite3.OperationalError:
            pass  # table may not exist in older DBs
    # thin time-series to the latest row per film
    cur.execute("DELETE FROM film_stats WHERE id NOT IN "
                "(SELECT MAX(id) FROM film_stats GROUP BY film_id)")
    cur.execute("DELETE FROM score_snapshots WHERE id NOT IN "
                "(SELECT MAX(id) FROM score_snapshots GROUP BY film_id)")
    db.commit()
    cur.execute("VACUUM")
    db.commit()
    films = cur.execute("SELECT COUNT(*) FROM films").fetchone()[0]
    db.close()
    print(f"wrote {OUT}  ({OUT.stat().st_size/1_000_000:.1f} MB, {films} films)")


if __name__ == "__main__":
    main()
