"""FastAPI dashboard — /films, /film/{id}, /outbox, /settings.

Human-in-the-loop: drafting and approval happen here; nothing sends without an
explicit dashboard click, and every send passes guardrails.assert_can_send.
Sends are additionally serialized by a process-wide lock plus an atomic
status claim (approved -> sending) so a double click can never double-send.
"""
import bisect
import re
import threading
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger
from sqlalchemy import func, update
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import get_db, init_db, session_scope
from app.jobs import ensure_seed_queries, extract_contacts_for_channel
from app.models import (
    Channel,
    Contact,
    DoNotContact,
    Film,
    FilmStat,
    OutreachEmail,
    ScoreSnapshot,
    SeedQuery,
)
from app.outreach.drafts import (
    DEFAULT_BULK_TEMPLATE,
    DEFAULT_SUBJECT,
    DEFAULT_TEMPLATE,
    get_setting,
    has_unedited_placeholder,
    render_draft,
    set_setting,
)
from app.outreach.guardrails import (
    GuardrailViolation,
    assert_can_send,
    effective_daily_cap,
    is_do_not_contact,
    sent_today_count,
)

PAGE_SIZE = 48
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with session_scope() as db:
        ensure_seed_queries(db)
    yield


app = FastAPI(title="ScoutReel", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_send_lock = threading.Lock()  # serializes guardrail-check + Gmail call
_crawls_running: set[int] = set()  # channel ids with a background crawl


def compact_number(n) -> str:
    n = int(n or 0)
    for div, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if n >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + suffix
    return str(n)


def yt_thumb(url: str, quality: str = "hq") -> str:
    """Swap a YouTube thumbnail URL to another resolution variant
    (mq=320w, hq=480w, sd=640w, maxres=1280w)."""
    if url and "ytimg.com" in url:
        return re.sub(r"/(?:mq|hq|sd|maxres)?default", f"/{quality}default", url)
    return url


templates.env.filters["compact"] = compact_number
templates.env.filters["yt_thumb"] = yt_thumb


@app.middleware("http")
async def csrf_origin_guard(request: Request, call_next):
    """Reject cross-site POSTs. This is a single-user localhost tool with no
    login, so a malicious webpage could otherwise fire form posts at it."""
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin:
            if urlparse(origin).netloc != request.headers.get("host", ""):
                return PlainTextResponse("cross-origin POST rejected", status_code=403)
        else:
            sfs = request.headers.get("sec-fetch-site")
            if sfs and sfs not in ("same-origin", "none"):
                return PlainTextResponse("cross-site POST rejected", status_code=403)
    return await call_next(request)


def _err(path: str, message: str) -> RedirectResponse:
    return RedirectResponse(f"{path}?error={quote(message)}", status_code=303)


def _info(path: str, message: str) -> RedirectResponse:
    return RedirectResponse(f"{path}?info={quote(message)}", status_code=303)


# ---------- films ----------

def film_view_model(db: Session, film: Film) -> dict:
    """Single-film view (detail page + HTMX fragment)."""
    score = (
        db.query(ScoreSnapshot)
        .filter_by(film_id=film.id)
        .order_by(ScoreSnapshot.captured_at.desc())
        .first()
    )
    stats = (
        db.query(FilmStat).filter_by(film_id=film.id).order_by(FilmStat.captured_at).all()
    )
    contact_count = (
        db.query(Contact).filter_by(channel_id=film.channel_id).count()
        if film.channel_id
        else 0
    )
    return _row(film, score, stats, contact_count, _score_population(db))


def _match_pct(total: float | None, population: list[float]) -> int | None:
    """Map a score to its percentile among all scored films, expressed the way
    a recommendation confidence usually is (55–99%). None until enough data."""
    if total is None or len(population) < 5:
        return None
    idx = bisect.bisect_left(population, total)
    pct = idx / max(len(population) - 1, 1)
    return 55 + round(pct * 44)


def _row(
    film: Film,
    score,
    stats: list[FilmStat],
    contact_count: int,
    population: list[float] | None = None,
) -> dict:
    from app.crm import STAR_LABELS, stars_for_match, stars_string

    match = _match_pct(score.total_score if score else None, population or [])
    stars = stars_for_match(match)
    return {
        "film": film,
        "score": score,
        "stats": stats,
        "sparkline": sparkline_points(stats),
        "views": stats[-1].views if stats else 0,
        "contact_count": contact_count,
        "match": match,
        "stars": stars,
        "stars_str": stars_string(stars),
        "stars_label": STAR_LABELS[stars],
    }


def sparkline_points(stats: list[FilmStat], width: int = 120, height: int = 28) -> str:
    """SVG polyline points for views-per-hour between consecutive snapshots."""
    if len(stats) < 2:
        return ""
    rates = []
    for prev, curr in zip(stats, stats[1:]):
        hours = (curr.captured_at - prev.captured_at).total_seconds() / 3600
        rates.append(max(0.0, (curr.views - prev.views) / hours) if hours > 0 else 0.0)
    peak = max(rates) or 1.0
    n = len(rates)
    pts = []
    for i, r in enumerate(rates):
        x = (i / max(n - 1, 1)) * width
        y = height - (r / peak) * (height - 4) - 2
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/films")


def _subqueries(db: Session):
    """Latest score + peak views per film (avoids N+1)."""
    latest = (
        db.query(
            ScoreSnapshot.film_id.label("fid"),
            func.max(ScoreSnapshot.captured_at).label("mx"),
        )
        .group_by(ScoreSnapshot.film_id)
        .subquery()
    )
    score_sq = (
        db.query(
            ScoreSnapshot.film_id.label("fid"),
            func.max(ScoreSnapshot.total_score).label("total"),
        )
        .join(
            latest,
            (ScoreSnapshot.film_id == latest.c.fid)
            & (ScoreSnapshot.captured_at == latest.c.mx),
        )
        .group_by(ScoreSnapshot.film_id)
        .subquery()
    )
    views_sq = (
        db.query(FilmStat.film_id.label("fid"), func.max(FilmStat.views).label("v"))
        .group_by(FilmStat.film_id)
        .subquery()
    )
    return score_sq, views_sq


def _score_population(db: Session) -> list[float]:
    """Sorted latest total_score per film — the basis for match percentiles."""
    score_sq, _ = _subqueries(db)
    return sorted(t for (t,) in db.query(score_sq.c.total).all() if t is not None)


def _build_view_models(db: Session, films: list[Film]) -> dict[int, dict]:
    """Batch-load stats / latest scores / contact counts for a set of films."""
    population = _score_population(db)
    ids = [f.id for f in films]
    stats_by: dict[int, list[FilmStat]] = defaultdict(list)
    for st in (
        db.query(FilmStat)
        .filter(FilmStat.film_id.in_(ids))
        .order_by(FilmStat.captured_at)
    ):
        stats_by[st.film_id].append(st)
    scores_by: dict[int, ScoreSnapshot] = {}
    for sc in (
        db.query(ScoreSnapshot)
        .filter(ScoreSnapshot.film_id.in_(ids))
        .order_by(ScoreSnapshot.captured_at)
    ):
        scores_by[sc.film_id] = sc  # last one wins (ordered ascending)
    channel_ids = {f.channel_id for f in films}
    contact_counts = dict(
        db.query(Contact.channel_id, func.count(Contact.id))
        .filter(Contact.channel_id.in_(channel_ids))
        .group_by(Contact.channel_id)
    )
    return {
        f.id: _row(f, scores_by.get(f.id), stats_by.get(f.id, []),
                   contact_counts.get(f.channel_id, 0), population)
        for f in films
    }


ROW_LIMIT = 12


GENRE_ROW_ORDER = [
    "drama", "comedy", "thriller", "horror", "sci-fi",
    "romance", "animation", "documentary",
]
MIN_ROW_FILMS = 4


@app.get("/films", response_class=HTMLResponse)
def films_page(
    request: Request,
    status: str = "active",
    sort: str = "score",
    genre: str = "",
    language: str = "",
    q: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
):
    score_sq, views_sq = _subqueries(db)
    base = (
        db.query(Film)
        .options(joinedload(Film.channel))
        .outerjoin(score_sq, Film.id == score_sq.c.fid)
        .outerjoin(views_sq, Film.id == views_sq.c.fid)
        .join(Channel, Film.channel_id == Channel.id)
    )

    browse = any(
        p in request.query_params
        for p in ("status", "sort", "page", "genre", "language", "q", "film_school",
                  "region", "festival", "award", "max_minutes")
    )
    if not browse:
        # ---- netflix-style home: billboard + curated rows ----
        from app.enrich import REGIONS

        try:
            quality_floor = float(get_setting(db, "quality_floor", "2"))
        except ValueError:
            quality_floor = 2.0
        active = base.filter(
            Film.status.in_(("new", "shortlisted")),
            Film.quality_score >= quality_floor,
        )
        by_views = views_sq.c.v.desc().nullslast()
        # quality-weighted heat: score first; until the second snapshot lands,
        # professional markers and views break ties
        by_heat = (score_sq.c.total.desc().nullslast(),
                   Film.quality_score.desc(), by_views)

        row_defs = [
            ("Trending Now", "/films?status=active&sort=score",
             active.order_by(*by_heat).limit(ROW_LIMIT).all()),
            ("Festival Films", "/films?status=active&sort=score&festival=1",
             active.filter(Film.is_festival.is_(True)).order_by(*by_heat).limit(ROW_LIMIT).all()),
            ("Award Winners", "/films?status=active&sort=score&award=1",
             active.filter(Film.is_award.is_(True)).order_by(*by_heat).limit(ROW_LIMIT).all()),
            ("Animated Short Films", "/animation",
             active.filter(Film.genre == "animation").order_by(*by_heat).limit(ROW_LIMIT).all()),
            ("New Discoveries", "/films?status=active&sort=recent",
             active.order_by(Film.discovered_at.desc()).limit(ROW_LIMIT).all()),
            ("Film School Picks", "/films?status=active&sort=score&film_school=1",
             active.filter(Film.film_school.is_(True)).order_by(*by_heat).limit(ROW_LIMIT).all()),
            ("Hidden Gems — Small Channels", "/films?status=active&sort=subs",
             active.filter(Channel.subscriber_count.between(1, 25_000))
             .order_by(*by_heat).limit(ROW_LIMIT).all()),
            ("Under 10 Minutes", "/films?status=active&sort=score&max_minutes=10",
             active.filter(Film.duration_seconds <= 600).order_by(*by_heat).limit(ROW_LIMIT).all()),
        ]
        # region rows (channel country, with language fallback baked in at ingest)
        region_rows = [("Indian Films", ["IN"])] + [(f"{name} Films" if "Films" not in name else name, codes)
                                                    for name, codes in (
            ("South Asian", REGIONS["South Asia"]),
            ("European", REGIONS["Europe"]),
            ("North American", REGIONS["North America"]),
            ("Latin American", REGIONS["Latin America"]),
            ("African", REGIONS["Africa"]),
            ("Middle Eastern", REGIONS["Middle East"]),
            ("East & Southeast Asian", REGIONS["East & Southeast Asia"]),
        )]
        for label, codes in region_rows:
            films = active.filter(Film.country.in_(codes)).order_by(*by_heat).limit(ROW_LIMIT).all()
            if len(films) >= MIN_ROW_FILMS:
                row_defs.append(
                    (label, f"/films?status=active&sort=score&region={codes[0] if len(codes)==1 else label}", films)
                )
        for g in GENRE_ROW_ORDER:
            if g == "animation":
                continue  # has its own dedicated row + hub
            films = active.filter(Film.genre == g).order_by(*by_heat).limit(ROW_LIMIT).all()
            if len(films) >= MIN_ROW_FILMS:
                row_defs.append(
                    (f"{g.title()}", f"/films?status=active&sort=views&genre={g}", films)
                )
        lang_counts = (
            db.query(Film.language, func.count(Film.id))
            .filter(Film.status.in_(("new", "shortlisted")),
                    Film.quality_score >= quality_floor,
                    Film.language.isnot(None))
            .group_by(Film.language)
            .having(func.count(Film.id) >= MIN_ROW_FILMS)
            .order_by(func.count(Film.id).desc())
            .limit(6)
            .all()
        )
        for lang, _count in lang_counts:
            films = active.filter(Film.language == lang).order_by(*by_heat).limit(ROW_LIMIT).all()
            if len(films) >= MIN_ROW_FILMS:
                row_defs.append(
                    (f"{lang.title()} Short Films",
                     f"/films?status=active&sort=views&language={lang}", films)
                )
        row_defs += [
            ("My Shortlist", "/films?status=shortlisted&sort=score",
             base.filter(Film.status == "shortlisted").order_by(*by_heat).limit(ROW_LIMIT).all()),
            ("Contacted", "/films?status=contacted&sort=recent",
             base.filter(Film.status == "contacted").order_by(Film.discovered_at.desc()).limit(ROW_LIMIT).all()),
        ]

        every_film = {f.id: f for _, _, films in row_defs for f in films}
        vms = _build_view_models(db, list(every_film.values()))
        max_score = max((r["score"].total_score for r in vms.values() if r["score"]), default=0.0)
        rows_list = [
            (title, link, [vms[f.id] for f in films])
            for title, link, films in row_defs
            if films
        ]
        trending = row_defs[0][2]
        with_poster = [f for f in trending if f.thumbnail_url] or trending
        billboard = vms[with_poster[0].id] if with_poster else None
        return templates.TemplateResponse(
            request,
            "films.html",
            {
                "view": "home",
                "rows_list": rows_list,
                "billboard": billboard,
                "max_score": max_score,
            },
        )

    # ---- grid browse view (filters + pagination) ----
    page = max(1, page)
    q = base
    if status == "active":
        q = q.filter(Film.status.in_(("new", "shortlisted")))
    elif status != "all":
        q = q.filter(Film.status == status)
    if genre:
        q = q.filter(Film.genre == genre)
    if language:
        q = q.filter(Film.language == language)
    if request.query_params.get("film_school"):
        q = q.filter(Film.film_school.is_(True))
    if request.query_params.get("festival"):
        q = q.filter(Film.is_festival.is_(True))
    if request.query_params.get("award"):
        q = q.filter(Film.is_award.is_(True))
    region = (request.query_params.get("region") or "").strip()
    if region:
        from app.enrich import REGIONS

        codes = REGIONS.get(region, [region.upper()])
        q = q.filter(Film.country.in_(codes))
    try:
        max_minutes = int(request.query_params.get("max_minutes", 0))
    except ValueError:
        max_minutes = 0
    if max_minutes:
        q = q.filter(Film.duration_seconds <= max_minutes * 60)
    search = (request.query_params.get("q") or "").strip()
    if search:
        like = f"%{search}%"
        q = q.filter(
            Film.title.ilike(like)
            | Channel.name.ilike(like)
            | Film.credits.ilike(like)      # directors, cast, producers, studios
            | Film.genre.ilike(like)
            | Film.language.ilike(like)
        )

    if sort == "views":
        q = q.order_by(views_sq.c.v.desc().nullslast())
    elif sort == "recent":
        q = q.order_by(Film.discovered_at.desc())
    elif sort == "subs":
        q = q.order_by(Channel.subscriber_count.asc())
    else:
        q = q.order_by(score_sq.c.total.desc().nullslast())

    total = q.count()
    films = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    vms = _build_view_models(db, films)
    rows = [vms[f.id] for f in films]
    max_score = max((r["score"].total_score for r in rows if r["score"]), default=0.0)
    return templates.TemplateResponse(
        request,
        "films.html",
        {
            "view": "grid",
            "rows": rows,
            "status": status,
            "sort": sort,
            "genre": genre,
            "language": language,
            "q": search,
            "max_score": max_score,
            "page": page,
            "total": total,
            "pages": max(1, -(-total // PAGE_SIZE)),
        },
    )


@app.get("/animation", response_class=HTMLResponse)
def animation_page(request: Request, db: Session = Depends(get_db)):
    """Dedicated animation discovery hub — sub-styles filtered by keyword."""
    score_sq, views_sq = _subqueries(db)
    base = (
        db.query(Film)
        .options(joinedload(Film.channel))
        .outerjoin(score_sq, Film.id == score_sq.c.fid)
        .outerjoin(views_sq, Film.id == views_sq.c.fid)
        .join(Channel, Film.channel_id == Channel.id)
        .filter(Film.status.in_(("new", "shortlisted")), Film.genre == "animation")
    )
    by_heat = (score_sq.c.total.desc().nullslast(),
               Film.quality_score.desc(), views_sq.c.v.desc().nullslast())

    def style(*words):
        from sqlalchemy import or_

        return base.filter(
            or_(*[Film.title.ilike(f"%{w}%") | Film.description.ilike(f"%{w}%") for w in words])
        )

    row_defs = [
        ("All Animated Short Films", "/films?status=active&sort=score&genre=animation",
         base.order_by(*by_heat).limit(ROW_LIMIT).all()),
        ("3D & CGI", "/films?status=active&sort=score&genre=animation&q=3d",
         style("3d", "cgi", "blender", "unreal", "maya").order_by(*by_heat).limit(ROW_LIMIT).all()),
        ("2D & Hand-Drawn", "/films?status=active&sort=score&genre=animation&q=2d",
         style("2d", "hand drawn", "hand-drawn", "traditional animation").order_by(*by_heat).limit(ROW_LIMIT).all()),
        ("Stop Motion", "/films?status=active&sort=score&genre=animation&q=stop+motion",
         style("stop motion", "stop-motion", "claymation").order_by(*by_heat).limit(ROW_LIMIT).all()),
        ("Student Animation", "/films?status=active&sort=score&genre=animation&film_school=1",
         base.filter(Film.film_school.is_(True)).order_by(*by_heat).limit(ROW_LIMIT).all()),
        ("Award-Winning Animation", "/films?status=active&sort=score&genre=animation&award=1",
         base.filter(Film.is_award.is_(True)).order_by(*by_heat).limit(ROW_LIMIT).all()),
    ]
    every_film = {f.id: f for _, _, films in row_defs for f in films}
    vms = _build_view_models(db, list(every_film.values()))
    max_score = max((r["score"].total_score for r in vms.values() if r["score"]), default=0.0)
    rows_list = [(t, l, [vms[f.id] for f in films]) for t, l, films in row_defs if films]
    first = row_defs[0][2]
    with_poster = [f for f in first if f.thumbnail_url] or first
    billboard = vms[with_poster[0].id] if with_poster else None
    return templates.TemplateResponse(
        request,
        "films.html",
        {"view": "home", "rows_list": rows_list, "billboard": billboard,
         "max_score": max_score, "active_page": "animation",
         "rank_label": "Top in Animation"},
    )


@app.get("/api/suggest")
def suggest(q: str = "", db: Session = Depends(get_db)):
    """Realtime search suggestions: titles, channels, credited people,
    genres, languages, regions."""
    from app.enrich import REGIONS

    q = q.strip()
    if len(q) < 2:
        return {"items": []}
    like = f"%{q}%"
    items: list[dict] = []

    for f in (
        db.query(Film)
        .filter(Film.title.ilike(like), Film.status != "rejected")
        .order_by(Film.quality_score.desc())
        .limit(5)
    ):
        items.append({"label": f.title, "sub": "film", "url": f"/film/{f.id}"})
    for c in db.query(Channel).filter(Channel.name.ilike(like)).limit(3):
        items.append({"label": c.name, "sub": "channel",
                      "url": f"/films?status=all&sort=score&q={quote(c.name)}"})
    # credited people / studios
    from app.enrich import credits_from_json

    seen_names: set[str] = set()
    for f in db.query(Film).filter(Film.credits.ilike(like)).limit(20):
        for role, names in credits_from_json(f.credits).items():
            for name in names:
                if q.lower() in name.lower() and name not in seen_names:
                    seen_names.add(name)
                    items.append({"label": name, "sub": role.lower(),
                                  "url": f"/films?status=all&sort=score&q={quote(name)}"})
        if len(seen_names) >= 4:
            break
    for g in GENRE_ROW_ORDER:
        if q.lower() in g:
            items.append({"label": f"{g.title()} films", "sub": "genre",
                          "url": f"/films?status=active&sort=score&genre={g}"})
    for lang in ("hindi", "tamil", "telugu", "kannada", "malayalam", "english",
                 "bengali", "korean", "japanese", "spanish", "french"):
        if q.lower() in lang:
            items.append({"label": f"{lang.title()} short films", "sub": "language",
                          "url": f"/films?status=active&sort=score&language={lang}"})
    for region in REGIONS:
        if q.lower() in region.lower():
            items.append({"label": f"{region} films", "sub": "region",
                          "url": f"/films?status=active&sort=score&region={quote(region)}"})
    return {"items": items[:10]}


@app.post("/films/{film_id}/status", response_class=HTMLResponse)
def set_film_status(
    film_id: int,
    request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    if status not in ("new", "shortlisted", "rejected"):
        raise HTTPException(400, "invalid status")
    film = db.get(Film, film_id)
    if film is None:
        raise HTTPException(404)
    film.status = status
    if status == "shortlisted" and film.channel:
        from app.crm import auto_advance

        auto_advance(film.channel, "shortlisted")
    db.commit()
    if request.headers.get("hx-request"):
        return templates.TemplateResponse(
            request, "_film_card.html", {"r": film_view_model(db, film), "max_score": None}
        )
    # plain form post (e.g. from the detail page) — go back where we came from
    return RedirectResponse(request.headers.get("referer") or "/films", status_code=303)


@app.get("/film/{film_id}", response_class=HTMLResponse)
def film_detail(film_id: int, request: Request, db: Session = Depends(get_db)):
    film = db.get(Film, film_id)
    if film is None:
        raise HTTPException(404)
    vm = film_view_model(db, film)
    contacts = (
        db.query(Contact).filter_by(channel_id=film.channel_id).all()
        if film.channel_id
        else []
    )
    contact_rows = [
        {"contact": c, "dnc": is_do_not_contact(db, c.email)} for c in contacts
    ]
    emails = (
        db.query(OutreachEmail)
        .filter_by(film_id=film.id)
        .order_by(OutreachEmail.created_at.desc())
        .all()
    )
    # "More Like This" — same genre first, fall back to same language.
    similar_q = (
        db.query(Film)
        .options(joinedload(Film.channel))
        .filter(Film.id != film.id, Film.status.in_(("new", "shortlisted")))
    )
    if film.genre:
        similar = similar_q.filter(Film.genre == film.genre).limit(9).all()
    else:
        similar = []
    if len(similar) < 3 and film.language:
        seen = {f.id for f in similar}
        similar += [
            f for f in similar_q.filter(Film.language == film.language).limit(9).all()
            if f.id not in seen
        ][: 9 - len(similar)]
    similar_vms = list(_build_view_models(db, similar).values()) if similar else []
    from app.enrich import COUNTRY_NAMES, credits_from_json, region_of
    from app.models import ContactLead

    leads = (
        db.query(ContactLead).filter_by(channel_id=film.channel_id).limit(5).all()
        if film.channel_id else []
    )
    return templates.TemplateResponse(
        request,
        "film_detail.html",
        {
            "r": vm,
            "credits": credits_from_json(film.credits),
            "country_name": COUNTRY_NAMES.get(film.country or "", None),
            "region": region_of(film.country),
            "contacts": contact_rows,
            "leads": leads,
            "emails": emails,
            "similar": similar_vms,
            "crawling": film.channel_id in _crawls_running,
            "error": request.query_params.get("error", ""),
            "info": request.query_params.get("info", ""),
        },
    )


def _crawl_channel_background(channel_id: int) -> None:
    try:
        with session_scope() as db:
            channel = db.get(Channel, channel_id)
            if channel is not None:
                stored = extract_contacts_for_channel(db, channel)
                logger.info("background crawl for channel {}: {} contacts", channel_id, len(stored))
    except Exception as exc:  # noqa: BLE001
        logger.error("background crawl failed for channel {}: {}", channel_id, exc)
    finally:
        _crawls_running.discard(channel_id)


@app.post("/film/{film_id}/find-contacts")
def find_contacts(film_id: int, db: Session = Depends(get_db)):
    film = db.get(Film, film_id)
    if film is None or film.channel is None:
        raise HTTPException(404)
    if film.channel_id in _crawls_running:
        return _info(f"/film/{film_id}", "Contact search already running — refresh in a moment.")
    _crawls_running.add(film.channel_id)
    threading.Thread(
        target=_crawl_channel_background, args=(film.channel_id,), daemon=True
    ).start()
    return _info(f"/film/{film_id}", "Contact search started — refresh in ~30s.")


@app.post("/film/{film_id}/draft")
def create_draft(
    film_id: int,
    contact_id: int = Form(...),
    confirm_inferred: str = Form(""),
    db: Session = Depends(get_db),
):
    film = db.get(Film, film_id)
    contact = db.get(Contact, contact_id)
    if film is None or contact is None:
        raise HTTPException(404)
    if is_do_not_contact(db, contact.email):
        return _err(f"/film/{film_id}", f"{contact.email} is on the do-not-contact list.")
    # Inferred contacts need an explicit extra confirmation (spec §6.4).
    if contact.confidence == "inferred" and confirm_inferred != "yes":
        return _err(
            f"/film/{film_id}",
            "This email's business-contact status is inferred, not explicit — "
            "tick the ⚠ confirmation box to draft anyway.",
        )
    prior = (
        db.query(OutreachEmail)
        .filter_by(film_id=film.id, contact_id=contact.id)
        .filter(OutreachEmail.status.in_(("draft", "approved", "sending", "sent", "replied")))
        .count()
    )
    subject, body = render_draft(db, film, contact)
    db.add(
        OutreachEmail(
            contact_id=contact.id,
            film_id=film.id,
            subject=subject,
            body=body,
            status="draft",
            is_followup=prior > 0,
        )
    )
    db.commit()
    return RedirectResponse("/outbox", status_code=303)


# ---------- outbox ----------

@app.get("/outbox", response_class=HTMLResponse)
def outbox(request: Request, db: Session = Depends(get_db)):
    # Recover claims stuck in 'sending' (e.g. server crash mid-send) after 10 min.
    stuck_cutoff = datetime.now(timezone.utc).replace(tzinfo=None)
    for e in db.query(OutreachEmail).filter_by(status="sending").all():
        claimed = e.claimed_at.replace(tzinfo=None) if e.claimed_at else None
        if claimed is None or (stuck_cutoff - claimed).total_seconds() > 600:
            e.status = "approved"
            logger.warning("recovered stuck 'sending' email #{} back to approved", e.id)
    db.commit()

    drafts = (
        db.query(OutreachEmail)
        .filter(OutreachEmail.status.in_(("draft", "approved", "sending")))
        .order_by(OutreachEmail.created_at.desc())
        .all()
    )
    sent = (
        db.query(OutreachEmail)
        .filter(OutreachEmail.status.in_(("sent", "replied", "bounced", "opted_out")))
        .order_by(OutreachEmail.sent_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        request,
        "outbox.html",
        {
            "drafts": drafts,
            "sent": sent,
            "sent_today": sent_today_count(db),
            "cap": effective_daily_cap(db),
            "error": request.query_params.get("error", ""),
        },
    )


@app.post("/outreach/{email_id}/update")
def update_draft(
    email_id: int,
    subject: str = Form(...),
    body: str = Form(...),
    db: Session = Depends(get_db),
):
    email_obj = db.get(OutreachEmail, email_id)
    if email_obj is None:
        raise HTTPException(404)
    if email_obj.status not in ("draft", "approved"):
        return _err("/outbox", "Only drafts can be edited.")
    from app.outreach.drafts import ensure_footer

    email_obj.subject = subject.strip()
    email_obj.body = ensure_footer(body.strip(), email_obj.contact.source_of_email)
    email_obj.status = "draft"  # any edit requires re-approval
    db.commit()
    return RedirectResponse("/outbox", status_code=303)


@app.post("/outreach/{email_id}/approve")
def approve_draft(email_id: int, db: Session = Depends(get_db)):
    email_obj = db.get(OutreachEmail, email_id)
    if email_obj is None:
        raise HTTPException(404)
    if email_obj.status != "draft":
        return _err("/outbox", f"Cannot approve from status {email_obj.status!r}.")
    if has_unedited_placeholder(email_obj.body):
        return _err("/outbox", "Edit the compliment placeholder before approving.")
    if is_do_not_contact(db, email_obj.contact.email):
        return _err("/outbox", f"{email_obj.contact.email} is on the do-not-contact list.")
    email_obj.status = "approved"
    db.commit()
    return RedirectResponse("/outbox", status_code=303)


def _perform_send(db: Session, email_id: int, interactive: bool = True) -> str | None:
    """Claim + guardrails + Gmail send + CRM advance. Returns an error message
    or None on success. Caller must hold _send_lock."""
    claimed = db.execute(
        update(OutreachEmail)
        .where(OutreachEmail.id == email_id, OutreachEmail.status == "approved")
        .values(status="sending", claimed_at=datetime.now(timezone.utc))
    ).rowcount
    db.commit()
    if not claimed:
        return "Not sendable — already sent, in flight, or not approved."
    email_obj = db.get(OutreachEmail, email_id)

    def _revert() -> None:
        email_obj.status = "approved"
        email_obj.claimed_at = None
        db.commit()

    try:
        assert_can_send(db, email_obj)
    except GuardrailViolation as exc:
        _revert()
        logger.error("GUARDRAIL BLOCKED send #{}: {}", email_id, exc)
        return f"Blocked: {exc}"

    from app.outreach.gmail_client import GmailClient

    try:
        client = GmailClient(interactive=interactive)
        result = client.send_email(
            to=email_obj.contact.email, subject=email_obj.subject, body=email_obj.body
        )
    except Exception as exc:  # noqa: BLE001
        _revert()
        logger.error("Gmail send failed for #{}: {}", email_id, exc)
        return f"Gmail send failed: {exc}"

    from app.crm import auto_advance

    email_obj.status = "sent"
    email_obj.sent_at = datetime.now(timezone.utc)
    email_obj.gmail_thread_id = result.thread_id
    if email_obj.film:
        email_obj.film.status = "contacted"
    if email_obj.contact and email_obj.contact.channel:
        channel = email_obj.contact.channel
        auto_advance(channel, "contacted")
        channel.last_contacted_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("sent outreach #{} to {}", email_id, email_obj.contact.email)
    return None


@app.post("/outreach/{email_id}/send")
def send_email(email_id: int, db: Session = Depends(get_db)):
    with _send_lock:
        error = _perform_send(db, email_id)
    if error:
        return _err("/outbox", error)
    return RedirectResponse("/outbox", status_code=303)


# ---- queued bulk sending: user approves in the dashboard, the queue sends
# everything approved while honouring the 3-minute spacing + daily cap. ----

_queue_state = {"running": False, "remaining": 0, "sent": 0, "last_error": ""}


def _send_queue_worker() -> None:
    import time as _time

    from app.outreach.guardrails import MIN_SPACING, last_send_at

    try:
        while True:
            with session_scope() as db:
                next_email = (
                    db.query(OutreachEmail)
                    .filter(OutreachEmail.status == "approved")
                    .order_by(OutreachEmail.created_at)
                    .first()
                )
                if next_email is None:
                    break
                _queue_state["remaining"] = (
                    db.query(OutreachEmail).filter(OutreachEmail.status == "approved").count()
                )
                last = last_send_at(db)
                email_id = next_email.id
            if last is not None:
                last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
                wait = (last + MIN_SPACING - datetime.now(timezone.utc)).total_seconds()
                if wait > 0:
                    _time.sleep(min(wait + 1, 200))
            with _send_lock:
                with session_scope() as db:
                    error = _perform_send(db, email_id, interactive=False)
            if error:
                _queue_state["last_error"] = error
                if "cap" in error or "Gmail" in error:
                    logger.warning("send queue stopped: {}", error)
                    break  # daily cap or auth problem — stop, don't spin
            else:
                _queue_state["sent"] += 1
    finally:
        _queue_state["running"] = False
        logger.info("send queue finished: {} sent", _queue_state["sent"])


@app.post("/outbox/send-approved")
def send_all_approved(db: Session = Depends(get_db)):
    pending = db.query(OutreachEmail).filter(OutreachEmail.status == "approved").count()
    if not pending:
        return _err("/outbox", "Nothing approved to send.")
    if _queue_state["running"]:
        return _info("/outbox", f"Queue already running ({_queue_state['remaining']} left).")
    _queue_state.update(running=True, remaining=pending, sent=0, last_error="")
    threading.Thread(target=_send_queue_worker, daemon=True).start()
    return _info("/outbox", f"Sending {pending} approved emails with 3-minute spacing — "
                            "you can leave this page.")


@app.post("/outreach/{email_id}/delete")
def delete_draft(email_id: int, db: Session = Depends(get_db)):
    email_obj = db.get(OutreachEmail, email_id)
    if email_obj is None:
        raise HTTPException(404)
    if email_obj.status not in ("draft", "approved"):
        return _err("/outbox", "Sent emails cannot be deleted.")
    db.delete(email_obj)
    db.commit()
    return RedirectResponse("/outbox", status_code=303)


# ---------- filmmaker pipeline (CRM) ----------

def _channel_card(db: Session, channel: Channel, vms_by_channel: dict) -> dict:
    films = [f for f in channel.films if f.status != "rejected"] or list(channel.films)
    best = vms_by_channel.get(channel.id)
    contact = next((c for c in channel.contacts), None)
    return {
        "channel": channel,
        "film_count": len(films),
        "best": best,
        "contact": contact,
        "contact_count": len(channel.contacts),
        "tags": [t.strip() for t in (channel.tags or "").split(",") if t.strip()],
    }


def _best_film_vms(db: Session, channels: list[Channel]) -> dict[int, dict]:
    """Highest-scored (else most-viewed) film per channel, as a view model."""
    ids = [c.id for c in channels]
    if not ids:
        return {}
    films = (
        db.query(Film)
        .options(joinedload(Film.channel))
        .filter(Film.channel_id.in_(ids), Film.status != "rejected")
        .all()
    )
    vms = _build_view_models(db, films)
    best: dict[int, dict] = {}
    for f in films:
        vm = vms[f.id]
        cur = best.get(f.channel_id)
        key = (vm["score"].total_score if vm["score"] else -1, vm["views"])
        cur_key = (-2, -1) if cur is None else (
            cur["score"].total_score if cur["score"] else -1, cur["views"])
        if cur is None or key > cur_key:
            best[f.channel_id] = vm
    return best


@app.get("/filmmakers", response_class=HTMLResponse)
def filmmakers_page(
    request: Request,
    stage: str = "shortlisted",
    q: str = "",
    sort: str = "recent",
    db: Session = Depends(get_db),
):
    from app.crm import PIPELINE_STAGES, STAGE_COLORS, STAGE_LABELS

    counts = dict(
        db.query(Channel.pipeline_stage, func.count(Channel.id))
        .group_by(Channel.pipeline_stage)
        .all()
    )
    query = db.query(Channel)
    if stage != "all":
        query = query.filter(Channel.pipeline_stage == stage)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(
            Channel.name.ilike(like) | Channel.notes.ilike(like) | Channel.tags.ilike(like)
        )
    if sort == "name":
        query = query.order_by(Channel.name.asc())
    elif sort == "subs":
        query = query.order_by(Channel.subscriber_count.desc())
    elif sort == "priority":
        query = query.order_by(Channel.priority.desc(), Channel.stage_changed_at.desc())
    elif sort == "followup":
        query = query.order_by(Channel.followup_at.asc().nullslast())
    else:  # recent
        query = query.order_by(Channel.stage_changed_at.desc().nullslast(), Channel.id.desc())
    channels = query.limit(120).all()
    best = _best_film_vms(db, channels)
    rows = [_channel_card(db, c, best) for c in channels]
    return templates.TemplateResponse(
        request,
        "filmmakers.html",
        {
            "rows": rows, "stage": stage, "q": q, "sort": sort,
            "counts": counts, "stages": PIPELINE_STAGES,
            "stage_labels": STAGE_LABELS, "stage_colors": STAGE_COLORS,
            "error": request.query_params.get("error", ""),
            "info": request.query_params.get("info", ""),
        },
    )


@app.get("/filmmaker/{channel_id}", response_class=HTMLResponse)
def filmmaker_detail(channel_id: int, request: Request, db: Session = Depends(get_db)):
    from app.crm import PIPELINE_STAGES, STAGE_COLORS, STAGE_LABELS
    from app.enrich import COUNTRY_NAMES, region_of
    from app.models import ContactLead

    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(404)
    films = (
        db.query(Film)
        .options(joinedload(Film.channel))
        .filter(Film.channel_id == channel_id)
        .all()
    )
    vms = list(_build_view_models(db, films).values())
    vms.sort(key=lambda r: (r["score"].total_score if r["score"] else -1, r["views"]), reverse=True)
    contact_rows = [
        {"contact": c, "dnc": is_do_not_contact(db, c.email)} for c in channel.contacts
    ]
    emails = (
        db.query(OutreachEmail)
        .join(Contact, OutreachEmail.contact_id == Contact.id)
        .filter(Contact.channel_id == channel_id)
        .order_by(OutreachEmail.created_at.desc())
        .all()
    )
    leads = db.query(ContactLead).filter_by(channel_id=channel_id).limit(5).all()
    return templates.TemplateResponse(
        request,
        "filmmaker_detail.html",
        {
            "c": channel, "films": vms, "contacts": contact_rows, "emails": emails,
            "leads": leads, "stages": PIPELINE_STAGES, "stage_labels": STAGE_LABELS,
            "stage_colors": STAGE_COLORS,
            "country_name": COUNTRY_NAMES.get((channel.country or "").upper()),
            "region": region_of(channel.country),
            "crawling": channel_id in _crawls_running,
            "tags": [t.strip() for t in (channel.tags or "").split(",") if t.strip()],
            "error": request.query_params.get("error", ""),
            "info": request.query_params.get("info", ""),
        },
    )


@app.post("/filmmakers/{channel_id}/stage")
def set_filmmaker_stage(
    channel_id: int,
    request: Request,
    stage: str = Form(...),
    db: Session = Depends(get_db),
):
    from app.crm import set_stage

    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(404)
    if not set_stage(channel, stage):
        return _err(request.headers.get("referer") or "/filmmakers", "Unknown stage.")
    db.commit()
    return RedirectResponse(request.headers.get("referer") or "/filmmakers", status_code=303)


@app.post("/filmmakers/{channel_id}/notes")
def save_filmmaker_notes(
    channel_id: int,
    request: Request,
    notes: str = Form(""),
    tags: str = Form(""),
    priority: int = Form(0),
    followup_at: str = Form(""),
    db: Session = Depends(get_db),
):
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(404)
    channel.notes = notes.strip()
    channel.tags = ",".join(t.strip() for t in tags.split(",") if t.strip())[:250]
    channel.priority = min(max(priority, 0), 3)
    if followup_at:
        try:
            channel.followup_at = datetime.strptime(followup_at, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    else:
        channel.followup_at = None
    db.commit()
    return RedirectResponse(
        request.headers.get("referer") or f"/filmmaker/{channel_id}", status_code=303
    )


@app.post("/filmmakers/bulk-draft")
async def bulk_draft(request: Request, db: Session = Depends(get_db)):
    """Create approvable outreach drafts for every selected filmmaker using
    the bulk template. Each draft still passes guardrails at send time."""
    form = await request.form()
    ids = [int(v) for v in form.getlist("channel_id")]
    if not ids:
        return _err("/filmmakers", "No filmmakers selected.")
    best = _best_film_vms(db, db.query(Channel).filter(Channel.id.in_(ids)).all())
    created, skipped = 0, []
    for channel_id in ids:
        channel = db.get(Channel, channel_id)
        if channel is None:
            continue
        contact = next(
            (c for c in channel.contacts
             if not is_do_not_contact(db, c.email) and c.confidence == "listed_business"),
            None,
        ) or next((c for c in channel.contacts if not is_do_not_contact(db, c.email)), None)
        vm = best.get(channel_id)
        if contact is None or vm is None:
            skipped.append(channel.name or f"#{channel_id}")
            continue
        if contact.confidence == "inferred":
            skipped.append(f"{channel.name} (inferred contact — draft individually)")
            continue
        film = vm["film"]
        already = (
            db.query(OutreachEmail)
            .filter(OutreachEmail.contact_id == contact.id, OutreachEmail.film_id == film.id,
                    OutreachEmail.status.in_(("draft", "approved", "sending", "sent", "replied")))
            .count()
        )
        if already:
            skipped.append(f"{channel.name} (already drafted)")
            continue
        subject, body = render_draft(db, film, contact, bulk=True)
        db.add(OutreachEmail(contact_id=contact.id, film_id=film.id,
                             subject=subject, body=body, status="draft"))
        created += 1
    db.commit()
    msg = f"Created {created} drafts — review and approve them in the Outbox."
    if skipped:
        msg += f" Skipped {len(skipped)}: {', '.join(skipped[:5])}" + ("…" if len(skipped) > 5 else "")
    return _info("/outbox", msg)


@app.get("/filmmakers/export")
def export_filmmakers(stage: str = "shortlisted", db: Session = Depends(get_db)):
    import csv
    import io

    from fastapi.responses import StreamingResponse

    query = db.query(Channel)
    if stage != "all":
        query = query.filter(Channel.pipeline_stage == stage)
    channels = query.order_by(Channel.name).all()
    best = _best_film_vms(db, channels)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "stage", "priority", "country", "subscribers", "emails",
                "best_film", "stars", "match_pct", "last_contacted", "followup",
                "tags", "notes", "channel_url"])
    for c in channels:
        vm = best.get(c.id)
        w.writerow([
            c.name, c.pipeline_stage, c.priority, c.country or "",
            c.subscriber_count, "; ".join(x.email for x in c.contacts),
            vm["film"].title if vm else "", vm["stars"] if vm else "",
            vm["match"] if vm else "",
            c.last_contacted_at.strftime("%Y-%m-%d") if c.last_contacted_at else "",
            c.followup_at.strftime("%Y-%m-%d") if c.followup_at else "",
            c.tags or "", (c.notes or "").replace("\n", " "), c.url,
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=filmmakers-{stage}.csv"},
    )


# ---------- inbox ----------

@app.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request, db: Session = Depends(get_db)):
    convos = (
        db.query(OutreachEmail)
        .options(joinedload(OutreachEmail.contact).joinedload(Contact.channel),
                 joinedload(OutreachEmail.film))
        .filter(OutreachEmail.status.in_(("sent", "replied", "opted_out", "bounced")))
        .order_by(OutreachEmail.unread.desc(),
                  func.coalesce(OutreachEmail.last_reply_at, OutreachEmail.sent_at).desc())
        .all()
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = []
    for e in convos:
        sent_at = e.sent_at.replace(tzinfo=None) if e.sent_at and e.sent_at.tzinfo else e.sent_at
        followup_due = (
            e.status == "sent" and sent_at is not None and (now - sent_at).days >= 7
        )
        rows.append({"e": e, "followup_due": followup_due})
    unread = sum(1 for e in convos if e.unread)
    return templates.TemplateResponse(
        request, "inbox.html",
        {"rows": rows, "unread": unread,
         "error": request.query_params.get("error", ""),
         "info": request.query_params.get("info", "")},
    )


@app.post("/inbox/{email_id}/read")
def mark_read(email_id: int, db: Session = Depends(get_db)):
    e = db.get(OutreachEmail, email_id)
    if e:
        e.unread = False
        db.commit()
    return RedirectResponse("/inbox", status_code=303)


# ---------- dashboard ----------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    from app.crm import PIPELINE_STAGES, STAGE_COLORS, STAGE_LABELS

    stage_counts = dict(
        db.query(Channel.pipeline_stage, func.count(Channel.id))
        .group_by(Channel.pipeline_stage).all()
    )
    films_total = db.query(Film).count()
    films_active = db.query(Film).filter(Film.status.in_(("new", "shortlisted"))).count()
    sent = db.query(OutreachEmail).filter(OutreachEmail.sent_at.isnot(None)).count()
    replied = db.query(OutreachEmail).filter(
        OutreachEmail.status.in_(("replied", "opted_out"))).count()
    unread = db.query(OutreachEmail).filter(OutreachEmail.unread.is_(True)).count()
    now = datetime.now(timezone.utc)
    followups_due = db.query(Channel).filter(
        Channel.followup_at.isnot(None), Channel.followup_at <= now).count()
    response_rate = round(replied / sent * 100) if sent else 0
    funnel = [(s, STAGE_LABELS[s], stage_counts.get(s, 0), STAGE_COLORS[s])
              for s in PIPELINE_STAGES]
    max_funnel = max((n for _, _, n, _ in funnel), default=1) or 1
    return templates.TemplateResponse(
        request, "dashboard.html",
        {"funnel": funnel, "max_funnel": max_funnel,
         "films_total": films_total, "films_active": films_active,
         "filmmakers_total": sum(stage_counts.values()),
         "sent": sent, "replied": replied, "unread": unread,
         "followups_due": followups_due, "response_rate": response_rate,
         "collaborating": stage_counts.get("collaborating", 0)},
    )


# ---------- settings ----------

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    from app.models import SeedChannel

    s = get_settings()
    queries = db.query(SeedQuery).order_by(SeedQuery.added_at).all()
    seed_channels = db.query(SeedChannel).order_by(SeedChannel.added_at).all()
    dnc = db.query(DoNotContact).order_by(DoNotContact.added_at.desc()).all()
    values = {
        "daily_send_cap": get_setting(db, "daily_send_cap", str(s.daily_send_cap)),
        "signature": get_setting(db, "signature", s.signature or s.user_name),
        "user_pitch": get_setting(db, "user_pitch", s.user_pitch),
        "email_subject_template": get_setting(db, "email_subject_template", DEFAULT_SUBJECT),
        "email_body_template": get_setting(db, "email_body_template", DEFAULT_TEMPLATE),
        "bulk_body_template": get_setting(db, "bulk_body_template", DEFAULT_BULK_TEMPLATE),
        "quality_floor": get_setting(db, "quality_floor", "2"),
        "harvest_pages_per_channel": get_setting(db, "harvest_pages_per_channel", "2"),
        "score_velocity_weight": get_setting(db, "score_velocity_weight", str(s.score_velocity_weight)),
        "score_engagement_weight": get_setting(db, "score_engagement_weight", str(s.score_engagement_weight)),
        "score_recency_weight": get_setting(db, "score_recency_weight", str(s.score_recency_weight)),
        "score_comment_weight": get_setting(db, "score_comment_weight", str(s.score_comment_weight)),
        "recency_window_days": get_setting(db, "recency_window_days", str(s.recency_window_days)),
    }
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"queries": queries, "seed_channels": seed_channels, "dnc": dnc, "v": values,
         "hard_max": 30, "error": request.query_params.get("error", "")},
    )


@app.post("/settings/channels/add")
def add_seed_channel(handle: str = Form(...), label: str = Form(""), db: Session = Depends(get_db)):
    from app.models import SeedChannel

    handle = handle.strip()
    if handle and not db.query(SeedChannel).filter_by(handle=handle).one_or_none():
        db.add(SeedChannel(handle=handle, label=label.strip() or handle))
        db.commit()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/channels/{seed_id}/toggle")
def toggle_seed_channel(seed_id: int, db: Session = Depends(get_db)):
    from app.models import SeedChannel

    s = db.get(SeedChannel, seed_id)
    if s:
        s.enabled = not s.enabled
        db.commit()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/channels/{seed_id}/delete")
def delete_seed_channel(seed_id: int, db: Session = Depends(get_db)):
    from app.models import SeedChannel

    s = db.get(SeedChannel, seed_id)
    if s:
        db.delete(s)
        db.commit()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/save")
async def save_settings(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    allowed = {
        "daily_send_cap", "signature", "user_pitch",
        "email_subject_template", "email_body_template", "bulk_body_template",
        "quality_floor", "harvest_pages_per_channel",
        "score_velocity_weight", "score_engagement_weight",
        "score_recency_weight", "score_comment_weight", "recency_window_days",
    }
    for key in allowed:
        if key in form:
            value = str(form[key]).strip()
            if key == "daily_send_cap":
                try:
                    value = str(min(max(int(value), 0), 30))  # hard max 30
                except ValueError:
                    continue
            set_setting(db, key, value)
    db.commit()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/queries/add")
def add_query(query: str = Form(...), db: Session = Depends(get_db)):
    query = query.strip()
    if query and not db.query(SeedQuery).filter_by(query=query).one_or_none():
        db.add(SeedQuery(query=query))
        db.commit()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/queries/{query_id}/toggle")
def toggle_query(query_id: int, db: Session = Depends(get_db)):
    q = db.get(SeedQuery, query_id)
    if q:
        q.enabled = not q.enabled
        db.commit()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/queries/{query_id}/delete")
def delete_query(query_id: int, db: Session = Depends(get_db)):
    q = db.get(SeedQuery, query_id)
    if q:
        db.delete(q)
        db.commit()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/dnc/add")
def add_dnc(email: str = Form(...), reason: str = Form(""), db: Session = Depends(get_db)):
    email = email.strip().lower()
    if email and not db.query(DoNotContact).filter_by(email=email).one_or_none():
        db.add(DoNotContact(email=email, reason=reason.strip() or "added manually"))
        db.commit()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/dnc/{dnc_id}/delete")
def delete_dnc(dnc_id: int, db: Session = Depends(get_db)):
    row = db.get(DoNotContact, dnc_id)
    if row:
        db.delete(row)
        db.commit()
    return RedirectResponse("/settings", status_code=303)
