"""Backfill credits / quality / festival flags / country for all stored films,
and re-apply the (stricter) classifier to still-'new' films.
Run after upgrading: .venv\\Scripts\\python.exe scripts\\enrich_backfill.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.classify import heuristic_classify  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.enrich import (  # noqa: E402
    assess_quality,
    credits_to_json,
    infer_country,
    parse_credits,
)
from app.models import Film  # noqa: E402


def main() -> None:
    init_db()
    rejected = 0
    with session_scope() as db:
        films = db.query(Film).all()
        for film in films:
            desc = film.description or ""
            r = heuristic_classify(film.title, desc, film.duration_seconds)
            film.genre = r.genre
            film.language = r.language
            film.film_school = r.film_school
            credits = parse_credits(desc)
            film.credits = credits_to_json(credits)
            quality, is_festival, is_award = assess_quality(
                film.title, desc, credits, r.film_school, r.genre,
                film.channel.subscriber_count if film.channel else 0,
            )
            film.quality_score = quality
            film.is_festival = is_festival
            film.is_award = is_award
            film.country = infer_country(
                film.channel.country if film.channel else None, r.language
            )
            if not r.is_short_film and film.status == "new":
                film.status = "rejected"
                rejected += 1
        db.commit()
        with_credits = sum(1 for f in films if f.credits)
        festival = sum(1 for f in films if f.is_festival)
        award = sum(1 for f in films if f.is_award)
        quality_ok = sum(1 for f in films if f.quality_score >= 2 and f.status != "rejected")
    print(f"enriched {len(films)} films: {with_credits} with credits, "
          f"{festival} festival, {award} award, {quality_ok} above quality floor, "
          f"{rejected} newly rejected")


if __name__ == "__main__":
    main()
