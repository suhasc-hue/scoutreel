# 🎬 ScoutReel

Self-hosted tool for a content scout: discovers short films gaining traction on
YouTube, scores them for virality (view velocity + engagement, normalized by
channel size), extracts the filmmaker's **publicly listed business contact
info**, and drafts personalized outreach emails that **you review and approve
in a dashboard** before they are sent from **your own Gmail account**.

Built per `../SCOUTREEL_SPEC.md`. Hard rules: no scraping of platforms that
prohibit it, no auto-sending without human approval, no collecting personal
(non-business) contact info.

## Quick start

```sh
cd scoutreel
make install          # creates .venv with uv and installs deps
make test             # unit tests (scoring, classifier, extraction, guardrails)
make demo             # seeds 3 fake films + 1 contact so the dashboard has data
make run              # dashboard at http://127.0.0.1:8000
```

Without `make` (e.g. plain Windows PowerShell):

```powershell
uv venv .venv --python 3.12
uv pip install -r requirements.txt --python .venv\Scripts\python.exe
.venv\Scripts\python.exe -m pytest tests\ -q
.venv\Scripts\python.exe scripts\seed_demo.py
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --reload
```

Copy `.env.example` to `.env` and fill in keys as you get them. Everything
degrades gracefully — the dashboard and tests work with zero keys.

## Getting a YouTube API key

1. Go to https://console.cloud.google.com/ and create (or pick) a project.
2. **APIs & Services → Library** → search "YouTube Data API v3" → **Enable**.
3. **APIs & Services → Credentials → Create credentials → API key.**
4. Put it in `.env` as `YOUTUBE_API_KEY=...`.

Free quota is 10,000 units/day. ScoutReel budgets `DAILY_YT_QUOTA_BUDGET=9000`
and caps discovery at 60 searches/day (a search costs 100 units; stat/channel
lookups cost 1). Usage is tracked in the DB per Pacific-time day (Google's
reset clock) and jobs stop gracefully when the budget is hit.

## Creating the Google OAuth client for Gmail

Sending uses *your* Gmail via OAuth — no SMTP passwords.

1. Same Cloud project → **APIs & Services → Library** → enable **Gmail API**.
2. **OAuth consent screen**: External, add yourself as a test user.
3. **Credentials → Create credentials → OAuth client ID → Desktop app.**
4. Download the JSON and save it as `credentials.json` in this folder
   (or point `GMAIL_CREDENTIALS_FILE` at it).

### First-run OAuth walkthrough

Run `make gmail-auth` once: a browser window opens → pick your Google account
→ allow the `gmail.send` and `gmail.readonly` scopes → the token is saved to
`token.json` (gitignored) and refreshed automatically afterwards. Background
jobs never start the OAuth flow themselves (they would hang waiting for a
browser); they log a warning until you've run this once.

## Running

| Command | What it does |
|---|---|
| `make run` | FastAPI dashboard on http://127.0.0.1:8000 |
| `make worker` | APScheduler worker: discovery + stat snapshots every 6h, contact extraction every 12h, Gmail reply polling every 2h, history pruning weekly |
| `make gmail-auth` | One-time interactive Gmail OAuth (required before any send) |
| `make demo` | Seed 3 fake films + 1 fake contact (idempotent) |
| `make backup` | Copy `scoutreel.db` to `backups/` with a timestamp |
| `make test` | Run the unit test suite |

Run the dashboard and the worker in two terminals. Both share `scoutreel.db`
(SQLite; set `DATABASE_URL` to a Postgres URL to swap).

## How outreach stays safe (hard-coded guardrails)

- Nothing sends unless **you approved it in the dashboard** — there is no
  auto-send code path; the send button calls `assert_can_send` first.
- Sends are race-proof: a process-wide lock plus an atomic `approved →
  sending` status claim means a double click (or two tabs) can never send the
  same email twice, and Gmail sends are deliberately **never retried** — a
  lost response must not become a duplicate email.
- Daily cap default 15, **hard max 30** regardless of config.
- Minimum 3 minutes between sends; one email per contact per film, max one
  follow-up after 7 days, never more.
- Do-not-contact list checked at draft time *and* send time; replies containing
  "unsubscribe" add the address automatically.
- Every email ends with how you found them + a one-line opt-out (the footer is
  re-appended programmatically, so template edits can't remove it).
- Contacts whose business-listing context is ambiguous are stored as
  `inferred` and require an extra confirmation click before drafting.

## Security notes

The dashboard has no login — it is built to run on `127.0.0.1` only (the
default in `make run`). Cross-site form posts are rejected by an
Origin/Sec-Fetch-Site check, but do not bind it to a LAN/public interface
without putting real auth in front. Schema changes are applied automatically
on startup for additive changes (new columns/indexes); take a `make backup`
before upgrading anyway.

## Optional LLM features

Set `ANTHROPIC_API_KEY` and `USE_LLM_CLASSIFIER=true` to enable:
- a second-pass classifier ("is this actually a short film?") on
  heuristic-positive candidates, and
- auto-generated `specific_compliment` lines in drafts.

Without it, the heuristic classifier runs alone and drafts contain a
placeholder that **must** be edited before approval (enforced).

## Phase 4 (X / Instagram)

Feature-flagged stubs live in `app/sources/x_api.py` and
`app/sources/instagram.py`, implementing the same `SourceAdapter` interface.
Enable only with official/licensed API access (`ENABLE_X_SOURCE`,
`ENABLE_INSTAGRAM_SOURCE`); browser-automation scraping is deliberately not
implemented and never will be.
