"""Showcase builder (F0.2 placeholder; F8.1 will replace this).

Assembles ``dist/showcase/`` — a static site served by GitHub Pages under
``/Qualizeal_Fabric/``. Until F8.1 lands, this ships only the QualiZeal
product shell with a "showcase build pending" banner, so the Pages URL
stops rendering the README through Jekyll immediately.

* Zero-dependency: pure standard library.
* Relative asset paths only (`./assets/...`) — works under any base path.
* Adds `.nojekyll` at the site root so GitHub Pages serves the artifact
  as-is without Jekyll's underscore-file rewriting.

Usage
-----
    python scripts/build_showcase.py --out dist/showcase
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys


ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
BRAND_SRC = os.path.join(ROOT, "knowledge_fabric", "surfaces", "static", "brand")

PLACEHOLDER_INDEX = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QualiZeal Knowledge Fabric</title>
<link rel="icon" href="./assets/brand/qualizeal-mark.jpg">
<style>
  :root{
    --ink:#0D1523;--body:#2B3B4A;--mut:#5A6B7C;--line:#CFE0F0;--panel:#F4F8FC;
    --blue:#0096FF;--blue-tint:#EAF4FF;--coral:#F53E5A;--good:#0CA678;
  }
  *{box-sizing:border-box}
  body{margin:0;background:#FFF;color:var(--body);
       font:14px/1.5 Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
       font-variant-numeric:tabular-nums}
  header{height:56px;border-bottom:1px solid var(--line);display:flex;align-items:center;
         gap:14px;padding:0 22px;background:#FFF;position:sticky;top:0;z-index:5}
  header .mark{height:28px;width:28px;border-radius:6px;background:#EAF4FF;
               display:inline-flex;align-items:center;justify-content:center}
  header .mark img{height:22px;width:22px;object-fit:contain}
  header .wordmark{font-weight:700;color:var(--ink);letter-spacing:.2px;font-size:15px}
  header .sub{color:var(--mut);font-size:12px}
  main{max-width:1080px;margin:0 auto;padding:24px 22px}
  .banner{background:var(--blue-tint);border:1px solid var(--line);border-left:4px solid var(--blue);
          border-radius:12px;padding:16px 18px;color:var(--ink);margin-bottom:18px;font-size:14px}
  .banner b{color:var(--blue)}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
  .card{background:#FFF;border:1px solid var(--line);border-radius:12px;padding:16px 18px;
        box-shadow:0 1px 2px rgba(13,21,35,.06),0 8px 24px rgba(13,21,35,.06)}
  .card h3{margin:0 0 6px;font-size:13px;color:var(--mut);text-transform:uppercase;letter-spacing:.4px}
  .card p{margin:0;color:var(--ink);font-size:14px}
  .lvl{display:inline-flex;gap:6px;flex-wrap:wrap;margin-top:8px}
  .lvl span{display:inline-block;padding:2px 10px;border-radius:8px;font-size:12px;font-weight:600;color:#FFF}
  .lvl .l0{background:var(--good)}.lvl .l1{background:var(--blue)}
  .lvl .l2{background:#7048E8}.lvl .l3{background:var(--coral)}
  .watermark{position:fixed;right:22px;bottom:56px;opacity:.04;pointer-events:none;height:220px}
  .watermark img{height:100%;width:auto}
  footer{border-top:1px solid var(--line);height:40px;display:flex;align-items:center;
         justify-content:space-between;padding:0 22px;color:var(--mut);font-size:12px;background:#FFF}
</style>
</head>
<body>
<header>
  <span class="mark"><img src="./assets/brand/qualizeal-mark.jpg" alt=""></span>
  <span class="wordmark">QualiZeal Knowledge Fabric</span>
  <span class="sub">Internal · Showcase</span>
</header>

<main>
  <div class="banner">
    <b>Showcase build pending.</b>
    This page will be replaced by the interactive product showcase (Workspace, Admin
    and Curator surfaces backed by a browser-side engine) once phase&nbsp;F8 lands.
    In the meantime, the fabric is running against real data on any laptop with
    <code>make up</code>.
  </div>

  <div class="grid">
    <div class="card">
      <h3>What it is</h3>
      <p>QualiZeal's own in-house Knowledge Fabric — documents, products, services,
      GitHub, Jira, Confluence and files behind one governed answer path, with
      voice in and out, analytics that explain which model answered and why.</p>
    </div>
    <div class="card">
      <h3>Four answer levels</h3>
      <p>The router uses no model. Complexity and relationships decide the level;
      the card always says which and why.</p>
      <div class="lvl">
        <span class="l0">Look it up</span><span class="l1">Quote it</span>
        <span class="l2">Summarise it</span><span class="l3">Reason about it</span>
      </div>
    </div>
    <div class="card">
      <h3>One image, three deployments</h3>
      <p>GitHub Pages (browser-side engine), a laptop (<code>make up</code>) and
      AWS from the same code. Runtime stays standard-library only (ADR&#8209;0001).</p>
    </div>
  </div>
</main>

<div class="watermark"><img src="./assets/brand/qualizeal-mark.jpg" alt=""></div>

<footer>
  <span>&copy; QualiZeal. All rights reserved.</span>
  <span>QualiZeal Knowledge Fabric · Internal</span>
</footer>
</body>
</html>
"""


def build(out_dir: str) -> None:
    """Write the placeholder showcase into `out_dir`."""
    out = os.path.abspath(out_dir)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)

    # .nojekyll so Pages serves the artifact untouched
    with open(os.path.join(out, ".nojekyll"), "w") as fh:
        fh.write("")

    # Brand assets (relative path used by index.html)
    brand_dst = os.path.join(out, "assets", "brand")
    os.makedirs(brand_dst, exist_ok=True)
    for name in os.listdir(BRAND_SRC):
        src = os.path.join(BRAND_SRC, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(brand_dst, name))

    # The placeholder index
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(PLACEHOLDER_INDEX)

    print(f"showcase built at {out} ({len(os.listdir(out))} entries)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="build_showcase")
    ap.add_argument("--out", default="dist/showcase", help="output directory")
    args = ap.parse_args(argv)
    build(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
