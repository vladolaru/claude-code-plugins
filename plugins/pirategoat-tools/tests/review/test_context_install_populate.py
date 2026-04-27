"""Tests for the install-cache populate step in review/context.py."""

import pytest


@pytest.fixture
def repo_with_lockfile(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "composer.json").write_text("{}")
    (repo / "composer.lock").write_text('{"content-hash":"x"}')
    return repo


class TestEnsureInstalledFromContext:
    def test_populate_runs_before_host_context(self, repo_with_lockfile, monkeypatch):
        """context.py must call ensure_installed before _fill_host_context."""
        from review import context as ctx_mod

        call_order = []

        def fake_populate(repo_path):
            call_order.append("populate")
            return {"status": "ok", "managers": []}

        original_fill = ctx_mod._fill_host_context
        def tracking_fill(*args, **kwargs):
            call_order.append("host_context_fill")
            return original_fill(*args, **kwargs)

        monkeypatch.setattr(ctx_mod, "_populate_install_cache", fake_populate)
        monkeypatch.setattr(ctx_mod, "_fill_host_context", tracking_fill)

        ctx = ctx_mod.load_and_fill(
            ctx_path=str(repo_with_lockfile / "review-context.json"),
            branch=True,
            repo_path=str(repo_with_lockfile),
        )
        assert call_order == ["populate", "host_context_fill"]

    def test_populate_failure_does_not_block_review(self, repo_with_lockfile, monkeypatch):
        """If ensure_installed.py raises, review continues with degraded host_context."""
        from review import context as ctx_mod

        def fake_populate(repo_path):
            raise RuntimeError("install failed catastrophically")

        monkeypatch.setattr(ctx_mod, "_populate_install_cache", fake_populate)

        ctx = ctx_mod.load_and_fill(
            ctx_path=str(repo_with_lockfile / "review-context.json"),
            branch=True,
            repo_path=str(repo_with_lockfile),
        )
        # No exception raised; host_context is present (possibly empty).
        assert "host_context" in ctx
