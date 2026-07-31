"""Tests for scope-aware dependency-root detection and slot naming."""

import os

import pytest

from hosts.install.lockfile import (
    DepRoot, detect_dep_roots, manager_for_slot, slot_name,
)


def _write(path, content="{}"):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as handle:
        handle.write(content)


@pytest.fixture
def woo_like(tmp_path):
    """A monorepo shaped like WooCommerce: no composer.lock at the root,
    the one that matters nested under plugins/, decoys elsewhere."""
    repo = tmp_path / "repo"
    _write(repo / "package.json")
    _write(repo / "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    _write(repo / "plugins/woocommerce/composer.json")
    _write(repo / "plugins/woocommerce/composer.lock")
    _write(repo / "packages/php/blueprint/composer.json")
    _write(repo / "packages/php/blueprint/composer.lock")
    _write(repo / "plugins/woocommerce/bin/composer/phpcs/composer.json")
    _write(repo / "plugins/woocommerce/bin/composer/phpcs/composer.lock")
    return repo


def test_root_only_detection_without_scope(woo_like):
    """No scope -> repo root only, matching pre-scope behavior."""
    selected, dropped = detect_dep_roots(str(woo_like))

    assert selected == [DepRoot("pnpm", ".")]
    assert dropped == []


def test_changed_file_pulls_in_its_nearest_composer_root(woo_like):
    """The bug this fixes: a PHP review under plugins/woocommerce used to
    resolve no composer root at all."""
    selected, _ = detect_dep_roots(str(woo_like), [
        "plugins/woocommerce/src/Internal/Caches/VersionStringGenerator.php",
    ])

    assert DepRoot("composer", "plugins/woocommerce") in selected


def test_unrelated_composer_roots_are_not_pulled_in(woo_like):
    """47 composer.lock files in the real repo — scope must not install them all."""
    selected, _ = detect_dep_roots(str(woo_like), [
        "plugins/woocommerce/src/Foo.php",
    ])

    paths = [root.rel_path for root in selected if root.manager == "composer"]
    assert paths == ["plugins/woocommerce"]


def test_nearest_ancestor_wins_over_higher_one(woo_like):
    """A file under bin/composer/phpcs belongs to that root, not the plugin."""
    selected, _ = detect_dep_roots(str(woo_like), [
        "plugins/woocommerce/bin/composer/phpcs/somefile.php",
    ])

    paths = [root.rel_path for root in selected if root.manager == "composer"]
    assert paths == ["plugins/woocommerce/bin/composer/phpcs"]


def test_two_scoped_roots_both_selected(woo_like):
    selected, _ = detect_dep_roots(str(woo_like), [
        "plugins/woocommerce/src/Foo.php",
        "packages/php/blueprint/src/Bar.php",
    ])

    paths = sorted(root.rel_path for root in selected if root.manager == "composer")
    assert paths == ["packages/php/blueprint", "plugins/woocommerce"]


def test_deleted_file_path_still_resolves(woo_like):
    """Changed-file lists include deletions; detection is lexical above the
    lockfile check, so a nonexistent path still finds its ancestor."""
    selected, _ = detect_dep_roots(str(woo_like), [
        "plugins/woocommerce/src/Gone/Removed.php",
    ])

    assert DepRoot("composer", "plugins/woocommerce") in selected


def test_scope_outside_any_root_is_ignored(woo_like):
    selected, _ = detect_dep_roots(str(woo_like), ["docs/readme.md"])

    assert [root.rel_path for root in selected if root.manager == "composer"] == []


def test_per_manager_cap_reports_dropped_roots(tmp_path):
    """Coverage may be capped, but never silently."""
    repo = tmp_path / "repo"
    scope = []
    for index in range(6):
        _write(repo / f"pkg{index}/composer.json")
        _write(repo / f"pkg{index}/composer.lock")
        scope.append(f"pkg{index}/src/File.php")

    selected, dropped = detect_dep_roots(str(repo), scope, max_per_manager=4)

    assert len(selected) == 4
    assert len(dropped) == 2
    assert not set(selected) & set(dropped)


def test_scope_paths_are_confined_to_the_repo(woo_like):
    """A traversing path must not resolve to a root outside the clone."""
    selected, _ = detect_dep_roots(str(woo_like), ["../../../etc/passwd"])

    for root in selected:
        assert not root.rel_path.startswith("..")


@pytest.mark.parametrize("dep_root,expected", [
    (DepRoot("composer", "."), "composer"),
    (DepRoot("pnpm", "."), "pnpm"),
    (DepRoot("composer", "plugins/woocommerce"), "composer@plugins-woocommerce"),
])
def test_slot_name(dep_root, expected):
    assert slot_name(dep_root) == expected


def test_slot_names_are_injective_across_slash_and_dash():
    """'a/b' and 'a-b' must not share a slot — a collision would serve one
    root's dependencies to a reviewer asking about the other's."""
    assert slot_name(DepRoot("composer", "a/b")) != slot_name(DepRoot("composer", "a-b"))


@pytest.mark.parametrize("slot,expected", [
    ("composer", "composer"),
    ("pnpm", "pnpm"),
    ("composer@plugins-woocommerce", "composer"),
])
def test_manager_for_slot(slot, expected):
    assert manager_for_slot(slot) == expected


def test_root_slot_name_is_unchanged_for_backward_compat():
    """Slots populated before nested roots existed must stay valid."""
    assert slot_name(DepRoot("composer", ".")) == "composer"
    assert slot_name(DepRoot("composer", "")) == "composer"
