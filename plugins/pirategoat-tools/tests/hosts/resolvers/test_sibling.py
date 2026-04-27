"""Tests for sibling-convention resolver."""

import pytest

from hosts.resolvers.sibling import SiblingResolver


def test_empty_when_no_siblings(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    result = SiblingResolver().resolve(str(repo))
    assert result.entries == []


def test_woocommerce_develop_sibling_found(tmp_path):
    parent = tmp_path
    repo = parent / "my-plugin"
    repo.mkdir()
    wc = parent / "woocommerce-develop" / "plugins" / "woocommerce"
    wc.mkdir(parents=True)
    result = SiblingResolver().resolve(str(repo))
    names = [e.name for e in result.entries]
    assert "woocommerce" in names


def test_wordpress_develop_sibling_found(tmp_path):
    parent = tmp_path
    repo = parent / "my-plugin"
    repo.mkdir()
    wp = parent / "wordpress-develop"
    wp.mkdir()
    result = SiblingResolver().resolve(str(repo))
    names = [e.name for e in result.entries]
    assert "wordpress" in names


def test_siblings_have_inferred_confidence(tmp_path):
    parent = tmp_path
    repo = parent / "p"
    repo.mkdir()
    (parent / "wordpress-develop").mkdir()
    result = SiblingResolver().resolve(str(repo))
    assert result.entries[0].confidence == "medium"
    assert result.entries[0].source == "sibling"
