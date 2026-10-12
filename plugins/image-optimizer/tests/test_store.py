import json
import os
import shutil
import time
from pathlib import Path

import pytest

from imgopt_lib import candidates as C
from imgopt_lib import cli
from imgopt_lib import gates as G
from imgopt_lib import ladder
from imgopt_lib import store as ST

SRC, REF = "a" * 64, "b" * 64  # the source and reference hashes `candidates` passes to `put`


def test_put_then_get_returns_the_record_and_keeps_the_file(tmp_path):
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "v.png").write_bytes(b"variant")
    ST.put({"key": "abcd1234", "label": "oxipng", "file": "v.png", "kind": "lossless", "size": 7}, folder,
           source_sha=SRC, ref_sha=REF)
    got = ST.get("abcd1234")
    assert got["label"] == "oxipng"
    restored = tmp_path / "other"
    restored.mkdir()
    assert ST.restore(got, restored) and (restored / "v.png").read_bytes() == b"variant"


def test_restore_replaces_a_stale_file_of_the_same_name(tmp_path):
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "v.jpg").write_bytes(b"this variant")
    ST.put({"key": "k5", "file": "v.jpg", "kind": "lossless"}, folder, source_sha=SRC, ref_sha=REF)
    other = tmp_path / "other"
    other.mkdir()
    (other / "v.jpg").write_bytes(b"a variant of another run (other settings)")
    assert ST.restore(ST.get("k5"), other) and (other / "v.jpg").read_bytes() == b"this variant"
    assert ST.restore(ST.get("k5"), folder) and (folder / "v.jpg").read_bytes() == b"this variant"


def test_a_rerun_with_other_settings_in_the_same_folder_gets_the_stored_files(factory, toolset, tmp_path):
    tools = {**toolset("prepare", "high", {"jpeg"}), **toolset("recompress", "high", {"jpeg"})}
    src = factory.photo(size=(400, 300), quality=95).resolve()
    opts = lambda out, **kw: C.Options(profile="high", out=out, gates=G.gates_for("high"), **kw)
    C.run([src], opts(tmp_path / "F", resize=200), tools, log=lambda _: None)
    C.run([src], opts(tmp_path / "G"), tools, log=lambda _: None)  # the store now holds the full-size variants
    C.run([src], opts(tmp_path / "F"), tools, log=lambda _: None)  # F still holds the resized files of those names
    [(f, record)] = C.load_records(tmp_path / "F")
    [(g, _)] = C.load_records(tmp_path / "G")
    for c in (c for c in record["candidates"] if c.get("file")):
        assert (f / c["file"]).read_bytes() == (g / c["file"]).read_bytes(), c["label"]
        assert (f / c["file"]).stat().st_size == c["size"]


def test_a_failed_hardlink_falls_back_to_a_copy(tmp_path, monkeypatch):
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "v.png").write_bytes(b"variant")
    monkeypatch.setattr(os, "link", lambda *a: (_ for _ in ()).throw(OSError("cross-device")))
    ST.put({"key": "k1", "file": "v.png", "kind": "lossless"}, folder, source_sha=SRC, ref_sha=REF)
    assert ST.get("k1") is not None


@pytest.mark.parametrize("links", [True, False], ids=["hard-link", "copy"])
def test_placing_a_variant_never_writes_into_an_existing_file(tmp_path, monkeypatch, links):
    # a stored variant may be hard-linked into working folders: another run that got there first, or a file
    # already placed, must keep its bytes and inode
    placed = tmp_path / "work" / "v.png"
    placed.parent.mkdir()
    placed.write_bytes(b"stored first")
    entry = tmp_path / "store" / "v.png"
    entry.parent.mkdir()
    os.link(placed, entry)
    src = tmp_path / "src.png"
    src.write_bytes(b"a second writer")
    if not links:
        monkeypatch.setattr(os, "link", lambda *a: (_ for _ in ()).throw(OSError("cross-device")))
    if links:  # an existing name is reported; the caller decides whether it is this variant
        with pytest.raises(FileExistsError):
            ST._link_or_copy(src, entry)
    else:
        ST._link_or_copy(src, entry)
    assert placed.read_bytes() == b"stored first"


def test_a_copy_lands_whole_or_not_at_all(tmp_path, monkeypatch):
    src, dst = tmp_path / "src.png", tmp_path / "out" / "v.png"
    dst.parent.mkdir()
    src.write_bytes(b"variant")
    monkeypatch.setattr(os, "link", lambda *a: (_ for _ in ()).throw(OSError("cross-device")))
    src.chmod(0o644)
    ST._link_or_copy(src, dst)
    assert dst.read_bytes() == b"variant" and [p.name for p in dst.parent.iterdir()] == ["v.png"]
    assert dst.stat().st_mode & 0o777 == 0o644  # a copied variant can reach a repo through `apply`


def test_restore_reports_a_name_taken_by_another_writer(tmp_path, monkeypatch):
    # a working folder's file is named by its label, which another run's variant can share: only the store's
    # own entries, named by key, may take an existing file as already placed
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "v.png").write_bytes(b"variant")
    ST.put({"key": "k9", "file": "v.png", "kind": "lossless"}, folder, source_sha=SRC, ref_sha=REF)
    other = tmp_path / "other"
    other.mkdir()
    real_link = os.link

    def raced(src, dst):
        Path(dst).write_bytes(b"another run's variant")  # lands between restore's unlink and its link
        real_link(src, dst)

    monkeypatch.setattr(os, "link", raced)
    assert not ST.restore(ST.get("k9"), other)


def test_storing_an_unchanged_record_writes_and_hashes_nothing(tmp_path, monkeypatch):
    # a fully cached re-run stores every candidate again; only a changed record is worth the write
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "v.jpg").write_bytes(b"lossy variant")
    rec = {"key": "k7", "file": "v.jpg", "kind": "lossy", "size": 13}
    ST.put(rec, folder, source_sha=SRC, ref_sha=REF)
    stored = ST._entry("k7") / "rec.json"
    before = stored.stat().st_ino
    hashed = []
    monkeypatch.setattr(ST, "sha256", lambda path: hashed.append(path) or "c" * 64)
    ST.put(dict(rec), folder, source_sha=SRC, ref_sha=REF)
    assert hashed == [] and stored.stat().st_ino == before
    ST.put({**rec, "ssim_evidence": 0.99}, folder, source_sha=SRC, ref_sha=REF)
    assert ST.get("k7")["ssim_evidence"] == 0.99


def test_lossy_variants_are_remembered_as_produced(tmp_path):
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "v.jpg").write_bytes(b"lossy bytes")
    ST.put({"key": "k2", "file": "v.jpg", "kind": "lossy"}, folder, source_sha=SRC, ref_sha=REF)
    sha = ST.sha256(folder / "v.jpg")
    assert ST.produced_lossy(sha)
    assert json.loads(ST._produced(sha).read_text()) == {"kind": "lossy", "source": SRC}


@pytest.mark.parametrize("bytes_of, rec", [
    ("source", {}),  # a lossy rung that handed back the source's own bytes
    ("reference", {}),
    ("other", {"identical": True}),  # pixel-identical to the reference: nothing was lost
])
def test_a_variant_that_lost_nothing_is_not_remembered_as_produced(tmp_path, bytes_of, rec):
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "v.png").write_bytes(b"the bytes")
    sha = ST.sha256(folder / "v.png")
    hashes = {"source": (sha, REF), "reference": (SRC, sha), "other": (SRC, REF)}[bytes_of]
    ST.put({"key": "k3", "file": "v.png", "kind": "lossy", **rec}, folder, source_sha=hashes[0], ref_sha=hashes[1])
    assert ST.get("k3") is not None and not ST.produced_lossy(sha)


def test_prune_removes_the_legacy_produced_index(tmp_path):
    legacy = ST.root() / "produced" / "ab" / ("ab" + "0" * 62)
    legacy.parent.mkdir(parents=True)
    legacy.write_text("lossy")
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "v.jpg").write_bytes(b"lossy bytes")
    ST.put({"key": "k4", "file": "v.jpg", "kind": "lossy"}, folder, source_sha=SRC, ref_sha=REF)
    ST.prune()
    assert not (ST.root() / "produced").exists() and ST.produced_lossy(ST.sha256(folder / "v.jpg"))


def test_prune_drops_old_entries_and_keeps_the_store_under_its_cap(tmp_path):
    for i, age_days in enumerate((40, 5, 1)):
        folder = tmp_path / f"r{i}"
        folder.mkdir()
        (folder / "v.png").write_bytes(b"x" * 1000)
        ST.put({"key": f"key{i}", "file": "v.png", "kind": "lossy"}, folder, source_sha=SRC, ref_sha=REF)
        then = time.time() - age_days * 86400
        os.utime(ST._entry(f"key{i}"), (then, then))
    removed, _, left = ST.prune(max_bytes=1500)
    assert left == ST.size() <= 1500
    assert removed == 2 and ST.get("key2") is not None and ST.get("key0") is None
    assert ST.produced_lossy(ST.sha256(tmp_path / "r0" / "v.png"))  # the index outlives the variant


def test_a_second_folder_reuses_every_candidate_without_encoding(factory, toolset, tmp_path, monkeypatch):
    tools = toolset("recompress", "lossless", {"png"})
    src = factory.logo().resolve()
    opts = lambda out: C.Options(profile="lossless", out=out, gates=G.gates_for("lossless"))
    C.run([src], opts(tmp_path / "one"), tools, log=lambda _: None)
    calls = []
    monkeypatch.setattr(ladder, "generate", lambda *a, **k: calls.append(1))
    [r] = C.run([src], opts(tmp_path / "two"), tools, log=lambda _: None)
    assert calls == [] and C.pick_of(r) is not None
    lines = []
    C.run([src], opts(tmp_path / "three"), tools, log=lines.append)
    n = len([c for c in r["candidates"] if "error" not in c or c.get("error_kind") == "refused"])
    assert f"{n} variant(s) from the store, 0 encoded." in lines
    folder = next(p for p in (tmp_path / "two").iterdir() if p.is_dir())
    assert (folder / C.pick_of(r)["file"]).is_file()


def test_the_summary_counts_what_was_encoded(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"png"})
    src = factory.logo().resolve()
    opts = lambda out: C.Options(profile="lossless", out=out, gates=G.gates_for("lossless"))
    lines = []
    [r] = C.run([src], opts(tmp_path / "one"), tools, log=lines.append)
    assert f"0 variant(s) from the store, {len(r['candidates'])} encoded." in lines
    lines = []
    C.run([src], opts(tmp_path / "one"), tools, log=lines.append)  # the same folder: its own files are reused
    assert f"0 variant(s) from the store, 0 encoded, {len(r['candidates'])} already in the working folder." in lines


def test_a_copied_lossy_variant_is_not_measured_again(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"jpeg"})
    src = factory.photo(size=(200, 150), quality=95).resolve()
    opts = lambda out: C.Options(profile="high", out=out, gates=G.gates_for("high"))
    [r] = C.run([src], opts(tmp_path / "one"), tools, log=lambda _: None)
    folder = next(p for p in (tmp_path / "one").iterdir() if p.is_dir() and (p / "metrics.json").is_file())
    src.write_bytes((folder / C.pick_of(r)["file"]).read_bytes())  # an agent copies the pick into place
    lines = []
    assert C.run([src], opts(tmp_path / "two"), tools, log=lines.append) == []
    assert any("its bytes match a lossy variant imgopt produced" in l for l in lines)


def test_a_lossy_rung_that_returns_the_source_leaves_the_original_measurable(factory, toolset, tmp_path, monkeypatch):
    tools = toolset("recompress", "high", {"png"})
    src = factory.gradient(size=(300, 60)).resolve()
    real = ladder.generate

    def hands_back_the_source(rung, *, inputs, out_dir, tools):
        if rung.tool == "pngquant":
            out = Path(out_dir) / f"{rung.label}{rung.ext}"
            shutil.copyfile(src, out)  # the source's own bytes, whatever the rung reads
            return out
        return real(rung, inputs=inputs, out_dir=out_dir, tools=tools)

    monkeypatch.setattr(ladder, "generate", hands_back_the_source)
    opts = lambda out: C.Options(profile="high", out=out, gates=G.gates_for("high"))
    C.run([src], opts(tmp_path / "one"), tools, log=lambda _: None)
    assert not ST.produced_lossy(ST.sha256(src))
    monkeypatch.setattr(ladder, "generate", real)
    assert len(C.run([src], opts(tmp_path / "two"), tools, log=lambda _: None)) == 1


def test_a_run_without_the_store_neither_reads_nor_writes_it(factory, toolset, tmp_path, monkeypatch):
    tools = toolset("recompress", "lossless", {"png"})
    src = factory.logo().resolve()
    opts = lambda out, **kw: C.Options(profile="lossless", out=out, gates=G.gates_for("lossless"), **kw)
    C.run([src], opts(tmp_path / "one"), tools, log=lambda _: None)  # seeds the store
    seeded = ST.size()
    calls, real = [], ladder.generate
    monkeypatch.setattr(ladder, "generate", lambda *a, **k: calls.append(1) or real(*a, **k))
    C.run([src], opts(tmp_path / "two", use_store=False), tools, log=lambda _: None)
    assert calls and ST.size() == seeded  # it encoded despite the seeded store, and added nothing to it


def test_prune_and_size_survive_entries_that_vanish_or_cannot_be_read(tmp_path, monkeypatch):
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "v.png").write_bytes(b"x" * 1000)
    for key in ("aa-good", "bb-vanishes", "cc-unreadable"):
        ST.put({"key": key, "file": "v.png", "kind": "lossless"}, folder, source_sha=SRC, ref_sha=REF)
    real_stat, real_iterdir = Path.stat, Path.iterdir

    def stat(self, *a, **k):  # another process pruned this entry between the listing and the stat
        if self.name == "bb-vanishes":
            raise FileNotFoundError(self)
        return real_stat(self, *a, **k)

    def iterdir(self):
        if self.name == "cc-unreadable":
            raise PermissionError(self)
        return real_iterdir(self)

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    good = ST.size()
    assert good >= 1000
    assert ST.prune(max_age_days=0, now=time.time() + 1) == (1, good, 0)


def test_clean_store_prunes_by_age_and_needs_no_folder(tmp_path, capsys):
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "v.png").write_bytes(b"x" * 1000)
    ST.put({"key": "keyold", "file": "v.png", "kind": "lossless"}, folder, source_sha=SRC, ref_sha=REF)
    then = time.time() - 5 * 86400
    os.utime(ST._entry("keyold"), (then, then))
    assert cli.main(["clean", "--store"]) == 0 and ST._entry("keyold").is_dir()  # a get would count as a use
    assert cli.main(["clean", "--store", "--older-than", "2"]) == 0
    assert "Pruned 1 variant(s)" in capsys.readouterr().out and ST.get("keyold") is None
    assert cli.main(["clean"]) == 2
