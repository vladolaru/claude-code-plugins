"""Composer installs in place with its vendor dir redirected into the cache.

Staging composer into an isolated directory cannot work for repos that
declare `type: path` repositories — composer resolves those relative to the
composer.json it is reading, and a staging dir has no siblings. WooCommerce's
nested root declares "lib" and "../../packages/php/*" and fails with
"Source path ... is not found". Running in place with COMPOSER_VENDOR_DIR
pointed at the cache slot keeps the working tree clean and lets the relative
paths resolve.
"""

import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from hosts.ensure_installed import _handle_dep_root
from hosts.install.lockfile import DepRoot


def _write(path, content="{}"):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as handle:
        handle.write(content)


@pytest.fixture
def nested_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _write(repo / "plugins/woocommerce/composer.json")
    _write(repo / "plugins/woocommerce/composer.lock")
    monkeypatch.setenv("HOME", str(tmp_path))
    return repo


def test_composer_runs_in_the_dep_root_not_the_cache(nested_repo):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cwd"] = kwargs["cwd"]
        captured["env"] = kwargs["env"]
        Path(kwargs["env"]["COMPOSER_VENDOR_DIR"]).mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("hosts.ensure_installed.subprocess.run", side_effect=fake_run):
        result = _handle_dep_root(
            DepRoot("composer", "plugins/woocommerce"), str(nested_repo), [],
        )

    assert result["status"] == "ok"
    # cwd is the real dep root, so `type: path` repositories resolve.
    assert captured["cwd"] == str(nested_repo / "plugins" / "woocommerce")


def test_vendor_dir_is_redirected_outside_the_repo(nested_repo):
    """The working tree must not gain a vendor/ directory."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["vendor"] = kwargs["env"]["COMPOSER_VENDOR_DIR"]
        Path(captured["vendor"]).mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("hosts.ensure_installed.subprocess.run", side_effect=fake_run):
        _handle_dep_root(
            DepRoot("composer", "plugins/woocommerce"), str(nested_repo), [],
        )

    vendor = captured["vendor"]
    assert os.path.isabs(vendor)
    assert not vendor.startswith(str(nested_repo) + os.sep)
    assert not (nested_repo / "plugins" / "woocommerce" / "vendor").exists()


def test_result_carries_the_dep_root_path(nested_repo):
    def fake_run(cmd, **kwargs):
        Path(kwargs["env"]["COMPOSER_VENDOR_DIR"]).mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("hosts.ensure_installed.subprocess.run", side_effect=fake_run):
        result = _handle_dep_root(
            DepRoot("composer", "plugins/woocommerce"), str(nested_repo), [],
        )

    assert result["manager"] == "composer"
    assert result["path"] == "plugins/woocommerce"


def test_nested_root_gets_its_own_cache_slot(nested_repo):
    def fake_run(cmd, **kwargs):
        Path(kwargs["env"]["COMPOSER_VENDOR_DIR"]).mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("hosts.ensure_installed.subprocess.run", side_effect=fake_run):
        result = _handle_dep_root(
            DepRoot("composer", "plugins/woocommerce"), str(nested_repo), [],
        )

    assert result["cache_path"].endswith("composer@plugins-woocommerce")


def test_js_still_installs_from_staged_inputs(tmp_path, monkeypatch):
    """The in-place path is composer-only; JS keeps staging."""
    repo = tmp_path / "repo"
    _write(repo / "package.json")
    _write(repo / "package-lock.json")
    monkeypatch.setenv("HOME", str(tmp_path))
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cwd"] = kwargs["cwd"]
        captured["env"] = kwargs["env"]
        # Assert while the staging dir still exists — ensure_current renames
        # it into the slot as soon as install_fn returns.
        captured["staged_manifest"] = Path(kwargs["cwd"], "package.json").is_file()
        Path(kwargs["cwd"], "node_modules").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("hosts.ensure_installed.subprocess.run", side_effect=fake_run):
        result = _handle_dep_root(DepRoot("npm", "."), str(repo), [])

    assert result["status"] == "ok"
    assert captured["cwd"] != str(repo)  # staged, not in place
    assert "COMPOSER_VENDOR_DIR" not in captured["env"]
    assert captured["staged_manifest"]
