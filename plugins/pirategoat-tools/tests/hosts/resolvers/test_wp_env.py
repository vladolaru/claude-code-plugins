"""Tests for the wp-env resolver."""

import json
import os
from pathlib import Path

import pytest

from hosts.resolvers.wp_env import WpEnvResolver


def test_empty_when_no_config(make_repo):
    repo = make_repo({"README.md": "# repo"})
    result = WpEnvResolver().resolve(str(repo))
    assert result.entries == []
    assert result.unresolved == []


def test_mappings_produces_runtime_hosts(tmp_path):
    repo = tmp_path / "repo"
    wc = tmp_path / "wc-develop" / "plugins" / "woocommerce"
    wc.mkdir(parents=True)
    repo.mkdir()
    (repo / ".wp-env.override.json").write_text(json.dumps({
        "mappings": {
            "wp-content/plugins/woocommerce": "../wc-develop/plugins/woocommerce"
        }
    }))
    result = WpEnvResolver().resolve(str(repo))
    assert len(result.entries) == 1
    e = result.entries[0]
    assert e.name == "woocommerce"
    assert e.kind == "runtime-host"
    assert e.path == str(wc.resolve())
    assert e.source == "wp-env"


def test_mappings_skip_non_code_targets(tmp_path):
    repo = tmp_path / "repo"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    repo.mkdir()
    (repo / ".wp-env.override.json").write_text(json.dumps({
        "mappings": {
            "wp-content/uploads": "../uploads"
        }
    }))

    result = WpEnvResolver().resolve(str(repo))

    assert result.entries == []
    assert result.unresolved == []


def test_core_local_path_produces_entry(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    wp = tmp_path / "wordpress-develop"
    wp.mkdir()
    (repo / ".wp-env.override.json").write_text(json.dumps({
        "core": "../wordpress-develop"
    }))
    result = WpEnvResolver().resolve(str(repo))
    assert len(result.entries) == 1
    e = result.entries[0]
    assert e.name == "wordpress"
    assert e.path == str(wp.resolve())


def test_core_remote_ref_produces_unresolved(make_repo):
    repo = make_repo({".wp-env.override.json": json.dumps({
        "core": "WordPress/WordPress#6.9"
    })})
    result = WpEnvResolver().resolve(str(repo))
    assert result.entries == []
    assert len(result.unresolved) == 1
    u = result.unresolved[0]
    assert u["name"] == "wordpress"
    assert u["version"] == "6.9"
    assert u["reason"] == "remote_ref_not_local"


def test_plugins_array_mix_of_local_and_remote(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    jetpack = tmp_path / "jetpack-dev-tools"
    jetpack.mkdir()
    (repo / ".wp-env.override.json").write_text(json.dumps({
        "plugins": [
            "../jetpack-dev-tools",
            "Automattic/jetpack-debug-helper#2.2.4",
        ]
    }))
    result = WpEnvResolver().resolve(str(repo))
    # Local dir -> resolved; remote ref -> unresolved
    assert len(result.entries) == 1
    assert result.entries[0].name == "jetpack-dev-tools"
    assert len(result.unresolved) == 1
    assert result.unresolved[0]["name"] == "jetpack-debug-helper"


def test_override_merges_with_base(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    theme = tmp_path / "theme"
    theme.mkdir()
    (repo / ".wp-env.json").write_text(json.dumps({
        "themes": ["../theme"],
    }))
    (repo / ".wp-env.override.json").write_text(json.dumps({
        "port": 9001,  # override doesn't touch themes
    }))
    result = WpEnvResolver().resolve(str(repo))
    assert len(result.entries) == 1
    assert result.entries[0].name == "theme"


def test_override_mappings_merge_with_base_mappings(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    woocommerce = tmp_path / "woocommerce"
    dev_tools = tmp_path / "dev-tools"
    woocommerce.mkdir()
    dev_tools.mkdir()
    (repo / ".wp-env.json").write_text(json.dumps({
        "mappings": {
            "wp-content/plugins/woocommerce": "../woocommerce",
        },
    }))
    (repo / ".wp-env.override.json").write_text(json.dumps({
        "mappings": {
            "wp-content/plugins/dev-tools": "../dev-tools",
        },
    }))

    result = WpEnvResolver().resolve(str(repo))

    entries = {entry.name: entry.path for entry in result.entries}
    assert entries == {
        "woocommerce": str(woocommerce.resolve()),
        "dev-tools": str(dev_tools.resolve()),
    }


def test_dot_entry_in_plugins_is_self_and_skipped(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".wp-env.json").write_text(json.dumps({
        "plugins": ["."]
    }))
    result = WpEnvResolver().resolve(str(repo))
    # "." means the repo itself is the plugin — not an upstream host
    assert result.entries == []


def test_local_plugin_inside_repo_is_self_owned_and_skipped(tmp_path):
    repo = tmp_path / "woocommerce-subscriptions"
    helper = repo / "tests" / "e2e" / "test-configuration-plugin"
    helper.mkdir(parents=True)
    (repo / ".wp-env.json").write_text(json.dumps({
        "plugins": [
            ".",
            "./tests/e2e/test-configuration-plugin",
        ]
    }))
    result = WpEnvResolver().resolve(str(repo))
    assert result.entries == []
    assert result.unresolved == []


def test_symlinked_mapping_resolving_into_repo_is_self_owned_and_skipped(tmp_path):
    """A mapping spelled as an outside path can be a symlink resolving back
    into the reviewed repo. Reporting it as a runtime host would present
    the PR's own code as independent upstream — reviewers would "verify"
    first-party changes against themselves and could emit wrongful
    integration findings. Conservative skip is correct: better a missing
    advisory path than a wrong one. This is a behavioral pin: any
    containment re-derivation, in any spelling, must reproduce it."""
    repo = tmp_path / "repo"
    (repo / "embedded-wc").mkdir(parents=True)
    os.symlink(str(repo / "embedded-wc"), str(tmp_path / "wc-link"))
    (repo / ".wp-env.json").write_text(json.dumps({
        "mappings": {"wp-content/plugins/woocommerce": "../wc-link"}
    }))

    result = WpEnvResolver().resolve(str(repo))

    assert result.entries == []
    assert result.unresolved == []


def test_symlinked_in_repo_mapping_resolving_outside_is_a_runtime_host(tmp_path):
    """The inverse: an in-repo spelling whose directory is a symlink to an
    external tree genuinely provides external content — classification
    follows the resolved identity, not the spelling."""
    repo = tmp_path / "repo"
    repo.mkdir()
    external = tmp_path / "wc-develop" / "plugins" / "woocommerce"
    external.mkdir(parents=True)
    os.symlink(str(external), str(repo / "wc-link"))
    (repo / ".wp-env.json").write_text(json.dumps({
        "mappings": {"wp-content/plugins/woocommerce": "./wc-link"}
    }))

    result = WpEnvResolver().resolve(str(repo))

    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.name == "woocommerce"
    assert entry.kind == "runtime-host"
    assert entry.path == str(repo / "wc-link")


def test_local_plugin_outside_repo_is_runtime_host(tmp_path):
    repo = tmp_path / "plugin-under-review"
    repo.mkdir()
    external = tmp_path / "woocommerce-develop" / "plugins" / "woocommerce"
    external.mkdir(parents=True)
    (repo / ".wp-env.json").write_text(json.dumps({
        "plugins": ["../woocommerce-develop/plugins/woocommerce"]
    }))
    result = WpEnvResolver().resolve(str(repo))
    assert len(result.entries) == 1
    assert result.entries[0].name == "woocommerce"
    assert result.entries[0].path == str(external.resolve())


def test_wp_env_tolerates_non_dict_mappings(tmp_path):
    """mappings: [] or mappings: "/foo" must not crash — emit no entries."""
    (tmp_path / ".wp-env.json").write_text(json.dumps({"mappings": []}))
    result = WpEnvResolver().resolve(str(tmp_path))
    assert result.entries == []

    (tmp_path / ".wp-env.json").write_text(json.dumps({"mappings": "/tmp/foo"}))
    result = WpEnvResolver().resolve(str(tmp_path))
    assert result.entries == []


def test_wp_env_tolerates_non_string_source_values(tmp_path):
    """mappings values can be objects (wp-env ref form) — must not crash."""
    (tmp_path / ".wp-env.json").write_text(
        json.dumps({"mappings": {"wp-content/plugins/foo": {"ref": "main"}}})
    )
    result = WpEnvResolver().resolve(str(tmp_path))
    # Object-form source is not a local path; should be skipped or recorded as unresolved
    assert result.entries == []


def test_wp_env_resolver_source_label():
    assert WpEnvResolver.source == "wp-env"
