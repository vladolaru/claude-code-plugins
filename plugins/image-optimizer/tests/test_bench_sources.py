import base64
import hashlib
import sys
from pathlib import Path

from PIL import Image, ImageDraw

BENCH = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(BENCH))

from imgbench import sources as SR  # noqa: E402


def fake_api(pages):
    def get(params):
        return pages[params["gcmtitle"]]
    return get


def _page(*files):
    return {"query": {"pages": {str(i): {"title": f"File:{name}", "imageinfo": [{
        "url": f"https://upload.example/{name}", "sha1": "ab" * 20, "width": w, "size": size, "mime": "image/jpeg",
        "extmetadata": {"LicenseShortName": {"value": lic}, "Artist": {"value": "<a>Someone</a>"}}}]}
        for i, (name, w, size, lic) in enumerate(files)}}}


def test_select_keeps_allowed_licenses_and_size_rules():
    pages = {"Category:A": _page(("a.jpg", 4000, 5_000_000, "CC BY-SA 4.0"),
                                 ("b.jpg", 4000, 5_000_000, "All rights reserved"),
                                 ("c.jpg", 1000, 5_000_000, "CC0"), ("d.jpg", 5000, 90_000_000, "CC0"))}
    chosen = SR.select({"photo-camera": ["Category:A"]}, {"photo-camera": 5}, api=fake_api(pages),
                       rules={"photo-camera": {"min_width": 3000, "max_bytes": 30_000_000}})
    assert [s.key for s in chosen] == ["a.jpg"]
    assert chosen[0].license == "CC BY-SA 4.0" and chosen[0].author == "Someone"


def test_fetch_checks_the_checksum(tmp_path):
    src = SR.Source("x.jpg", "photo-camera", "https://e/x.jpg", "CC0", "a", sha1="0" * 40, sha256=None)
    problems = SR.fetch([src], tmp_path, get=lambda url, dest: dest.write_bytes(b"data"))
    assert problems and "checksum" in problems[0]
    assert not (tmp_path / "photo-camera" / "x.jpg").exists()


def test_the_committed_sources_follow_the_license_rule():
    path = BENCH / "sources.json"
    if not path.exists():
        return  # before Task 2 Step 4 writes it
    for s in SR.load(path):
        assert s.license.startswith(SR.ALLOWED_LICENSE_PREFIXES), s
        assert s.sha1 or s.sha256, s


def _studio(path: Path, background: int) -> Path:
    im = Image.new("RGB", (400, 300), (background,) * 3)
    ImageDraw.Draw(im).ellipse((120, 80, 280, 220), fill=(180, 40, 40))
    im.save(path, "JPEG", quality=95)
    return path


def _busy(path: Path) -> Path:
    im = Image.new("RGB", (400, 300))
    px = im.load()
    for y in range(300):
        for x in range(400):
            px[x, y] = ((x * 37 + y * 11) % 256, (x * 5 + y * 91) % 256, (x * y) % 256)
    im.save(path, "JPEG", quality=95)
    return path


def test_plain_background_accepts_studio_backdrops_and_rejects_busy_borders(tmp_path):
    assert SR.plain_background(_studio(tmp_path / "white.jpg", 250))
    assert SR.plain_background(_studio(tmp_path / "black.jpg", 12))
    assert not SR.plain_background(_studio(tmp_path / "mid.jpg", 128))  # flat, but neither white nor black
    assert not SR.plain_background(_busy(tmp_path / "busy.jpg"))


def test_pick_plain_keeps_the_first_plain_ones_and_discards_the_rest(tmp_path):
    files = {"a.jpg": _busy(tmp_path / "src-a.jpg"), "b.jpg": _studio(tmp_path / "src-b.jpg", 250),
             "c.jpg": _studio(tmp_path / "src-c.jpg", 245), "d.jpg": _studio(tmp_path / "src-d.jpg", 255)}
    cands = [SR.Source(k, "product-plain", f"https://e/{k}", "CC0", "x", hashlib.sha1(p.read_bytes()).hexdigest(), None)
             for k, p in files.items()]
    dest = tmp_path / "downloads"
    kept = SR.pick_plain(cands, 2, dest, get=lambda url, to: to.write_bytes(files[url.rsplit("/", 1)[1]].read_bytes()))
    assert [s.key for s in kept] == ["b.jpg", "c.jpg"]
    assert sorted(p.name for p in (dest / "product-plain").iterdir()) == ["b.jpg", "c.jpg"]
    assert not (dest / "_probe").exists()


def test_pin_unpinned_records_sha256_and_leaves_pinned_sources_alone(tmp_path):
    (tmp_path / "photo-small").mkdir()
    (tmp_path / "photo-small" / "k.png").write_bytes(b"png")
    bare = SR.Source("k.png", "photo-small", "https://e/k.png", "Kodak", "Kodak", None, None)
    pinned = SR.Source("w.jpg", "photo-camera", "https://e/w.jpg", "CC0", "a", "ab" * 20, None)
    out = SR.pin_unpinned([bare, pinned], tmp_path)
    assert out[0].sha256 == hashlib.sha256(b"png").hexdigest() and out[1] is pinned


def test_save_and_load_round_trip(tmp_path):
    src = [SR.Source("k.png", "photo-small", "https://e/k.png", "Kodak", "Kodak", None, "cd" * 32)]
    SR.save(src, tmp_path / "sources.json")
    assert SR.load(tmp_path / "sources.json") == src


def test_gpl_assets_take_the_largest_rasters_and_svgs_at_the_pinned_commit():
    tree = {"tree": [{"path": n, "type": "blob", "size": s} for n, s in
                     [("a.png", 50), ("b.jpg", 400), ("c.png", 300), ("d.svg", 20), ("e.svg", 90), ("f.gif", 9999),
                      ("g.png", 200), ("tiny.svg", 1)]] + [{"path": "sub", "type": "tree"}]}

    def gh(path):
        if path == "repos/o/r/commits/HEAD":
            return {"sha": "c0ffee"}
        if path == "repos/o/r/git/trees/c0ffee":
            return {"tree": [{"path": "img", "type": "tree", "sha": "t1"}]}
        if path == "repos/o/r/git/trees/t1?recursive=1":
            return tree
        if path == "repos/o/r/contents/readme.txt?ref=c0ffee":
            return {"content": base64.b64encode(b"=== R ===\nLicense: GPLv2 or later\n").decode()}
        raise AssertionError(path)

    repo = SR.GplRepo("o/r", ("img",), "readme.txt", raster=2, svg=2)
    got = SR.gpl_assets([repo], gh=gh)
    assert [s.key for s in got] == ["r__img__b.jpg", "r__img__c.png", "r__img__e.svg", "r__img__d.svg"]
    assert got[0].url == "https://raw.githubusercontent.com/o/r/c0ffee/img/b.jpg"
    assert {s.license for s in got} == {"GPL-2.0-or-later"} and got[0].category == "gpl-asset"


def test_the_fetch_command_pins_what_it_downloads_and_reports_failures(monkeypatch, tmp_path):
    from imgbench import cli

    listed = [SR.Source("k.png", "photo-small", "https://e/k.png", "Kodak", "Kodak", None, None),
              SR.Source("x.jpg", "photo-camera", "https://e/x.jpg", "CC0", "a", "0" * 40, None)]
    monkeypatch.setattr(cli, "SOURCES_FILE", tmp_path / "sources.json")
    SR.save(listed, cli.SOURCES_FILE)
    monkeypatch.setattr(SR, "download", lambda url, dest: dest.write_bytes(b"data"))
    assert cli.main(["fetch"]) == 1  # x.jpg's sha1 cannot match
    saved = SR.load(cli.SOURCES_FILE)
    assert saved[0].sha256 == hashlib.sha256(b"data").hexdigest() and saved[1] == listed[1]
    assert cli.main(["fetch"]) == 1 and SR.load(cli.SOURCES_FILE) == saved  # pinned once, then stable


def test_pick_plain_reuses_a_kept_file_without_downloading_it_again(tmp_path):
    (tmp_path / "downloads" / "product-plain").mkdir(parents=True)
    kept = _studio(tmp_path / "downloads" / "product-plain" / "a.jpg", 250)
    sha1 = hashlib.sha1(kept.read_bytes()).hexdigest()
    src = SR.Source("a.jpg", "product-plain", "https://e/a.jpg", "CC0", "x", sha1, None)

    def no_network(url, dest):
        raise AssertionError("downloaded again")
    assert SR.pick_plain([src], 1, tmp_path / "downloads", get=no_network) == [src] and kept.is_file()


def test_download_waits_and_retries_when_the_server_says_slow_down(monkeypatch, tmp_path):
    import io
    import urllib.error

    calls, naps = [], []

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(req, timeout):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "slow", {"Retry-After": "7"}, None)
        return Reply(b"image")

    monkeypatch.setattr(SR.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(SR.time, "sleep", naps.append)
    SR.download("https://e/a.jpg", tmp_path / "a.jpg")
    assert len(calls) == 2 and naps == [7] and (tmp_path / "a.jpg").read_bytes() == b"image"
