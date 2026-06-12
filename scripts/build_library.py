"""One-time deep harvest of all curated source channels to build the library.

Round-robins the seed channels, paging each uploads playlist (50 videos /
quota unit) until the target film count or quota budget is reached.

Usage: .venv\\Scripts\\python.exe scripts\\build_library.py [target_films]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.jobs import ensure_seed_channels, harvest_seed_channel  # noqa: E402
from app.models import Film, SeedChannel  # noqa: E402
from app.sources.youtube import QuotaExceeded, YouTubeAdapter  # noqa: E402

ROUND_PAGES = 10  # 500 videos per channel per round


def main() -> None:
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000
    init_db()
    logger.remove()
    logger.add(sys.stderr, level="WARNING")  # keep the progress output readable
    with session_scope() as db:
        ensure_seed_channels(db)
        adapter = YouTubeAdapter()
        exhausted: set[int] = set()
        round_no = 0
        while True:
            total = db.query(Film).count()
            if total >= target:
                print(f"target reached: {total} films")
                break
            seeds = [s for s in db.query(SeedChannel).filter_by(enabled=True).all()
                     if s.id not in exhausted]
            if not seeds:
                print(f"all sources exhausted at {total} films")
                break
            round_no += 1
            print(f"-- round {round_no} (library: {total}) --", flush=True)
            for seed in seeds:
                try:
                    added = harvest_seed_channel(
                        db, adapter, seed, max_pages=ROUND_PAGES, resume=True
                    )
                except QuotaExceeded as exc:
                    print(f"quota exhausted: {exc}")
                    return
                except Exception as exc:  # noqa: BLE001
                    print(f"  {seed.handle}: error {exc}")
                    exhausted.add(seed.id)
                    continue
                print(f"  {seed.label or seed.handle}: +{added}", flush=True)
                if seed.next_page_token is None and seed.last_harvested_at is not None:
                    exhausted.add(seed.id)  # playlist fully walked
                if db.query(Film).count() >= target:
                    break
        print(f"final library size: {db.query(Film).count()} films")


if __name__ == "__main__":
    main()
