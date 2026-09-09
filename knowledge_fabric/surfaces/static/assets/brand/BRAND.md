# QualiZeal brand kit for the Knowledge Fabric product

Extracted from the QualiZeal deck template (the same assets and tokens the leadership decks use). Copy `logo/` and `tokens.css` into `knowledge_fabric/surfaces/static/assets/brand/` and reference them from every page.

## The name
- Always **QualiZeal** — capital Q, capital Z, one word. Never "Qualizeal", "QualiZEAL", "Quali Zeal" or "QZ" in user-facing text (repository slugs are the only exception).
- Product name in full on first use per screen and in the `<title>`: **QualiZeal Knowledge Fabric**. Shorten to **Knowledge Fabric** in navigation only.

## The logo
| File | Use |
|---|---|
| `logo/qualizeal-lockup.png` (1674×204, transparent) | Wordmark, top-left of every page at 148×18 px (retina-safe); links to the Workspace. Never stretched, never recoloured, never on a dark background in the product. |
| `logo/qualizeal-lockup@1x.png` | Same, half-size source for e-mail/PDF exports. |
| `logo/qualizeal-mark.png` (512×512, transparent) | The mark alone: favicon, avatar fallback, loading state, the watermark. |
| `logo/qualizeal-watermark.png` | The mark pre-rendered at 4 % opacity for contexts without CSS opacity (PDF export, e-mail). In the app use the CSS `.qz-watermark` class with the full-opacity mark instead. |
| `logo/favicon-{16,32,48,180,512}.png` | Favicons and app icons. |

Clear space around the lockup: at least the height of the "Q" on all sides. Minimum width 120 px.

## Watermark and copyright (every page)
- Watermark: the mark, bottom-right of the content area, 220 px, **4 % opacity**, never over a chart legend or a table.
- Footer: `© QualiZeal. All rights reserved.` on the left · `QualiZeal Knowledge Fabric · Internal` in the centre · version and build on the right. 12 px, `--qz-soft`.
- Browser tab: `QualiZeal Knowledge Fabric — <page>` with `favicon-32.png`.

## Colour
Brand blue `#0096FF` and brand coral `#F53E5A` are the only two brand colours (sampled from the logo itself). Everything else is neutral. Level colours are fixed: Level 0 green `#0CA678` · Level 1 blue `#0096FF` · Level 2 purple `#7048E8` · Level 3 coral `#F53E5A`. White page background always; the navy canvas from the earlier demos is used **only** inside the galaxy panel.

## Type
Decks use Calibri. The product uses **Inter** (OFL, self-hosted) because Calibri is not licensed for the web; sizes: page title 20/600, section 14/600, body 14/400, label 12/500 uppercase tracking .04em, KPI number 28/600 tabular.

## Voice of the interface
Labels, not explanations. No headers that narrate what a screen is for. One-line hints under a chart title at most. Level words (Look it up · Quote it · Summarise it · Reason about it), never numbers; no technical terms on screen.
