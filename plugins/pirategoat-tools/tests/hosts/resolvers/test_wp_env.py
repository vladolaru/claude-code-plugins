"""Tests for the wp-env resolver."""

import json
import os

import pytest

from hosts.chain import ResolverChain
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
    assert u["root"] == ""


@pytest.mark.parametrize("ref", ["trunk", "feature/x"])
def test_core_remote_branch_ref_is_not_a_version(make_repo, ref):
    """A `#ref` names a branch as readily as a version, and a branch name
    can be private. Only a version-shaped ref is recorded; the raw pin
    stays in `raw`, which no projection carries."""
    repo = make_repo({".wp-env.override.json": json.dumps({
        "core": f"WordPress/WordPress#{ref}"
    })})
    result = WpEnvResolver().resolve(str(repo))
    u = result.unresolved[0]
    assert u["version"] is None
    assert u["raw"] == f"WordPress/WordPress#{ref}"


def test_plugin_remote_branch_ref_is_not_a_version(make_repo):
    """The core and plugin paths diverge after `_parse_remote_ref`: core
    (`_handle_core`) discards the parsed name and always appends `wordpress`
    regardless of parse success, while the plugin path (`_handle_array_item`)
    appends only `if name is not None` — so a plugin ref this resolver fails
    to name at all would silently vanish from `unresolved`, with no core row
    catching the regression. A slash in the ref (a private branch name) must
    still parse the plugin name and land it as unresolved with no version."""
    repo = make_repo({".wp-env.override.json": json.dumps({
        "plugins": ["Automattic/jetpack-debug-helper#add/private-thing"]
    })})
    result = WpEnvResolver().resolve(str(repo))
    assert [(u["name"], u["version"]) for u in result.unresolved] == [
        ("jetpack-debug-helper", None),
    ]


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
    assert result.unresolved[0]["root"] == ""


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


def test_wp_env_tolerates_non_string_source_values(tmp_path):
    """mappings values can be objects (wp-env ref form) — must not crash."""
    (tmp_path / ".wp-env.json").write_text(
        json.dumps({"mappings": {"wp-content/plugins/foo": {"ref": "main"}}})
    )
    result = WpEnvResolver().resolve(str(tmp_path))
    # Object-form source is not a local path; should be skipped or recorded as unresolved
    assert result.entries == []


def test_a_wp_env_file_two_levels_down_is_read_relative_to_its_own_directory(tmp_path):
    """`plugins/woocommerce/.wp-env.json` in the WooCommerce monorepo:
    `plugins: ["."]` is the plugin itself, `core` is a wp.org zip."""
    repo = tmp_path / "woocommerce-develop"
    plugin = repo / "plugins" / "woocommerce"
    plugin.mkdir(parents=True)
    (plugin / ".wp-env.json").write_text(json.dumps({
        "core": "https://wordpress.org/wordpress-latest.zip",
        "plugins": ["."],
    }))
    result = WpEnvResolver().resolve(str(repo))
    assert result.entries == []
    assert result.unresolved == [{
        "name": "wordpress", "version": None, "reason": "remote_url_not_local",
        "source": "wp-env", "raw": "https://wordpress.org/wordpress-latest.zip",
        "root": "plugins/woocommerce",
    }]


def test_a_non_object_wp_env_document_does_not_abort_other_scan_roots(tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "packages" / "wordpress"
    nested.mkdir(parents=True)
    (repo / ".wp-env.json").write_text(json.dumps(["unexpected"]))
    (nested / ".wp-env.json").write_text(json.dumps({
        "core": "https://wordpress.org/wordpress-latest.zip",
    }))

    result = WpEnvResolver().resolve(str(repo))

    assert result.unresolved == [{
        "name": "wordpress", "version": None, "reason": "remote_url_not_local",
        "source": "wp-env", "raw": "https://wordpress.org/wordpress-latest.zip",
        "root": "packages/wordpress",
    }]


def test_a_versioned_core_zip_carries_its_version(make_repo):
    repo = make_repo({".wp-env.json": json.dumps({"core": "https://wordpress.org/wordpress-6.7.1.zip"})})
    result = WpEnvResolver().resolve(str(repo))
    assert result.unresolved[0]["name"] == "wordpress"
    assert result.unresolved[0]["version"] == "6.7.1"
    assert result.unresolved[0]["root"] == ""


def test_a_plugin_zip_from_wordpress_org_names_its_slug(make_repo):
    repo = make_repo({".wp-env.json": json.dumps({"plugins": [
        ".", "https://downloads.wordpress.org/plugin/woocommerce.zip",
        "https://downloads.wordpress.org/plugin/jetpack.14.1.zip",
        "https://example.com/private-plugin.zip",
    ]})})
    result = WpEnvResolver().resolve(str(repo))
    assert [(u["name"], u["version"], u["reason"]) for u in result.unresolved] == [
        ("woocommerce", None, "remote_url_not_local"),
        ("jetpack", "14.1", "remote_url_not_local"),
    ]


def test_a_local_mapping_two_levels_down_resolves_against_that_directory(tmp_path):
    repo = tmp_path / "repo"
    wc = tmp_path / "wc-develop" / "plugins" / "woocommerce"
    wc.mkdir(parents=True)
    nested = repo / "tools" / "env"
    nested.mkdir(parents=True)
    (nested / ".wp-env.json").write_text(json.dumps({
        "mappings": {"wp-content/plugins/woocommerce": "../../../wc-develop/plugins/woocommerce"},
    }))
    result = WpEnvResolver().resolve(str(repo))
    assert [(e.name, e.path) for e in result.entries] == [("woocommerce", str(wc.resolve()))]


@pytest.mark.parametrize("bad_document, field", [
    pytest.param({"plugins": 7}, "plugins", id="plugins-number"),
    pytest.param({"themes": "invalid"}, "themes", id="themes-string"),
    pytest.param({"mappings": []}, "mappings", id="mappings-list"),
])
def test_malformed_nested_fields_preserve_other_roots(tmp_path, bad_document, field):
    repo = tmp_path / "repo"
    wc = tmp_path / "woocommerce"
    wc.mkdir()
    bad = repo / "tools" / "bad"
    good = repo / "tools" / "later"
    bad.mkdir(parents=True)
    good.mkdir()
    (repo / ".wp-env.json").write_text(json.dumps({
        "mappings": {"wp-content/plugins/woocommerce": "../woocommerce"},
    }))
    bad_file = bad / ".wp-env.json"
    bad_file.write_text(json.dumps(bad_document))
    (good / ".wp-env.json").write_text(json.dumps({
        "plugins": ["https://downloads.wordpress.org/plugin/optional-plugin.zip"],
    }))

    manifest = ResolverChain([WpEnvResolver()]).run(str(repo))

    assert [(entry.name, entry.path) for entry in manifest.resolved] == [("woocommerce", str(wc))]
    assert [item["name"] for item in manifest.unresolved] == ["optional-plugin"]
    assert manifest.unresolved[0]["declared_by"] == [{
        "source": "wp-env", "reason": "remote_url_not_local", "root": "tools/later",
    }]
    diagnostic = manifest.diagnostics["resolver_detail"]["wp-env"]["notes"]["parse_error"]
    assert str(bad_file) in diagnostic
    assert field in diagnostic
    assert "expected" in diagnostic
    assert manifest.banner.degraded is True
    assert manifest.banner.reason == "partial_unresolved"
    assert manifest.banner.unresolved == manifest.unresolved


def test_malformed_override_field_preserves_valid_base_signals(make_repo):
    repo = make_repo({
        ".wp-env.json": json.dumps({"plugins": ["vendor/optional-plugin#2.0"]}),
        ".wp-env.override.json": json.dumps({"plugins": 7}),
    })

    manifest = ResolverChain([WpEnvResolver()]).run(str(repo))

    assert [item["name"] for item in manifest.unresolved] == ["optional-plugin"]
    assert manifest.unresolved[0]["version"] == "2.0"
    diagnostic = manifest.diagnostics["resolver_detail"]["wp-env"]["notes"]["parse_error"]
    assert ".wp-env.override.json" in diagnostic
    assert "plugins" in diagnostic
    assert manifest.banner.reason == "fully_unavailable"
