# Product Roadmap — filmmaker discovery platform

Mission: the fastest way for a scout to find rising filmmakers before anyone
else does — not a YouTube directory, but a talent radar with built-in outreach.

## Where the product is today (shipped)
- YouTube discovery with quota budgeting; velocity + engagement scoring
  normalized by channel size; quality scoring (festival/award/credits markers,
  clip-farm suppression); genre/language/country/film-school detection;
  credits auto-extraction; Netflix-grade browse UI; contact pipeline
  (channel → video descriptions → bio links → web search) with human-approved
  Gmail outreach, guardrails and do-not-contact handling.

## Pillar 1 — Discovery quality (the moat)
1. **LLM curation pass** (key exists, flag off): second-opinion classifier +
   1–10 "craft" rating from title/description/credits. Biggest single quality
   lever available today.
2. **Festival-seeded discovery**: ingest from festival/aggregator channels
   (Omeleto, DUST, Short of the Week, NoBudge, Vimeo Staff Picks via API) and
   treat those creators as gold-standard seeds; expand via their collaborators
   (credits graph).
3. **Channel reputation score**: a channel that produced one festival film is
   likely to produce another — persist reputation, boost future uploads.
4. **Feedback loop**: every shortlist/reject is a label. Weekly job tunes
   keyword weights (or trains a tiny classifier) from accumulated labels.
5. **Thumbnail aesthetics model** (later): score key-art quality from the
   image itself to catch high-production films with weak metadata.

## Pillar 2 — Filmmaker intelligence (from videos to people)
1. **Person pages**: aggregate credits across films → director profile with
   filmography, total traction, festivals, contacts. The atomic unit of the
   product should become the *filmmaker*, not the video.
2. **Credits graph**: who works with whom (DOP ↔ director pairs); surfacing
   "crews to watch".
3. **Contact enrichment v2**: festival-program crawling (public PDFs/pages),
   production-house site discovery, confidence ranking of multiple emails.
4. **CRM depth**: reply sentiment triage, follow-up reminders, notes,
   pipeline stages (scouted → contacted → call → signed).

## Pillar 3 — Scale & freshness
1. **Vimeo source adapter** (official API) — where serious shorts live; spec's
   SourceAdapter interface makes this a drop-in.
2. **Festival calendar awareness**: discovery boost during/after major
   festival announcement windows.
3. **Alerting**: daily digest email / Slack ping when a film crosses a
   velocity or match threshold — scouts shouldn't need to check the app.
4. Postgres + hosted deployment once multi-device access matters.

## Pillar 4 — Product surface
1. Watchlists/collections beyond a single shortlist (per project/client).
2. Hover video preview (muted inline embed on long-hover, Netflix-style).
3. Compare view: two films/channels side by side.
4. Export: CSV/Notion of shortlists with contacts for teams.

## Suggested sequence (effort × impact)
| Step | Items | Why first |
|---|---|---|
| Now | LLM pass, festival-seeded discovery | direct hit on content quality complaints |
| Next | Person pages + credits graph | transforms product identity |
| Then | Vimeo adapter, alerting digest | scale + retention |
| Later | Aesthetics model, CRM stages, deployment | polish & teams |

## Naming candidates
Premium, memorable, global; should say "finding cinema early".
- **FrameRadar** — early-detection metaphor, technical-cool, ownable.
- **FirstCut** — scout meaning ("first look") + film term; warm and premium.
- **CineAtlas** — fits the country/region browsing identity; encyclopedic.
- **Reelscout** — most literal; friendly; close to current name.
- **Lumière** (or "Lumen") — prestige cinema heritage; very premium, less descriptive.
- From the user's list, strongest: **Film Atlas** and **Film Radar**
  (Film Finder/Film Facts feel generic; Frame Finder is tongue-twisty).
Recommendation: **FrameRadar** for a product feel, **CineAtlas** for a
discovery-encyclopedia feel. Renaming is a one-line logo/config change.
