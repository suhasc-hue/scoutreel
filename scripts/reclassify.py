"""Re-run the (stricter) heuristic classifier over every stored film.

- Updates genre / language / film_school on all films.
- Films that no longer pass and are still 'new' get status='rejected'
  (shortlisted/contacted films are never auto-rejected — the human decided).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.classify import heuristic_classify  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.models import Film  # noqa: E402


def main() -> None:
    init_db()
    rejected = kept = 0
    with session_scope() as db:
        films = db.query(Film).all()
        for film in films:
            r = heuristic_classify(film.title, film.description or "", film.duration_seconds)
            film.genre = r.genre
            film.language = r.language
            film.film_school = r.film_school
            if not r.is_short_film and film.status == "new":
                film.status = "rejected"
                rejected += 1
            else:
                kept += 1
        db.commit()
    print(f"reclassified {len(films)} films: {kept} kept, {rejected} auto-rejected")


if __name__ == "__main__":
    main()
