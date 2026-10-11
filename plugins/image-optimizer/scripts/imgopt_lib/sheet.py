"""`imgopt sheet`: 1:1 tiles for the agent and a review page for the human.

The session wrote "no banding" from contact sheets an image viewer had
shrunk from 2400 to 1000 px. Tiles here are never wider than about 1000 px,
are cut at 1:1 from windows the script chooses (most error on smooth areas,
most error overall, and for alpha images an edge on dark grey), and show
reference | choice | amplified difference. The smooth-area tile is required
where banding is reported but not gated, and every tile when the format
changed or is uncalibrated.

One page covers the records of one or more working folders. Each card shows
before against the chosen variant, opens at 100% with panes that scroll
together, and offers a selector over every measured variant (one that fails
the gates says so). The selector always names the variant on screen: with
no choice, the first listed is shown, and a verdict sends it. The 1:1 crops are of the variant the page was built
with; switching to another dims them, labels the card's status as the
page's choice's, shows the scores of the variant on screen, and says how to
get crops of it. A card whose variant a keep would write without crops
(one the page saw untouched, under the saving floor) says so too. The human marks each
file keep, reject, milder or stronger, with an optional note, and "Copy
verdicts" gives one block (header line `review.BLOCK_HEADER`, then JSON
carrying the page's fingerprint and folders) for `imgopt record`. A verdict
names the variant on screen: switching the variant clears the file's
verdict (the note stays). A rebuilt page starts from the verdicts and
notes already recorded (`review.verdict_of`), and a lossless choice
without one starts as keep while it is shown.

A file whose pixels cannot be read gets its card but no tiles; build()
appends the reason to ``problems`` and carries on, and the command exits 1.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .candidates import choice_of, kb, removes
from .imaging import READ_FAILURES, display_pixels, flatten
from .metrics import diff_max, smooth_mask
from .review import BLOCK_HEADER, fingerprint, live_variants, rows_of, verdict_for, verdict_of

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
    for i, (img, label) in enumerate(((a, "reference"), (b, "choice"), (diff, f"difference x{AMPLIFY}"))):
        x = i * (w + GAP)
        draw.text((x + 2, 2), label, fill=(0, 0, 0))
        canvas.paste(img, (x, LABEL_H))
    if scale == 2:
        canvas = canvas.resize((canvas.width * 2, canvas.height * 2), Image.NEAREST)
    return canvas


def every_tile_required(record: dict) -> bool:
    """Every tile of a lossy choice is required viewing when its metrics are uncalibrated for the output
    format (WebP, AVIF) or the format changed: the spec makes the sheet review mandatory for a convert."""
    return bool(record["uncalibrated"]) or record["format"] != record["source"]["format"]


def needs_tiles(record: dict) -> bool:
    """A lossy choice that will be written is the only thing a human must judge at 1:1, so the only thing tiled."""
    chosen = choice_of(record)
    return bool(chosen) and chosen["kind"] == "lossy" and record["verdict"] == "apply"


def _tiles_for(folder: Path, record: dict, tiles_dir: Path, prefix: str = "") -> list[Tile]:
    chosen = choice_of(record)
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
            path = tiles_dir / f"{prefix}{name}__{window}{suffix}.png"
            _compose(ref, new, box, scale).save(path)
            required = every_tile_required(record) or (window == "smooth" and not chosen.get("band_gated"))
            out.append(Tile(path, record["source"]["path"], window + suffix, bool(required)))
    return out


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Image comparison</title>
<style>
:root{color-scheme:light dark;--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#d2d2d7;--pane:#fff;
  --s1:4px;--s2:8px;--s3:12px;--s4:16px;--s5:24px;--tag-bg:#ececf0;
  --ok:#1a7f37;--ok-bg:#e6f4ea;--bad:#b42318;--bad-bg:#fdecea;--warn:#8a5a00;--warn-bg:#fff4ce;
  --accent:#0a66d8;--accent-bg:#e8f0fe}
@media (prefers-color-scheme: dark){:root{--bg:#161617;--fg:#f5f5f7;--muted:#a1a1a6;--line:#3a3a3c;--tag-bg:#2c2c2e;
  --ok:#6fd38a;--ok-bg:#16301f;--bad:#ff8a80;--bad-bg:#3a1a18;--warn:#f5c451;--warn-bg:#3a2e10;
  --accent:#6aa8ff;--accent-bg:#14263f}}
*,*::before,*::after{box-sizing:border-box}
button,input,select,textarea{font:inherit}
input[type=text],button,select{min-height:32px}
input[type=text],button{padding:0 var(--s2)}
body{margin:0;padding:var(--s4);background:var(--bg);color:var(--fg);font:14px/1.4 -apple-system,system-ui,sans-serif}
header{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s2) var(--s4);margin-bottom:var(--s4)}
#totals{flex-basis:100%;font-variant-numeric:tabular-nums}
header label{display:inline-flex;align-items:center;gap:var(--s1)}
.banner{background:var(--warn-bg);color:var(--warn);padding:var(--s2) var(--s3);border-radius:6px;
  margin-bottom:var(--s2)}
.card{display:flex;flex-direction:column;gap:var(--s3);border:1px solid var(--line);border-radius:8px;
  margin-bottom:var(--s5);padding:var(--s4)}
.head{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:var(--s1) var(--s4);align-items:baseline;
  font-variant-numeric:tabular-nums}
.head h3{margin:0;font-size:18px;font-weight:600;overflow-wrap:anywhere}
.head .dir{grid-column:1;font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.gain{grid-column:2;grid-row:1/span 2;text-align:right;white-space:nowrap}
.gain .big{font-size:18px;font-weight:700}
.gain .better{color:var(--ok)}
.gain .worse{color:var(--warn)}
.gain .sizes{font-size:13px;color:var(--muted)}
.head .facts,.head .scores,.head .reason{grid-column:1/-1}
.facts{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s1) var(--s2);margin-top:var(--s2)}
.scores,.reason{font-size:13px;color:var(--muted)}
.panes{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:var(--s2)}
.pane{overflow:auto;max-height:70vh;border:1px solid var(--line);background:var(--pane)}
.pane img{display:block;max-width:100%}
body.actual .pane img{max-width:none}
body.dark{--pane:#282828}
.label{font-size:12px;color:var(--muted);padding:2px var(--s1)}
.label.warn{color:var(--warn);font-weight:600}
.pane .label{position:sticky;top:0;left:0;z-index:1;background:var(--bg);border-bottom:1px solid var(--line)}
.card > label{display:flex;align-items:center;gap:var(--s2)}
.card select{min-width:0;max-width:100%;flex:0 1 auto}
.tiles{display:flex;flex-direction:column;gap:var(--s3)}
.tiles.stale > .tile{opacity:.45}
.tile{overflow:auto;border:1px solid var(--line);width:fit-content;max-width:100%}
.tile img{display:block;max-width:none}
.tag{display:inline-block;padding:0 var(--s2);border-radius:4px;font-size:13px;font-weight:600;
  line-height:22px;color:var(--fg);background:var(--tag-bg)}
.tag.ok{color:var(--ok);background:var(--ok-bg)}
.tag.bad{color:var(--bad);background:var(--bad-bg)}
.tag.warn{color:var(--warn);background:var(--warn-bg)}
footer{position:sticky;bottom:0;z-index:2;display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;
  gap:var(--s2) var(--s4);background:var(--bg);border-top:1px solid var(--line);padding:var(--s2) 0}
#counts{flex:1 1 12em;font-variant-numeric:tabular-nums}
#copy{min-width:9em}
footer details{flex-basis:100%}
footer summary{cursor:pointer;font-size:12px;color:var(--muted)}
.verdicts{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s2)}
.verdicts label{display:inline-flex;align-items:center;gap:var(--s1);min-height:32px;padding:0 var(--s3);
  border:1px solid var(--line);border-radius:6px;cursor:pointer}
.verdicts label:has(input:checked){border-color:var(--accent);background:var(--accent-bg);font-weight:600}
.verdicts input[type=text]{flex:1;min-width:200px}
.verdicts .recorded{flex-basis:100%;padding:0}
textarea{width:100%;font:12px ui-monospace,monospace}
@media (max-width:600px){.pane{max-height:40vh}
  .head{grid-template-columns:minmax(0,1fr)}.gain{grid-column:1;grid-row:auto;text-align:left}}
</style></head><body class="actual">
<div id="banners"></div>
<header><strong id="totals"></strong>
<label><input type="checkbox" id="actual" checked> 100% (synced scroll)</label>
<label><input type="checkbox" id="dark"> dark background</label>
<label>Sort <select id="sort"><option value="bytes">bytes saved</option><option value="pct">% saved</option><option value="name">name</option></select></label>
<label>Show <select id="filter"><option value="all">all</option><option value="apply">to write</option><option value="lossy">lossy choices</option><option value="untouched">untouched</option><option value="open">no verdict yet</option></select></label>
</header><main id="cards"></main>
<footer><span id="counts"></span><button id="copy" type="button">Copy verdicts</button>
<details id="blockbox"><summary>Show the block</summary>
<div class="label">Paste the copied block to the agent; it stores it with <code>imgopt record</code>.</div>
<textarea id="block" rows="3" readonly></textarea></details></footer>
<script>
const DATA = __DATA__;
// The variant on screen, which a verdict names: the page's choice, else the first one listed.
const SELECTED = new Map(DATA.files.map(f=>[f.name,
  f.variants.some(v=>v.label===f.choice)?f.choice:(f.variants[0]||{}).label||null]));
// A rebuilt page starts from what was recorded; a lossless choice without a verdict starts as keep.
const VERDICTS = new Map(DATA.files.filter(f=>f.recorded||f.default_verdict).map(f=>[f.name,
  {verdict:f.recorded?f.recorded.verdict:f.default_verdict,variant:SELECTED.get(f.name)}]));
// Apart from VERDICTS: a note outlives a variant switch.
const NOTES = new Map(DATA.files.filter(f=>f.recorded&&f.recorded.note).map(f=>[f.name,f.recorded.note]));
const KINDS = ['keep','reject','milder','stronger'];
function variantOf(f){return f.variants.find(v=>v.label===SELECTED.get(f.name));}
// Status words get a colored chip as well; the words stay, so color is never the only channel.
function tag(text, kind){
  const t=document.createElement('span'); t.className='tag'+(kind?' '+kind:''); t.textContent=text; return t;
}
const KB=n=>(n/1024).toFixed(1)+' KB';
// What writing the page's choice gains: the headline a reviewer weighs the card by.
function gain(f){
  const g=document.createElement('div'); g.className='gain';
  if(f.after==null){g.append(line('big','no change'),line('sizes',KB(f.before))); return g;}
  const d=f.after-f.before, sign=d>0?'+':'\u2212', pct=Math.abs(Math.round(100*d/(f.before||1)));
  g.append(line('big '+(d>0?'worse':'better'),sign+KB(Math.abs(d))+' ('+sign+pct+'%)'),
           line('sizes',KB(f.before)+' \u2192 '+KB(f.after)));
  return g;
}
function gateTag(v){return v.pass?tag('passes','ok'):tag('fails the gates','bad');}
function line(cls, ...parts){const d=document.createElement('div'); d.className=cls; d.append(...parts); return d;}
function pane(label, src, name, ...tags){
  const w=document.createElement('div'); w.className='pane';
  const l=line('label', label+' ', ...tags);
  const i=document.createElement('img'); i.src=src; i.alt=label+': '+name;
  w.append(l,i); return w;
}
function card(f){
  const c=document.createElement('section'); c.className='card';
  // `apply` writes a lossy choice only after a keep of that variant; a lossless one unless rejected.
  const kept=()=>{const k=VERDICTS.get(f.name); return !!k&&k.verdict==='keep'&&k.variant===f.choice;};
  const writeTag=()=>tag(f.choice_kind==='lossy'&&!kept()?'written if kept':'will be written');
  const cut=f.name.lastIndexOf('/')+1;  // the file name leads; its folder sits below it
  const h=document.createElement('h3'); h.textContent=f.name.slice(cut);
  const v=variantOf(f), switched=SELECTED.get(f.name)!==f.choice;
  // The status worked out for the page's choice: sizes, format change, LARGER, why it is or is not written.
  const status=[f.verdict==='apply'?writeTag():tag('untouched','warn')];
  if(f.larger) status.push(tag('LARGER','warn'));
  if(v&&!switched) status.push(gateTag(v));
  const head=document.createElement('div'); head.className='head';
  head.append(h,gain(f),line('dir',f.name.slice(0,cut)),
    line('facts',...(switched&&f.choice?["The page's choice: "]:[]),...status,f.facts),
    ...(f.scores?[line('scores',f.scores)]:[]),...(f.reason?[line('reason',f.reason)]:[]));
  if(v&&switched) head.append(line('facts','On screen: ',gateTag(v),v.meta));
  c.append(head);
  const panes=document.createElement('div'); panes.className='panes';
  if(f.before_src) panes.append(pane('Before', f.before_src, f.name));
  if(v) panes.append(pane('Variant: '+v.label, v.src, f.name, ...(v.pass?[]:[tag('fails the gates','bad')])));
  const all=[...panes.querySelectorAll('.pane')];
  all.forEach(p=>p.addEventListener('scroll',()=>all.forEach(o=>{if(o!==p){o.scrollTop=p.scrollTop;o.scrollLeft=p.scrollLeft;}})));
  c.append(panes);
  if(f.variants.length){  // even one option: the selector names the variant a verdict keeps
    const s=document.createElement('select');
    for(const x of f.variants){const o=document.createElement('option'); o.value=x.label; o.textContent=x.meta; o.selected=x.label===SELECTED.get(f.name); s.append(o);}
    s.onchange=()=>{  // a verdict names the variant on screen; the rebuilt card's selector keeps the focus
      SELECTED.set(f.name,s.value); VERDICTS.delete(f.name);
      const n=card(f); c.replaceWith(n); n.querySelector('select').focus(); update();
    };
    const l=document.createElement('label'); l.append('Variant ',s); c.append(l);
  }
  if(f.tiles.length||(v&&v.changes_pixels&&v.keep_writes)){  // a keep would write pixels nobody saw at 1:1
    const t=document.createElement('div'); t.className='tiles'+(switched?' stale':'');
    const cap=document.createElement('div'); cap.className='label'+(switched||!f.tiles.length?' warn':'');
    const ask='ask the agent to choose it and rebuild the page.';
    cap.textContent=!f.tiles.length?'This page has no 1:1 crops of '+v.label+'. For them, '+ask
      :switched?'These 1:1 crops are of '+f.choice+'. For crops of '+v.label+', '+ask
      :'1:1 crops, required ones first: reference | choice | difference x8 (how much each pixel changed, '
        +'brightened 8 times so small errors show; black means unchanged)';
    t.append(cap);
    for(const tile of f.tiles){
      const w=document.createElement('div'); w.className='tile';
      const l=line('label',tile.label+' ',...(tile.required?[tag('required','warn')]:[]));
      const i=document.createElement('img'); i.src=tile.src; i.alt=tile.label+' crop: '+f.name;
      w.append(l,i); t.append(w);
    }
    c.append(t);
  }
  if(f.variants.length){
    const row=document.createElement('div'); row.className='verdicts';
    const cur=VERDICTS.get(f.name);
    if(f.recorded) row.append(line('label recorded','Recorded earlier: '+f.recorded.verdict));  // its note is below
    const note=document.createElement('input'); note.type='text'; note.placeholder='note for the agent (optional)';
    note.value=NOTES.get(f.name)||'';
    note.oninput=()=>{NOTES.set(f.name,note.value); update();};
    for(const k of KINDS){
      const l=document.createElement('label'); const r=document.createElement('input');
      r.type='radio'; r.name='v-'+f.name; r.value=k; r.checked=!!cur&&cur.verdict===k;
      r.onchange=()=>{
        NOTES.set(f.name,note.value); VERDICTS.set(f.name,{verdict:k,variant:SELECTED.get(f.name)});
        const chip=c.querySelector('.tag'); if(f.verdict==='apply'&&chip) chip.textContent=writeTag().textContent;
        update();
      };
      l.append(r,k); row.append(l);
    }
    row.append(note);
    c.append(row);
  }
  return c;
}
function render(){
  const sort=document.getElementById('sort').value, filter=document.getElementById('filter').value;
  const rows=DATA.files.filter(f=>filter==='all'||(filter==='apply'&&f.verdict==='apply')||(filter==='untouched'&&f.verdict!=='apply')
    ||(filter==='lossy'&&f.choice_kind==='lossy')||(filter==='open'&&f.variants.length&&!VERDICTS.has(f.name)));
  rows.sort((a,b)=>sort==='name'?a.name.localeCompare(b.name):sort==='pct'?(b.saved/(b.before||1))-(a.saved/(a.before||1)):b.saved-a.saved);
  const main=document.getElementById('cards'); main.textContent='';
  for(const f of rows) main.append(card(f));
}
function update(){
  const counts={}; for(const e of VERDICTS.values()) counts[e.verdict]=(counts[e.verdict]||0)+1;
  const open=DATA.files.filter(f=>f.variants.length&&!VERDICTS.has(f.name)).length;
  document.getElementById('counts').textContent=KINDS.map(k=>`${counts[k]||0} ${k}`).join(', ')+`; ${open} without a verdict`;
  const files=Object.fromEntries([...VERDICTS].map(([name,e])=>[name,{...e,note:NOTES.get(name)||''}]));
  document.getElementById('block').value=DATA.block_header+'\\n'+JSON.stringify({fingerprint:DATA.fingerprint,folders:DATA.folders,files});
}
document.getElementById('copy').onclick=()=>{
  const button=document.getElementById('copy'), block=document.getElementById('block');
  // No clipboard: show the block selected, so Cmd/Ctrl+C copies it.
  const byHand=()=>{document.getElementById('blockbox').open=true; block.focus(); block.select();};
  if(!navigator.clipboard) return byHand();
  navigator.clipboard.writeText(block.value).then(()=>{
    button.textContent='Copied'; clearTimeout(button.reset);
    button.reset=setTimeout(()=>{button.textContent='Copy verdicts';},2000);
  },byHand);
};
document.getElementById('totals').textContent=DATA.totals;
for(const b of DATA.banners){const d=document.createElement('div');d.className='banner';d.textContent=b;document.getElementById('banners').append(d);}
document.getElementById('actual').onchange=e=>document.body.classList.toggle('actual',e.target.checked);
document.getElementById('dark').onchange=e=>document.body.classList.toggle('dark',e.target.checked);
document.getElementById('sort').onchange=render;
document.getElementById('filter').onchange=render;
render(); update();
</script></body></html>
"""


def _floor(value: float | None) -> str:
    return f" (floor {value:g})" if value is not None else ""


def _facts(record: dict, chosen: dict | None) -> str:
    """The card's choice in a few words: its variant, the image's pixel size, the format (and any change), and
    the metadata it drops. The sizes and the saving are the card's headline, so they are not repeated here."""
    s = record["source"]
    changed = chosen and record["format"] != s["format"]
    parts = [chosen["label"]] if chosen else []
    parts += [f"{s['width']}×{s['height']}"] if s.get("width") else []
    parts += [f"{s['format']} → {record['format']}" if changed else s["format"]]
    parts += [removes(chosen)] if removes(chosen) else []
    return " · ".join(parts)


def _reason(record: dict) -> str:
    """Why the file is or is not written, unless the saving in the card's headline already says it."""
    reason = record["verdict_reason"]
    return "" if reason.startswith("saves ") else reason


def _quality(record: dict, c: dict, ungated: str = "reported, not gated") -> str:
    """A candidate's scores against the record's gates; ``ungated`` qualifies a banding no gate applies to."""
    if c.get("identical"):
        return "pixel-identical"
    if "ssim" not in c:
        return "not measured"
    gates = record["gates"]
    gated = c.get("band_gated") and gates["band"] is not None
    band = f"banding {c['band']:.1f} " + (f"(gated at ≤{gates['band']:g})" if gated else f"({ungated})")
    return f"SSIM {c['ssim']:.4f}{_floor(gates['ssim'])} · ss2 {c['ss2']:.1f}{_floor(gates['ss2'])} · {band}"


def _variant_meta(record: dict, c: dict) -> str:
    """One option of a card's variant selector: label, size, scores, and whether it passes the gates."""
    gate = "passes" if c.get("pass") else f"FAILS: {c.get('reason', '')}"
    dropped = f" · {removes(c)}" if removes(c) else ""
    return f"{c['label']} · {kb(c['size'])} · {_quality(record, c)} · {gate}{dropped}"


def build(outs, *, problems: list[str] | None = None) -> tuple[list[Tile], Path]:
    """Write the tiles and one page for every record of ``outs`` (a working folder or a list of them) under the
    first folder's ``_sheet``. The page lets the human switch each file between its measured variants, mark a
    verdict, and copy a verdict block that `record` stores. Refuses (UsageError) a folder without records before
    writing anything; per-file failures go to ``problems``."""
    outs = [Path(o).resolve() for o in ([outs] if isinstance(outs, (str, Path)) else outs)]
    rows = rows_of(outs)
    sheet_dir = outs[0] / "_sheet"
    tiles_dir = sheet_dir / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    tiles, files, banners = [], [], []
    before = after = applied = total_before = total_after = 0
    for index, (folder, record) in enumerate(rows):
        chosen = choice_of(record)

        def url(name: str) -> str:
            return quote(os.path.relpath(folder / name, sheet_dir))

        sources = sorted(folder.glob("source.*"))[:1]
        if not sources and problems is not None:
            problems.append(f"{folder.name}: source copy is missing, so its card has no Before pane")
        writes = bool(chosen) and record["verdict"] == "apply"
        saved = record["source"]["size"] - chosen["size"] if writes else 0
        total_before += record["source"]["size"]
        total_after += chosen["size"] if writes else record["source"]["size"]
        if writes:
            applied += 1
            before += record["source"]["size"]
            after += chosen["size"]
        record_tiles: list[Tile] = []
        if needs_tiles(record):
            try:
                record_tiles = _tiles_for(folder, record, tiles_dir, prefix=f"{index}__")
            except READ_FAILURES as error:
                if problems is not None:
                    problems.append(f"{folder.name}: no tiles: {error}")
        tiles += record_tiles
        variants = [{"label": c["label"], "src": url(c["file"]), "meta": _variant_meta(record, c),
                     "pass": bool(c.get("pass")), "changes_pixels": c["kind"] == "lossy" and not c.get("identical"),
                     "keep_writes": verdict_for(record, c, by_hand=True)[0] == "apply"}
                    for c in live_variants(record) if (folder / c["file"]).is_file()]
        entry = verdict_of(folder.parent, record)
        lossless_write = writes and chosen["kind"] == "lossless"
        files.append({"name": record["source"]["path"], "facts": _facts(record, chosen),
                      "scores": _quality(record, chosen, "not gated for this image; check the crops") if chosen else "",
                      "reason": _reason(record), "after": chosen["size"] if writes else None,
                      "before_src": url(sources[0].name) if sources else "",
                      "choice": chosen["label"] if chosen else None, "variants": variants,
                      "verdict": record["verdict"], "choice_kind": chosen["kind"] if chosen else None,
                      "before": record["source"]["size"], "saved": saved,
                      "larger": writes and chosen["size"] > record["source"]["size"],
                      "default_verdict": "keep" if lossless_write else "",
                      "recorded": {"verdict": entry["verdict"], "note": entry.get("note", "")} if entry else None,
                      "tiles": [{"label": t.window, "src": quote(f"tiles/{t.path.name}"), "required": t.required}
                                for t in sorted(record_tiles, key=lambda t: not t.required)]})
        if record["uncalibrated"]:
            banners.append("UNCALIBRATED FORMAT: WebP/AVIF output was never calibrated against these gates; "
                           "judge every file by eye.")
        if record.get("waived"):
            banners.append("Waived tools (fewer candidates were tried): " + ", ".join(record["waived"]))
    overall = (f"{len(files)} file(s), {kb(total_before)} -> {kb(total_after)}"
               + (f" ({(total_after - total_before) / total_before:+.1%}) overall" if total_before else " overall"))
    totals = overall + (f"; {applied} to write: {kb(before)} -> {kb(after)}" if applied else "; nothing to write")
    data = {"files": files, "totals": totals, "banners": sorted(set(banners)), "fingerprint": fingerprint(rows),
            "folders": [str(o) for o in outs], "block_header": BLOCK_HEADER}
    page = sheet_dir / "index.html"
    page.write_text(PAGE.replace("__DATA__", json.dumps(data).replace("</", "<\\/")))
    return tiles, page
