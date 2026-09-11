"""Tests for the explicit (.pirategoat/config.json) resolver."""

import json
import os

import pytest

from hosts.resolvers.explicit import ExplicitResolver


def test_absent_config_returns_empty(make_repo):
    repo = make_repo({"README.md": "# repo"})
    result = ExplicitResolver().resolve(str(repo))
    assert result.entries == []
    assert result.unresolved == []


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


def _malformed_json(tmp_path):
    return "{not json"


def _non_dict_host_entry(tmp_path):
    """An entry in the runtime list is a string instead of an object."""
    return json.dumps({"hosts": {"runtime": ["not-an-object"]}})


def _entry_missing_name(tmp_path):
    """Declarer omitted 'name'."""
    host = tmp_path / "wc"
    host.mkdir()
    return json.dumps({"hosts": {"runtime": [{"path": str(host)}]}})


def _entry_missing_path(tmp_path):
    """Declarer omitted 'path'."""
    return json.dumps({"hosts": {"runtime": [{"name": "wordpress"}]}})


def _declared_host_with_missing_path(tmp_path):
    return json.dumps({"hosts": {"runtime": [
        {"name": "wordpress", "path": str(tmp_path / "nonexistent")}
    ]}})


def _declared_host_with_file_path(tmp_path):
    host_file = tmp_path / "not-a-host"
    host_file.write_text("not a directory")
    return json.dumps({"hosts": {"runtime": [{"name": "wordpress", "path": str(host_file)}]}})


@pytest.mark.parametrize("config_builder, note_fragment", [
    pytest.param(_malformed_json, None, id="malformed-json"),
    pytest.param(_non_dict_host_entry, None, id="non-dict-host-entry"),
    pytest.param(_entry_missing_name, "name", id="entry-missing-name"),
    pytest.param(_entry_missing_path, "path", id="entry-missing-path"),
    pytest.param(_declared_host_with_missing_path, "does not exist", id="declared-host-missing-path"),
    pytest.param(_declared_host_with_file_path, "not a directory", id="declared-host-file-path"),
])
def test_explicit_config_rejections(make_repo, tmp_path, config_builder, note_fragment):
    """Every shape of a bad `.pirategoat/config.json` (malformed JSON, a
    non-object root or entry, a missing required field, or a declared path
    that doesn't resolve) resolves to no entries and a `parse_error` note."""
    config = config_builder(tmp_path)
    repo = make_repo({".pirategoat/config.json": config})

    result = ExplicitResolver().resolve(str(repo))

    assert result.entries == []
    assert result.notes.get("parse_error") is not None
    if note_fragment is not None:
        assert note_fragment in result.notes["parse_error"]


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
