"""Backfill Film.thumb_ok by HEAD-checking YouTube thumbnails.

A 404 on hqdefault.jpg means the video was deleted/made private — its poster
will never load, so we mark it dead and the home page hides it. Transient
errors leave thumb_ok untouched (None) for a later retry. Concurrent and
idempotent: re-running only re-checks rows still NULL (pass --all to recheck).
"""
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import or_

from app.db import SessionLocal, init_db
from app.models import Channel, Film, SeedChannel

RECHECK_ALL = "--all" in sys.argv


def thumb_url(f: Film) -> str | None:
    if f.thumbnail_url and "ytimg.com" in f.thumbnail_url:
        return re.sub(r"/(?:mq|hq|sd|maxres)?default", "/hqdefault", f.thumbnail_url)
    if f.source == "youtube" and f.source_id:
        return f"https://i.ytimg.com/vi/{f.source_id}/hqdefault.jpg"
    return None


def check(url: str) -> bool | None:
    """True = live, False = 404 (dead), None = unknown/transient."""
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        return False if e.code == 404 else None
    except Exception:
        return None


def main() -> None:
    init_db()  # auto-migrate adds the thumb_ok column if missing
    db = SessionLocal()
    refs = [r for (r,) in db.query(SeedChannel.channel_ref)
            .filter(SeedChannel.channel_ref.isnot(None)).all()]
    cur_ids = [c for (c,) in db.query(Channel.id)
               .filter(Channel.source_channel_id.in_(refs)).all()] if refs else []
    src = [Film.is_award.is_(True), Film.is_festival.is_(True), Film.film_school.is_(True)]
    if cur_ids:
        src.insert(0, Film.channel_id.in_(cur_ids))
    q = db.query(Film).filter(Film.status.in_(("new", "shortlisted")), or_(*src))
    if not RECHECK_ALL:
        q = q.filter(Film.thumb_ok.is_(None))
    films = q.all()
    print(f"Checking {len(films)} cinematic-pool thumbnails "
          f"({'all' if RECHECK_ALL else 'unchecked only'})...", flush=True)

    targets = [(f.id, thumb_url(f)) for f in films]
    targets = [(fid, u) for fid, u in targets if u]
    results: dict[int, bool | None] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=64) as ex:
        futs = {ex.submit(check, u): fid for fid, u in targets}
        for fut in as_completed(futs):
            results[futs[fut]] = fut.result()
            done += 1
            if done % 1000 == 0:
                print(f"  {done}/{len(targets)}", flush=True)

    live = dead = unknown = 0
    by_id = {f.id: f for f in films}
    for fid, ok in results.items():
        if ok is None:
            unknown += 1
            continue
        by_id[fid].thumb_ok = ok
        if ok:
            live += 1
        else:
            dead += 1
    db.commit()
    print(f"DONE — live: {live}, dead(hidden): {dead}, unknown(left null): {unknown}")
    db.close()


if __name__ == "__main__":
    main()
