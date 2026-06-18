"""One-off cleanup: remove adult/sexual + junk content that slipped into the
library via auto-promoted source channels.

  1. Disable the offending auto-promoted seed channels (stops re-harvesting).
  2. Reject every film from the soft-porn channel(s) outright.
  3. Reject any film whose title matches ADULT_TITLE_PATTERNS, anywhere.

Rejected films drop out of the home page and Browse (both only show
new/shortlisted) and out of the public deploy DB. Idempotent — safe to re-run.
Run from project root:
    .venv/Scripts/python.exe scripts/purge_adult.py
"""
from app.db import SessionLocal
from app.main import ADULT_TITLE_PATTERNS
from app.models import Channel, Film, SeedChannel

# Auto-promoted channels that should never have become curated sources:
# soft-porn, TED-talk/tour uploads, comedy serials.
BAD_CHANNELS = [
    "Monalisa Films Bongaon",          # soft-porn "short films"
    "Short Movies",                    # wife-swap / clickbait
    "Pan American School of Porto Alegre",  # TEDx talks + facility tours
    "Kaumudy",                         # comedy serials / sitcom clips
]
# Channels whose entire catalogue is adult — reject every film, not just matches.
PORN_CHANNELS = [
    "Monalisa Films Bongaon",
    "Garam Garam Movies",
    "Guwahati Boudi Enjoy",
    "DESI MASTI PINDA WALE",
    "Rangili Bhabhi Films",
]


def main() -> None:
    db = SessionLocal()

    # 1. disable the seed channels (resolve name -> source_channel_id -> ref)
    disabled = 0
    for name in BAD_CHANNELS:
        refs = [c for (c,) in db.query(Channel.source_channel_id)
                .filter(Channel.name == name).all()]
        if not refs:
            continue
        n = (db.query(SeedChannel)
             .filter(SeedChannel.channel_ref.in_(refs), SeedChannel.enabled.is_(True))
             .update({SeedChannel.enabled: False}, synchronize_session=False))
        disabled += n
    print(f"seed channels disabled: {disabled}")

    # 2. reject the whole catalogue of the porn channel(s)
    porn_ch_ids = [c for (c,) in db.query(Channel.id)
                   .filter(Channel.name.in_(PORN_CHANNELS)).all()]
    n_porn = 0
    if porn_ch_ids:
        n_porn = (db.query(Film)
                  .filter(Film.channel_id.in_(porn_ch_ids),
                          Film.status.in_(("new", "shortlisted")))
                  .update({Film.status: "rejected"}, synchronize_session=False))
    print(f"porn-channel films rejected: {n_porn}")

    # 3. reject adult-keyword titles anywhere
    n_kw = 0
    for pat in ADULT_TITLE_PATTERNS:
        n_kw += (db.query(Film)
                 .filter(Film.title.ilike(pat),
                         Film.status.in_(("new", "shortlisted")))
                 .update({Film.status: "rejected"}, synchronize_session=False))
    db.commit()
    print(f"adult-keyword films rejected: {n_kw}")

    remaining = db.query(Film).filter(Film.status.in_(("new", "shortlisted"))).count()
    print(f"active films remaining: {remaining}")
    db.close()


if __name__ == "__main__":
    main()
