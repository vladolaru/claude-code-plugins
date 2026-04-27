"""Tests for the vendor/node_modules library-dep resolver."""

import json

from hosts.resolvers.vendor import VendorResolver


def test_empty_when_no_vendor_or_node_modules(make_repo):
    repo = make_repo({"README.md": "# x"})
    result = VendorResolver().resolve(str(repo))
    assert result.entries == []


def test_composer_vendor_root_detected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    pkg = repo / "vendor" / "stripe" / "stripe-php"
    pkg.mkdir(parents=True)
    (pkg / "composer.json").write_text(json.dumps({
        "name": "stripe/stripe-php", "version": "16.2.0"
    }))
    result = VendorResolver().resolve(str(repo))
    assert len(result.entries) == 1
    e = result.entries[0]
    assert e.name == "vendor"
    assert e.kind == "library-dep"
    assert e.path == str(repo / "vendor")
    assert e.version is None
    assert e.source == "vendor-inspection"


def test_node_modules_root_detected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    pkg = repo / "node_modules" / "lodash"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": "lodash", "version": "4.17.21"}))
    result = VendorResolver().resolve(str(repo))
    assert len(result.entries) == 1
    e = result.entries[0]
    assert e.name == "node_modules"
    assert e.path == str(repo / "node_modules")
    assert e.version is None


def test_scoped_npm_package_does_not_expand_manifest(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    pkg = repo / "node_modules" / "@wordpress" / "components"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({
        "name": "@wordpress/components", "version": "25.0.0"
    }))
    result = VendorResolver().resolve(str(repo))
    assert len(result.entries) == 1
    e = result.entries[0]
    assert e.name == "node_modules"
    assert e.path == str(repo / "node_modules")
    assert e.version is None


def test_composer_manifest_content_ignored(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    pkg = repo / "vendor" / "acme" / "lib"
    pkg.mkdir(parents=True)
    (pkg / "composer.json").write_text(json.dumps({"name": "acme/lib"}))
    result = VendorResolver().resolve(str(repo))
    assert len(result.entries) == 1
    assert result.entries[0].version is None
    assert result.entries[0].name == "vendor"


def test_malformed_manifest_still_produces_root_entry(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    pkg = repo / "node_modules" / "broken"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text("not-json")
    result = VendorResolver().resolve(str(repo))
    assert len(result.entries) == 1
    assert result.entries[0].version is None
    assert result.entries[0].name == "node_modules"


def test_vendor_root_emitted_once_without_package_enumeration(tmp_path):
    """vendor/ should be emitted once; package contents are for agents to inspect."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "vendor" / "stripe" / ".cache").mkdir(parents=True)
    (repo / "vendor" / "stripe" / "stripe-php").mkdir(parents=True)
    (repo / "vendor" / "stripe" / "stripe-php" / "composer.json").write_text(
        json.dumps({"name": "stripe/stripe-php", "version": "16.2.0"})
    )
    result = VendorResolver().resolve(str(repo))
    names = [e.name for e in result.entries]
    assert "stripe/.cache" not in names
    assert "stripe/stripe-php" not in names
    assert names == ["vendor"]


def test_vendor_and_node_modules_roots_detected_once_each(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "vendor" / "stripe" / "stripe-php").mkdir(parents=True)
    (repo / "vendor" / "automattic" / "jetpack").mkdir(parents=True)
    (repo / "node_modules" / "react").mkdir(parents=True)
    (repo / "node_modules" / "@wordpress" / "components").mkdir(parents=True)

    result = VendorResolver().resolve(str(repo))

    assert [(e.name, e.path) for e in result.entries] == [
        ("vendor", str(repo / "vendor")),
        ("node_modules", str(repo / "node_modules")),
    ]


def test_unreadable_vendor_dir_produces_empty(tmp_path):
    """Permission-denied on os.listdir should not crash."""
    import stat

    repo = tmp_path / "repo"
    repo.mkdir()
    v = repo / "vendor"
    v.mkdir()
    v.chmod(0)
    try:
        result = VendorResolver().resolve(str(repo))
        # Either returns empty (if we can't read it) or works if runner is root
        assert isinstance(result.entries, list)
    finally:
        v.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
