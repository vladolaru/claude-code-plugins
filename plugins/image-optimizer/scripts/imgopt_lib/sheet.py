"""`imgopt sheet`: 1:1 tiles for the agent and a comparison page for the human.

The session wrote "no banding" from contact sheets an image viewer had
shrunk from 2400 to 1000 px. Tiles here are never wider than about 1000 px,
are cut at 1:1 from windows the script chooses (most error on smooth areas,
most error overall, and for alpha images an edge on dark grey), and show
reference | pick | amplified difference. The smooth-area tile is required
where banding is reported but not gated, and every tile when the format
changed or is uncalibrated. The page shows before, pick and an
optional alternative, with a 100% toggle that scrolls panes together.

A file whose pixels cannot be read gets its card but no tiles; build()
appends the reason to ``problems`` and carries on, and the command exits 1.
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .candidates import kb, load_records, pick_of
from .imaging import READ_FAILURES, display_pixels, flatten
from .ladder import UsageError
from .metrics import diff_max, smooth_mask

TILE = 320
SMALL_TILE = 160
SMALL_IMAGE = 400
AMPLIFY = 8
GAP = 6
LABEL_H = 18


@dataclass(frozen=True)
class Tile:
    path: Path
    source: str
    window: str
    required: bool


def _best_window(score: Image.Image, w: int, h: int) -> tuple[int, int, int, int]:
    width, height = score.size
    block = max(8, min(w, h) // 4)
    gw, gh = max(1, math.ceil(width / block)), max(1, math.ceil(height / block))
    grid = score.resize((gw, gh), Image.BOX).load()
    bw, bh = max(1, w // block), max(1, h // block)
    best, bx, by = -1, 0, 0
    for y in range(max(1, gh - bh + 1)):
        for x in range(max(1, gw - bw + 1)):
            total = sum(grid[i, j] for i in range(x, min(gw, x + bw)) for j in range(y, min(gh, y + bh)))
            if total > best:
                best, bx, by = total, x, y
    left = max(0, min(bx * block, width - w))
    top = max(0, min(by * block, height - h))
    return left, top, left + w, top + h


def _compose(ref: Image.Image, new: Image.Image, box, scale: int) -> Image.Image:
    a, b = ref.crop(box), new.crop(box)
    diff = diff_max(a, b).point(lambda v: min(255, v * AMPLIFY)).convert("RGB")
    w, h = a.size
    canvas = Image.new("RGB", (3 * w + 2 * GAP, h + LABEL_H), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    for i, (img, label) in enumerate(((a, "reference"), (b, "pick"), (diff, f"difference x{AMPLIFY}"))):
        x = i * (w + GAP)
        draw.text((x + 2, 2), label, fill=(0, 0, 0))
        canvas.paste(img, (x, LABEL_H))
    if scale == 2:
        canvas = canvas.resize((canvas.width * 2, canvas.height * 2), Image.NEAREST)
    return canvas


def every_tile_required(record: dict) -> bool:
    """Every tile of a lossy pick is required viewing when its metrics are uncalibrated for the output
    format (WebP, AVIF) or the format changed: the spec makes the sheet review mandatory for a convert."""
    return bool(record["uncalibrated"]) or record["format"] != record["source"]["format"]


def needs_tiles(record: dict) -> bool:
    """A lossy pick that will be applied is the only thing a human must approve, so the only thing tiled."""
    chosen = pick_of(record)
    return bool(chosen) and chosen["kind"] == "lossy" and record["verdict"] == "apply"


def _tiles_for(folder: Path, record: dict, tiles_dir: Path) -> list[Tile]:
    chosen = pick_of(record)
    ref_rgba = Image.open(folder / "reference.png").convert("RGBA")
    new_rgba = display_pixels(folder / chosen["file"])
    ref_w, new_w = flatten(ref_rgba, "white"), flatten(new_rgba, "white")
    diff = diff_max(ref_w, new_w)
    windows = {"smooth": (ImageChops.multiply(diff, smooth_mask(ref_w)), ref_w, new_w),
               "overall": (diff, ref_w, new_w)}
    alpha = ref_rgba.getchannel("A")
    if alpha.getextrema()[0] < 255:
        edges = ImageChops.subtract(alpha.filter(ImageFilter.MaxFilter(3)), alpha.filter(ImageFilter.MinFilter(3)))
        windows["edge"] = (edges, flatten(ref_rgba, "dark"), flatten(new_rgba, "dark"))
    name = folder.name
    out: list[Tile] = []
    sizes = [("", TILE, 1)]
    if ref_rgba.width <= SMALL_IMAGE:  # the displayed width, after any --resize
        sizes.append(("@2x", SMALL_TILE, 2))
    for window, (score, ref, new) in windows.items():
        for suffix, side, scale in sizes:
            w, h = min(side, ref.width), min(side, ref.height)
            box = _best_window(score, w, h)
            path = tiles_dir / f"{name}__{window}{suffix}.png"
            _compose(ref, new, box, scale).save(path)
            required = every_tile_required(record) or (window == "smooth" and not chosen.get("band_gated"))
            out.append(Tile(path, record["source"]["path"], window + suffix, bool(required)))
    return out


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Image comparison</title>
<style>
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#d2d2d7;--pane:#fff}
@media (prefers-color-scheme: dark){:root{--bg:#161617;--fg:#f5f5f7;--muted:#a1a1a6;--line:#3a3a3c}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.4 -apple-system,system-ui,sans-serif}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-bottom:12px}
.banner{background:#fff4ce;color:#5c4400;padding:8px 12px;border-radius:6px;margin-bottom:8px}
.card{border:1px solid var(--line);border-radius:8px;margin-bottom:16px;padding:12px}
.card h3{margin:0;font-size:15px;word-break:break-all}
.meta{color:var(--muted);font-size:13px;margin:4px 0 8px}
.panes{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:8px}
.pane{overflow:auto;max-height:70vh;border:1px solid var(--line);background:var(--pane)}
.pane img{display:block;max-width:100%}
body.actual .pane img{max-width:none}
body.dark{--pane:#282828}
.label{font-size:12px;color:var(--muted);padding:2px 4px}
</style></head><body>
<div id="banners"></div>
<header><strong id="totals"></strong>
<label><input type="checkbox" id="actual"> 100% (synced scroll)</label>
<label><input type="checkbox" id="dark"> dark background</label>
<label>Sort <select id="sort"><option value="bytes">bytes saved</option><option value="pct">% saved</option><option value="name">name</option></select></label>
<label>Show <select id="filter"><option value="all">all</option><option value="apply">to apply</option><option value="lossy">lossy picks</option><option value="untouched">untouched</option></select></label>
</header><main id="cards"></main>
<script>
const DATA = __DATA__;
function render(){
  const sort=document.getElementById('sort').value, filter=document.getElementById('filter').value;
  const rows=DATA.files.filter(f=>filter==='all'||(filter==='apply'&&f.verdict==='apply')||(filter==='untouched'&&f.verdict!=='apply')||(filter==='lossy'&&f.pick_kind==='lossy'));
  rows.sort((a,b)=>sort==='name'?a.name.localeCompare(b.name):sort==='pct'?(b.saved/(b.before||1))-(a.saved/(a.before||1)):b.saved-a.saved);
  const main=document.getElementById('cards'); main.textContent='';
  for(const f of rows){
    const card=document.createElement('section'); card.className='card';
    const h=document.createElement('h3'); h.textContent=f.name;
    const m=document.createElement('div'); m.className='meta'; m.textContent=f.meta;
    const panes=document.createElement('div'); panes.className='panes';
    for(const p of f.panes){
      const w=document.createElement('div'); w.className='pane';
      const l=document.createElement('div'); l.className='label'; l.textContent=p.label;
      const i=document.createElement('img'); i.src=p.src; i.alt=p.label+': '+f.name;
      w.append(l,i); panes.append(w);
    }
    const all=[...panes.querySelectorAll('.pane')];
    all.forEach(p=>p.addEventListener('scroll',()=>all.forEach(o=>{if(o!==p){o.scrollTop=p.scrollTop;o.scrollLeft=p.scrollLeft;}})));
    card.append(h,m,panes); main.append(card);
  }
}
document.getElementById('totals').textContent=DATA.totals;
for(const b of DATA.banners){const d=document.createElement('div');d.className='banner';d.textContent=b;document.getElementById('banners').append(d);}
document.getElementById('actual').onchange=e=>document.body.classList.toggle('actual',e.target.checked);
document.getElementById('dark').onchange=e=>document.body.classList.toggle('dark',e.target.checked);
document.getElementById('sort').onchange=render;
document.getElementById('filter').onchange=render;
render();
</script></body></html>
"""


def _meta(record: dict, chosen: dict | None) -> str:
    s = record["source"]
    dims = f"{s['width']}x{s['height']} " if s.get("width") else ""
    if not chosen:
        return f"{dims}{s['format']} · {kb(s['size'])} · untouched ({record['verdict_reason']})"
    if chosen.get("identical"):
        quality = "pixel-identical"
    elif "ssim" in chosen:
        quality = (f"SSIM {chosen['ssim']:.4f} · ss2 {chosen['ss2']:.1f} · banding {chosen['band']:.1f} "
                   f"({'gated' if chosen.get('band_gated') else 'reported'})")
    else:
        quality = "not measured"
    return (f"{dims}{s['format']} -> {record['format']} · {kb(s['size'])} -> {kb(chosen['size'])} · "
            f"{chosen['label']} · {quality} · {record['verdict']} ({record['verdict_reason']})")


def build(out: Path, *, alt: str | None = None,
          problems: list[str] | None = None) -> tuple[list[Tile], Path]:
    """Write the tiles and the page under out/_sheet.

    Refuses (UsageError) an ``out`` that is not a folder of `candidates` results, before writing anything.
    Per-file failures, and an ``alt`` label no record has, are appended to ``problems``.
    """
    out = Path(out)
    records = load_records(out) if out.is_dir() else []
    if not records:
        raise UsageError(f"{out}: no candidates results here (expected <folder>/metrics.json from "
                         "`imgopt candidates --out`); check the path")
    sheet_dir = out / "_sheet"
    tiles_dir = sheet_dir / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    tiles: list[Tile] = []
    files = []
    before = after = 0
    banners: list[str] = []
    alt_found = False
    for folder, record in records:
        chosen = pick_of(record)

        def url(name: str) -> str:
            return quote(f"../{folder.name}/{name}")

        panes = [{"label": "Before", "src": url(p.name)} for p in sorted(folder.glob("source.*"))[:1]]
        if not panes and problems is not None:
            problems.append(f"{folder.name}: source copy is missing, so its card has no Before pane")
        if chosen:
            panes.append({"label": f"Pick: {chosen['label']}", "src": url(chosen["file"])})
        alt_rec = next((c for c in record["candidates"] if c["label"] == alt and c.get("file")), None) if alt else None
        if alt_rec:
            alt_found = True
            panes.append({"label": f"Alternative: {alt}", "src": url(alt_rec["file"])})
        saved = record["source"]["size"] - chosen["size"] if chosen and record["verdict"] == "apply" else 0
        if record["verdict"] == "apply" and chosen:
            before += record["source"]["size"]
            after += chosen["size"]
        files.append({"name": record["source"]["path"], "meta": _meta(record, chosen), "panes": panes,
                      "verdict": record["verdict"], "pick_kind": chosen["kind"] if chosen else None,
                      "before": record["source"]["size"], "saved": saved})
        if record["uncalibrated"]:
            banners.append("UNCALIBRATED FORMAT: WebP/AVIF output was never calibrated against these gates; "
                           "judge every file by eye.")
        if record.get("waived"):
            banners.append("Waived tools (fewer candidates were tried): " + ", ".join(record["waived"]))
        if needs_tiles(record):
            try:
                tiles += _tiles_for(folder, record, tiles_dir)
            except READ_FAILURES as error:
                if problems is not None:
                    problems.append(f"{folder.name}: no tiles: {error}")
    if alt and not alt_found and problems is not None:
        problems.append(f"--alt {alt!r} matches no candidate in any record")
    totals = (f"{len(files)} file(s); apply {kb(before)} -> {kb(after)}"
              + (f" (-{(before - after) / before:.0%})" if before else ""))
    data = {"files": files, "totals": totals, "banners": sorted(set(banners))}
    page = sheet_dir / "index.html"
    page.write_text(PAGE.replace("__DATA__", json.dumps(data).replace("</", "<\\/")))
    return tiles, page


def screenshot(page: Path, chrome: str) -> Path:
    """Render the page at 2x with headless Chrome; raises RuntimeError when no screenshot results."""
    png = page.with_name("page@2x.png")
    png.unlink(missing_ok=True)  # a stale picture must never pass for this run's
    try:
        proc = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--force-device-scale-factor=2",
                               "--allow-file-access-from-files", "--window-size=1400,2000",
                               f"--screenshot={png}", page.resolve().as_uri()],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Chrome screenshot failed ({error.__class__.__name__}: {error})") from error
    if not png.is_file():
        # Under the Codex sandbox (macOS seatbelt, 2026-10-08) Chrome aborts at start: exit -6, no stderr.
        raise RuntimeError(f"Chrome produced no screenshot (exit {proc.returncode}): {proc.stderr.strip()[-300:]}"
                           " (inside a sandbox such as Codex's Chrome cannot start; the page itself is fine, "
                           "drop --browser and open it instead)")
    return png
