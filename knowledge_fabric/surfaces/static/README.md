# Static assets served under `/static/*`

Everything the consoles need at runtime lives here. The HTTP surface serves
it directly (see `surfaces/http_api.py` → `/static/*`) with a
`Cache-Control: public, max-age=3600` header. No path outside this directory
is ever served, so a request like `/static/../etc/passwd` yields a 404.

Nothing here is fetched from the internet at runtime: the served HTML has
no `http://` or `https://` script/link/img tag (enforced by
`tests/test_stage2_ui.py::TestUiNoExternalUrls`).

## Layout

    static/
      assets/brand/   # QualiZeal brand kit (L0.1): logo/ (lockup, mark,
                      #   favicons, watermark — transparent PNGs), tokens.css,
                      #   BRAND.md. First-party assets; the deck template's own
                      #   files. Served from /static/assets/brand/…
      vendor/         # Third-party JS bundles vendored under permissive licences
      fonts/          # Self-hosted webfonts (Inter, OFL) — see fonts/README.md

The demo-era JPEGs (`brand/qualizeal-*.jpeg|jpg`, white backgrounds) were
removed in L0.1; the transparent PNGs under `assets/brand/logo/` replace them.

## Deferred (P1.1)

* **vis-network bundle** — the P1.3 galaxy panel would prefer the real
  `vis-network@9.x` UMD bundle (Apache-2.0 / MIT dual). This session's
  outbound proxy denies every allowed JS CDN, so it is not yet vendored;
  a minimal inline-SVG force-directed renderer stands in until the
  operator drops the real `vis-network.min.js` into `vendor/` (recorded in
  `docs/progress/P1.1.md`).
* **Inter font faces** — the OFL Inter files are also blocked at fetch
  time; the served HTML falls back to the system stack (`Inter,
  system-ui, Segoe UI, Roboto, sans-serif`). Drop the Inter woff2 files
  into `fonts/` and uncomment the `@font-face` block in `ui_common.py`
  when they become available.
