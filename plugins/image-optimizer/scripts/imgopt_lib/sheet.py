"""`imgopt sheet`: 1:1 tiles for the agent and a comparison page for the human.

The session wrote "no banding" from contact sheets an image viewer had
shrunk from 2400 to 1000 px. Tiles here are never wider than about 1000 px,
are cut at 1:1 from windows the script chooses (most error on smooth areas,
most error overall, and for alpha images an edge on dark grey), and show
reference | pick | amplified difference. The smooth-area tile is required
where banding is reported but not gated, and every tile when the format
changed or is uncalibrated. The page shows before, pick and an
optional alternative, opens at 100% with panes that scroll together, shows each
lossy pick's 1:1 tiles on its card, and builds the `apply --approve` command from
the picks the human ticks.

A file whose pixels cannot be read gets its card but no tiles; build()
appends the reason to ``problems`` and carries on, and the command exits 1.
"""

from __future__ import annotations

import json
import math
import shlex
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


# `apply --approve` splits its value on commas, and a name it cannot match falls back to the basename,
# so a path with a comma could approve a different file; the page withholds the tick instead.
COMMA_NOTE = "The path contains a comma, which the apply command cannot carry: rename the file, then run candidates again."


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
.tiles{display:flex;flex-direction:column;gap:8px;margin-top:8px}
.tile{overflow:auto;border:1px solid var(--line)}
.tile img{display:block;max-width:none}
.approve{margin:8px 0;font-weight:600}
footer{position:sticky;bottom:0;background:var(--bg);border-top:1px solid var(--line);padding:8px 0}
code{word-break:break-all}
</style></head><body class="actual">
<div id="banners"></div>
<header><strong id="totals"></strong>
<label><input type="checkbox" id="actual" checked> 100% (synced scroll)</label>
<label><input type="checkbox" id="dark"> dark background</label>
<label>Sort <select id="sort"><option value="bytes">bytes saved</option><option value="pct">% saved</option><option value="name">name</option></select></label>
<label>Show <select id="filter"><option value="all">all</option><option value="apply">to apply</option><option value="lossy">lossy picks</option><option value="untouched">untouched</option></select></label>
</header><main id="cards"></main>
<footer><span id="approved-count"></span> <button id="copy">Copy apply command</button>
<div><code id="command"></code></div></footer>
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
    card.append(h,m,panes);
    if(f.tiles.length){
      const t=document.createElement('div'); t.className='tiles';
      const cap=document.createElement('div'); cap.className='label';
      cap.textContent='1:1 crops: reference | pick | difference x8 (required ones first)'; t.append(cap);
      for(const tile of f.tiles){
        const w=document.createElement('div'); w.className='tile';
        const l=document.createElement('div'); l.className='label'; l.textContent=tile.label+(tile.required?' (required)':'');
        const i=document.createElement('img'); i.src=tile.src; i.alt=tile.label+' crop: '+f.name;
        w.append(l,i); t.append(w);
      }
      card.append(t);
    }
    if(f.approve_note){
      const n=document.createElement('div'); n.className='banner'; n.textContent=f.approve_note; card.append(n);
    }
    if(f.approvable){
      const a=document.createElement('label'); a.className='approve';
      const box=document.createElement('input'); box.type='checkbox'; box.checked=APPROVED.has(f.name);
      box.onchange=()=>{box.checked?APPROVED.add(f.name):APPROVED.delete(f.name); updateCommand();};
      a.append(box,' Approve this pick'); card.append(a);
    }
    main.append(card);
  }
}
const APPROVED=new Set();
function shq(s){return "'"+s.replace(/'/g,"'\\\\''")+"'";}
function updateCommand(){
  const n=DATA.files.filter(f=>f.approvable).length;
  document.getElementById('approved-count').textContent=`Approved ${APPROVED.size} of ${n} lossy pick(s)`;
  document.getElementById('command').textContent=APPROVED.size?DATA.apply_prefix+' --approve '+shq([...APPROVED].join(',')):'';
}
document.getElementById('copy').onclick=()=>{const c=document.getElementById('command').textContent;if(c&&navigator.clipboard)navigator.clipboard.writeText(c);};
document.getElementById('totals').textContent=DATA.totals;
for(const b of DATA.banners){const d=document.createElement('div');d.className='banner';d.textContent=b;document.getElementById('banners').append(d);}
document.getElementById('actual').onchange=e=>document.body.classList.toggle('actual',e.target.checked);
document.getElementById('dark').onchange=e=>document.body.classList.toggle('dark',e.target.checked);
document.getElementById('sort').onchange=render;
document.getElementById('filter').onchange=render;
render();
updateCommand();
</script></body></html>
"""


def _floor(value: float | None) -> str:
    return f" (floor {value:g})" if value is not None else ""


def _meta(record: dict, chosen: dict | None) -> str:
    s = record["source"]
    dims = f"{s['width']}x{s['height']} " if s.get("width") else ""
    if not chosen:
        return f"{dims}{s['format']} · {kb(s['size'])} · untouched ({record['verdict_reason']})"
    if chosen.get("identical"):
        quality = "pixel-identical"
    elif "ssim" in chosen:
        gates = record["gates"]
        band = (f"banding {chosen['band']:.1f} (gated at ≤{gates['band']:g})"
                if chosen.get("band_gated") and gates["band"] is not None
                else f"banding {chosen['band']:.1f} (reported, not gated: gated only on palette PNG; view the tiles)")
        quality = (f"SSIM {chosen['ssim']:.4f}{_floor(gates['ssim'])} · "
                   f"ss2 {chosen['ss2']:.1f}{_floor(gates['ss2'])} · {band}")
    else:
        quality = "not measured"
    removes = f" · removes {', '.join(chosen['metadata_removed'])}" if chosen.get("metadata_removed") else ""
    return (f"{dims}{s['format']} -> {record['format']} · {kb(s['size'])} -> {kb(chosen['size'])} · "
            f"{chosen['label']} · {quality}{removes} · {record['verdict']} ({record['verdict_reason']})")


def build(out: Path, *, alt: str | None = None, problems: list[str] | None = None,
          script: Path | None = None) -> tuple[list[Tile], Path]:
    """Write the tiles and the page under out/_sheet.

    The page builds its ``apply --approve`` command from ``script`` (the imgopt.py path) and the full
    source paths of the lossy picks the human ticks.

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
    before = after = applied = 0
    total_before = total_after = 0
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
        total_before += record["source"]["size"]
        total_after += chosen["size"] if chosen and record["verdict"] == "apply" else record["source"]["size"]
        if record["verdict"] == "apply" and chosen:
            applied += 1
            before += record["source"]["size"]
            after += chosen["size"]
        record_tiles: list[Tile] = []
        if needs_tiles(record):
            try:
                record_tiles = _tiles_for(folder, record, tiles_dir)
            except READ_FAILURES as error:
                if problems is not None:
                    problems.append(f"{folder.name}: no tiles: {error}")
        tiles += record_tiles
        source_path = record["source"]["path"]
        files.append({"name": source_path, "meta": _meta(record, chosen), "panes": panes,
                      "verdict": record["verdict"], "pick_kind": chosen["kind"] if chosen else None,
                      "before": record["source"]["size"], "saved": saved,
                      "approvable": needs_tiles(record) and bool(record_tiles) and "," not in source_path,
                      "approve_note": COMMA_NOTE if needs_tiles(record) and "," in source_path else "",
                      "tiles": [{"label": t.window, "src": quote(f"tiles/{t.path.name}"), "required": t.required}
                                for t in sorted(record_tiles, key=lambda t: not t.required)]})
        if record["uncalibrated"]:
            banners.append("UNCALIBRATED FORMAT: WebP/AVIF output was never calibrated against these gates; "
                           "judge every file by eye.")
        if record.get("waived"):
            banners.append("Waived tools (fewer candidates were tried): " + ", ".join(record["waived"]))
        if record.get("optional_missing"):
            banners.append("Optional tools missing (fewer candidates were tried): "
                           + ", ".join(record["optional_missing"]))
    if alt and not alt_found and problems is not None:
        problems.append(f"--alt {alt!r} matches no candidate in any record")
    overall = (f"{len(files)} file(s), {kb(total_before)} -> {kb(total_after)}"
               + (f" ({(total_after - total_before) / total_before:+.1%}) overall" if total_before else " overall"))
    totals = overall + (f"; {applied} to apply: {kb(before)} -> {kb(after)}" if applied else "; nothing to apply")
    apply_prefix = f"python3 {shlex.quote(str(script or Path('imgopt.py')))} apply {shlex.quote(str(out))}"
    data = {"files": files, "totals": totals, "banners": sorted(set(banners)), "apply_prefix": apply_prefix}
    page = sheet_dir / "index.html"
    page.write_text(PAGE.replace("__DATA__", json.dumps(data).replace("</", "<\\/")))
    return tiles, page

