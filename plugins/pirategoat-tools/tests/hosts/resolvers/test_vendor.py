"""Tests for the vendor/node_modules library-dep resolver."""

from hosts.resolvers.vendor import VendorResolver


def test_empty_when_no_vendor_or_node_modules(make_repo):
    repo = make_repo({"README.md": "# x"})
    result = VendorResolver().resolve(str(repo))
    assert result.entries == []


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
