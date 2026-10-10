import os
import time
from pathlib import Path

from imgopt_lib import candidates as C
from imgopt_lib import cli
from imgopt_lib import gates as G
from imgopt_lib import ladder
from imgopt_lib import store as ST


def test_put_then_get_returns_the_record_and_keeps_the_file(tmp_path):
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "v.png").write_bytes(b"variant")
    ST.put({"key": "abcd1234", "label": "oxipng", "file": "v.png", "kind": "lossless", "size": 7}, folder)
    got = ST.get("abcd1234")
    assert got["label"] == "oxipng"
    restored = tmp_path / "other"
    restored.mkdir()
    assert ST.restore(got, restored) and (restored / "v.png").read_bytes() == b"variant"


def test_a_failed_hardlink_falls_back_to_a_copy(tmp_path, monkeypatch):
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "v.png").write_bytes(b"variant")
    monkeypatch.setattr(os, "link", lambda *a: (_ for _ in ()).throw(OSError("cross-device")))
    ST.put({"key": "k1", "file": "v.png", "kind": "lossless"}, folder)
    assert ST.get("k1") is not None


def test_lossy_variants_are_remembered_as_produced(tmp_path):
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "v.jpg").write_bytes(b"lossy bytes")
    ST.put({"key": "k2", "file": "v.jpg", "kind": "lossy"}, folder)
    assert ST.produced_lossy(ST.sha256(folder / "v.jpg"))


def test_prune_drops_old_entries_and_keeps_the_store_under_its_cap(tmp_path):
    for i, age_days in enumerate((40, 5, 1)):
        folder = tmp_path / f"r{i}"
        folder.mkdir()
        (folder / "v.png").write_bytes(b"x" * 1000)
        ST.put({"key": f"key{i}", "file": "v.png", "kind": "lossy"}, folder)
        then = time.time() - age_days * 86400
        os.utime(ST._entry(f"key{i}"), (then, then))
    removed, _ = ST.prune(max_bytes=1500)
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
    folder = next(p for p in (tmp_path / "two").iterdir() if p.is_dir())
    assert (folder / C.pick_of(r)["file"]).is_file()


def test_a_copied_lossy_variant_is_not_measured_again(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"jpeg"})
    src = factory.photo(size=(200, 150), quality=95).resolve()
    opts = lambda out: C.Options(profile="high", out=out, gates=G.gates_for("high"))
    [r] = C.run([src], opts(tmp_path / "one"), tools, log=lambda _: None)
    folder = next(p for p in (tmp_path / "one").iterdir() if p.is_dir() and (p / "metrics.json").is_file())
    src.write_bytes((folder / C.pick_of(r)["file"]).read_bytes())  # an agent copies the pick into place
    lines = []
    assert C.run([src], opts(tmp_path / "two"), tools, log=lines.append) == []
    assert any("an earlier imgopt lossy pick wrote this file" in l for l in lines)


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
        ST.put({"key": key, "file": "v.png", "kind": "lossless"}, folder)
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
    assert ST.prune(max_age_days=0, now=time.time() + 1) == (1, good)


def test_clean_store_prunes_by_age_and_needs_no_folder(tmp_path, capsys):
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "v.png").write_bytes(b"x" * 1000)
    ST.put({"key": "keyold", "file": "v.png", "kind": "lossless"}, folder)
    then = time.time() - 5 * 86400
    os.utime(ST._entry("keyold"), (then, then))
    assert cli.main(["clean", "--store"]) == 0 and ST._entry("keyold").is_dir()  # a get would count as a use
    assert cli.main(["clean", "--store", "--older-than", "2"]) == 0
    assert "Pruned 1 variant(s)" in capsys.readouterr().out and ST.get("keyold") is None
    assert cli.main(["clean"]) == 2
