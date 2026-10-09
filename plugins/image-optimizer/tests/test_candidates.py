import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageOps, ImageStat, PngImagePlugin

from imgopt_lib import applying as AP
from imgopt_lib import candidates as C
from imgopt_lib import gates as G
from imgopt_lib import imaging as I
from imgopt_lib import ladder
from imgopt_lib import sheet as S
from imgopt_lib.tools import Tool

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "imgopt.py"


def opts(out, profile="lossless", **kw):
    return C.Options(profile=profile, out=out, gates=G.gates_for(profile), **kw)


def quiet(_):
    return None


def test_the_summary_leads_with_the_whole_batch(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"png", "jpeg"})
    lines = []
    C.run([factory.logo().resolve(), factory.photo().resolve()], opts(tmp_path / "out"), tools, log=lines.append)
    summary = next(l for l in lines if "file(s)," in l)
    assert "overall;" in summary and "to apply" in summary


def test_next_commands_quote_paths_with_spaces(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"png"})
    lines = []
    out = tmp_path / "out with space"
    C.run([factory.logo().resolve()], opts(out), tools, log=lines.append, script=Path("/a b/imgopt.py"))
    nxt = next(l for l in lines if l.startswith("Next:"))
    assert "'/a b/imgopt.py'" in nxt or "nothing to apply" in nxt
    if "apply" in nxt:
        assert f"'{out}'" in nxt


def test_lossless_default_on_a_folder(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"jpeg", "png"})
    factory.photo()
    factory.logo()
    from imgopt_lib.formats import expand_inputs
    records = C.run(expand_inputs([factory.root]), opts(tmp_path / "out"), tools, log=quiet)
    assert len(records) == 2
    for r in records:
        chosen = C.pick_of(r)
        if chosen:
            assert chosen["kind"] == "lossless" and chosen["identical"]
        assert (tmp_path / "out").exists()
    assert len(C.load_records(tmp_path / "out")) == 2


def test_lossless_jpeg_keeps_orientation(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"jpeg"})
    src = factory.photo(orientation=6, quality=95)
    [r] = C.run([src.resolve()], opts(tmp_path / "out"), tools, log=quiet)
    assert not any(c.get("discarded") for c in r["candidates"] if c["label"] == "lossless-jpegoptim")
    chosen = C.pick_of(r)
    assert chosen, "a lossless rung keeps the tag, so one passes"
    folder = C.load_records(tmp_path / "out")[0][0]
    with Image.open(folder / chosen["file"]) as im:
        assert im.getexif().get(0x0112) == 6


def test_few_colour_gradient_keeps_the_lossless_floor(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"png"})
    src = factory.gradient(colors=24)
    [r] = C.run([src.resolve()], opts(tmp_path / "out", "high"), tools, log=quiet)
    assert [c["label"] for c in r["candidates"]] == ["oxipng", "oxipng-zopfli"]
    assert any("palette candidates skipped" in n for n in r["notes"])


def test_banded_palettes_fail_the_gate(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"png"})
    src = factory.gradient(size=(480, 60), lo=20, hi=230)
    [r] = C.run([src.resolve()], opts(tmp_path / "out", "high"), tools, log=quiet)
    palette = [c for c in r["candidates"] if c.get("band_gated") and "band" in c]
    assert palette, "expected palette candidates"
    assert all(c["band"] <= 3 for c in palette if c["pass"])
    assert any("banding" in c["reason"] for c in palette)


def test_cache_reuses_unchanged_candidates_and_invalidates_on_tool_version(factory, toolset, tmp_path, monkeypatch):
    tools = toolset("recompress", "lossless", {"png"})
    src = factory.logo().resolve()
    C.run([src], opts(tmp_path / "out"), tools, log=quiet)

    def boom(*a, **k):
        raise AssertionError("re-encoded a cached candidate")

    monkeypatch.setattr(ladder, "generate", boom)
    C.run([src], opts(tmp_path / "out"), tools, log=quiet)
    bumped = dict(tools)
    bumped["oxipng"] = type(tools["oxipng"])("oxipng", tools["oxipng"].path, "99.0", "path")
    with pytest.raises(AssertionError, match="re-encoded"):
        C.run([src], opts(tmp_path / "out"), bumped, log=quiet)


def counting_generate(monkeypatch):
    """Wrap ladder.generate; the returned list records the label of every rung actually encoded."""
    real, calls = ladder.generate, []

    def spy(rung, **kw):
        calls.append(rung.label)
        return real(rung, **kw)

    monkeypatch.setattr(ladder, "generate", spy)
    return calls


def with_version(tools, name, version):
    bumped = dict(tools)
    bumped[name] = Tool(name, tools[name].path, version, tools[name].source)
    return bumped


def test_cache_invalidates_when_pillow_changes(factory, toolset, tmp_path, monkeypatch):
    tools = toolset("recompress", "lossless", {"png"})
    src = factory.logo().resolve()
    C.run([src], opts(tmp_path / "out"), tools, log=quiet)
    calls = counting_generate(monkeypatch)
    C.run([src], opts(tmp_path / "out"), tools, log=quiet)
    assert calls == []
    C.run([src], opts(tmp_path / "out"), with_version(tools, "pillow", "99.0"), log=quiet)
    assert calls == ["oxipng", "oxipng-zopfli"]


def test_cache_invalidates_when_rsvg_convert_changes(tmp_path, toolset, monkeypatch):
    tools = toolset("recompress", "lossless", {"svg"})
    src = tmp_path / "images" / "dot.svg"
    src.parent.mkdir()
    src.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
                   '<!-- a comment --><circle cx="10" cy="10" r="8" fill="#c8285a"/></svg>')
    C.run([src.resolve()], opts(tmp_path / "out"), tools, log=quiet)
    calls = counting_generate(monkeypatch)
    C.run([src.resolve()], opts(tmp_path / "out"), tools, log=quiet)
    assert calls == []
    C.run([src.resolve()], opts(tmp_path / "out"), with_version(tools, "rsvg-convert", "99.0"), log=quiet)
    assert calls == ["svgo"]


def test_svgo_keeps_ids_and_role(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"svg"})
    src = factory.accessible_svg()
    [r] = C.run([src.resolve()], opts(tmp_path / "out"), tools, log=quiet)
    [cand] = r["candidates"]
    assert "discarded" not in cand, cand.get("discarded")
    folder = C.load_records(tmp_path / "out")[0][0]
    text = (folder / cand["file"]).read_text()
    assert 'role="img"' in text and 'id="t"' in text and 'id="cart-body"' in text


def test_svgo_keeps_classes_of_a_style_block(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"svg"})
    src = factory.styled_svg()
    [r] = C.run([src.resolve()], opts(tmp_path / "out"), tools, log=quiet)
    [cand] = r["candidates"]
    assert "discarded" not in cand, cand.get("discarded")
    folder = C.load_records(tmp_path / "out")[0][0]
    text = (folder / cand["file"]).read_text()
    assert 'class="a"' in text and 'class="b"' in text


def test_svg_pick_that_loses_semantics_is_discarded_with_the_reason(factory, toolset, tmp_path, monkeypatch):
    tools = toolset("recompress", "lossless", {"svg"})
    monkeypatch.setattr(C.metrics, "svg_semantics_lost", lambda a, b: "removed id='t'")
    [r] = C.run([factory.accessible_svg().resolve()], opts(tmp_path / "out"), tools, log=quiet)
    [cand] = r["candidates"]
    assert "removed id='t'" in cand["discarded"]


def test_cache_regenerates_a_candidate_whose_file_was_deleted(factory, toolset, tmp_path, monkeypatch):
    tools = toolset("recompress", "lossless", {"png"})
    src = factory.logo().resolve()
    [first] = C.run([src], opts(tmp_path / "out"), tools, log=quiet)
    picked = C.pick_of(first)
    folder = C.load_records(tmp_path / "out")[0][0]
    (folder / picked["file"]).unlink()
    calls = counting_generate(monkeypatch)
    [r] = C.run([src], opts(tmp_path / "out"), tools, log=quiet)
    assert calls == [picked["label"]]
    assert (folder / picked["file"]).is_file() and C.pick_of(r)["file"] == picked["file"]


def test_same_format_conversion_of_an_optimized_png_is_left_untouched(factory, toolset, tmp_path):
    tools = toolset("convert", "lossless", {"png"}, "png")
    src = factory.logo().resolve()
    subprocess.run([tools["oxipng"].path, "-o", "max", "--strip", "safe", "-q", str(src)], check=True)
    [r] = C.run([src], opts(tmp_path / "out", out_format="png"), tools, log=quiet)
    assert r["verdict"] == "untouched", r["verdict_reason"]
    assert r["target"] == str(src)


def test_gif_under_ref_names_the_real_reason(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"gif"})
    frames = [Image.new("RGB", (40, 40), c) for c in ((250, 0, 0), (0, 250, 0), (0, 0, 250))]
    src, ref = factory.root / "a.gif", factory.root / "b.gif"
    for path, ordered in ((src, frames), (ref, frames[::-1])):
        ordered[0].save(path, save_all=True, append_images=ordered[1:], duration=100, loop=0)
    [r] = C.run([src.resolve()], opts(tmp_path / "out", ref=ref.resolve()), tools, log=quiet)
    [gif] = r["candidates"]
    assert "reference is not the source" in gif["reason"]


def test_ref_with_an_svg_input_is_a_usage_error(factory, tmp_path):
    svg = factory.root / "dot.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>')
    ref = factory.logo()
    proc = subprocess.run([sys.executable, str(SCRIPT), "candidates", str(svg), "--ref", str(ref),
                           "--out", str(tmp_path / "o")], capture_output=True, text=True)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "--ref" in proc.stderr and "SVG" in proc.stderr
    assert not (tmp_path / "o").exists()


def test_summary_names_the_tools_even_when_every_input_was_skipped(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"png"})
    bad = factory.photo(name="bad.png", icc=b"not an ICC profile")
    lines = []
    assert C.run([bad.resolve()], opts(tmp_path / "out"), tools, log=lines.append) == []
    assert any(line.startswith("tools: ") and "oxipng" in line for line in lines)


def test_ref_measures_lossless_candidates_against_the_baseline(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"jpeg"})
    original = factory.photo(name="orig.jpg", quality=95)
    current = factory.photo(name="current.jpg", quality=50)
    [r] = C.run([current.resolve()], opts(tmp_path / "out", "high", ref=original.resolve()), tools, log=quiet)
    lossless = [c for c in r["candidates"] if c["label"].startswith("lossless-") and "ssim" in c]
    assert lossless and all(not c["identical"] and c["kind"] == "lossy" for c in lossless)
    assert r["ref"]["path"] == str(original.resolve())


def test_webp_output_is_labelled_uncalibrated(factory, toolset, tmp_path):
    tools = toolset("convert", "high", {"png"}, "webp")
    src = factory.logo().resolve()
    [r] = C.run([src], opts(tmp_path / "out", "high", out_format="webp"), tools, log=quiet)
    assert r["uncalibrated"] and r["format"] == "webp"
    assert r["target"].endswith(".webp")


def test_png_to_jpeg_flattens_alpha_with_a_note(factory, toolset, tmp_path):
    tools = toolset("convert", "high", {"png"}, "jpeg")
    src = factory.logo().resolve()
    [r] = C.run([src], opts(tmp_path / "out", "high", out_format="jpeg"), tools, log=quiet)
    assert any("flattened onto white" in n for n in r["notes"])


def test_cli_candidates_prints_tools_and_next_step(factory, tmp_path):
    src = factory.logo()
    proc = subprocess.run([sys.executable, str(SCRIPT), "candidates", str(src), "--out", str(tmp_path / "o")],
                          capture_output=True, text=True)
    if proc.returncode == 2 and "BLOCKED" in proc.stdout:
        pytest.skip("tools missing: " + proc.stdout.splitlines()[-1])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "tools:" in proc.stdout and "Next:" in proc.stdout


def test_unconvertible_colour_profile_skips_that_file_and_continues(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"png"})
    bad = factory.photo(name="bad.png", icc=b"not an ICC profile")
    good = factory.logo()
    lines = []
    records = C.run([bad.resolve(), good.resolve()], opts(tmp_path / "out"), tools, log=lines.append)
    assert [r["source"]["path"] for r in records] == [str(good.resolve())]
    assert any(f"== {bad.resolve()}: skipped:" in line for line in lines)
    assert [r["source"]["path"] for _, r in C.load_records(tmp_path / "out")] == [str(good.resolve())]
    assert not list((tmp_path / "out").glob("*/metrics.json.tmp"))
    assert len([d for d in (tmp_path / "out").iterdir() if d.is_dir()]) == 1, "the skipped source left a folder behind"


def test_cli_exits_1_when_a_file_was_skipped(factory, tmp_path):
    factory.photo(name="bad.png", icc=b"not an ICC profile")
    factory.logo()
    proc = subprocess.run([sys.executable, str(SCRIPT), "candidates", str(factory.root), "--out", str(tmp_path / "o")],
                          capture_output=True, text=True)
    if proc.returncode == 2 and "BLOCKED" in proc.stdout:
        pytest.skip("tools missing: " + proc.stdout.splitlines()[-1])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "skipped:" in proc.stdout and "Next:" in proc.stdout
    assert len(C.load_records(tmp_path / "o")) == 1


def test_unreadable_source_skips_that_file_and_continues(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"jpeg", "png"})
    bad = factory.truncated_jpeg()
    good = factory.logo()
    lines = []
    records = C.run([bad.resolve(), good.resolve()], opts(tmp_path / "out"), tools, log=lines.append)
    assert [r["source"]["path"] for r in records] == [str(good.resolve())]
    assert any(f"== {bad.resolve()}: skipped:" in line for line in lines)
    assert len([d for d in (tmp_path / "out").iterdir() if d.is_dir()]) == 1, "the skipped source left a folder behind"


def test_cli_exits_1_when_a_source_is_unreadable(factory, tmp_path):
    factory.truncated_jpeg()
    factory.logo()
    proc = subprocess.run([sys.executable, str(SCRIPT), "candidates", str(factory.root), "--out", str(tmp_path / "o")],
                          capture_output=True, text=True)
    if proc.returncode == 2 and "BLOCKED" in proc.stdout:
        pytest.skip("tools missing: " + proc.stdout.splitlines()[-1])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "skipped:" in proc.stdout
    assert len(C.load_records(tmp_path / "o")) == 1


def test_unreadable_candidate_output_becomes_that_candidates_error(factory, toolset, tmp_path, monkeypatch):
    tools = toolset("recompress", "lossless", {"png"})
    src = factory.logo().resolve()
    real = C.read_facts

    def flaky(path):
        if Path(path).name.startswith("oxipng"):
            raise OSError("image file is truncated")
        return real(path)

    monkeypatch.setattr(C, "read_facts", flaky)
    [r] = C.run([src], opts(tmp_path / "out"), tools, log=quiet)
    assert [c["label"] for c in r["candidates"]] == ["oxipng", "oxipng-zopfli"]
    assert all("truncated" in c["error"] and not c["pass"] for c in r["candidates"])
    assert r["pick"] is None


def test_pick_of_is_none_without_a_pick_even_when_a_candidate_has_no_file():
    record = {"pick": None, "candidates": [{"label": "pngquant-c8", "error": "encoder failed"}]}
    assert C.pick_of(record) is None


def _refused_without_approval(out, src):
    before = src.read_bytes()
    logs = []
    assert AP.apply(out, tools={}, log=logs.append) == 1
    assert "--approve" in "\n".join(logs)
    assert src.read_bytes() == before


def test_display_p3_png_under_high_keeps_its_profile_or_waits_for_approval(factory, device_icc, toolset, tmp_path):
    tools = toolset("recompress", "high", {"png"})
    src = factory.photo(name="p3.png", icc=device_icc).resolve()
    [r] = C.run([src], opts(tmp_path / "out", "high"), tools, log=quiet)
    assert any("device colour profile" in n and "shifts mean" in n for n in r["notes"])
    oxipng = next(c for c in r["candidates"] if c["label"] == "oxipng")
    assert oxipng["kind"] == "lossless" and oxipng["pass"], oxipng
    folder = C.load_records(tmp_path / "out")[0][0]
    for c in r["candidates"]:  # whatever is called lossless kept the profile
        if c["kind"] == "lossless" and c.get("file"):
            assert I.read_facts(folder / c["file"]).icc == device_icc, c["label"]
    chosen = C.pick_of(r)
    assert chosen, "the profile-keeping lossless rung passes, so there is a pick"
    if chosen["label"] == "oxipng":
        assert I.read_facts(folder / chosen["file"]).icc == device_icc
    else:  # a pixel rung won on size: it is lossy, so it is tiled and waits for approval
        assert chosen["kind"] == "lossy" and S.needs_tiles(r)
        if r["verdict"] == "apply":
            _refused_without_approval(tmp_path / "out", src)


def test_a_resized_png_is_a_lossy_pick_that_waits_for_approval(factory, toolset, tmp_path):
    tools = toolset("prepare", "high", {"png"})
    src = factory.logo(size=(300, 300)).resolve()
    [r] = C.run([src], opts(tmp_path / "out", "high", resize=150), tools, log=quiet)
    oxipng = next(c for c in r["candidates"] if c["label"] == "oxipng")
    assert oxipng["identical"] and oxipng["kind"] == "lossy"
    chosen = C.pick_of(r)
    assert chosen and chosen["kind"] == "lossy" and r["verdict"] == "apply" and S.needs_tiles(r)
    _refused_without_approval(tmp_path / "out", src)


def test_rotated_jpeg_under_high_bakes_the_orientation_into_pixel_rungs(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"jpeg"})
    src = factory.photo(name="rot.jpg", size=(160, 120), orientation=6, quality=95).resolve()
    [r] = C.run([src], opts(tmp_path / "out", "high"), tools, log=quiet)
    folder = C.load_records(tmp_path / "out")[0][0]
    reference = Image.open(folder / "reference.png")
    assert reference.size == (120, 160)
    guetzli = [c for c in r["candidates"] if c["label"].startswith("guetzli-") and c.get("file")]
    assert guetzli, "guetzli reads oriented pixels on a rotated source"
    upside_down = ImageOps.flip(ImageOps.mirror(reference.convert("RGB")))
    for c in guetzli:
        out = I.read_facts(folder / c["file"])
        assert (out.orientation, out.width, out.height) == (1, 120, 160), c["label"]
        assert c["kind"] == "lossy", c
        shown = I.display_pixels(folder / c["file"]).convert("RGB")
        assert _mean_diff(shown, reference.convert("RGB")) < _mean_diff(shown, upside_down), c["label"]
    assert C.pick_of(r), "a rotated JPEG still gets a pick"


def _mean_diff(a, b):
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean)


def test_cli_lossless_resize_of_a_png_is_a_usage_error_and_writes_nothing(factory, tmp_path):
    src = factory.logo()
    proc = subprocess.run([sys.executable, str(SCRIPT), "candidates", str(src), "--resize", "60",
                           "--out", str(tmp_path / "o")], capture_output=True, text=True)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "lossless profile cannot re-encode pixels" in proc.stderr and src.name in proc.stderr
    assert not (tmp_path / "o").exists()


@pytest.mark.parametrize("name", ["photo.jpeg", "photo.JPG"])
def test_a_same_format_resize_targets_the_source_itself(factory, toolset, tmp_path, name):
    tools = toolset("prepare", "high", {"jpeg"})
    src = factory.root / name  # a smooth JPEG: the noisy test photo finds no pick once halved
    Image.open(factory.gradient(size=(320, 240))).save(src, "JPEG", quality=95)
    [r] = C.run([src.resolve()], opts(tmp_path / "out", "high", resize=160), tools, log=quiet)
    assert r["target"] == str(src.resolve())
    assert r["verdict"] == "apply" and "replaces the original" in r["verdict_reason"], r["verdict_reason"]


def test_a_file_whose_content_does_not_match_its_name_is_skipped_and_the_run_goes_on(factory, toolset, tmp_path):
    """The tool check reads extensions and the ladder the decoded format, so a JPEG named .png must not
    reach the ladder: it would ask for JPEG encoders nobody checked for and abort the whole batch."""
    tools = toolset("recompress", "high", {"png"})
    bad = factory.photo(name="b.jpg").rename(factory.root / "b.png").resolve()
    good = factory.logo(size=(200, 200)).resolve()
    lines = []
    records = C.run([bad, good], opts(tmp_path / "out", "high"), tools, log=lines.append)
    assert [r["source"]["path"] for r in records] == [str(good)]
    assert any(f"== {bad}: skipped:" in line and "its content is JPEG" in line for line in lines), lines


def _without(tools, name):
    return {**tools, name: Tool(name, None, note="not installed")}


def test_the_waived_note_names_only_tools_the_human_waived(factory, toolset, tmp_path):
    tools = _without(toolset("recompress", "high", {"jpeg"}), "guetzli")
    src = factory.photo().resolve()
    [r] = C.run([src], opts(tmp_path / "out", "high", waived=("guetzli",)), tools, log=quiet)
    assert any("tools waived:" in n and "guetzli" in n for n in r["notes"])
    assert not any(c["label"].startswith("guetzli") for c in r["candidates"])


def test_a_missing_jpegli_skips_its_rungs_with_a_note(factory, toolset, tmp_path):
    tools = dict(toolset("recompress", "high", {"jpeg"}))
    tools["cjpegli"] = Tool("cjpegli", None)
    lines = []
    [r] = C.run([factory.photo(size=(200, 150)).resolve()], opts(tmp_path / "out", "high", waived=()), tools,
                log=lines.append)
    assert r["optional_missing"] == ["cjpegli"]
    assert not any(c["tool"] == "cjpegli" for c in r["candidates"])
    assert any(l.startswith("OPTIONAL TOOLS MISSING") and "cjpegli" in l for l in lines)


def test_a_missing_tool_nobody_waived_is_a_bug_not_a_note(factory, toolset, tmp_path):
    tools = _without(toolset("recompress", "high", {"jpeg"}), "guetzli")
    with pytest.raises(RuntimeError, match="guetzli"):
        C.run([factory.photo().resolve()], opts(tmp_path / "out", "high"), tools, log=quiet)


@pytest.mark.parametrize("make_bad, reason", [
    (lambda f: f.deep_png(), "16-bit images are not supported yet"),
    (lambda f: f.photo(name="small.png", size=(40, 30)), "would upscale"),
])
def test_a_file_the_job_cannot_serve_is_skipped_and_the_run_goes_on(factory, toolset, tmp_path, make_bad, reason):
    tools = toolset("prepare", "high", {"png"})
    bad = make_bad(factory).resolve()
    good = factory.logo(size=(200, 200)).resolve()
    lines = []
    records = C.run([bad, good], opts(tmp_path / "out", "high", resize=100), tools, log=lines.append)
    assert [r["source"]["path"] for r in records] == [str(good)]
    assert any(f"== {bad}: skipped:" in line and reason in line for line in lines), lines


def _encoders_fail(monkeypatch):
    def boom(rung, **kw):
        raise ladder.EncodeError(f"{rung.label}: encoder crashed")
    monkeypatch.setattr(ladder, "generate", boom)


def test_a_file_whose_every_candidate_errored_is_a_problem_not_a_verdict(factory, toolset, tmp_path, monkeypatch):
    tools = toolset("recompress", "lossless", {"png"})
    _encoders_fail(monkeypatch)
    lines = []
    [r] = C.run([factory.logo().resolve()], opts(tmp_path / "out"), tools, log=lines.append)
    assert C.all_errored(r)
    assert any("problem:" in line and "encoder crashed" in line for line in lines), lines


def test_cli_exits_1_when_every_candidate_of_a_file_errored(factory, tmp_path, monkeypatch, capsys):
    from imgopt_lib import cli
    _encoders_fail(monkeypatch)
    code = cli.main(["candidates", str(factory.logo()), "--out", str(tmp_path / "o")])
    if code == 2 and "BLOCKED" in capsys.readouterr().out:
        pytest.skip("tools missing")
    assert code == 1


@pytest.mark.parametrize("inside", ["", "work/deeper"])
def test_cli_refuses_an_out_folder_inside_an_input_folder(factory, inside):
    factory.logo()
    out = factory.root / inside if inside else factory.root
    proc = subprocess.run([sys.executable, str(SCRIPT), "candidates", str(factory.root), "--out", str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "--out" in proc.stderr and str(factory.root.resolve()) in proc.stderr
    assert sorted(p.name for p in factory.root.iterdir()) == ["logo.png"]


def test_a_gif_in_a_convert_batch_is_skipped_not_the_batch(factory, toolset, tmp_path):
    tools = toolset("convert", "high", {"jpeg"}, "webp")
    photo = factory.photo(size=(200, 150))
    gif = factory.root / "a.gif"
    Image.new("P", (10, 10)).save(gif)
    lines = []
    records = C.run([photo.resolve(), gif.resolve()], opts(tmp_path / "out", "high", out_format="webp"), tools,
                    log=lines.append)
    assert len(records) == 1
    assert any("a.gif: skipped: GIF supports only in-place lossless optimization" in l for l in lines)


def test_a_batch_where_every_file_is_refused_is_a_usage_error(factory, tmp_path):
    gif = factory.root / "a.gif"
    factory.root.mkdir(parents=True, exist_ok=True)
    Image.new("P", (10, 10)).save(gif)
    with pytest.raises(ladder.UsageError, match="a.gif"):
        C.run([gif.resolve()], opts(tmp_path / "out", "high", out_format="webp"), {}, log=quiet)


def test_out_inside_the_git_work_tree_of_a_file_input_is_refused(factory, tmp_path):
    repo = tmp_path / "repo"
    (repo / "img").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    src = repo / "img" / "a.png"
    src.write_bytes(factory.logo().read_bytes())
    proc = subprocess.run([sys.executable, str(SCRIPT), "candidates", str(src), "--out", str(repo / "work")],
                          capture_output=True, text=True)
    assert proc.returncode == 2 and "inside the git work tree" in proc.stderr
    assert not (repo / "work").exists()


def test_out_gets_a_gitignore(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"png"})
    C.run([factory.logo().resolve()], opts(tmp_path / "out"), tools, log=quiet)
    assert (tmp_path / "out" / ".gitignore").read_text() == "*\n"


def test_the_pick_records_the_ssim_a_reviewer_will_see(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"jpeg"})
    [r] = C.run([factory.photo(quality=95).resolve()], opts(tmp_path / "out", "high"), tools, log=quiet)
    chosen = C.pick_of(r)
    assert chosen and chosen["kind"] == "lossy", r["verdict_reason"]
    assert chosen["ssim_evidence"] >= 0.98
    assert abs(chosen["ssim_evidence"] - chosen["ssim"]) < 2e-3  # the decoders differ by up to about 1e-3


def test_a_pick_the_reviewer_check_fails_gives_way_to_the_next(factory, toolset, tmp_path, monkeypatch):
    from imgopt_lib import compare as CP
    tools = toolset("recompress", "high", {"jpeg"})
    calls = []

    def reviewer(ffmpeg, ref, new, ri, ni):
        calls.append(new.name)
        return (0.97 if len(calls) == 1 else 0.99), "graph"
    monkeypatch.setattr(CP, "reviewer_check", reviewer)
    src = factory.photo(quality=95).resolve()
    [r] = C.run([src], opts(tmp_path / "out", "high"), tools, log=quiet)
    first = next(c for c in r["candidates"] if c.get("file") == calls[0])
    assert not first["pass"] and first["reason"] == "Evidence SSIM 0.970000 < 0.98"
    chosen = C.pick_of(r)
    assert chosen["file"] != calls[0] and chosen["size"] >= first["size"]
    assert chosen.get("identical") or chosen["ssim_evidence"] == 0.99

    def no_more(*a):
        raise AssertionError("a cached pick is not re-checked")
    monkeypatch.setattr(CP, "reviewer_check", no_more)
    [again] = C.run([src], opts(tmp_path / "out", "high"), tools, log=quiet)
    assert again["pick"] == r["pick"]


def test_a_pick_ffmpeg_cannot_reproduce_says_why(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"jpeg"})
    [r] = C.run([factory.photo(quality=95, orientation=6).resolve()], opts(tmp_path / "out", "high"), tools,
                log=quiet)
    chosen = C.pick_of(r)
    assert chosen and chosen["kind"] == "lossy", r["verdict_reason"]
    assert chosen["ssim_evidence"] is None and "EXIF orientation" in chosen["evidence_note"]


def test_the_summary_flags_picks_larger_than_their_original(tmp_path):
    def record(name, before, after):
        return {"source": {"path": f"/x/{name}", "size": before}, "verdict": "apply", "pick": "p",
                "candidates": [{"file": "p", "size": after, "kind": "lossy"}], "uncalibrated": False,
                "waived": []}
    lines = []
    C.summarize([record("a.png", 100, 120), record("b.png", 100, 80)], tmp_path, Path("imgopt.py"), {},
                log=lines.append)
    assert any(line.startswith("LARGER: 1 pick(s)") and line.endswith("a.png") for line in lines), lines


def test_a_resize_that_finds_no_pick_names_the_closest_and_suggests_medium(factory, toolset, tmp_path):
    tools = toolset("prepare", "high", {"jpeg"})
    src = factory.photo(size=(320, 240), quality=95).resolve()  # noisy: nothing passes once halved
    [r] = C.run([src], opts(tmp_path / "out", "high", resize=160), tools, log=quiet)
    assert r["pick"] is None
    assert r["verdict_reason"].startswith("no candidate passed the gates; closest: ")
    assert "SSIM 0." in r["verdict_reason"] and "--profile medium" in r["verdict_reason"]


def test_a_resize_to_the_width_both_images_already_have_is_skipped(factory, toolset, tmp_path):
    tools = toolset("prepare", "high", {"jpeg"})
    src = factory.photo(size=(160, 120), quality=95).resolve()
    lines = []
    assert C.run([src], opts(tmp_path / "out", "high", resize=160), tools, log=lines.append) == []
    assert any("already displays 160 px wide" in line for line in lines), lines


def test_a_file_already_resized_can_be_measured_against_its_larger_original(factory, toolset, tmp_path):
    """The Baselines follow-up: the merged file is 160 px, --ref is the 320 px pre-merge original."""
    tools = toolset("prepare", "high", {"jpeg"})
    original = factory.photo(name="original.jpg", size=(320, 240), quality=95).resolve()
    merged = factory.root / "merged.jpg"
    I.resize_width(Image.open(original), 160).save(merged, "JPEG", quality=90)
    [r] = C.run([merged.resolve()], opts(tmp_path / "out", "high", resize=160, ref=original), tools, log=quiet)
    assert r["candidates"] and r["ref"]["path"] == str(original)


def test_an_mpo_phone_jpeg_gets_the_reviewer_check(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"jpeg"})
    first = Image.open(factory.photo(name="a.jpg", quality=95))
    src = factory.root / "phone.jpg"
    first.save(src, "MPO", save_all=True, append_images=[first.copy()], quality=95)
    [r] = C.run([src.resolve()], opts(tmp_path / "out", "high"), tools, log=quiet)
    chosen = C.pick_of(r)
    assert chosen and chosen["kind"] == "lossy", r["verdict_reason"]
    assert chosen["ssim_evidence"] is not None, chosen.get("evidence_note")


def test_a_failed_ffmpeg_run_is_noted_and_checked_again_next_run(factory, toolset, tmp_path, monkeypatch):
    from imgopt_lib import compare as CP
    from imgopt_lib.metrics import MetricError
    tools = toolset("recompress", "high", {"jpeg"})

    def broken(*a):
        raise MetricError("ffmpeg timed out")
    monkeypatch.setattr(CP, "reviewer_check", broken)
    src = factory.photo(quality=95).resolve()
    [r] = C.run([src], opts(tmp_path / "out", "high"), tools, log=quiet)
    chosen = C.pick_of(r)
    assert chosen["ssim_evidence"] is None and "ffmpeg timed out" in chosen["evidence_note"]
    calls = []
    monkeypatch.setattr(CP, "reviewer_check", lambda *a: calls.append(1) or (0.99, "graph"))
    [again] = C.run([src], opts(tmp_path / "out", "high"), tools, log=quiet)
    assert calls and C.pick_of(again)["ssim_evidence"] == 0.99
    assert "evidence_failed" not in C.pick_of(again)


def test_no_pick_reason_names_why_the_closest_failed_and_skips_hints_for_tool_failures():
    o = opts(Path("out"), "high", resize=100)
    close = {"label": "cjpeg-q95", "ssim": 0.97, "ss2": 85.0, "reason": "SSIM 0.9700 < 0.98", "pass": False}
    assert "(failed: SSIM 0.9700 < 0.98)" in C._no_pick_reason([close], o)
    errored = [{"label": "a", "error": "crashed", "pass": False}]
    assert C._no_pick_reason(errored, o) == "no candidate passed the gates; every candidate errored"


def test_the_cache_key_changes_when_a_versionless_tool_is_replaced(tmp_path):
    exe = tmp_path / "ssimulacra2"
    exe.write_bytes(b"old build")
    rung = ladder.Rung("oxipng", "oxipng", "lossless", "source", ".png", ())
    tools = {"ssimulacra2": Tool("ssimulacra2", str(exe), "unknown", "path")}
    before = C._key("src", "ref", opts(tmp_path / "o"), rung, tools)
    exe.write_bytes(b"a newer build")
    assert C._key("src", "ref", opts(tmp_path / "o"), rung, tools) != before


def test_a_gray_source_gets_a_one_channel_pixels_file(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"jpeg"})
    src = factory.photo(gray=True).resolve()
    C.run([src], opts(tmp_path / "out", "high"), tools, log=quiet)
    folder = C.load_records(tmp_path / "out")[0][0]
    assert Image.open(folder / "pixels_gray.png").mode == "L"


def test_an_apng_keeps_every_frame_under_high(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"png"})
    src = factory.apng(frames=3)
    [r] = C.run([src.resolve()], opts(tmp_path / "out", "high"), tools, log=quiet)
    chosen = C.pick_of(r)
    assert r["source"]["frames"] == 3
    if chosen:
        folder = C.load_records(tmp_path / "out")[0][0]
        with Image.open(folder / chosen["file"]) as im:
            assert im.n_frames == 3


def test_a_lossless_pick_keeps_the_gamma_chunk(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"png"})
    src = factory.gamma_png(size=(400, 300))
    [r] = C.run([src.resolve()], opts(tmp_path / "out"), tools, log=quiet)
    for c in r["candidates"]:
        if c.get("file"):
            folder = C.load_records(tmp_path / "out")[0][0]
            assert I.read_facts(folder / c["file"]).colour_chunks == I.read_facts(src).colour_chunks


def test_a_lossless_jpeg_pick_lists_removed_metadata(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"jpeg"})
    src = factory.root / "c.jpg"
    Image.open(factory.photo(size=(400, 300), quality=95)).save(src, "JPEG", quality=95, comment=b"(c) Someone")
    lines = []
    [r] = C.run([src.resolve()], opts(tmp_path / "out"), tools, log=lines.append)
    stripped = [c for c in r["candidates"] if c.get("metadata_removed")]
    assert stripped and all("comment" in c["metadata_removed"] for c in stripped)
    if C.pick_of(r) and C.pick_of(r).get("metadata_removed"):
        assert any("removes metadata: " in line for line in lines)


def test_a_lossy_pixel_candidate_lists_removed_metadata(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"png"})
    info = PngImagePlugin.PngInfo()
    info.add_text("Copyright", "Someone")
    src = factory.root / "t.png"
    Image.open(factory.gradient(colors=200)).save(src, pnginfo=info)
    [r] = C.run([src.resolve()], opts(tmp_path / "out", "high"), tools, log=quiet)
    plan = ladder.plan(I.read_facts(src), profile="high")
    from_pixels = {rung.label for rung in plan.rungs if rung.input.startswith("pixels")}
    made = [c for c in r["candidates"] if c["label"] in from_pixels and "file" in c]
    assert made and all(c.get("metadata_removed") == ["text"] for c in made)


def test_print_record_names_the_metadata_the_pick_removes():
    record = {"source": {"path": "a.jpg", "size": 2048, "width": 10, "height": 10, "format": "jpeg", "colors": None},
              "notes": [], "verdict": "apply", "verdict_reason": "smaller", "pick": "a.jpg",
              "candidates": [{"label": "x", "file": "a.jpg", "size": 1024, "pass": True, "metadata_removed": ["exif"]}]}
    lines = []
    C.print_record(record, log=lines.append)
    assert any("pick removes metadata: exif" in line for line in lines)


def test_records_from_an_earlier_run_are_ignored(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"png", "jpeg"})
    a, b = factory.logo(), factory.photo()
    out = tmp_path / "out"
    C.run([a.resolve(), b.resolve()], opts(out), tools, log=quiet)
    C.run([a.resolve()], opts(out), tools, log=quiet)
    assert [Path(r["source"]["path"]).name for _, r in C.load_records(out)] == ["logo.png"]
    assert any("photo.jpg" in s and "not in the last run" in s for s in C.stale_records(out))


def test_records_made_with_other_settings_are_ignored(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"png"})
    src = factory.logo().resolve()
    out = tmp_path / "out"
    C.run([src], opts(out, "high"), tools, log=quiet)
    (folder, record), = C.load_records(out)
    record["profile"] = "medium"
    (folder / "metrics.json").write_text(json.dumps(record))
    assert C.load_records(out) == []
    assert any("other settings" in s for s in C.stale_records(out))


def test_an_interrupted_record_is_reused_and_not_applied(factory, toolset, tmp_path, monkeypatch):
    tools = toolset("recompress", "lossless", {"png"})
    src = factory.logo()
    out = tmp_path / "out"
    calls = {"n": 0}
    real = ladder.generate

    def die_on_second(rung, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real(rung, **kw)

    monkeypatch.setattr(ladder, "generate", die_on_second)
    with pytest.raises(KeyboardInterrupt):
        C.run([src.resolve()], opts(out), tools, log=quiet)
    [meta] = list(out.glob("*/metrics.json"))
    partial = json.loads(meta.read_text())
    assert partial["complete"] is False and len(partial["candidates"]) == 1
    assert C.load_records(out) == []
    assert any("interrupted" in s for s in C.stale_records(out))
    monkeypatch.setattr(ladder, "generate", real)
    first_key = partial["candidates"][0]["key"]
    [r] = C.run([src.resolve()], opts(out), tools, log=quiet)
    assert r["complete"] and r["candidates"][0]["key"] == first_key
    assert len(C.load_records(out)) == 1 and C.stale_records(out) == []


@pytest.mark.parametrize("command", ["sheet", "apply"])
def test_the_cli_names_the_folders_it_ignores(factory, toolset, tmp_path, command):
    tools = toolset("recompress", "lossless", {"png", "jpeg"})
    a, b = factory.logo(), factory.photo()
    out = tmp_path / "out"
    C.run([a.resolve(), b.resolve()], opts(out), tools, log=quiet)
    C.run([a.resolve()], opts(out), tools, log=quiet)
    proc = subprocess.run([sys.executable, str(SCRIPT), command, str(out)], capture_output=True, text=True)
    assert any(l.startswith("  ignored:") and "photo.jpg" in l for l in proc.stdout.splitlines()), proc.stdout


def test_an_encoder_refusal_is_cached(factory, toolset, tmp_path, monkeypatch):
    tools = toolset("recompress", "lossless", {"png"})
    src = factory.logo()
    real = ladder.generate
    calls = []

    def refuse_oxipng(rung, **kw):
        calls.append(rung.label)
        if rung.label == "oxipng":
            raise ladder.EncoderRefused("oxipng: oxipng exited 1: unsupported")
        return real(rung, **kw)

    monkeypatch.setattr(ladder, "generate", refuse_oxipng)
    C.run([src.resolve()], opts(tmp_path / "out"), tools, log=quiet)
    C.run([src.resolve()], opts(tmp_path / "out"), tools, log=quiet)
    assert calls.count("oxipng") == 1
