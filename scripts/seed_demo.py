"""Seed the DB with 3 fake films + 1 fake contact so the dashboard is
reviewable before any real API keys exist.  Run via `make demo`.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db, session_scope  # noqa: E402
from app.jobs import ensure_seed_queries, score_films  # noqa: E402
from app.models import Channel, Contact, Film, FilmStat  # noqa: E402

NOW = datetime.now(timezone.utc)

DEMO = [
    {
        "channel": dict(name="Luna Frame Films", subscriber_count=2_300,
                        description="Indie shorts from Lisbon. business inquiries: hello@lunaframe.example"),
        "film": dict(title="STATIC — a sci-fi short film", genre="sci-fi",
                     duration_seconds=12 * 60 + 40,
                     description="A radio operator hears tomorrow. Short film, directed by M. Costa. Festival run 2026."),
        "stats": [(48, 9_800, 850, 120), (36, 14_200, 1_300, 210), (24, 22_500, 2_100, 380),
                  (12, 38_000, 3_900, 690), (6, 51_000, 5_200, 940)],
    },
    {
        "channel": dict(name="Backyard Pictures", subscriber_count=48_000,
                        description="Two brothers making films. Contact for work: team@backyardpics.example"),
        "film": dict(title="The Last Delivery | Award Winning Short Film", genre="drama",
                     duration_seconds=19 * 60 + 5,
                     description="A courier's final route. Written and directed by S. Ade. Cast: ..."),
        "stats": [(48, 120_000, 8_000, 900), (36, 131_000, 8_700, 980), (24, 140_000, 9_200, 1_030),
                  (12, 149_000, 9_800, 1_100), (6, 153_000, 10_000, 1_150)],
    },
    {
        "channel": dict(name="Nisha Rao", subscriber_count=410,
                        description="Film school grad. I make things."),
        "film": dict(title="Chai Break — short film (hindi)", genre="comedy",
                     duration_seconds=7 * 60 + 30,
                     description="An office romance over chai. Short film, fiction, student cast."),
        "stats": [(48, 1_200, 210, 60), (36, 4_800, 700, 190), (24, 16_000, 2_300, 540),
                  (12, 41_000, 6_100, 1_300), (6, 62_000, 9_400, 2_050)],
    },
]


def main() -> None:
    init_db()
    with session_scope() as db:
        ensure_seed_queries(db)
        if db.query(Film).filter(Film.source_id.like("demo%")).count():
            print("demo data already present — nothing to do")
            return
        film_ids = []
        for i, d in enumerate(DEMO):
            ch = Channel(source="youtube", source_channel_id=f"UCdemo{i}",
                         url=f"https://www.youtube.com/channel/UCdemo{i}",
                         last_checked_at=NOW, **d["channel"])
            db.add(ch)
            db.flush()
            film = Film(source="youtube", source_id=f"demo{i}",
                        url=f"https://www.youtube.com/watch?v=demo{i}",
                        published_at=NOW - timedelta(days=4),
                        thumbnail_url="", channel_id=ch.id,
                        is_short_film=True, status="new", **d["film"])
            db.add(film)
            db.flush()
            for hours_ago, views, likes, comments in d["stats"]:
                db.add(FilmStat(film_id=film.id,
                                captured_at=NOW - timedelta(hours=hours_ago),
                                views=views, likes=likes, comments=comments))
            film_ids.append(film.id)
        db.flush()
        # 1 fake contact (on the first channel, per its description)
        first_channel = db.query(Channel).filter_by(source_channel_id="UCdemo0").one()
        db.add(Contact(channel_id=first_channel.id, email="hello@lunaframe.example",
                       source_of_email="channel_about", confidence="listed_business",
                       verified_at=NOW))
        db.commit()
        scored = score_films(db, film_ids)
        print(f"seeded 3 demo films, 1 contact, scored {scored} films")


if __name__ == "__main__":
    main()
