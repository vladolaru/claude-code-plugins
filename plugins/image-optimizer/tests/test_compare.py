import functools
import http.server
import subprocess
import threading

import pytest

from imgopt_lib import cli
from imgopt_lib import compare as CP
from imgopt_lib import metrics
from imgopt_lib.ladder import UsageError


def test_identical_files(factory, toolset, tmp_path):
    tools = toolset("compare")
    a = factory.photo()
    r = CP.compare(str(a), str(a), tools, tmp_path)
    assert r["identical"] and r["reproducible"]


@pytest.mark.parametrize("kind", ["alpha_png", "jpeg"])
def test_reviewer_one_liner_prints_exactly_the_reported_number(factory, toolset, tmp_path, kind):
    """The evidence quotes ssim_evidence, so the shell one-liner must print it
    digit for digit. ffmpeg's default overlay composites in YUV and drifted
    5e-4 from Pillow; ffmpeg's JPEG decoder drifts up to ~1e-3 from Pillow's."""
    tools = toolset("compare")
    from PIL import Image
    if kind == "alpha_png":
        a = factory.logo(name="a.png")
        b = tmp_path / "b.png"
        Image.open(a).convert("RGBA").quantize(8).convert("RGBA").save(b)
    else:
        a = factory.photo(name="a.jpg", quality=95)
        b = factory.photo(name="b.jpg", quality=60)
    r = CP.compare(str(a), str(b), tools, tmp_path / "w")
    assert r["reproducible"]
    cmd = r["command"].replace("REF", f"'{a}'").replace("NEW", f"'{b}'")
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout
    assert f"All:{r['ssim_evidence']:.6f}" in out
    assert abs(r["ssim_evidence"] - r["ssim_white"]) <= 5e-4


def test_resized_or_profiled_pairs_are_marked_not_reproducible(factory, toolset, tmp_path):
    tools = toolset("compare")
    a = factory.photo(name="a.jpg", size=(160, 120))
    b = factory.photo(name="b.jpg", size=(80, 60))
    r = CP.compare(str(a), str(b), tools, tmp_path)
    assert not r["reproducible"] and "dimensions differ" in r["reason"]
    assert r["command"] is None and r["ssim_evidence"] is None


def test_oriented_pair_is_marked_not_reproducible(factory, toolset, tmp_path):
    tools = toolset("compare")
    a = factory.photo(name="a.jpg", size=(160, 120), orientation=6)
    b = factory.photo(name="b.jpg", size=(160, 120), orientation=6, quality=60)
    r = CP.compare(str(a), str(b), tools, tmp_path)
    assert not r["reproducible"] and "orientation" in r["reason"]


def test_device_profile_pair_is_marked_not_reproducible(factory, toolset, device_icc, tmp_path):
    tools = toolset("compare")
    a = factory.photo(name="a.jpg", icc=device_icc)
    b = factory.photo(name="b.jpg", icc=device_icc, quality=60)
    r = CP.compare(str(a), str(b), tools, tmp_path)
    assert not r["reproducible"] and "colour profile" in r["reason"]


def test_git_spec(factory, toolset, tmp_path):
    tools = toolset("compare")
    repo = tmp_path / "repo"
    repo.mkdir()
    img = factory.photo(name="p.jpg")
    target = repo / "p.jpg"
    target.write_bytes(img.read_bytes())
    for cmd in (["git", "init", "-q"], ["git", "add", "p.jpg"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
        subprocess.run(cmd, cwd=repo, check=True)
    r = CP.compare("git:HEAD:p.jpg", str(target), tools, tmp_path / "w", cwd=repo)
    assert r["identical"]


def test_fetched_files_with_the_same_name_do_not_collide(tmp_path):
    repo = tmp_path / "repo"
    (repo / "x").mkdir(parents=True)
    (repo / "y").mkdir()
    (repo / "x" / "p.jpg").write_bytes(b"one")
    (repo / "y" / "p.jpg").write_bytes(b"two")
    for cmd in (["git", "init", "-q"], ["git", "add", "."],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
        subprocess.run(cmd, cwd=repo, check=True)
    one = CP.fetch("git:HEAD:x/p.jpg", tmp_path / "w", cwd=repo)
    two = CP.fetch("git:HEAD:y/p.jpg", tmp_path / "w", cwd=repo)
    assert one != two and one.read_bytes() == b"one" and two.read_bytes() == b"two"


def test_url_spec(factory, tmp_path):
    img = factory.photo(name="p.jpg")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(img.parent))
    handler.log_message = lambda *a, **k: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        got = CP.fetch(f"http://127.0.0.1:{server.server_port}/p.jpg?raw=1", tmp_path / "w")
    finally:
        server.shutdown()
        server.server_close()
    assert got.name == "p.jpg" and got.read_bytes() == img.read_bytes()


def test_bad_spec_is_a_value_error(tmp_path):
    with pytest.raises(ValueError, match="not a file"):
        CP.fetch(str(tmp_path / "missing.png"), tmp_path / "w")
    with pytest.raises(ValueError, match="git:<rev>:<path>"):
        CP.fetch("git:HEAD", tmp_path / "w")


def test_svg_is_refused(tmp_path, toolset):
    tools = toolset("compare")
    svg = tmp_path / "a.svg"
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    with pytest.raises(ValueError, match="raster"):
        CP.compare(str(svg), str(svg), tools, tmp_path / "w")


def test_cli_prints_the_evidence_number_and_the_tools_line(factory, toolset, tmp_path, capsys):
    toolset("compare")
    a = factory.photo(name="a.jpg", quality=95)
    b = factory.photo(name="b.jpg", quality=60)
    assert cli.main(["compare", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "Evidence SSIM (luma): " in out and "ffmpeg -hide_banner -i REF -i NEW" in out
    assert "ffmpeg version" in out or "ffmpeg:" in out
    assert "ffmpeg" in out.splitlines()[-1]


def test_cli_maps_failures_to_exit_2(tmp_path, capsys, toolset, monkeypatch):
    toolset("compare")
    assert cli.main(["compare", str(tmp_path / "nope.png"), str(tmp_path / "nope.png")]) == 2
    assert "error:" in capsys.readouterr().err
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    assert cli.main(["compare", "git:HEAD:p.jpg", "git:HEAD:p.jpg"]) == 2
    assert "error:" in capsys.readouterr().err
    assert cli.main(["compare", "http://127.0.0.1:9/a.jpg", "http://127.0.0.1:9/a.jpg"]) == 2
    assert "error:" in capsys.readouterr().err


def test_git_spec_cannot_inject_options(tmp_path):
    target = tmp_path / "x"
    with pytest.raises(UsageError, match="git:--output"):
        CP.fetch(f"git:--output={target}:p", tmp_path / "w", cwd=tmp_path)
    assert not target.exists()


def _gif(path, second, size=(40, 30)):
    from PIL import Image
    first = Image.new("RGB", size, (200, 30, 30))
    first.save(path, save_all=True, append_images=[Image.new("RGB", size, second)], duration=100, loop=0)
    return path


def test_animations_are_compared_by_all_frames_and_not_reproducible(toolset, tmp_path):
    tools = toolset("compare")
    a = _gif(tmp_path / "a.gif", (30, 200, 30))
    same = _gif(tmp_path / "same.gif", (30, 200, 30))
    other = _gif(tmp_path / "other.gif", (30, 30, 200))
    r = CP.compare(str(a), str(other), tools, tmp_path / "w")
    assert not r["identical"] and not r["reproducible"] and "animation" in r["reason"]
    assert r["command"] is None and r["ssim_evidence"] is None
    r = CP.compare(str(a), str(same), tools, tmp_path / "w")
    assert r["identical"] and not r["reproducible"] and "animation" in r["reason"]


def test_an_input_ffmpeg_cannot_decode_keeps_the_gate_numbers(factory, toolset, tmp_path, monkeypatch):
    tools = toolset("compare")
    a = factory.photo(name="a.jpg", quality=95)
    b = factory.photo(name="b.jpg", quality=60)

    def refuse(*args):
        raise metrics.MetricError("Invalid data found when processing input")

    monkeypatch.setattr(CP, "run_reviewer_graph", refuse)
    r = CP.compare(str(a), str(b), tools, tmp_path / "w")
    assert not r["reproducible"] and r["ssim_evidence"] is None and r["command"] is None
    assert "Invalid data" in r["reason"] and 0 < r["ssim"] < 1


def test_paths_with_spaces_reproduce_and_the_hint_says_to_quote(factory, toolset, tmp_path):
    tools = toolset("compare")
    from PIL import Image
    folder = tmp_path / "my images"
    folder.mkdir()
    a = factory.photo(name="a.jpg", quality=95)
    b = folder / "b one.jpg"
    Image.open(a).save(b, quality=60)
    r = CP.compare(str(a), str(b), tools, tmp_path / "w")
    cmd = r["command"].replace("REF", f"'{a}'").replace("NEW", f"'{b}'")
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout
    assert f"All:{r['ssim_evidence']:.6f}" in out
    lines = []
    CP.print_result(r, log=lines.append)
    assert any("quoted if the paths contain spaces" in line for line in lines)


def test_a_download_that_stalls_times_out_as_exit_2(tmp_path, capsys, toolset, monkeypatch):
    import socket
    toolset("compare")
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(1)  # accepts the connection, never answers
    monkeypatch.setattr(CP, "FETCH_TIMEOUT", 0.5)
    url = f"http://127.0.0.1:{silent.getsockname()[1]}/a.jpg"
    try:
        with pytest.raises(OSError):
            CP.fetch(url, tmp_path / "w")
        assert cli.main(["compare", url, url]) == 2
    finally:
        silent.close()
    assert "error:" in capsys.readouterr().err


def test_compare_measures_content_whatever_its_name(factory, toolset, tmp_path):
    """compare keys nothing on the extension, so a JPEG named .png is measured, not refused."""
    tools = toolset("compare")
    a = factory.photo(name="a.jpg")
    misnamed = factory.photo(name="b.jpg", quality=60).rename(factory.root / "b.png")
    r = CP.compare(str(a), str(misnamed), tools, tmp_path)
    assert not r["identical"] and r["reproducible"] and r["ssim_evidence"] is not None
