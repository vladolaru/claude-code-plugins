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



def fake_run_cmd(responses):
    """A `_run_cmd` stand-in: the first `(needle, value)` whose needle is in
    the joined command answers; anything else is None (a failed command).
    Returns `(calls, mock)`, `calls` being every joined command asked."""
    calls = []

    def mock_run_cmd(cmd, cwd=None, **kwargs):
        cmd_str = " ".join(cmd)
        calls.append(cmd_str)
        for needle, value in responses:
            if needle in cmd_str:
                return value
        return None

    return calls, mock_run_cmd

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
    @pytest.mark.parametrize(
        ("username", "expected"),
        [
            pytest.param("octocat", "human", id="human"),
            pytest.param("dependabot[bot]", "bot", id="bot"),
            pytest.param("coderabbitai", "ai", id="ai"),
        ],
    )
    def test_categorize_reviewer(self, mod, username, expected):
        assert mod.categorize_reviewer(username) == expected

    @pytest.mark.parametrize(
        ("body", "expected_ids"),
        [
            pytest.param(
                "Fixes WOOPLUG-1234 and WOOPRD-56",
                ["WOOPLUG-1234", "WOOPRD-56"],
                id="linear-ids",
            ),
            pytest.param(
                "Closes #99, refs #100", ["99", "100"], id="github-refs",
            ),
            pytest.param("", [], id="empty-body"),
        ],
    )
    def test_extract_linked_issues(self, mod, body, expected_ids):
        ids = mod.extract_linked_issues(body)
        if expected_ids:
            for expected_id in expected_ids:
                assert expected_id in ids
        else:
            assert ids == []

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
        (tmp_path / ".branch-review-baseline.json").write_text(json.dumps(state))

        def mock_run_cmd(cmd, cwd=None, **kwargs):
            cmd_str = " ".join(cmd)
            if "merge-base" in cmd_str and "--is-ancestor" in cmd_str:
                return ""  # exit 0 = is ancestor
            if "branch --show-current" in cmd_str:
                return "feature-branch"
            return None

        ctx = {"output": {"directory": str(tmp_path)}}
        from unittest.mock import patch
        with patch.object(mod, '_run_cmd', side_effect=mock_run_cmd):
            mod._fill_git_context(
                ctx, branch=True, incremental=True,
                config={"target_dir": str(tmp_path)},
            )

        assert ctx["git"]["merge_base"] == "abc123valid"

    def test_invalid_ancestor_falls_back_to_full_range(self, mod, tmp_path):
        """When last_reviewed_sha is NOT an ancestor (e.g., after rebase), fall back."""
        state = {"last_reviewed_sha": "deadbeefdeadbeef"}
        (tmp_path / ".branch-review-baseline.json").write_text(json.dumps(state))

        def mock_run_cmd(cmd, cwd=None, **kwargs):
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
            mod._fill_git_context(
                ctx, branch=True, incremental=True,
                config={"target_dir": str(tmp_path)},
            )

        assert ctx["git"]["merge_base"] != "deadbeefdeadbeef", (
            "Invalid ancestor SHA should NOT be used as merge_base"
        )
        assert ctx["git"]["merge_base"] == "fallback123"

    def test_no_state_file_falls_through(self, mod, tmp_path):
        """No .review-state.json → falls through to full-branch detection."""
        _calls, mock_run_cmd = fake_run_cmd([
            ("branch --show-current", "feature-branch"),
            ("symbolic-ref", "refs/remotes/origin/main"),
            ("merge-base", "fullrange123"),
        ])

        ctx = {"output": {"directory": str(tmp_path)}}
        from unittest.mock import patch
        with patch.object(mod, '_run_cmd', side_effect=mock_run_cmd):
            mod._fill_git_context(
                ctx, branch=True, incremental=True,
                config={"target_dir": str(tmp_path)},
            )

        assert ctx["git"]["merge_base"] == "fullrange123"


class TestReviewedHeadSha:
    """Step 3 records the reviewed head as a commit SHA post-checkout —
    step 1 resolves HEAD before the PR checkout, so the durable identity
    must come from here."""

    def test_resolves_head_ref_to_full_sha(self, mod):
        head_sha = "a" * 40

        def mock_run_cmd(cmd, cwd=None, **kwargs):
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
        def mock_run_cmd(cmd, cwd=None, **kwargs):
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
        def mock_run_cmd(cmd, cwd=None, **kwargs):
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

        def mock_run_cmd(cmd, cwd=None, **kwargs):
            calls.append(" ".join(cmd))
            return None

        from unittest.mock import patch
        with patch.object(mod, '_run_cmd', side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, git_range=None)

        assert ctx["git"]["head_sha"] == "d" * 40
        assert not any("rev-parse --verify" in call for call in calls)

    def test_unresolvable_head_leaves_head_sha_absent(self, mod):
        def mock_run_cmd(cmd, cwd=None, **kwargs):
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


class TestRefreshHostContextMode:
    """--refresh-host-context re-resolves host_context in place."""

    def _init_repo(self, path):
        subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@e.com"],
                       cwd=path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=path, capture_output=True, check=True)

    def _run(self, *args, cwd):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)

    def test_refresh_updates_host_context_and_preserves_fields(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        ctx = {
            "git": {"git_range": "abc..def", "changed_files": ["a.py"]},
            "pr": {"number": 42},
            "review_config": {"rules": [{"id": "preserved-rule"}]},
            "output": "opaque-output",
            "host_context": {"stale": True},
        }
        (out_dir / "review-context.json").write_text(json.dumps(ctx))

        r = self._run("--refresh-host-context",
                      "--output-dir", str(out_dir),
                      "--repo-path", str(repo),
                      cwd=repo)

        assert r.returncode == 0
        updated = json.loads((out_dir / "review-context.json").read_text())
        assert updated["host_context"] != {"stale": True}
        expected = {**ctx, "host_context": json.loads(r.stdout)}
        assert updated == expected

    def test_refresh_with_corrupt_context_fails_without_overwriting(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        ctx_path = out_dir / "review-context.json"
        original = b"{not json"
        ctx_path.write_bytes(original)

        r = self._run("--refresh-host-context",
                      "--output-dir", str(out_dir),
                      "--repo-path", str(repo),
                      cwd=repo)

        assert r.returncode != 0
        assert "ERROR:" in r.stderr
        assert "refusing to overwrite" in r.stderr
        assert ctx_path.read_bytes() == original

    def test_refresh_with_non_object_context_fails_without_overwriting(
        self, tmp_path
    ):
        # No `_init_repo`: this refusal fires before host discovery, on a
        # plain directory, same as `test_host_context_filled_when_missing`.
        repo = tmp_path / "repo"
        repo.mkdir()
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        ctx_path = out_dir / "review-context.json"
        original = " [1, 2]\n"
        ctx_path.write_text(original)

        r = self._run("--refresh-host-context",
                      "--output-dir", str(out_dir),
                      "--repo-path", str(repo),
                      cwd=repo)

        assert r.returncode != 0
        assert "ERROR:" in r.stderr
        assert "JSON object" in r.stderr
        assert "refusing to overwrite" in r.stderr
        assert ctx_path.read_text() == original


class TestBaseFetch:
    """The base ref is fetched before merge-base so a stale local
    origin/<base> cannot inflate the range (run 6e6a: 8-file PR reviewed
    as 91 files because origin/trunk was 15 commits behind)."""

    def _calls_and_mock(self, responses):
        return fake_run_cmd(responses)

    def test_pr_mode_fetches_base_before_merge_base(self, mod):
        calls, mock_run_cmd = self._calls_and_mock([
            ("pr view", "trunk fix/topic"),
            ("fetch --no-tags origin +refs/heads/trunk:refs/remotes/origin/trunk", ""),
            ("rev-parse --verify origin/trunk", "56e4e8c2" + "0" * 32),
            ("merge-base origin/trunk fix/topic", "56e4e8c2" + "0" * 32),
        ])
        ctx = {"github_cli_command": "gh"}
        from unittest.mock import patch
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, pr_number="66900")

        fetch_idx = next(i for i, c in enumerate(calls) if "fetch --no-tags origin +refs/heads/trunk:" in c)
        mb_idx = next(i for i, c in enumerate(calls) if "merge-base origin/trunk" in c)
        assert fetch_idx < mb_idx
        assert any("rev-list fix/topic ^origin/trunk" in c for c in calls)
        assert ctx["git"]["base_fetch"] == {
            "ref": "origin/trunk",
            "status": "fetched",
            "sha": "56e4e8c2" + "0" * 32,
            "shallow": None,
        }
        assert ctx["git"]["merge_base"] == "56e4e8c2" + "0" * 32

    def test_records_a_shallow_clone_beside_the_fetch(self, mod):
        """Verified on a depth-1 single-branch clone: the refspec fetch brings
        origin/<base> in and merge-base still fails until the clone is
        deepened. The fact is recorded so the briefing can say why."""
        calls, mock_run_cmd = self._calls_and_mock([
            ("pr view", "trunk fix/topic"),
            ("is-shallow-repository", "true"),
            ("fetch --no-tags origin +refs/heads/trunk:", ""),
            ("rev-parse --verify origin/trunk", "56e4e8c2" + "0" * 32),
        ])
        ctx = {"github_cli_command": "gh"}
        from unittest.mock import patch
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, pr_number="66900")
        assert ctx["git"]["base_fetch"]["status"] == "fetched"
        assert ctx["git"]["base_fetch"]["shallow"] is True
        assert "merge_base" not in ctx["git"]

    def test_default_branch_comes_from_the_remote_before_the_cached_symref(self, mod):
        """origin/HEAD is written at clone time and never refreshed by a
        fetch; a repository that moved its default from main to trunk keeps
        the stale value in the cache. The remote's answer wins."""
        calls, mock_run_cmd = self._calls_and_mock([
            ("ls-remote --symref origin HEAD", "ref: refs/heads/trunk\tHEAD\n" + "a" * 40 + "\tHEAD"),
            ("symbolic-ref refs/remotes/origin/HEAD", "refs/remotes/origin/main"),
        ])
        from unittest.mock import patch
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            assert mod._detect_default_branch() == "trunk"

        calls, mock_run_cmd = self._calls_and_mock([
            ("symbolic-ref refs/remotes/origin/HEAD", "refs/remotes/origin/main"),
        ])
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            assert mod._detect_default_branch() == "main"  # offline: cached value

    def test_pr_mode_records_default_branch_without_guessing(self, mod):
        """The step-3 stacked-base line compares base_ref with the default
        branch, so PR mode records it when the symbolic ref resolves and
        leaves it absent (never a guessed "main") when it does not."""
        calls, mock_run_cmd = self._calls_and_mock([
            ("pr view", "trunk fix/topic"),
            ("symbolic-ref refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"),
        ])
        ctx = {"github_cli_command": "gh"}
        from unittest.mock import patch
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, pr_number="66900")
        assert ctx["git"]["default_branch"] == "trunk"

        calls, mock_run_cmd = self._calls_and_mock([("pr view", "trunk fix/topic")])
        ctx = {"github_cli_command": "gh"}
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, pr_number="66900")
        assert "default_branch" not in ctx["git"]

    def test_fetch_that_exits_clean_but_leaves_the_ref_absent_is_failed(self, mod):
        """A single-branch clone answers a bare-name fetch with exit 0 and no
        tracking ref. The explicit refspec prevents that, and status is
        derived from the ref resolving so the briefing never states a
        fetched base that does not exist."""
        calls, mock_run_cmd = self._calls_and_mock([
            ("pr view", "feat/parent-pr feat/child-pr"),
            ("fetch --no-tags origin +refs/heads/feat/parent-pr:", ""),
        ])
        ctx = {"github_cli_command": "gh"}
        from unittest.mock import patch
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, pr_number="66901")

        assert ctx["git"]["base_fetch"] == {
            "ref": "origin/feat/parent-pr", "status": "failed", "sha": None,
            "shallow": None,
        }
        assert "merge_base" not in ctx["git"]

    def test_pr_mode_records_failed_fetch_and_still_computes_merge_base(self, mod):
        calls, mock_run_cmd = self._calls_and_mock([
            ("pr view", "trunk fix/topic"),
            ("rev-parse --verify origin/trunk", "c725aac2" + "0" * 32),
            ("merge-base origin/trunk fix/topic", "c725aac2" + "0" * 32),
        ])
        ctx = {"github_cli_command": "gh"}
        from unittest.mock import patch
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, pr_number="66900")

        assert ctx["git"]["base_fetch"]["status"] == "failed"
        assert ctx["git"]["base_fetch"]["ref"] == "origin/trunk"
        assert ctx["git"]["merge_base"] == "c725aac2" + "0" * 32

    def test_branch_mode_fetches_detected_default_branch(self, mod, tmp_path):
        calls, mock_run_cmd = self._calls_and_mock([
            ("branch --show-current", "feature-branch"),
            ("symbolic-ref refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"),
            ("fetch --no-tags origin +refs/heads/trunk:refs/remotes/origin/trunk", ""),
            ("rev-parse --verify origin/trunk", "abc" + "0" * 37),
            ("merge-base origin/trunk HEAD", "abc" + "0" * 37),
        ])
        ctx = {"output": {"directory": str(tmp_path)}}
        from unittest.mock import patch
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, branch=True, incremental=False,
                                  config={"target_dir": str(tmp_path)})

        assert any("+refs/heads/trunk:refs/remotes/origin/trunk" in c for c in calls)
        assert ctx["git"]["base_fetch"]["status"] == "fetched"
        assert ctx["git"]["git_range"] == "abc" + "0" * 37 + "..HEAD"

    def test_explicit_range_does_not_fetch(self, mod):
        calls, mock_run_cmd = self._calls_and_mock([])
        ctx = {}
        from unittest.mock import patch
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, git_range="main..HEAD")

        assert not any("fetch" in c for c in calls)
        assert "base_fetch" not in ctx["git"]


class TestChangedFilesQuoting:
    def test_run_cmd_can_return_stdout_verbatim(self, mod):
        """The default strip would remove leading whitespace from the first
        NUL-delimited path; strip=False keeps every path as git spelled it."""
        assert mod._run_cmd(["printf", " lead.php\\0b.php\\0"], strip=False) == " lead.php\0b.php\0"
        assert mod._run_cmd(["printf", "  x  "]) == "x"

    def test_run_cmd_decodes_non_utf8_paths_losslessly(self, mod):
        """A git-valid path need not be UTF-8. Strict decoding raised before
        the context was written; surrogateescape keeps the bytes."""
        emit = "import sys; sys.stdout.buffer.write(b'bad\\xff.py\\0')"
        out = mod._run_cmd(["python3", "-c", emit], strip=False)
        assert out == "bad\udcff.py\0"
        import os
        assert os.fsencode(out.rstrip("\0")) == b"bad\xff.py"

    def test_file_list_is_read_nul_delimited_and_verbatim(self, mod):
        """git C-quotes non-ASCII, backslash and control-character paths in
        newline output; NUL output spells every path as GitHub does, and
        leading whitespace on the first entry survives (the default strip
        would remove it)."""
        def mock_run_cmd(cmd, cwd=None, **kwargs):
            if "--name-only" in cmd:
                return " lead.php\0café.php\0back\\slash.php\0"
            return None

        ctx = {}
        from unittest.mock import patch
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            mod._fill_git_context(ctx, git_range="main..HEAD")
        assert ctx["git"]["changed_files"] == [
            " lead.php", "café.php", "back\\slash.php",
        ]


class TestScopeCheckAgainstGithub:
    """PR mode records whether the local range agrees with GitHub. The
    witness is GitHub's file list; counts alone never produce a match."""

    BASE = "56e4e8c2" + "0" * 32
    HEAD = "a534276d" + "0" * 32
    PR_JSON = json.dumps({
        "title": "T", "author": {"login": "a"}, "state": "OPEN",
        "isDraft": False, "baseRefName": "trunk", "headRefName": "fix/x",
        "body": "", "labels": [], "url": "u",
        "baseRefOid": BASE, "headRefOid": HEAD, "changedFiles": 2,
        "files": [{"path": "a.php", "additions": 1, "deletions": 0},
                  {"path": "b.php", "additions": 1, "deletions": 0}],
    })

    def _verified(self, changed_files, count, paths=None):
        pr = {"number": 1, "changed_files_count": count,
              "head_ref_oid": self.HEAD, "base_ref_oid": self.BASE}
        if paths is not None:
            pr["changed_files_paths"] = paths
        return {"pr": pr,
                "git": {"changed_files": changed_files, "head_sha": self.HEAD,
                        "merge_base": self.BASE,
                        "base_fetch": {"ref": "origin/trunk", "status": "fetched",
                                       "sha": self.BASE}}}

    def test_metadata_carries_github_oids_count_and_complete_file_list(self, mod):
        from unittest.mock import patch
        ctx = {"pr": {"number": 66900}, "github_cli_command": "gh"}
        with patch.object(mod, "_run_cmd", return_value=self.PR_JSON):
            mod._fill_pr_metadata(ctx)
        assert ctx["pr"]["base_ref_oid"] == self.BASE
        assert ctx["pr"]["head_ref_oid"] == self.HEAD
        assert ctx["pr"]["changed_files_count"] == 2
        assert ctx["pr"]["changed_files_paths"] == ["a.php", "b.php"]

    def test_metadata_drops_a_truncated_file_list(self, mod):
        """gh caps the file list; a partial list is not a set to compare."""
        from unittest.mock import patch
        data = json.loads(self.PR_JSON)
        data["changedFiles"] = 150
        ctx = {"pr": {"number": 66900}, "github_cli_command": "gh"}
        with patch.object(mod, "_run_cmd", return_value=json.dumps(data)):
            mod._fill_pr_metadata(ctx)
        assert ctx["pr"]["changed_files_count"] == 150
        assert "changed_files_paths" not in ctx["pr"]

    # Each row: the local range and GitHub's view (`_verified`), one
    # mutation of that context, and the fields the check must record.
    #   moved-base-tip — run 4: origin/develop had advanced past the PR's
    #   fork point, so the fetched tip was not GitHub's baseRefOid while
    #   the merge base was, and the nine files matched; comparing the tip
    #   called that a mismatch.
    #   base-wins-over-list — equal file sets can hide different hunks: a
    #   range computed from an older base carries the base's own changes
    #   to files the PR also touches (A→B→C all editing a.php: the PR is
    #   B..C, the local range A..C). A known base disagreement is a
    #   mismatch whatever the lists say.
    #   head-wins-over-list — a checkout behind the author's latest push can
    #   hold the same file set with different content.
    #   merge-base-differs-counts-only — a stale base can swap files without
    #   changing the count, so with a truncated list the merge base must be
    #   where GitHub's recorded base meets the head.
    @pytest.mark.parametrize("changed, count, paths, mutate, expected", [
        pytest.param(["a.php", "c.php"], 2, ["a.php", "b.php"], None,
                     {"status": "mismatch", "extra_local_files": ["c.php"], "missing_local_files": ["b.php"]},
                     id="file-sets-compared-when-list-complete"),
        pytest.param(["b.php", "a.php"], 2, ["a.php", "b.php"], None,
                     {"status": "match", "extra_local_files": [], "missing_local_files": []},
                     id="equal-file-sets-match"),
        pytest.param([f"f{i}.php" for i in range(91)], 8, None, None,
                     {"status": "mismatch", "github_changed_files": 8, "local_changed_files": 91,
                      "head_matches": True, "base_matches": True, "extra_local_files": None},
                     id="more-local-files-counts-only"),
        pytest.param(["a.php", "b.php"], 2, None, lambda ctx: ctx["git"].__setitem__("head_sha", "b" * 40),
                     {"status": "mismatch", "head_matches": False}, id="head-differs"),
        pytest.param(["a.php", "b.php"], 2, None,
                     lambda ctx: ctx["git"].__setitem__("merge_base", "c725aac2" + "0" * 32),
                     {"status": "mismatch", "base_matches": False}, id="merge-base-differs-counts-only"),
        pytest.param(["a.php", "b.php"], 2, ["a.php", "b.php"],
                     lambda ctx: ctx["git"]["base_fetch"].__setitem__("sha", "60add377" + "0" * 32),
                     {"status": "match", "base_matches": True, "head_matches": True}, id="moved-base-tip"),
        pytest.param(["a.php", "b.php"], 2, ["a.php", "b.php"],
                     lambda ctx: ctx["git"].__setitem__("merge_base", "c725aac2" + "0" * 32),
                     {"status": "mismatch", "base_matches": False, "extra_local_files": [], "missing_local_files": []},
                     id="base-wins-over-list"),
        pytest.param(["a.php", "b.php"], 2, ["a.php", "b.php"],
                     lambda ctx: ctx["git"].__setitem__("head_sha", "b" * 40),
                     {"status": "mismatch", "head_matches": False, "extra_local_files": [], "missing_local_files": []},
                     id="head-wins-over-list"),
        pytest.param(["a.php", "b.php"], 2, ["a.php", "b.php"], lambda ctx: ctx["git"].pop("merge_base"),
                     {"status": "match", "base_matches": None}, id="no-merge-base-is-unknown"),
        pytest.param(["a.php", "b.php"], 2, None, None, {"status": "match"},
                     id="equal-counts-with-verified-identities"),
        pytest.param(None, 2, None, lambda ctx: ctx["git"].update(changed_files=None, head_sha="b" * 40),
                     {"status": "mismatch", "head_matches": False, "local_changed_files": None},
                     id="head-mismatch-without-a-local-file-list"),
    ])
    def test_the_status_follows_the_witnesses(self, mod, changed, count, paths, mutate, expected):
        from unittest.mock import patch
        ctx = self._verified(changed, count, paths=paths)
        if mutate is not None:
            mutate(ctx)
        # GitHub's recorded base meets the head at the fork point, which
        # the verified context records as its merge base.
        _, run_cmd = fake_run_cmd([("merge-base", self.BASE)])
        with patch.object(mod, "_run_cmd", side_effect=run_cmd):
            check = mod._check_scope_against_github(ctx)
        assert {key: check[key] for key in expected} == expected
        assert ctx["git"]["scope_check"] is check

    def test_a_fork_point_behind_the_recorded_base_is_still_githubs_base(self, mod):
        """GitHub's `baseRefOid` is the base as of the PR's last
        synchronisation, not the fork point: a branch forked before the
        base advanced has a merge base behind it and is the same range.
        The identity compared is where the recorded base meets the head."""
        from unittest.mock import patch
        fork = "c725aac2" + "0" * 32
        ctx = self._verified(["a.php", "b.php"], 2)
        ctx["git"]["merge_base"] = fork
        calls, run_cmd = fake_run_cmd([("merge-base", fork)])
        with patch.object(mod, "_run_cmd", side_effect=run_cmd):
            check = mod._check_scope_against_github(ctx)
        assert f"git merge-base {self.BASE} {self.HEAD}" in calls
        assert check["base_matches"] is True
        assert check["status"] == "match"

    def test_a_recorded_base_absent_locally_leaves_the_base_unknown(self, mod):
        """A stale base fetch or a shallow clone may not hold GitHub's
        recorded base; the identity is then unknown, never a mismatch."""
        from unittest.mock import patch
        ctx = self._verified(["a.php", "b.php"], 2)
        _, run_cmd = fake_run_cmd([])
        with patch.object(mod, "_run_cmd", side_effect=run_cmd):
            check = mod._check_scope_against_github(ctx)
        assert check["base_matches"] is None
        assert check["status"] == "count_only"

    def test_a_ref_name_or_short_sha_merge_base_is_unknown_not_false(self, mod):
        """`--pr-number` with `--git-range` leaves `merge_base` as the range's
        left operand verbatim; a string compare against the OID would read
        as a false mismatch."""
        from unittest.mock import patch
        for left in ("origin/trunk", "c725aac2"):
            ctx = self._verified(["a.php", "b.php"], 2)
            ctx["git"]["merge_base"] = left
            calls, run_cmd = fake_run_cmd([("merge-base", self.BASE)])
            with patch.object(mod, "_run_cmd", side_effect=run_cmd):
                check = mod._check_scope_against_github(ctx)
            assert calls == [], "an unresolvable left operand asks git nothing"
            assert check["base_matches"] is None
            assert check["status"] == "count_only"

    def test_equal_counts_without_verified_identities_are_count_only(self, mod):
        ctx = {
            "pr": {"number": 1, "changed_files_count": 2, "head_ref_oid": "a" * 40},
            "git": {"changed_files": ["a.php", "b.php"], "head_sha": "a" * 40},
        }
        check = mod._check_scope_against_github(ctx)
        assert check["status"] == "count_only"
        assert check["base_matches"] is None

    def test_unavailable_without_github_count(self, mod):
        ctx = {"pr": {"number": 1}, "git": {"changed_files": ["a.php"]}}
        check = mod._check_scope_against_github(ctx)
        assert check["status"] == "unavailable"
        assert check["github_changed_files"] is None
        assert check["head_matches"] is None


FETCHED = {"ref": "origin/trunk", "status": "fetched", "sha": "f" * 40}


class TestForeignMerges:
    """Merge commits whose second parent is not on origin/<base> bring in
    work from elsewhere; the briefing must say so instead of the
    orchestrator guessing."""

    def test_failed_base_fetch_claims_nothing(self, mod):
        """Against a stale origin/<base>, a merge of the real base's newer
        commits would look foreign. No classification without a fresh base."""
        calls = []

        def mock_run_cmd(cmd, cwd=None, **kwargs):
            calls.append(" ".join(cmd))
            return ""

        git = {"merge_base": "b" * 40, "base_ref": "trunk",
               "base_fetch": {"ref": "origin/trunk", "status": "failed", "sha": "s" * 40}}
        from unittest.mock import patch
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            assert mod._detect_foreign_merges(git, "HEAD") is None
        assert git["foreign_merges"] is None
        assert calls == []

    def test_records_merges_whose_second_parent_is_off_base(self, mod):
        def mock_run_cmd(cmd, cwd=None, **kwargs):
            cmd_str = " ".join(cmd)
            if "rev-list HEAD ^origin/trunk" in cmd_str:
                # Reachable from HEAD, not from origin/trunk: the branch's own
                # commits plus the sibling it merged. The trunk merge's second
                # parent is on trunk and therefore absent.
                return "\n".join(["m1" + "0" * 38, "p1" + "0" * 37, "sib" + "0" * 37,
                                  "m2" + "0" * 38, "p2" + "0" * 37])
            if "rev-list --merges --parents" in cmd_str:
                return ("m1" + "0" * 38 + " p1" + "0" * 37 + " sib" + "0" * 37 + "\n"
                        "m2" + "0" * 38 + " p2" + "0" * 37 + " trk" + "0" * 37)
            return None

        git = {"merge_base": "base" + "0" * 36, "base_ref": "trunk", "base_fetch": FETCHED}
        from unittest.mock import patch
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            result = mod._detect_foreign_merges(git, "HEAD")

        assert result == [{"sha": "m1" + "0" * 38, "second_parent": "sib" + "0" * 37}]
        assert git["foreign_merges"] is result

    def test_scans_the_range_head_not_the_checkout(self, mod):
        """PR mode computes the range against the PR branch; the merge scan
        must read the same head, not whatever happens to be checked out."""
        calls = []

        def mock_run_cmd(cmd, cwd=None, **kwargs):
            calls.append(" ".join(cmd))
            return ""

        git = {"merge_base": "b" * 40, "base_ref": "trunk", "base_fetch": FETCHED}
        from unittest.mock import patch
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            mod._detect_foreign_merges(git, "fix/topic")
        assert any("rev-list fix/topic ^origin/trunk" in c for c in calls)
        assert any(("--parents " + "b" * 40 + "..fix/topic") in c for c in calls)
        assert not any("HEAD" in c for c in calls)

    def test_no_merges_gives_empty_list(self, mod):
        """A scan that ran and found nothing is [], distinct from None."""
        from unittest.mock import patch
        git = {"merge_base": "b" * 40, "base_ref": "trunk", "base_fetch": FETCHED}
        with patch.object(mod, "_run_cmd", return_value=""):
            assert mod._detect_foreign_merges(git, "HEAD") == []
        assert git["foreign_merges"] == []

    def test_failed_merge_listing_is_none_not_empty(self, mod):
        def mock_run_cmd(cmd, cwd=None, **kwargs):
            return "" if "^origin/trunk" in " ".join(cmd) else None

        from unittest.mock import patch
        git = {"merge_base": "b" * 40, "base_ref": "trunk", "base_fetch": FETCHED}
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            assert mod._detect_foreign_merges(git, "HEAD") is None
        assert git["foreign_merges"] is None

    def test_unresolvable_base_ref_claims_nothing(self, mod):
        """A missing origin/<base> — the state a failed base fetch leaves —
        must not mark every merge foreign on precisely the least trustworthy
        run. The off-base listing fails as a whole and nothing is claimed."""
        calls = []

        def mock_run_cmd(cmd, cwd=None, **kwargs):
            calls.append(" ".join(cmd))
            return None  # ^origin/trunk does not resolve

        git = {"merge_base": "b" * 40, "base_ref": "trunk", "base_fetch": FETCHED}
        from unittest.mock import patch
        with patch.object(mod, "_run_cmd", side_effect=mock_run_cmd):
            assert mod._detect_foreign_merges(git, "HEAD") is None

        assert git["foreign_merges"] is None
        assert any("rev-list HEAD ^origin/trunk" in c for c in calls)
        assert not any("--merges" in c for c in calls)
