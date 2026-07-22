"""Tests for review/review_config.py — repo-contributed review config loader."""

import importlib.util
import json
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "review" / "review_config.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("review_config", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def _write_config(repo: Path, data: dict):
    cfg_dir = repo / ".pirategoat"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(json.dumps(data))


def _touch(repo: Path, relpath: str):
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# rule body\n")
    return p


class TestAbsence:
    def test_no_config_file(self, mod, tmp_path):
        result = mod.load_review_config(str(tmp_path))
        assert result["rules"] == []
        assert result["reviewers"] == []
        assert result["diagnostics"] == []
        assert result["defaults"] == {"execution": "inline", "channel": "blocking"}

    def test_config_without_review_section(self, mod, tmp_path):
        _write_config(tmp_path, {"hosts": {"runtime": []}})
        result = mod.load_review_config(str(tmp_path))
        assert result["rules"] == []
        assert result["reviewers"] == []

    def test_malformed_json_does_not_raise(self, mod, tmp_path):
        (tmp_path / ".pirategoat").mkdir()
        (tmp_path / ".pirategoat" / "config.json").write_text("{not json")
        result = mod.load_review_config(str(tmp_path))
        assert result["rules"] == []
        assert any("parse error" in d for d in result["diagnostics"])


class TestRules:
    def test_valid_rule_resolves(self, mod, tmp_path):
        _touch(tmp_path, ".ai/rules/review/runtime.md")
        _write_config(tmp_path, {"review": {"rules": [
            {"id": "runtime-env", "path": ".ai/rules/review/runtime.md",
             "applies_to": {"domains": ["wp-architecture"], "paths": ["**/*.php"]}}
        ]}})
        result = mod.load_review_config(str(tmp_path))
        assert len(result["rules"]) == 1
        rule = result["rules"][0]
        assert rule["id"] == "runtime-env"
        assert rule["path"] == ".ai/rules/review/runtime.md"
        assert rule["resolved_path"].endswith(".ai/rules/review/runtime.md")
        assert rule["applies_to"]["domains"] == ["wp-architecture"]
        assert rule["applies_to"]["paths"] == ["**/*.php"]
        assert rule["channel"] == "blocking"

    def test_missing_file_dropped(self, mod, tmp_path):
        _write_config(tmp_path, {"review": {"rules": [
            {"id": "ghost", "path": ".ai/rules/nope.md"}
        ]}})
        result = mod.load_review_config(str(tmp_path))
        assert result["rules"] == []
        assert any("ghost" in d and "not found" in d for d in result["diagnostics"])

    def test_path_escaping_repo_dropped(self, mod, tmp_path):
        outside = tmp_path.parent / "outside.md"
        outside.write_text("x")
        _write_config(tmp_path, {"review": {"rules": [
            {"id": "escape", "path": "../outside.md"}
        ]}})
        result = mod.load_review_config(str(tmp_path))
        assert result["rules"] == []
        assert any("escape" in d and "escapes" in d for d in result["diagnostics"])

    def test_duplicate_id_dropped(self, mod, tmp_path):
        _touch(tmp_path, "a.md")
        _touch(tmp_path, "b.md")
        _write_config(tmp_path, {"review": {"rules": [
            {"id": "dup", "path": "a.md"},
            {"id": "dup", "path": "b.md"},
        ]}})
        result = mod.load_review_config(str(tmp_path))
        assert len(result["rules"]) == 1
        assert any("duplicate" in d for d in result["diagnostics"])

    def test_invalid_id_dropped(self, mod, tmp_path):
        _touch(tmp_path, "a.md")
        _write_config(tmp_path, {"review": {"rules": [
            {"id": "Bad Id!", "path": "a.md"}
        ]}})
        result = mod.load_review_config(str(tmp_path))
        assert result["rules"] == []


class TestReviewers:
    def test_valid_reviewer_resolves(self, mod, tmp_path):
        _touch(tmp_path, ".ai/agents/review/renewals.md")
        _write_config(tmp_path, {"review": {
            "defaults": {"execution": "inline", "channel": "blocking"},
            "reviewers": [
                {"id": "renewals", "label": "Renewals Expert",
                 "ref": ".ai/agents/review/renewals.md",
                 "applies_to": {"paths": ["includes/**"]},
                 "channel": "blocking", "model": "sonnet"}
            ]}})
        result = mod.load_review_config(str(tmp_path))
        assert len(result["reviewers"]) == 1
        rev = result["reviewers"][0]
        assert rev["id"] == "renewals"
        assert rev["label"] == "Renewals Expert"
        assert rev["ref"] == ".ai/agents/review/renewals.md"
        assert rev["resolved_ref"].endswith(".ai/agents/review/renewals.md")
        assert rev["channel"] == "blocking"
        assert rev["execution"] == "inline"
        assert rev["model"] == "sonnet"

    def test_label_defaults_to_id(self, mod, tmp_path):
        _touch(tmp_path, "r.md")
        _write_config(tmp_path, {"review": {"reviewers": [
            {"id": "lens-a", "ref": "r.md"}
        ]}})
        result = mod.load_review_config(str(tmp_path))
        assert result["reviewers"][0]["label"] == "lens-a"

    def test_advisory_channel_and_default_execution(self, mod, tmp_path):
        _touch(tmp_path, "r.md")
        _write_config(tmp_path, {"review": {
            "defaults": {"execution": "isolated"},
            "reviewers": [{"id": "adv", "ref": "r.md", "channel": "advisory"}]
        }})
        result = mod.load_review_config(str(tmp_path))
        rev = result["reviewers"][0]
        assert rev["channel"] == "advisory"
        assert rev["execution"] == "isolated"  # inherits default

    def test_bad_channel_falls_back(self, mod, tmp_path):
        _touch(tmp_path, "r.md")
        _write_config(tmp_path, {"review": {"reviewers": [
            {"id": "x", "ref": "r.md", "channel": "nonsense"}
        ]}})
        result = mod.load_review_config(str(tmp_path))
        assert result["reviewers"][0]["channel"] == "blocking"
        assert any("invalid channel" in d for d in result["diagnostics"])

    def test_missing_ref_dropped(self, mod, tmp_path):
        _write_config(tmp_path, {"review": {"reviewers": [
            {"id": "noref"}
        ]}})
        result = mod.load_review_config(str(tmp_path))
        assert result["reviewers"] == []
