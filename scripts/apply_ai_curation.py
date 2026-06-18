"""Apply the AI-curation workflow result: flag confirmed premium AI films.

Reads ai_keep.json (list of {id, tier, genre}) produced from the curation
workflow, sets Film.is_ai_film + Film.ai_tier for those, and clears the flag
on everything else. Idempotent.

    .venv/Scripts/python.exe scripts/apply_ai_curation.py
"""
import json
from pathlib import Path
from sqlalchemy import func
from app.db import SessionLocal
from app.models import Film

keep = json.loads((Path(__file__).parent / "ai_keep.json").read_text(encoding="utf-8"))
keep_map = {int(k["id"]): k for k in keep}

db = SessionLocal()
# reset, then set the confirmed keepers
db.query(Film).filter(Film.is_ai_film.is_(True)).update(
    {Film.is_ai_film: False, Film.ai_tier: 0}, synchronize_session=False)
n = 0
for fid, k in keep_map.items():
    f = db.get(Film, fid)
    if f is None:
        continue
    f.is_ai_film = True
    try:
        f.ai_tier = max(1, min(5, int(k.get("tier", 3))))
    except (TypeError, ValueError):
        f.ai_tier = 3
    n += 1
db.commit()
print(f"flagged {n} AI films (of {len(keep_map)} in keep list)")
print("tier distribution:")
for t, c in (db.query(Film.ai_tier, func.count(Film.id))
             .filter(Film.is_ai_film.is_(True)).group_by(Film.ai_tier)
             .order_by(Film.ai_tier.desc()).all()):
    print(f"  tier {t}: {c}")
# show the top premium picks
print("top tier-5/4 AI films:")
for f in (db.query(Film).filter(Film.is_ai_film.is_(True), Film.ai_tier >= 4)
          .order_by(Film.ai_tier.desc()).limit(15).all()):
    print(f"  [{f.ai_tier}] {(f.title or '')[:64]}")
db.close()
