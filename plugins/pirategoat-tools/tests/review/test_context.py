"""Tests for review/context.py."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent

# Import shared fixtures — tests run from various CWDs, so use path-based import
sys.path.insert(0, str(TESTS_DIR))
from helpers.context_fixtures import COMPLETE_CONTEXT, PARTIAL_CONTEXT

SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "review" / "context.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("gather_review_context", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


class TestGapFilling:
    """The script fills missing fields without re-computing existing ones."""

    def test_complete_file_passes_through(self, mod, tmp_path):
        """All fields present → no changes needed."""
        ctx_file = tmp_path / "review-context.json"
        ctx_file.write_text(json.dumps(COMPLETE_CONTEXT))
        result = mod.load_and_fill(str(ctx_file), pr_number="42")
        assert result["git"]["merge_base"] == "abc123"
        assert result["pr"]["body"] == "Fixes WOOPLUG-1234"
        assert result["linked_issues"] == ["WOOPLUG-1234"]

    def test_generates_csv_if_missing(self, mod, tmp_path):
        context = json.loads(json.dumps(COMPLETE_CONTEXT))
        del context["git"]["changed_files_csv"]
        ctx_file = tmp_path / "review-context.json"
        ctx_file.write_text(json.dumps(context))
        result = mod.load_and_fill(str(ctx_file), pr_number="42")
        assert result["git"]["changed_files_csv"] == "src/a.js,src/b.js"

    def test_computes_size_category_if_missing(self, mod, tmp_path):
        context = json.loads(json.dumps(COMPLETE_CONTEXT))
        del context["pr_size"]["category"]
        ctx_file = tmp_path / "review-context.json"
        ctx_file.write_text(json.dumps(context))
        result = mod.load_and_fill(str(ctx_file), pr_number="42")
        assert result["pr_size"]["category"] == "small"

    def test_extracts_linked_issues_if_missing(self, mod, tmp_path):
        context = json.loads(json.dumps(COMPLETE_CONTEXT))
        del context["linked_issues"]
        ctx_file = tmp_path / "review-context.json"
        ctx_file.write_text(json.dumps(context))
        result = mod.load_and_fill(str(ctx_file), pr_number="42")
        assert "WOOPLUG-1234" in result["linked_issues"]


class TestHelpers:
    def test_categorize_human(self, mod):
        assert mod.categorize_reviewer("octocat") == "human"

    def test_categorize_bot(self, mod):
        assert mod.categorize_reviewer("dependabot[bot]") == "bot"

    def test_categorize_ai(self, mod):
        assert mod.categorize_reviewer("coderabbitai") == "ai"

    def test_extract_linear_ids(self, mod):
        ids = mod.extract_linked_issues("Fixes WOOPLUG-1234 and WOOPRD-56")
        assert "WOOPLUG-1234" in ids
        assert "WOOPRD-56" in ids

    def test_extract_github_refs(self, mod):
        ids = mod.extract_linked_issues("Closes #99, refs #100")
        assert "99" in ids
        assert "100" in ids

    def test_extract_empty_body(self, mod):
        assert mod.extract_linked_issues("") == []

    def test_bucket_size(self, mod):
        assert mod.bucket_pr_size(15) == "tiny"
        assert mod.bucket_pr_size(100) == "small"
        assert mod.bucket_pr_size(500) == "medium"
        assert mod.bucket_pr_size(1500) == "large"
        assert mod.bucket_pr_size(4000) == "huge"
        assert mod.bucket_pr_size(10000) == "vlad-sized"


class TestIncrementalAncestryValidation:
    """Incremental review must validate that last_reviewed_sha is an ancestor of HEAD."""

    def test_valid_ancestor_used_directly(self, mod, tmp_path):
        """When last_reviewed_sha IS an ancestor, use it as merge_base."""
        state = {"last_reviewed_sha": "abc123valid"}
        (tmp_path / ".review-state.json").write_text(json.dumps(state))

        def mock_run_cmd(cmd, cwd=None):
            cmd_str = " ".join(cmd)
            if "merge-base" in cmd_str and "--is-ancestor" in cmd_str:
                return ""  # exit 0 = is ancestor
            if "branch --show-current" in cmd_str:
                return "feature-branch"
            return None

        ctx = {"output": {"directory": str(tmp_path)}}
        from unittest.mock import patch
        with patch.object(mod, '_run_cmd', side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, branch=True, incremental=True)

        assert ctx["git"]["merge_base"] == "abc123valid"

    def test_invalid_ancestor_falls_back_to_full_range(self, mod, tmp_path):
        """When last_reviewed_sha is NOT an ancestor (e.g., after rebase), fall back."""
        state = {"last_reviewed_sha": "deadbeefdeadbeef"}
        (tmp_path / ".review-state.json").write_text(json.dumps(state))

        def mock_run_cmd(cmd, cwd=None):
            cmd_str = " ".join(cmd)
            if "merge-base" in cmd_str and "--is-ancestor" in cmd_str:
                return None  # exit 1 = not ancestor
            if "branch --show-current" in cmd_str:
                return "feature-branch"
            if "symbolic-ref" in cmd_str:
                return "refs/remotes/origin/main"
            if "merge-base" in cmd_str:
                return "fallback123"  # full-branch merge base
            return None

        ctx = {"output": {"directory": str(tmp_path)}}
        from unittest.mock import patch
        with patch.object(mod, '_run_cmd', side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, branch=True, incremental=True)

        assert ctx["git"]["merge_base"] != "deadbeefdeadbeef", (
            "Invalid ancestor SHA should NOT be used as merge_base"
        )
        assert ctx["git"]["merge_base"] == "fallback123"

    def test_no_state_file_falls_through(self, mod, tmp_path):
        """No .review-state.json → falls through to full-branch detection."""
        def mock_run_cmd(cmd, cwd=None):
            cmd_str = " ".join(cmd)
            if "branch --show-current" in cmd_str:
                return "feature-branch"
            if "symbolic-ref" in cmd_str:
                return "refs/remotes/origin/main"
            if "merge-base" in cmd_str:
                return "fullrange123"
            return None

        ctx = {"output": {"directory": str(tmp_path)}}
        from unittest.mock import patch
        with patch.object(mod, '_run_cmd', side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, branch=True, incremental=True)

        assert ctx["git"]["merge_base"] == "fullrange123"


class TestReviewedHeadSha:
    """Step 3 records the reviewed head as a commit SHA post-checkout —
    step 1 resolves HEAD before the PR checkout, so the durable identity
    must come from here."""

    def test_resolves_head_ref_to_full_sha(self, mod):
        head_sha = "a" * 40

        def mock_run_cmd(cmd, cwd=None):
            cmd_str = " ".join(cmd)
            if cmd_str == "git rev-parse --verify feature-branch^{commit}":
                return head_sha
            if "branch --show-current" in cmd_str:
                return "feature-branch"
            if "symbolic-ref" in cmd_str:
                return "refs/remotes/origin/main"
            if "merge-base" in cmd_str:
                return "b" * 40
            return None

        ctx = {}
        from unittest.mock import patch
        with patch.object(mod, '_run_cmd', side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, branch=True)

        assert ctx["git"]["head_sha"] == head_sha

    @pytest.mark.parametrize(
        "git_range", ["main..feature", "main...feature"],
        ids=["two-dot", "three-dot"],
    )
    def test_explicit_range_resolves_the_range_head_endpoint(
        self, mod, git_range
    ):
        def mock_run_cmd(cmd, cwd=None):
            if " ".join(cmd) == "git rev-parse --verify feature^{commit}":
                return "c" * 40
            return None

        ctx = {}
        from unittest.mock import patch
        with patch.object(mod, '_run_cmd', side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, git_range=git_range)

        assert ctx["git"]["merge_base"] == "main"
        assert ctx["git"]["head_ref"] == "feature"
        assert ctx["git"]["head_sha"] == "c" * 40

    def test_omitted_range_head_endpoint_falls_back_to_head(self, mod):
        def mock_run_cmd(cmd, cwd=None):
            if " ".join(cmd) == "git rev-parse --verify HEAD^{commit}":
                return "e" * 40
            return None

        ctx = {}
        from unittest.mock import patch
        with patch.object(mod, '_run_cmd', side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, git_range="main..")

        assert "head_ref" not in ctx["git"]
        assert ctx["git"]["head_sha"] == "e" * 40

    def test_precomputed_head_sha_is_preserved(self, mod):
        """Bot-provided context already carries the resolved head."""
        ctx = {"git": {"git_range": "x..y", "head_ref": "y",
                       "head_sha": "d" * 40, "merge_base": "x"}}
        calls = []

        def mock_run_cmd(cmd, cwd=None):
            calls.append(" ".join(cmd))
            return None

        from unittest.mock import patch
        with patch.object(mod, '_run_cmd', side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, git_range=None)

        assert ctx["git"]["head_sha"] == "d" * 40
        assert not any("rev-parse --verify" in call for call in calls)

    def test_unresolvable_head_leaves_head_sha_absent(self, mod):
        def mock_run_cmd(cmd, cwd=None):
            return None

        ctx = {}
        from unittest.mock import patch
        with patch.object(mod, '_run_cmd', side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, git_range="main..gone")

        assert "head_sha" not in ctx["git"]


class TestCLI:
    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_pr_mode_succeeds_with_existing_context(self, mod, tmp_path):
        """Complete context file loads and fills without error."""
        ctx_file = tmp_path / "review-context.json"
        ctx_file.write_text(json.dumps(COMPLETE_CONTEXT))
        result = mod.load_and_fill(str(ctx_file), pr_number="42")
        assert result is not None
        assert result["git"]["merge_base"] == "abc123"

    def test_exits_1_without_pr_or_branch(self, tmp_path):
        r = self._run("--output-dir", str(tmp_path))
        assert r.returncode == 1


# ---------- Host-context integration ----------

def _insert_scripts_onto_path():
    """Ensure scripts/ is on sys.path so review.context imports cleanly in unit tests.

    The root conftest already does this for pytest collection, but subprocess
    tests rely on an explicit PYTHONPATH env; unit tests here re-assert it defensively.
    """
    scripts_dir = Path(__file__).parent.parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


def test_host_context_filled_when_missing(tmp_path, monkeypatch):
    """review/context.py should populate host_context when absent."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

    repo = tmp_path / "repo"
    repo.mkdir()
    outdir = tmp_path / "out"
    outdir.mkdir()
    # Partial review-context.json so load_and_fill has something to fill
    (outdir / "review-context.json").write_text(json.dumps({
        "version": 1,
        "git": {"merge_base": "abc", "head_ref": "HEAD", "git_range": "abc..HEAD"},
    }))

    _insert_scripts_onto_path()
    from review.context import load_and_fill

    ctx = load_and_fill(
        ctx_path=str(outdir / "review-context.json"),
        branch=True,
        repo_path=str(repo),
    )
    assert "host_context" in ctx
    assert ctx["host_context"]["banner"] is None


def test_host_context_recomputed_when_present(tmp_path, monkeypatch):
    """Existing host_context should be refreshed for the current repo."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    outdir = tmp_path / "out"
    outdir.mkdir()
    existing = {
        "version": 1,
        "git": {"merge_base": "abc", "head_ref": "HEAD", "git_range": "abc..HEAD"},
        "host_context": {
            "version": 1,
            "resolved": [{
                "name": "wordpress",
                "kind": "runtime-host",
                "path": "/stale/wordpress",
                "source": "explicit",
                "version": None,
                "version_freshness": None,
                "confidence": "high",
                "notes": {},
            }],
            "banner": None,
            "diagnostics": {"stale": True},
        },
    }
    (outdir / "review-context.json").write_text(json.dumps(existing))

    _insert_scripts_onto_path()
    from review.context import load_and_fill

    ctx = load_and_fill(
        ctx_path=str(outdir / "review-context.json"),
        branch=True,
        repo_path=str(repo),
    )
    assert ctx["host_context"]["resolved"] == []
    assert ctx["host_context"]["banner"] is None


def test_install_cache_failure_banner_is_preserved(mod, tmp_path, monkeypatch):
    """ensure_installed.py failure banners should survive host_context rebuild."""
    repo = tmp_path / "repo"
    repo.mkdir()
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "review-context.json").write_text(json.dumps({
        "version": 1,
        "git": {"merge_base": "abc", "head_ref": "HEAD", "git_range": "abc..HEAD"},
    }))

    install_banner = {
        "degraded": True,
        "reason": "install_failed",
        "message": "library-dep verification degraded: install failed for composer",
        "unresolved": [{"name": "composer", "reason": "missing_binary"}],
    }

    class FakeManifest:
        def to_dict(self):
            return {
                "version": 1,
                "resolved": [],
                "unresolved": [],
                "banner": None,
                "diagnostics": {},
            }

    class FakeChain:
        def run(self, repo_path):
            return FakeManifest()

    monkeypatch.setattr(mod, "_populate_install_cache", lambda repo_path, scope_paths=None: {"banner": install_banner})
    monkeypatch.setattr(mod, "_HOSTS_CHAIN", FakeChain)

    ctx = mod.load_and_fill(
        ctx_path=str(outdir / "review-context.json"),
        branch=True,
        repo_path=str(repo),
    )

    assert ctx["host_context"]["banner"] == install_banner


def test_host_context_uses_git_root_when_repo_path_omitted_from_subdir(tmp_path, monkeypatch):
    """CWD fallback should discover repo-root wp-env config from subdirectories."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

    repo = tmp_path / "repo"
    subdir = repo / "src"
    subdir.mkdir(parents=True)
    upstream = tmp_path / "woocommerce"
    upstream.mkdir()
    (repo / ".wp-env.json").write_text(json.dumps({
        "mappings": {
            "wp-content/plugins/woocommerce": "../woocommerce",
        }
    }))
    subprocess.run(
        ["git", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "review-context.json").write_text(json.dumps({
        "version": 1,
        "git": {"merge_base": "abc", "head_ref": "HEAD", "git_range": "abc..HEAD"},
    }))

    _insert_scripts_onto_path()
    from review.context import load_and_fill

    monkeypatch.chdir(subdir)
    ctx = load_and_fill(
        ctx_path=str(outdir / "review-context.json"),
        branch=True,
    )

    assert ctx["host_context"]["banner"] is None
    assert ctx["host_context"]["resolved"][0]["name"] == "woocommerce"
    assert ctx["host_context"]["resolved"][0]["path"] == str(upstream.resolve())


def test_context_cli_passes_repo_path_to_host_discovery(tmp_path):
    """CLI --repo-path is honored and host-context lands in review-context.json."""
    scripts = Path(__file__).parent.parent.parent / "scripts"

    repo = tmp_path / "some-repo"
    repo.mkdir()
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "review-context.json").write_text(json.dumps({
        "version": 1,
        "git": {"merge_base": "abc", "head_ref": "HEAD", "git_range": "abc..HEAD"},
    }))

    env = {**os.environ, "PYTHONPATH": str(scripts), "HOME": str(tmp_path / "home")}
    env.pop("XDG_CACHE_HOME", None)
    result = subprocess.run(
        [sys.executable, "-m", "review.context",
         "--branch", "--output-dir", str(outdir),
         "--repo-path", str(repo)],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    ctx = json.loads((outdir / "review-context.json").read_text())
    assert "host_context" in ctx
    assert ctx["host_context"]["banner"] is None


def test_fill_host_context_does_not_mutate_sys_path(tmp_path, monkeypatch):
    """_fill_host_context must not reorder sys.path as a side effect."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

    repo = tmp_path / "repo"
    repo.mkdir()
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "review-context.json").write_text(json.dumps({
        "version": 1,
        "git": {"merge_base": "abc", "head_ref": "HEAD", "git_range": "abc..HEAD"},
    }))

    _insert_scripts_onto_path()
    from review.context import load_and_fill

    before = list(sys.path)
    load_and_fill(
        ctx_path=str(outdir / "review-context.json"),
        branch=True,
        repo_path=str(repo),
    )
    after = list(sys.path)
    assert after == before, (
        f"sys.path was mutated by _fill_host_context. "
        f"Before: {before!r}\nAfter: {after!r}"
    )


def test_fill_review_config_populates_context(tmp_path, monkeypatch):
    """load_and_fill writes a review_config key sourced from .pirategoat/config.json."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

    repo = tmp_path / "repo"
    (repo / ".pirategoat").mkdir(parents=True)
    (repo / "rule.md").write_text("# rule\n")
    (repo / ".pirategoat" / "config.json").write_text(json.dumps({
        "review": {"rules": [{"id": "r1", "path": "rule.md"}]}
    }))
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "review-context.json").write_text(json.dumps({
        "version": 1,
        # changed_files is the provenance the loader gates on: known, and
        # not touching the config or rule file, so the rule is trusted.
        "git": {
            "merge_base": "abc",
            "head_ref": "HEAD",
            "git_range": "abc..HEAD",
            "changed_files": ["src/app.php"],
        },
    }))

    _insert_scripts_onto_path()
    from review.context import load_and_fill

    ctx = load_and_fill(
        ctx_path=str(outdir / "review-context.json"),
        branch=True,
        repo_path=str(repo),
    )
    assert "review_config" in ctx
    assert ctx["review_config"] is not None
    ids = [r["id"] for r in ctx["review_config"]["rules"]]
    assert "r1" in ids
