"""Tests for the explicit (.pirategoat/config.json) resolver."""

import json
import os

import pytest
from pathlib import Path

from hosts.resolvers.explicit import ExplicitResolver


def test_absent_config_returns_empty(make_repo):
    repo = make_repo({"README.md": "# repo"})
    result = ExplicitResolver().resolve(str(repo))
    assert result.entries == []
    assert result.unresolved == []


def test_malformed_json_returns_empty_with_note(make_repo):
    repo = make_repo({".pirategoat/config.json": "{not json"})
    result = ExplicitResolver().resolve(str(repo))
    assert result.entries == []
    assert result.notes.get("parse_error") is not None


def test_declared_host_with_absolute_path_resolves(tmp_path, make_repo):
    host_dir = tmp_path / "wc"
    host_dir.mkdir()
    config = {"hosts": {"runtime": [
        {"name": "woocommerce", "path": str(host_dir), "version": "9.5"}
    ]}}
    repo = make_repo({".pirategoat/config.json": json.dumps(config)})
    result = ExplicitResolver().resolve(str(repo))
    assert len(result.entries) == 1
    e = result.entries[0]
    assert e.name == "woocommerce"
    assert e.kind == "runtime-host"
    assert e.path == str(host_dir)
    assert e.version == "9.5"
    assert e.source == "explicit"
    assert e.confidence == "high"


def test_declared_host_with_missing_path_is_noted(make_repo, tmp_path):
    config = {"hosts": {"runtime": [
        {"name": "wordpress", "path": str(tmp_path / "nonexistent")}
    ]}}
    repo = make_repo({".pirategoat/config.json": json.dumps(config)})
    result = ExplicitResolver().resolve(str(repo))
    assert result.entries == []
    assert "parse_error" in result.notes
    assert "does not exist" in result.notes["parse_error"]


def test_declared_host_with_file_path_is_noted(make_repo, tmp_path):
    host_file = tmp_path / "not-a-host"
    host_file.write_text("not a directory")
    config = {"hosts": {"runtime": [
        {"name": "wordpress", "path": str(host_file)}
    ]}}
    repo = make_repo({".pirategoat/config.json": json.dumps(config)})
    result = ExplicitResolver().resolve(str(repo))
    assert result.entries == []
    assert "parse_error" in result.notes
    assert "not a directory" in result.notes["parse_error"]


def test_relative_path_is_resolved_from_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    host = tmp_path / "host"
    host.mkdir()
    (repo / ".pirategoat").mkdir()
    (repo / ".pirategoat" / "config.json").write_text(json.dumps({
        "hosts": {"runtime": [{"name": "host", "path": "../host"}]}
    }))
    result = ExplicitResolver().resolve(str(repo))
    assert result.entries[0].path == str(host.resolve())


@pytest.mark.parametrize("raw_path", [".", "./plugins/my-plugin"])
def test_paths_inside_reviewed_repo_are_not_runtime_hosts(tmp_path, raw_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "plugins" / "my-plugin").mkdir(parents=True)
    (repo / ".pirategoat").mkdir()
    (repo / ".pirategoat" / "config.json").write_text(json.dumps({
        "hosts": {"runtime": [{"name": "repo", "path": raw_path}]}
    }))

    result = ExplicitResolver().resolve(str(repo))

    assert result.entries == []
    assert "inside reviewed repo" in result.notes.get("skipped", "")


def test_non_dict_json_root_returns_empty_with_note(make_repo):
    """JSON root is a string/list/number, not an object."""
    repo = make_repo({".pirategoat/config.json": '"hello"'})
    result = ExplicitResolver().resolve(str(repo))
    assert result.entries == []
    assert result.notes.get("parse_error") is not None


def test_non_dict_host_entry_returns_empty_with_note(make_repo):
    """An entry in the runtime list is a string instead of an object."""
    repo = make_repo({".pirategoat/config.json": json.dumps({
        "hosts": {"runtime": ["not-an-object"]}
    })})
    result = ExplicitResolver().resolve(str(repo))
    assert result.entries == []
    assert result.notes.get("parse_error") is not None


def test_entry_missing_name_is_noted(make_repo, tmp_path):
    """Declarer omitted 'name'."""
    host = tmp_path / "wc"
    host.mkdir()
    repo = make_repo({".pirategoat/config.json": json.dumps({
        "hosts": {"runtime": [{"path": str(host)}]}
    })})
    result = ExplicitResolver().resolve(str(repo))
    assert result.entries == []
    assert "parse_error" in result.notes
    assert "name" in result.notes["parse_error"]


def test_entry_missing_path_is_noted(make_repo):
    """Declarer omitted 'path'."""
    repo = make_repo({".pirategoat/config.json": json.dumps({
        "hosts": {"runtime": [{"name": "wordpress"}]}
    })})
    result = ExplicitResolver().resolve(str(repo))
    assert result.entries == []
    assert "parse_error" in result.notes
    assert "path" in result.notes["parse_error"]


def test_symlinked_path_resolving_into_repo_is_skipped(tmp_path, make_repo):
    """.pirategoat/config.json is repo-controlled; a declared host path
    that is really a symlink back into the reviewed repo must hit the
    self-skip — presenting first-party code as trusted upstream source
    would let reviewers "verify" the PR's changes against themselves.
    Behavioral pin for any containment re-derivation, in any spelling."""
    link = tmp_path / "wc-link"
    config = {"hosts": {"runtime": [
        {"name": "woocommerce", "path": str(link)}
    ]}}
    repo = make_repo({
        ".pirategoat/config.json": json.dumps(config),
        "embedded/placeholder.txt": "x",
    })
    os.symlink(str(repo / "embedded"), str(link))

    result = ExplicitResolver().resolve(str(repo))

    assert result.entries == []
    assert "inside reviewed repo" in result.notes.get("skipped", "")
