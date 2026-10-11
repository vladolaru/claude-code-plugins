import hashlib
import json
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote
from pathlib import Path

import pytest
from PIL import Image

from imgopt_lib import candidates as C
from imgopt_lib import gates as G
from imgopt_lib import review as RV
from imgopt_lib import sheet as S
from imgopt_lib.ladder import UsageError

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "imgopt.py"


def fake_out(tmp_path, factory, *, uncalibrated=False, band_gated=False, size=(160, 120), alpha=False,
             name="photo--x"):
    out = tmp_path / "out"
    folder = out / name
    folder.mkdir(parents=True)
    src = factory.logo(size=size) if alpha else factory.photo(size=size)
    ext = src.suffix
    (folder / f"source{ext}").write_bytes(src.read_bytes())
    ref = Image.open(src).convert("RGBA")
    ref.save(folder / "reference.png")
    pick = folder / "pick.png"
    ref.quantize(8).convert("RGBA").save(pick)
    record = {
        "schema": 1, "source": {"path": str(src), "size": src.stat().st_size, "format": "png",
                                "width": size[0], "height": size[1], "colors": None,
                                "sha256": hashlib.sha256(src.read_bytes()).hexdigest()},
        "format": "webp" if uncalibrated else "png", "uncalibrated": uncalibrated, "waived": ["jpegoptim"],
        "notes": [], "candidates": [{"label": "pngquant-c8", "file": "pick.png", "kind": "lossy",
                                     "size": pick.stat().st_size, "band_gated": band_gated, "ssim": 0.95,
                                     "ssim_white": 0.95, "ss2": 70.0, "band": 5.0, "pass": True, "reason": ""}],
        "pick": "pick.png", "verdict": "apply", "verdict_reason": "x", "profile": "high",
        "gates": {"ssim": 0.98, "ss2": 80.0, "band": 3.0, "lossless_only": False},
    }
    (folder / "metrics.json").write_text(json.dumps(record))
    return out


def _move_source(out, name, source):
    """Give a record another source path: a page refuses one source path in two records."""
    path = out / name / "metrics.json"
    record = json.loads(path.read_text())
    record["source"]["path"] = str(source)
    path.write_text(json.dumps(record))


def test_tiles_stay_under_1000px_and_mark_the_smooth_tile_required(tmp_path, factory):
    out = fake_out(tmp_path, factory, size=(900, 600))
    tiles, page = S.build(out)
    assert {t.window for t in tiles} == {"smooth", "overall"}
    assert [t.window for t in tiles if t.required] == ["smooth"]
    for t in tiles:
        assert Image.open(t.path).width <= 1000
    assert page.is_file() and "pngquant-c8" in page.read_text()


def test_small_images_get_a_2x_tile(tmp_path, factory):
    tiles, _ = S.build(fake_out(tmp_path, factory, size=(160, 120)))
    two_x = [t for t in tiles if t.window.endswith("@2x")]
    assert two_x and all(Image.open(t.path).width <= 1000 for t in two_x)


def test_uncalibrated_requires_every_tile_and_alpha_adds_an_edge_tile(tmp_path, factory):
    tiles, page = S.build(fake_out(tmp_path, factory, uncalibrated=True, alpha=True, size=(500, 500)))
    assert "edge" in {t.window for t in tiles}
    assert all(t.required for t in tiles)
    text = page.read_text()
    assert "UNCALIBRATED" in text and "jpegoptim" in text


def test_gated_palette_pick_has_no_required_tiles(tmp_path, factory):
    tiles, _ = S.build(fake_out(tmp_path, factory, band_gated=True, size=(900, 600)))
    assert tiles and not any(t.required for t in tiles)


def test_lossless_and_untouched_files_get_a_card_but_no_tiles(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    path = out / "photo--x" / "metrics.json"
    record = json.loads(path.read_text())
    record["candidates"][0]["kind"] = "lossless"
    path.write_text(json.dumps(record))
    tiles, page = S.build(out)
    assert tiles == [] and "pngquant-c8" in page.read_text()
    # No pick at all, and an errored candidate without a file: nothing to show, nothing to crash on.
    record.update(pick=None, verdict="keep", verdict_reason="nothing beats the original")
    record["candidates"] = [{"label": "pngquant-c8", "kind": "lossy", "band_gated": True,
                             "error": "encoder failed", "pass": False, "reason": "error"}]
    path.write_text(json.dumps(record))
    tiles, page = S.build(out)
    assert tiles == [] and "nothing beats the original" in page.read_text()


def test_page_urls_are_quoted(tmp_path, factory):
    _, page = S.build(fake_out(tmp_path, factory, name="my photo--x"))
    assert "../my%20photo--x/pick.png" in page.read_text()


def test_unreadable_pick_is_reported_and_the_other_files_still_get_tiles(tmp_path, factory):
    out = fake_out(tmp_path, factory, name="a--x")
    (out / "a--x" / "pick.png").write_bytes(b"not an image")
    good = fake_out(tmp_path, factory, name="b--x")
    assert good == out
    _move_source(out, "b--x", tmp_path / "other" / "photo.jpg")
    problems: list[str] = []
    tiles, page = S.build(out, problems=problems)
    assert tiles and all("b--x" in str(t.path) for t in tiles)
    assert len(problems) == 1 and "a--x" in problems[0]
    assert page.is_file()


def run_sheet(*args):
    return subprocess.run([sys.executable, str(SCRIPT), "sheet", *map(str, args)], capture_output=True, text=True)


def test_sheet_cli_lists_required_tiles_and_the_page(tmp_path, factory):
    out = fake_out(tmp_path, factory, size=(900, 600))
    proc = run_sheet(out)
    assert proc.returncode == 0, proc.stderr
    assert "REQUIRED" in proc.stdout and "index.html" in proc.stdout and "tools: pillow" in proc.stdout
    last = proc.stdout.splitlines()[-1]
    assert "record" in last and "--verdicts -" in last and "apply" not in last


def test_sheet_cli_exits_1_when_a_file_cannot_be_read(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    (out / "photo--x" / "pick.png").write_bytes(b"not an image")
    proc = run_sheet(out)
    assert proc.returncode == 1
    assert "photo--x" in proc.stdout + proc.stderr


def window_flags(tiles):
    return {(t.window, t.required) for t in tiles}


@pytest.mark.parametrize("kwargs, expected", [
    (dict(size=(900, 600)), {("smooth", True), ("overall", False)}),
    (dict(size=(160, 120)), {("smooth", True), ("smooth@2x", True), ("overall", False), ("overall@2x", False)}),
    (dict(size=(900, 600), band_gated=True), {("smooth", False), ("overall", False)}),
    (dict(size=(500, 500), alpha=True), {("smooth", True), ("overall", False), ("edge", False)}),
    (dict(size=(500, 500), alpha=True, uncalibrated=True), {("smooth", True), ("overall", True), ("edge", True)}),
    (dict(size=(120, 120), alpha=True, uncalibrated=True),
     {(w + s, True) for w in ("smooth", "overall", "edge") for s in ("", "@2x")}),
], ids=["large", "small-2x", "gated", "alpha-edge-optional", "uncalibrated-large", "uncalibrated-small"])
def test_which_tiles_are_required(tmp_path, factory, kwargs, expected):
    tiles, _ = S.build(fake_out(tmp_path, factory, **kwargs))
    assert window_flags(tiles) == expected


def test_the_2x_rule_follows_the_displayed_width_not_the_source_width(tmp_path, factory):
    out = fake_out(tmp_path, factory, size=(300, 200))
    path = out / "photo--x" / "metrics.json"
    record = json.loads(path.read_text())
    record["source"]["width"] = 2400  # a 2400 px source resized to 300
    path.write_text(json.dumps(record))
    tiles, _ = S.build(out)
    assert {"smooth@2x", "overall@2x"} <= {t.window for t in tiles}
    out = fake_out(tmp_path / "up", factory, size=(900, 600))
    path = out / "photo--x" / "metrics.json"
    record = json.loads(path.read_text())
    record["source"]["width"] = 300  # a small source scaled up to 900
    path.write_text(json.dumps(record))
    tiles, _ = S.build(out)
    assert not any(t.window.endswith("@2x") for t in tiles)


def test_a_mistyped_or_empty_out_is_refused_before_anything_is_written(tmp_path):
    missing = tmp_path / "nope"
    proc = run_sheet(missing)
    assert proc.returncode == 2 and str(missing) in proc.stdout + proc.stderr
    assert not missing.exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    proc = run_sheet(empty)
    assert proc.returncode == 2 and str(empty) in proc.stdout + proc.stderr
    assert not (empty / "_sheet").exists()
    assert "Next:" not in proc.stdout
    with pytest.raises(UsageError, match="nope"):
        S.build(missing)


def test_a_missing_source_copy_is_a_problem(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    (out / "photo--x" / "source.jpg").unlink()
    problems: list[str] = []
    S.build(out, problems=problems)
    assert len(problems) == 1 and "photo--x" in problems[0] and "source" in problems[0]


def test_cli_counts_lossy_picks_it_could_not_tile_and_withholds_the_apply_line(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    (out / "photo--x" / "pick.png").write_bytes(b"not an image")
    proc = run_sheet(out)
    assert proc.returncode == 1
    assert "No lossy choices" not in proc.stdout
    assert "1 lossy choice(s) could not be tiled" in proc.stdout
    assert "Next:" not in proc.stdout
    assert "photo--x" in proc.stderr


def test_cli_with_only_lossless_picks_points_at_apply_without_approval(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    path = out / "photo--x" / "metrics.json"
    record = json.loads(path.read_text())
    record["candidates"][0]["kind"] = "lossless"
    path.write_text(json.dumps(record))
    proc = run_sheet(out)
    assert proc.returncode == 0
    assert "No lossy choices" in proc.stdout
    last = proc.stdout.splitlines()[-1]
    assert "apply" in last and "need no verdict" in last and "--approve" not in last


def _edit_record(out, **changes):
    path = out / "photo--x" / "metrics.json"
    record = json.loads(path.read_text())
    record["source"]["format"] = changes.pop("source_format", record["source"]["format"])
    record["candidates"][0].update(changes.pop("pick", {}))
    record.update(changes)
    path.write_text(json.dumps(record))


def test_a_lossy_pick_in_a_new_format_requires_every_tile(tmp_path, factory):
    out = fake_out(tmp_path, factory, band_gated=True, size=(900, 600))  # band-gated alone: nothing required
    _edit_record(out, source_format="jpeg", format="png")
    tiles, _ = S.build(out)
    assert tiles and all(t.required for t in tiles)


def test_a_pixel_identical_pick_in_a_new_format_gets_no_tiles(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    _edit_record(out, source_format="png", format="jpeg", pick={"kind": "lossless", "identical": True})
    tiles, _ = S.build(out)
    assert tiles == []


def test_the_card_facts_name_the_metadata_the_pick_removes():
    record = {"source": {"width": 10, "height": 10, "format": "jpeg", "size": 2048}, "format": "jpeg",
              "verdict": "apply", "verdict_reason": "smaller"}
    chosen = {"size": 1024, "label": "x", "identical": True, "metadata_removed": ["exif"]}
    assert S._facts(record, chosen) == "x · 10×10 · jpeg · removes exif"
    assert "removes" not in S._facts(record, {k: v for k, v in chosen.items() if k != "metadata_removed"})


def _page_data(page: Path) -> dict:
    """The page's embedded data (json.dumps writes it on one line)."""
    line = re.search(r"const DATA = (.*);\n", page.read_text()).group(1)
    return json.loads(line.replace("<\\/", "</"))


def test_cards_carry_their_tiles_and_open_at_100_percent(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    tiles, page = S.build(out)
    data = _page_data(page)
    [card] = data["files"]
    assert card["tiles"] and all(t["src"].startswith("tiles/") for t in card["tiles"])
    assert {t["required"] for t in card["tiles"]} >= {True}
    assert [t["required"] for t in card["tiles"]] == sorted((t["required"] for t in card["tiles"]), reverse=True)
    assert 'id="actual" checked' in page.read_text() and '<body class="actual">' in page.read_text()


def test_a_file_that_cannot_be_tiled_still_offers_its_variants(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    (out / "photo--x" / "pick.png").write_bytes(b"not an image")
    _, page = S.build(out, problems=[])
    [card] = _page_data(page)["files"]
    assert card["tiles"] == [] and [v["label"] for v in card["variants"]] == ["pngquant-c8"]


def test_same_basename_files_get_distinct_cards(tmp_path, factory):
    out = fake_out(tmp_path, factory, name="a--x")
    fake_out(tmp_path, factory, name="b--x")
    for folder, source in (("a--x", "/shop/a/photo.png"), ("b--x", "/shop/b/photo.png")):
        _move_source(out, folder, source)
    _, page = S.build(out)
    names = [f["name"] for f in _page_data(page)["files"]]
    assert sorted(names) == ["/shop/a/photo.png", "/shop/b/photo.png"]


def test_scores_state_the_gate_beside_a_reported_banding(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    _, page = S.build(out)
    scores = _page_data(page)["files"][0]["scores"]
    assert "banding 5.0 (not gated for this image; check the crops)" in scores
    assert "SSIM 0.9500 (floor 0.98)" in scores and "ss2 70.0 (floor 80)" in scores


def test_scores_state_the_gate_on_a_gated_banding(tmp_path, factory):
    out = fake_out(tmp_path, factory, band_gated=True)
    _, page = S.build(out)
    assert "banding 5.0 (gated at ≤3)" in _page_data(page)["files"][0]["scores"]


def test_totals_cover_the_whole_batch(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    _, page = S.build(out)
    totals = _page_data(page)["totals"]
    assert "1 file(s)" in totals and "overall" in totals and "1 to write" in totals


def test_totals_count_an_untouched_file_at_its_original_size(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    path = out / "photo--x" / "metrics.json"
    record = json.loads(path.read_text())
    record.update(pick=None, verdict="keep", verdict_reason="nothing beats the original")
    path.write_text(json.dumps(record))
    _, page = S.build(out)
    size = f"{record['source']['size'] / 1024:.1f} KB"
    assert _page_data(page)["totals"] == f"1 file(s), {size} -> {size} (+0.0%) overall; nothing to write"


def test_totals_sign_growth_and_shrinkage(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    path = out / "photo--x" / "metrics.json"
    record = json.loads(path.read_text())
    size = record["source"]["size"]
    for pick_size, sign in ((size // 2, "-"), (size * 2, "+")):
        record["candidates"][0]["size"] = pick_size
        path.write_text(json.dumps(record))
        _, page = S.build(out)
        assert re.search(rf"\({re.escape(sign)}\d+\.\d%\) overall", _page_data(page)["totals"])


def test_the_page_lists_variants_and_carries_the_fingerprint(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    _, page = S.build(out)
    data = _page_data(page)
    [f] = data["files"]
    assert data["fingerprint"] and f["choice"] == "pngquant-c8"
    assert [v["label"] for v in f["variants"]] == ["pngquant-c8"] and f["default_verdict"] == ""
    assert f["choice_kind"] == "lossy" and "pick_kind" not in f
    assert data["block_header"] == RV.BLOCK_HEADER
    # The header and the JSON are joined by a JS "\n" escape: a raw newline inside the literal breaks the script.
    assert "DATA.block_header+'\\n'+JSON.stringify(" in page.read_text()


def test_one_page_from_two_folders_points_at_both(tmp_path, factory):
    first = fake_out(tmp_path / "one", factory, name="a--1")
    second = fake_out(tmp_path / "two", factory, name="b--2")
    _move_source(second, "b--2", tmp_path / "elsewhere" / "photo.jpg")  # one source path per page
    _, page = S.build([first, second])
    data = _page_data(page)
    assert page.parent == first / "_sheet" and data["folders"] == [str(first), str(second)]
    assert len(data["files"]) == 2
    for f in data["files"]:
        assert (page.parent / unquote(f["before_src"])).resolve().is_file()
        for v in f["variants"]:
            assert (page.parent / unquote(v["src"])).resolve().is_file()


def test_a_lossless_choice_starts_as_keep(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    meta = next(out.glob("*/metrics.json"))
    record = json.loads(meta.read_text())
    record["candidates"][0]["kind"] = "lossless"
    meta.write_text(json.dumps(record))
    _, page = S.build(out)
    assert _page_data(page)["files"][0]["default_verdict"] == "keep"


def test_a_failing_variant_is_listed_and_labelled(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    path = out / "photo--x" / "metrics.json"
    record = json.loads(path.read_text())
    smaller = out / "photo--x" / "smaller.png"
    smaller.write_bytes((out / "photo--x" / "pick.png").read_bytes()[:-1])
    record["candidates"].append({**record["candidates"][0], "label": "pngquant-c4", "file": "smaller.png",
                                 "size": smaller.stat().st_size, "pass": False, "reason": "SSIM below the floor"})
    path.write_text(json.dumps(record))
    _, page = S.build(out)
    [f] = _page_data(page)["files"]
    assert [v["label"] for v in f["variants"]] == ["pngquant-c4", "pngquant-c8"] and f["choice"] == "pngquant-c8"
    assert [v["pass"] for v in f["variants"]] == [False, True]
    assert "FAILS: SSIM below the floor" in f["variants"][0]["meta"]


def test_the_sheet_cli_takes_several_folders(tmp_path, factory):
    first = fake_out(tmp_path / "one", factory, name="a--1")
    second = fake_out(tmp_path / "two", factory, name="b--2")
    _move_source(second, "b--2", tmp_path / "elsewhere" / "photo.jpg")
    proc = run_sheet(first, second)
    assert proc.returncode == 0, proc.stderr
    assert str(first / "_sheet" / "index.html") in proc.stdout
    last = proc.stdout.splitlines()[-1]
    assert f"record {first} {second} --verdicts -" in last


def test_a_block_made_as_the_page_makes_it_is_recorded_in_both_folders(tmp_path, factory):
    first = fake_out(tmp_path / "one", factory, name="a--1")
    second = fake_out(tmp_path / "two", factory, name="b--2")
    _move_source(second, "b--2", tmp_path / "elsewhere" / "photo.jpg")
    path = first / "a--1" / "metrics.json"
    record = json.loads(path.read_text())
    record["candidates"].append({**record["candidates"][0], "label": "pngquant-c16", "file": "c16.png",
                                 "size": record["candidates"][0]["size"] + 10})
    (first / "a--1" / "c16.png").write_bytes((first / "a--1" / "pick.png").read_bytes())
    path.write_text(json.dumps(record))
    _, page = S.build([first, second])
    data = _page_data(page)
    keep, reject = data["files"]
    assert keep["choice"] == "pngquant-c8" and "pngquant-c16" in [v["label"] for v in keep["variants"]]
    files = {keep["name"]: {"verdict": "keep", "variant": "pngquant-c16", "note": ""},
             reject["name"]: {"verdict": "reject", "variant": reject["choice"], "note": "too soft"}}
    block = data["block_header"] + "\n" + json.dumps({"fingerprint": data["fingerprint"],
                                                      "folders": data["folders"], "files": files})
    assert data["folders"] == [str(first.resolve()), str(second.resolve())]
    assert RV.record_verdicts(None, block) == {"keep": 1, "reject": 1}  # the block names its folders
    one = json.loads((first / RV.VERDICTS_FILE).read_text())["files"]
    two = json.loads((second / RV.VERDICTS_FILE).read_text())["files"]
    assert one[keep["name"]]["verdict"] == "keep" and one[keep["name"]]["variant"] == "pngquant-c16"
    assert two[reject["name"]]["verdict"] == "reject" and two[reject["name"]]["note"] == "too soft"
    assert set(one) == {keep["name"]} and set(two) == {reject["name"]}


def test_a_rebuilt_page_starts_from_the_verdicts_already_recorded(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    _, page = S.build(out)
    data = _page_data(page)
    [f] = data["files"]
    assert f["recorded"] is None
    RV.record_verdicts(None, data["block_header"] + "\n" + json.dumps({
        "fingerprint": data["fingerprint"], "folders": data["folders"],
        "files": {f["name"]: {"verdict": "reject", "variant": f["choice"], "note": "too soft"}}}))
    _, page = S.build(out)
    [f] = _page_data(page)["files"]
    assert f["recorded"] == {"verdict": "reject", "note": "too soft"}
    shown = run_page(page, """(() => {
      const c = cards()[0], note = c.querySelectorAll('input').find(i => i.type === 'text');
      return [c.querySelector('.recorded').textContent, note.value,
              c.querySelectorAll('input').filter(i => i.checked).map(i => i.value)];
    })()""")
    assert shown == ["Recorded earlier: reject", "too soft", ["reject"]]  # the note shows once, in its input


def test_variants_say_whether_they_change_pixels(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    path = out / "photo--x" / "metrics.json"
    record = json.loads(path.read_text())
    (out / "photo--x" / "same.png").write_bytes((out / "photo--x" / "pick.png").read_bytes())
    record["candidates"].append({**record["candidates"][0], "label": "oxipng", "file": "same.png", "kind": "lossless",
                                 "identical": True, "size": record["candidates"][0]["size"] + 1})
    path.write_text(json.dumps(record))
    _, page = S.build(out)
    [f] = _page_data(page)["files"]
    assert {v["label"]: v["changes_pixels"] for v in f["variants"]} == {"pngquant-c8": True, "oxipng": False}


# Just enough DOM for the page's script to run under node: what a card shows is its text, not DATA.
DOM = r"""
class Text { constructor(t) { this.textContent = t; } }
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.parent = null; this.className = '';
                     this.style = {}; }
  append(...xs) { for (const x of xs) { const n = typeof x === 'string' ? new Text(x) : x; n.parent = this;
                                        this.children.push(n); } }
  get textContent() { return this.children.map(c => c.textContent).join(''); }
  set textContent(t) { this.children = []; if (t !== '') this.append(String(t)); }
  replaceWith(n) { this.parent.children[this.parent.children.indexOf(this)] = n; n.parent = this.parent; }
  addEventListener() {}
  get classList() { const e = this; return { toggle(c) { e.className += ' ' + c; } }; }
  focus() { document.activeElement = this; }
  select() {}
  *walk() { for (const c of this.children) if (c instanceof El) { yield c; yield* c.walk(); } }
  querySelectorAll(sel) { return [...this.walk()].filter(e => sel.startsWith('.')
    ? e.className.split(' ').includes(sel.slice(1)) : e.tagName === sel.toUpperCase()); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
const ids = {};
const document = { body: new El('body'), activeElement: null, createElement: t => new El(t),
  getElementById: id => ids[id]
    || (ids[id] = Object.assign(new El('div'), {id, value: {sort: 'bytes', filter: 'all'}[id]})) };
const cards = () => document.getElementById('cards').children;
"""


def run_page(page: Path, then: str):
    """Run the page's script under node, then ``then`` (JS that prints one JSON value), and return that value."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("needs node to run the page's script")
    script = re.search(r"<script>(.*)</script>", page.read_text(), re.S).group(1)
    js = page.parent / "run-page.js"
    js.write_text(DOM + script + "\nconsole.log(JSON.stringify(" + then + "));\n")
    proc = subprocess.run([node, str(js)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _rewrite(out, name="photo--x", **changes):
    path = out / name / "metrics.json"
    record = json.loads(path.read_text())
    record.update(changes)
    path.write_text(json.dumps(record))
    return record


def test_a_card_shows_the_status_line_with_larger_sizes_and_the_format_change(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    record = json.loads((out / "photo--x" / "metrics.json").read_text())
    pick = record["candidates"][0]
    source = {**record["source"], "format": "jpeg", "size": pick["size"] // 2}
    verdict, why = G.verdict(source["size"], pick, in_place=False)
    _rewrite(out, source=source, target=str(Path(source["path"]).with_suffix(".png")), verdict=verdict,
             verdict_reason=why)
    _, page = S.build(out)
    [[text, tags]] = run_page(page, "[...cards()].map(c => [c.textContent, c.querySelectorAll('.tag')"
                                    ".map(t => t.className + ':' + t.textContent)])")
    assert "LARGER than the original" in text and "jpeg → png" in text
    assert tags[:3] == ["tag:written if kept", "tag warn:LARGER", "tag ok:passes"]  # a chip as well as the words
    assert f"{C.kb(source['size'])} → {C.kb(pick['size'])}" in text and f"+{C.kb(pick['size'] - source['size'])}" in text
    assert "SSIM 0.9500 (floor 0.98)" in text  # the variant's scores stay beside the status


def test_an_untouched_card_shows_why(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    _rewrite(out, verdict="untouched", verdict_reason="saving 308 B is below max(1 KB, 1%)")
    _, page = S.build(out)
    [[text, facts, gain]] = run_page(page, "[...cards()].map(c => [c.textContent, "
                                           "...['.facts', '.gain'].map(k => c.querySelector(k).textContent)])")
    assert "saving 308 B is below max(1 KB, 1%)" in text
    assert facts.count("untouched") == 1 and gain.startswith("no change")  # the chip says it, the reason why


def test_the_chip_says_a_lossy_choice_is_written_only_once_kept(tmp_path, factory):
    out = fake_out(tmp_path, factory)  # the pick is lossy: `apply` writes it only after a keep
    _, page = S.build(out)
    chips = run_page(page, """(() => {
      const chip = () => cards()[0].querySelector('.tag').textContent, seen = [chip()];
      const keep = cards()[0].querySelectorAll('input').find(i => i.value === 'keep');
      keep.checked = true; keep.onchange(); seen.push(chip());
      const reject = cards()[0].querySelectorAll('input').find(i => i.value === 'reject');
      reject.checked = true; reject.onchange(); seen.push(chip());
      return seen;
    })()""")
    assert chips == ["written if kept", "will be written", "written if kept"]


def test_the_headline_gives_the_saving_and_both_sizes(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    record = json.loads((out / "photo--x" / "metrics.json").read_text())
    before, after = record["source"]["size"], record["candidates"][0]["size"]
    _, page = S.build(out)
    [gain] = run_page(page, "[...cards()].map(c => c.querySelector('.gain').textContent)")
    pct = round(100 * (before - after) / before)
    assert gain == f"\u2212{C.kb(before - after)} (\u2212{pct}%){C.kb(before)} → {C.kb(after)}"


def test_the_chip_says_a_lossless_choice_is_written(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    record = json.loads((out / "photo--x" / "metrics.json").read_text())
    record["candidates"][0]["kind"] = "lossless"
    (out / "photo--x" / "metrics.json").write_text(json.dumps(record))
    _, page = S.build(out)
    [chip] = run_page(page, "[...cards()].map(c => c.querySelector('.tag').textContent)")
    assert chip == "will be written"  # no keep needed


def with_a_second_variant(out, *, size_delta=10, **extra):
    """fake_out's record plus a pngquant-c16 variant ``size_delta`` bytes larger than the pick."""
    folder = out / "photo--x"
    record = json.loads((folder / "metrics.json").read_text())
    (folder / "c16.png").write_bytes((folder / "pick.png").read_bytes())
    record["candidates"].append({**record["candidates"][0], "label": "pngquant-c16", "file": "c16.png",
                                 "size": record["candidates"][0]["size"] + size_delta, **extra})
    (folder / "metrics.json").write_text(json.dumps(record))


def test_changing_the_variant_keeps_keyboard_focus_on_the_selector(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    with_a_second_variant(out)
    _, page = S.build(out)
    focused = run_page(page, """(() => {
      const s = cards()[0].querySelector('select'); s.focus(); s.value = 'pngquant-c16'; s.onchange();
      const now = cards()[0].querySelector('select');
      return [document.activeElement === now, now.children.find(o => o.selected).value];
    })()""")
    assert focused == [True, "pngquant-c16"]  # arrow keys keep stepping through the variants


def test_a_card_whose_keep_would_write_a_variant_without_crops_says_so(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    record = json.loads((out / "photo--x" / "metrics.json").read_text())
    pick = record["candidates"][0]
    _rewrite(out, source={**record["source"], "size": pick["size"] + 500}, target=record["source"]["path"],
             resize=None, min_saving=10**6, verdict="untouched", verdict_reason="saving 500 B is below the floor")
    with_a_second_variant(out, size_delta=1000)  # larger than the in-place source: a keep writes nothing
    _, page = S.build(out)
    assert _page_data(page)["files"][0]["tiles"] == []  # untouched when built: no crops
    texts = run_page(page, """(() => {
      const before = cards()[0].textContent, s = cards()[0].querySelector('select');
      s.value = 'pngquant-c16'; s.onchange();
      return [before, cards()[0].textContent];
    })()""")
    assert "This page has no 1:1 crops of pngquant-c8. For them, ask the agent" in texts[0]
    assert "1:1 crops" not in texts[1]
