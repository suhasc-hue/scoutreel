# ScoutReel Design Spec — Netflix-pattern study

Distilled from public analyses of Netflix's web product (CXL teardown, design-token
breakdowns, interface analyses, CSS-Tricks animation study). We replicate the
*patterns and reasoning*, not Netflix's assets, logo, or proprietary typeface.

## Core product thinking
1. **Content-first / "the UI is a stage."** Chrome is minimal and dark so artwork
   carries the screen. Thumbnails ARE the interface.
2. **Rows convert a huge library into small, low-stakes choices.** Each rail is a
   ranked shelf; rails themselves are ranked. Personalization decides both the
   order within a row and the order of rows.
3. **Reduce decision friction.** Hover gives more information (preview, metadata,
   actions) without committing to a click. Information appears progressively:
   tile → hover card → detail page.
4. **Red is a signal, not decoration.** Used only for: logo, primary highlights,
   progress, "hot" badges. Everything else is monochrome.
5. **The dark room.** Near-black background mimics a cinema; reduces visual noise.

## Tokens
| Token | Value | Use |
|---|---|---|
| bg | `#141414` | page |
| surface | `#181818` | cards, hover panel, detail body |
| surface-2 | `#2f2f2f` | inputs, secondary buttons |
| border | `#333333` | hairlines |
| text | `#ffffff` | primary |
| text-secondary | `#b3b3b3` | meta |
| text-dim | `#808080` | tertiary |
| red | `#e50914` | brand/CTA accents only |
| red-dark | `#b20710` | hover/depth on red |
| match-green | `#46d369` | match % — the one non-brand color |
| radius | 4px | everything (6px tiles) |

Typography: Netflix Sans is proprietary → stack `"Helvetica Neue", Helvetica,
Segoe UI, Roboto, Arial, sans-serif`. Headings 700–900 with `-0.01em` tracking;
UI labels sentence-case; body ~14–16px/1.4.

## Patterns we implement
- **Nav**: fixed; transparent over the billboard, fades to solid `#141414` on
  scroll. Left: wordmark (red) + text links. Right: expanding search.
- **Billboard**: full-bleed artwork, left-bottom content block (eyebrow rank,
  huge title, meta line with green match %, 2-line synopsis), white ▶ primary +
  translucent gray secondary buttons, and a **bottom fade into the page bg** so
  the hero melts into the first rail.
- **Rails**: title (~1.2rem bold) with "Explore all ›" revealed on hover;
  horizontal scroll, hidden scrollbar, **edge chevron paddles** that page by
  ~90% of the viewport; tiles nearly touching (6px gap).
- **Title cards**: 16:9, radius 6px, no text on artwork except small badges.
  **Hover (the signature interaction)**: after a short delay the card scales up
  and an information panel unfolds beneath it inside the same elevated card:
  row of circular ghost buttons (▶ watch, ＋ shortlist, 👎 not interested,
  ⌄ details) then a meta line (green match %, duration, language badge) and a
  dot-separated tag line (genre · language · film school). Neighbors are
  overlapped (z-lift), not reflowed.
- **Match %**: percentile of our virality score mapped to 55–99%, shown green
  and bold — same role as Netflix's recommendation confidence.
- **Detail page**: hero with double gradient (left vignette + bottom fade),
  content overlapping the hero bottom; left column = match/meta/synopsis +
  actions; right column = "About" facts (channel, genre, language, source);
  then **More Like This** (same genre/language grid); business panels
  (stats, contacts, outreach) follow in surface cards.
- **Top-10 treatment**: red square badge on the hottest tiles.

## Sources studied
- cxl.com/blog/netflix-design (UX teardown: rows, hover, choice architecture)
- explainx.ai Netflix DESIGN.md (token values)
- laurenmk.medium.com Design Talk: Netflix's Interface (nav, hero, tiled pattern)
- css-tricks.com nifty-netflix-animation (hover scale mechanics)
- designpieces.com / brand palette references (#e50914, #b20710, #221f1f)
