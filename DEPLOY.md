# Deploying ScoutReel to Render

This repo is ready to deploy as a Docker web service. A **sanitized** copy of
the film library (`data/library.db`) is bundled into the image, so the hosted
site has content immediately — with **no private contact/outreach data**.

## First time

1. Push this repo to GitHub (or GitLab/Bitbucket).
2. Go to <https://render.com> → **New +** → **Blueprint**, and connect the repo.
   Render reads `render.yaml` and creates the `scoutreel` web service.
3. **(Recommended)** Open the service → **Environment** → set `SITE_PASSWORD`
   to any password. The whole site is then behind HTTP Basic auth (any
   username + that password). Leave it unset to make the site fully public.
4. **Create Web Service** / **Apply**. First build takes a few minutes; you get
   a `https://scoutreel-xxxx.onrender.com` URL to share.

## Updating

- **Code**: push to the connected branch → redeploy (Render → Manual Deploy, or
  flip `autoDeploy: true` in `render.yaml`).
- **Library content**: regenerate and commit the public DB, then redeploy:
  ```
  .venv/Scripts/python.exe scripts/make_demo_db.py   # writes data/library.db
  git add -f data/library.db && git commit -m "Refresh public library" && git push
  ```

## Trade-offs (free tier)

- **Sleeps when idle** — first request after ~15 min of inactivity takes ~50s
  to wake. The $7/mo plan stays always-on.
- **Read-mostly** — the filesystem is ephemeral, so shortlisting / status
  changes don't survive a restart or redeploy. Browsing is unaffected.
- **Web only** — discovery jobs and Gmail outreach are **not** run here (they
  need API keys + your full DB); they stay on your local machine.

## What ships vs. what stays local

| Ships to Render (`data/library.db`) | Stays local only (`scoutreel.db`) |
|---|---|
| Films, channels, latest stats/scores | Scraped contact emails, outreach drafts |
| Genre / region / language / quality | Connected Gmail accounts, do-not-contact |
| Everything the public site renders | Full per-film snapshot history |
