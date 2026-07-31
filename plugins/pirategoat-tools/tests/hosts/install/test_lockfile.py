"""Tests for lockfile detection + hashing."""

import pytest

from hosts.install.lockfile import (
    detect_php_manager, detect_js_manager, lockfile_for_manager,
)


def test_detect_php_manager_composer(tmp_path):
    (tmp_path / "composer.lock").write_text('{}')
    (tmp_path / "composer.json").write_text('{}')
    assert detect_php_manager(str(tmp_path)) == "composer"


def test_detect_php_manager_none(tmp_path):
    assert detect_php_manager(str(tmp_path)) is None


@pytest.mark.parametrize("lockfile,expected", [
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
])
def test_detect_js_manager_by_lockfile(tmp_path, lockfile, expected):
    (tmp_path / lockfile).write_text("# x")
    assert detect_js_manager(str(tmp_path)) == expected


def test_detect_js_prefers_pnpm_over_npm(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("# x")
    (tmp_path / "package-lock.json").write_text("{}")
    # Multiple lockfiles -> pnpm wins per spec §8.1
    assert detect_js_manager(str(tmp_path)) == "pnpm"


def test_detect_js_manager_none(tmp_path):
    (tmp_path / "package.json").write_text('{}')  # no lockfile
    assert detect_js_manager(str(tmp_path)) is None


