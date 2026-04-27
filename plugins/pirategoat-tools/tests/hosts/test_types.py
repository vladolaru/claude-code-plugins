"""Tests for host-context data types."""

import pytest

from hosts.types import HostEntry, HostContextManifest, Banner


def test_host_entry_minimal():
    entry = HostEntry(
        name="wordpress",
        kind="runtime-host",
        path="/path/to/wordpress-develop",
        source="wp-env",
    )
    assert entry.name == "wordpress"
    assert entry.kind == "runtime-host"
    assert entry.version is None
    assert entry.confidence == "medium"


def test_host_entry_full():
    entry = HostEntry(
        name="woocommerce",
        kind="runtime-host",
        path="/path/to/woocommerce-develop/plugins/woocommerce",
        source="wp-env",
        version="9.5.1",
        version_freshness="2026-04-20",
        confidence="high",
        notes={"personal": True},
    )
    assert entry.version == "9.5.1"
    assert entry.notes["personal"] is True


def test_manifest_serialization_roundtrip():
    manifest = HostContextManifest(
        version=1,
        resolved=[HostEntry(name="wp", kind="runtime-host", path="/x", source="sibling")],
        unresolved=[{"name": "jetpack", "reason": "not_found"}],
        banner=None,
        diagnostics={"resolvers_consulted": ["sibling"]},
    )
    payload = manifest.to_dict()
    assert payload["version"] == 1
    assert payload["resolved"][0]["name"] == "wp"
    rebuilt = HostContextManifest.from_dict(payload)
    assert rebuilt.resolved[0].name == "wp"


def test_banner_degraded_shape():
    banner = Banner(
        degraded=True,
        reason="partial_unresolved",
        message="WordPress core unresolved.",
        unresolved=[{"name": "wordpress", "reason": "not_found_locally"}],
    )
    payload = banner.to_dict()
    assert payload["degraded"] is True
    assert payload["reason"] == "partial_unresolved"
    assert payload["unresolved"][0]["name"] == "wordpress"
