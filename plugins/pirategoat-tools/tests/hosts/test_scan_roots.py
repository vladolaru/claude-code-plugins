"""Tests for the resolvers' shared scan roots."""

import json
import os
import pytest

import hosts.scan_roots as scan_roots_module
from hosts.chain import ResolverChain
from hosts.resolvers.wp_env import WpEnvResolver
from hosts.scan_roots import (
    MAX_DIRS_PER_LEVEL, ORIGIN_CONFIG, ORIGIN_REPO, ORIGIN_SUBDIRECTORY, scan_roots,
)


def _relatives(result):
    return [root.relative for root in result.roots]


def test_the_repo_root_is_always_first(make_repo):
    repo = make_repo({"README.md": "# repo"})
    result = scan_roots(str(repo))
    assert _relatives(result) == [""]
    assert result.roots[0].origin == ORIGIN_REPO
    assert result.roots[0].path == os.path.abspath(str(repo))
    assert result.config_errors == []


def test_depth_one_and_two_directories_are_roots_in_sorted_order(make_repo):
    repo = make_repo({
        "plugins/woocommerce/woocommerce.php": "<?php",
        "plugins/woocommerce-beta-tester/x.php": "<?php",
        "packages/js/a/b/c.txt": "deeper than two is not scanned",
        "tools/README.md": "",
    })
    result = scan_roots(str(repo))
    assert _relatives(result) == [
        "", "packages", "packages/js", "plugins", "plugins/woocommerce",
        "plugins/woocommerce-beta-tester", "tools",
    ]
    assert {root.origin for root in result.roots[1:]} == {ORIGIN_SUBDIRECTORY}


def test_dot_directories_dependency_roots_and_symlinks_are_skipped(make_repo, tmp_path):
    repo = make_repo({
        ".github/workflows/ci.yml": "",
        "node_modules/pkg/index.js": "",
        "vendor/lib/a.php": "",
        "src/vendor/x.php": "",
        "src/.hidden/y.php": "",
    })
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), str(repo / "linked"))
    result = scan_roots(str(repo))
    assert _relatives(result) == ["", "src"]


def test_directory_caps_hold(make_repo):
    repo = make_repo({f"d{n:03d}/keep": "" for n in range(MAX_DIRS_PER_LEVEL + 5)})
    result = scan_roots(str(repo))
    assert len([r for r in result.roots if r.origin == ORIGIN_SUBDIRECTORY]) == MAX_DIRS_PER_LEVEL


def test_configured_roots_are_added_after_the_scan(make_repo):
    repo = make_repo({
        "projects/plugins/jetpack/jetpack.php": "<?php",
        ".pirategoat/config.json": json.dumps({"hosts": {"roots": ["projects/plugins/jetpack"]}}),
    })
    result = scan_roots(str(repo))
    assert _relatives(result) == ["", "projects", "projects/plugins", "projects/plugins/jetpack"]
    assert result.roots[-1].origin == ORIGIN_CONFIG
    assert result.config_errors == []


def test_a_configured_root_already_scanned_is_not_duplicated(make_repo):
    repo = make_repo({
        "plugins/woocommerce/woocommerce.php": "<?php",
        ".pirategoat/config.json": json.dumps({"hosts": {"roots": ["plugins/woocommerce"]}}),
    })
    result = scan_roots(str(repo))
    assert _relatives(result) == ["", "plugins", "plugins/woocommerce"]
    assert result.roots[-1].origin == ORIGIN_SUBDIRECTORY


def test_bad_configured_roots_are_errors_not_roots(make_repo, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    repo = make_repo({
        ".pirategoat/config.json": json.dumps({"hosts": {"roots": [
            "../elsewhere", "missing/dir", 7, "",
        ]}}),
    })
    result = scan_roots(str(repo))
    assert _relatives(result) == [""]
    assert result.config_errors == [
        "hosts.roots: '../elsewhere' is outside the repository",
        "hosts.roots: 'missing/dir' is not a directory",
        "hosts.roots: expected a repo-relative directory string, got 7",
        "hosts.roots: expected a repo-relative directory string, got ''",
    ]


def test_a_non_list_configured_roots_value_is_an_error(make_repo):
    repo = make_repo({".pirategoat/config.json": json.dumps({"hosts": {"roots": 7}})})
    result = scan_roots(str(repo))
    assert _relatives(result) == [""]
    assert result.config_errors == ["hosts.roots: expected a list, got int"]


def test_configured_roots_are_never_dropped_by_the_scan_cap(make_repo, monkeypatch):
    """The cap bounds the walk; a root the configuration names is explicit
    intent and is kept even when the walk filled the cap."""
    monkeypatch.setattr(scan_roots_module, "MAX_ROOTS", 3)
    repo = make_repo({
        "a/file": "",
        "b/file": "",
        "configured/one/deep/file": "",
        "configured/two/deep/file": "",
        ".pirategoat/config.json": json.dumps({"hosts": {"roots": [
            "configured/one/deep", "configured/two/deep",
        ]}}),
    })
    result = scan_roots(str(repo))
    assert _relatives(result) == ["", "a", "b", "configured/one/deep", "configured/two/deep"]
    assert result.config_errors == []


def test_a_malformed_config_is_one_error_and_the_scan_still_runs(make_repo):
    repo = make_repo({".pirategoat/config.json": "{", "src/a.php": "<?php"})
    result = scan_roots(str(repo))
    assert _relatives(result) == ["", "src"]
    assert len(result.config_errors) == 1
    assert result.config_errors[0].endswith("config.json: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)")


def test_invalid_configured_path_preserves_earlier_and_later_roots(make_repo):
    repo = make_repo({
        "projects/plugins/first/keep": "",
        "projects/plugins/later/keep": "",
        ".pirategoat/config.json": json.dumps({"hosts": {"roots": [
            "projects/plugins/first", "bad\u0000name", "projects/plugins/later",
        ]}}),
    })

    result = scan_roots(str(repo))

    assert _relatives(result) == [
        "", "projects", "projects/plugins", "projects/plugins/first", "projects/plugins/later",
    ]
    assert len(result.config_errors) == 1
    assert "hosts.roots" in result.config_errors[0]
    assert repr("bad\u0000name") in result.config_errors[0]


@pytest.mark.parametrize("config, error_fragment", [
    pytest.param(json.dumps({"hosts": {"roots": ["bad\u0000name"]}}), "hosts.roots", id="nul-path"),
])
def test_invalid_config_keeps_the_chain_advisory(make_repo, config, error_fragment):
    repo = make_repo({
        ".pirategoat/config.json": config,
        "tools/env/.wp-env.json": json.dumps({"plugins": ["vendor/optional-plugin#2.0"]}),
    })

    manifest = ResolverChain([WpEnvResolver()]).run(str(repo))

    assert manifest.diagnostics["scan_roots"] == 3
    assert len(manifest.diagnostics["config_errors"]) == 1
    assert error_fragment in manifest.diagnostics["config_errors"][0]
    assert [item["name"] for item in manifest.unresolved] == ["optional-plugin"]
    assert manifest.unresolved[0]["root"] == "tools/env"
    assert manifest.banner.reason == "fully_unavailable"
