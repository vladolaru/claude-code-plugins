import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(BENCH))

from imgbench import manifest as MF, paths as P  # noqa: E402


def test_corpus_lives_under_the_cache_root(monkeypatch, tmp_path):
    monkeypatch.setenv("IMGOPT_CACHE", str(tmp_path))
    assert P.corpus_dir().is_relative_to(tmp_path / "bench")


def test_manifest_round_trips_and_hashes(tmp_path, factory):
    corpus = tmp_path / "corpus"
    (corpus / "icon").mkdir(parents=True)
    f = corpus / "icon" / "a.png"
    f.write_bytes(factory.logo().read_bytes())
    e = MF.entry_for(f, corpus, category="icon", origin="synthetic", transform="seed=1", license="MIT (generated)")
    assert e.path == "icon/a.png" and e.width == 120 and len(e.sha256) == 64
    MF.write([e], corpus)
    assert MF.read(corpus) == [e]
