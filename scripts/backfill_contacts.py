"""Backfill channel contacts from descriptions we already store.

contact_extraction_job only runs for *shortlisted* films' channels, so most
channels never had their description scanned — even though ~150 of them publish
a business email right in the channel/video description. This runs the
description-level extraction (crawl_links=False → no network, fast) over every
channel that has none yet. Re-run any time; idempotent.

    .venv/Scripts/python.exe scripts/backfill_contacts.py
"""
from app.db import SessionLocal
from app.jobs import extract_contacts_for_channel
from app.models import Channel, Contact

db = SessionLocal()
have = {cid for (cid,) in db.query(Contact.channel_id).distinct()}
channels = db.query(Channel).all()
todo = [c for c in channels if c.id not in have]
print(f"{len(channels)} channels, {len(have)} already have a contact, "
      f"scanning {len(todo)} ...")

found_total = 0
for i, ch in enumerate(todo, 1):
    try:
        stored = extract_contacts_for_channel(db, ch, crawl_links=False)
    except Exception as exc:  # keep going
        print(f"  ! {ch.name[:30]}: {exc}")
        continue
    if stored:
        found_total += len(stored)
        print(f"  + {ch.name[:34]:34} {[s.email for s in stored]}")
    if i % 200 == 0:
        print(f"  ...{i}/{len(todo)}")

chans_with = db.query(Contact.channel_id).distinct().count()
print(f"\nDONE — {found_total} new contacts; "
      f"{chans_with} channels now have at least one email.")
db.close()
