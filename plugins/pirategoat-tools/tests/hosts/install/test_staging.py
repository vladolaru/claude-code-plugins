"""Tests for staging a repo's install inputs into the cache slot."""

import json
import os

import pytest

from hosts.install.staging import stage_inputs


def _write(path, content=""):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as handle:
        handle.write(content)


@pytest.fixture
def repo(tmp_path):
    return tmp_path / "repo"


@pytest.fixture
def cache(tmp_path):
    target = tmp_path / "cache"
    target.mkdir()
    return target


def test_stages_manifest_and_lockfile(repo, cache):
    _write(repo / "package.json", "{}")
    _write(repo / "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")

    stage_inputs("pnpm", str(repo), str(cache))

    assert (cache / "package.json").is_file()
    assert (cache / "pnpm-lock.yaml").is_file()


def test_stages_pnpm_workspace_file(repo, cache):
    """Catalogs live here; without it pnpm fails before reading the lockfile."""
    _write(repo / "package.json", "{}")
    _write(repo / "pnpm-lock.yaml", "")
    _write(repo / "pnpm-workspace.yaml", "catalogs:\n  wp-min:\n    a: 1.0.0\n")

    stage_inputs("pnpm", str(repo), str(cache))

    assert (cache / "pnpm-workspace.yaml").is_file()


def test_stages_pnpmfile_and_npmrc(repo, cache):
    """.pnpmfile.cjs is checksummed into the lockfile; omitting it breaks
    --frozen-lockfile with ERR_PNPM_LOCKFILE_CONFIG_MISMATCH."""
    _write(repo / "package.json", "{}")
    _write(repo / "pnpm-lock.yaml", "")
    _write(repo / ".pnpmfile.cjs", "module.exports = {}\n")
    _write(repo / ".npmrc", "hoist=false\n")

    stage_inputs("pnpm", str(repo), str(cache))

    assert (cache / ".pnpmfile.cjs").is_file()
    assert (cache / ".npmrc").is_file()


def test_stages_patch_files_preserving_relative_path(repo, cache):
    _write(repo / "package.json", json.dumps({
        "pnpm": {"patchedDependencies": {"pkg@1.0.0": "bin/patches/pkg@1.0.0.patch"}}
    }))
    _write(repo / "pnpm-lock.yaml", "")
    _write(repo / "bin/patches/pkg@1.0.0.patch", "--- a\n+++ b\n")

    stage_inputs("pnpm", str(repo), str(cache))

    assert (cache / "bin" / "patches" / "pkg@1.0.0.patch").is_file()


def test_missing_patch_file_is_skipped_not_fatal(repo, cache):
    _write(repo / "package.json", json.dumps({
        "pnpm": {"patchedDependencies": {"pkg@1.0.0": "bin/patches/absent.patch"}}
    }))
    _write(repo / "pnpm-lock.yaml", "")

    stage_inputs("pnpm", str(repo), str(cache))  # must not raise

    assert not (cache / "bin" / "patches" / "absent.patch").exists()


def test_refuses_to_stage_outside_the_repo(repo, cache, tmp_path):
    """rel_path comes from repo-controlled JSON and reviews run against
    untrusted branches, so traversal must not escape the repo."""
    _write(tmp_path / "outside" / "secret.patch", "sensitive")
    _write(repo / "package.json", json.dumps({
        "pnpm": {"patchedDependencies": {"pkg@1.0.0": "../outside/secret.patch"}}
    }))
    _write(repo / "pnpm-lock.yaml", "")

    stage_inputs("pnpm", str(repo), str(cache))

    staged = [name for _, _, files in os.walk(str(cache)) for name in files]
    assert "secret.patch" not in staged


def test_stages_pnpm_workspace_member_manifests(repo, cache):
    """Root-only staging silently under-installs a monorepo."""
    _write(repo / "package.json", "{}")
    _write(repo / "pnpm-lock.yaml", (
        "lockfileVersion: '9.0'\n"
        "\n"
        "importers:\n"
        "\n"
        "  .:\n"
        "    dependencies:\n"
        "      left-pad:\n"
        "        specifier: 1.0.0\n"
        "  packages/js/data:\n"
        "    dependencies:\n"
        "      right-pad:\n"
        "        specifier: 2.0.0\n"
        "  plugins/woocommerce:\n"
        "    dependencies: {}\n"
        "\n"
        "packages:\n"
        "\n"
        "  left-pad@1.0.0:\n"
        "    resolution: {integrity: sha512-x}\n"
    ))
    _write(repo / "packages/js/data/package.json", '{"name":"data"}')
    _write(repo / "plugins/woocommerce/package.json", '{"name":"woo"}')

    stage_inputs("pnpm", str(repo), str(cache))

    assert (cache / "packages" / "js" / "data" / "package.json").is_file()
    assert (cache / "plugins" / "woocommerce" / "package.json").is_file()
    # `packages:` is a sibling top-level key, not an importer.
    assert not (cache / "left-pad@1.0.0").exists()


def test_workspace_yaml_with_tabs_does_not_break_staging(repo, cache):
    """Hand-maintained pnpm-workspace.yaml files contain literal tabs, which
    pnpm tolerates and a strict YAML parser rejects. Staging must not care."""
    _write(repo / "package.json", "{}")
    _write(repo / "pnpm-lock.yaml", "importers:\n\n  .:\n    dependencies: {}\n")
    _write(repo / "pnpm-workspace.yaml", "catalogs:\n\t# tabbed comment\n    wp:\n        a: 1.0.0\n")

    stage_inputs("pnpm", str(repo), str(cache))  # must not raise

    assert (cache / "pnpm-workspace.yaml").is_file()


def test_stages_npm_workspace_members_from_globs(repo, cache):
    _write(repo / "package.json", json.dumps({"workspaces": ["packages/*"]}))
    _write(repo / "package-lock.json", "{}")
    _write(repo / "packages/alpha/package.json", '{"name":"alpha"}')
    _write(repo / "packages/beta/package.json", '{"name":"beta"}')

    stage_inputs("npm", str(repo), str(cache))

    assert (cache / "packages" / "alpha" / "package.json").is_file()
    assert (cache / "packages" / "beta" / "package.json").is_file()


def test_npm_workspaces_object_form(repo, cache):
    _write(repo / "package.json", json.dumps({
        "workspaces": {"packages": ["libs/*"]}
    }))
    _write(repo / "package-lock.json", "{}")
    _write(repo / "libs/one/package.json", '{"name":"one"}')

    stage_inputs("npm", str(repo), str(cache))

    assert (cache / "libs" / "one" / "package.json").is_file()


def test_composer_stages_only_its_pair(repo, cache):
    """Composer is self-contained; no workspace or config expansion applies."""
    _write(repo / "composer.json", "{}")
    _write(repo / "composer.lock", "{}")
    _write(repo / ".npmrc", "hoist=false\n")

    stage_inputs("composer", str(repo), str(cache))

    assert (cache / "composer.json").is_file()
    assert (cache / "composer.lock").is_file()
    assert not (cache / ".npmrc").exists()


def test_malformed_package_json_does_not_raise(repo, cache):
    _write(repo / "package.json", "{ not json")
    _write(repo / "pnpm-lock.yaml", "")

    stage_inputs("pnpm", str(repo), str(cache))  # must not raise

    assert (cache / "package.json").is_file()
