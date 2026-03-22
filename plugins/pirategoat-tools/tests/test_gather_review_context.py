"""Tests for review/context.py."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Import shared fixtures — tests run from various CWDs, so use path-based import
sys.path.insert(0, str(Path(__file__).resolve().parent))
from context_fixtures import COMPLETE_CONTEXT, PARTIAL_CONTEXT

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "review" / "context.py"


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


class TestCLI:
    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_pr_mode_exits_0_with_existing_context(self, tmp_path):
        ctx_file = tmp_path / "review-context.json"
        ctx_file.write_text(json.dumps(COMPLETE_CONTEXT))
        r = self._run("--pr-number", "42", "--output-dir", str(tmp_path))
        assert r.returncode == 0

    def test_exits_1_without_pr_or_branch(self, tmp_path):
        r = self._run("--output-dir", str(tmp_path))
        assert r.returncode == 1
