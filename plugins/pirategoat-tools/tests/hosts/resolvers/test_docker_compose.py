"""Tests for the docker-compose resolver."""

import os
import json
import textwrap
from pathlib import Path

import pytest

from hosts.resolvers.docker_compose import DockerComposeResolver
from hosts.chain import ResolverChain


def _write_compose(repo: Path, filename: str, content: str):
    (repo / filename).write_text(textwrap.dedent(content))


def test_empty_when_no_compose_files(make_repo):
    repo = make_repo({"README.md": "# x"})
    result = DockerComposeResolver().resolve(str(repo))
    assert result.entries == []


@pytest.mark.parametrize("bad_document, field", [
    pytest.param({"services": {"wp": None}}, "services.wp", id="null-service"),
    pytest.param({"services": ["wp"]}, "services", id="services-list"),
    pytest.param({"services": {"wp": {"volumes": 7}}}, "volumes", id="volumes-number"),
])
def test_malformed_nested_fields_preserve_other_files(tmp_path, bad_document, field):
    pytest.importorskip("yaml")
    repo = tmp_path / "repo"
    wc = tmp_path / "woocommerce"
    wc.mkdir()
    bad = repo / "tools" / "bad"
    good = repo / "tools" / "later"
    bad.mkdir(parents=True)
    good.mkdir()
    (repo / "compose.yml").write_text(json.dumps({"services": {"wp": {"volumes": [
        "../woocommerce:/var/www/html/wp-content/plugins/woocommerce",
    ]}}}))
    bad_file = bad / "compose.yml"
    bad_file.write_text(json.dumps(bad_document))
    (good / "compose.yml").write_text(json.dumps({"services": {"wp": {"volumes": [
        "../../../missing:/var/www/html/wp-content/plugins/optional-plugin",
    ]}}}))

    manifest = ResolverChain([DockerComposeResolver()]).run(str(repo))

    assert [(entry.name, entry.path) for entry in manifest.resolved] == [("woocommerce", str(wc))]
    assert [item["name"] for item in manifest.unresolved] == ["optional-plugin"]
    assert manifest.unresolved[0]["declared_by"] == [{
        "source": "docker-compose", "reason": "path_missing",
    }]
    diagnostic = manifest.diagnostics["resolver_detail"]["docker-compose"]["notes"]["parse_error"]
    assert str(bad_file) in diagnostic
    assert field in diagnostic
    assert "expected" in diagnostic
    assert manifest.banner.degraded is True
    assert manifest.banner.reason == "partial_unresolved"
    assert manifest.banner.unresolved == manifest.unresolved


def test_absolute_path_volume_produces_entry_and_personal_note(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    wc = tmp_path / "wc-dev" / "plugins" / "woocommerce"
    wc.mkdir(parents=True)
    _write_compose(repo, "docker-compose.override.yml", f"""\
        services:
          wordpress:
            volumes:
              - {wc}:/var/www/html/wp-content/plugins/woocommerce
    """)
    result = DockerComposeResolver().resolve(str(repo))
    assert len(result.entries) == 1
    e = result.entries[0]
    assert e.name == "woocommerce"
    assert e.path == str(wc)
    assert e.kind == "runtime-host"
    assert e.notes.get("personal") is True
    assert e.source == "docker-compose"


def test_short_form_volume_resolves_without_pyyaml(tmp_path, monkeypatch):
    from hosts.resolvers import docker_compose

    repo = tmp_path / "repo"
    repo.mkdir()
    wc = tmp_path / "wc-dev" / "plugins" / "woocommerce"
    wc.mkdir(parents=True)
    monkeypatch.setattr(docker_compose, "yaml", None)
    _write_compose(repo, "docker-compose.override.yml", f"""\
        services:
          wordpress:
            volumes:
              - {wc}:/var/www/html/wp-content/plugins/woocommerce
    """)

    result = docker_compose.DockerComposeResolver().resolve(str(repo))

    assert len(result.entries) == 1
    assert result.entries[0].name == "woocommerce"
    assert result.entries[0].path == str(wc)
    assert result.notes == {}


def test_relative_path_volume_resolves_from_compose_dir(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    bridge = tmp_path / "wc-calypso-bridge"
    bridge.mkdir()
    _write_compose(repo, "docker-compose.override.yml", """\
        services:
          wordpress:
            volumes:
              - ../wc-calypso-bridge:/var/www/html/wp-content/plugins/wc-calypso-bridge
    """)
    result = DockerComposeResolver().resolve(str(repo))
    assert len(result.entries) == 1
    assert result.entries[0].name == "wc-calypso-bridge"
    assert result.entries[0].path == str(bridge.resolve())
    assert result.entries[0].notes.get("personal") is not True


def test_a_compose_file_two_levels_down_is_read(tmp_path):
    repo = tmp_path / "repo"
    wp = tmp_path / "wordpress-develop"
    wp.mkdir()
    env = repo / "tools" / "docker"
    env.mkdir(parents=True)
    (env / "docker-compose.yml").write_text("""
services:
  wordpress:
    volumes:
      - ../../../wordpress-develop:/var/www/html
""")
    result = DockerComposeResolver().resolve(str(repo))
    assert [(e.name, e.path) for e in result.entries] == [("wordpress", str(wp.resolve()))]


def test_self_plugin_mount_is_not_reported_as_runtime_host(tmp_path):
    repo = tmp_path / "woocommerce-subscriptions"
    repo.mkdir()
    _write_compose(repo, "docker-compose.yml", """\
        services:
          wordpress:
            volumes:
              - .:/var/www/html/wp-content/plugins/woocommerce-subscriptions
    """)
    result = DockerComposeResolver().resolve(str(repo))
    assert result.entries == []
    assert result.unresolved == []


def test_repo_subdirectory_mount_is_not_reported_as_runtime_host(tmp_path):
    repo = tmp_path / "repo"
    plugin = repo / "plugins" / "my-plugin"
    plugin.mkdir(parents=True)
    _write_compose(repo, "docker-compose.yml", """\
        services:
          wordpress:
            volumes:
              - ./plugins/my-plugin:/var/www/html/wp-content/plugins/my-plugin
    """)

    result = DockerComposeResolver().resolve(str(repo))

    assert result.entries == []
    assert result.unresolved == []


def test_named_volume_under_plugin_path_is_not_reported_missing(tmp_path):
    repo = tmp_path / "woocommerce-subscriptions"
    repo.mkdir()
    _write_compose(repo, "docker-compose.yml", """\
        volumes:
          dockerdirectory:
        services:
          wordpress:
            volumes:
              - dockerdirectory:/var/www/html/wp-content/plugins/woocommerce-subscriptions/docker
    """)
    result = DockerComposeResolver().resolve(str(repo))
    assert result.entries == []
    assert result.unresolved == []


@pytest.mark.parametrize("source_spelling, env_setup", [
    pytest.param(
        "${HOST_ROOT}/woocommerce",
        lambda tmp_path, repo, monkeypatch, plugin: monkeypatch.setenv(
            "HOST_ROOT", str(plugin.parent)
        ),
        id="environment-variable",
    ),
    pytest.param(
        "${WC_PATH}",
        lambda tmp_path, repo, monkeypatch, plugin: (
            monkeypatch.delenv("WC_PATH", raising=False),
            (repo / ".env").write_text(f"WC_PATH={plugin}\n"),
        ),
        id="env-file-variable",
    ),
    pytest.param(
        "~/plugins/woocommerce",
        lambda tmp_path, repo, monkeypatch, plugin: monkeypatch.setenv(
            "HOME", str(plugin.parent.parent)
        ),
        id="tilde",
    ),
])
def test_compose_source_expansion(tmp_path, monkeypatch, source_spelling, env_setup):
    """A volume source spelled as an environment variable, an `.env`-file
    variable, or a `~` path is expanded before the path-existence check —
    each is the same expand-then-check contract, differing only in where
    the value comes from."""
    repo = tmp_path / "repo"
    repo.mkdir()
    if source_spelling == "~/plugins/woocommerce":
        plugin = tmp_path / "home" / "plugins" / "woocommerce"
    else:
        plugin = tmp_path / "host-root" / "woocommerce"
    plugin.mkdir(parents=True)
    env_setup(tmp_path, repo, monkeypatch, plugin)
    _write_compose(repo, "docker-compose.override.yml", f"""\
        services:
          wordpress:
            volumes:
              - {source_spelling}:/var/www/html/wp-content/plugins/woocommerce
    """)

    result = DockerComposeResolver().resolve(str(repo))

    assert len(result.entries) == 1
    assert result.entries[0].name == "woocommerce"
    assert result.entries[0].path == str(plugin)
    assert result.unresolved == []


def test_unresolved_env_file_variable_is_reported_without_empty_path_resolution(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.delenv("WC_PATH", raising=False)
    _write_compose(repo, "docker-compose.override.yml", """\
        services:
          wordpress:
            volumes:
              - ${WC_PATH}:/var/www/html/wp-content/plugins/woocommerce
    """)

    result = DockerComposeResolver().resolve(str(repo))

    assert result.entries == []
    assert len(result.unresolved) == 1
    assert result.unresolved[0]["name"] == "woocommerce"
    assert result.unresolved[0]["reason"] == "variable_unresolved"
    assert result.unresolved[0]["variables"] == ["WC_PATH"]


def test_long_form_bind_mount_resolves_runtime_host(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    wc = tmp_path / "woocommerce"
    wc.mkdir()
    _write_compose(repo, "compose.yaml", """\
        services:
          wordpress:
            volumes:
              - type: bind
                source: ../woocommerce
                target: /var/www/html/wp-content/plugins/woocommerce
    """)
    result = DockerComposeResolver().resolve(str(repo))
    assert len(result.entries) == 1
    e = result.entries[0]
    assert e.name == "woocommerce"
    assert e.path == str(wc.resolve())
    assert e.notes.get("wp_kind") == "plugin"
    assert e.notes.get("personal") is not True


def test_core_self_mount_inside_repo_emits_unresolved(tmp_path):
    """A `./docker/wordpress:/var/www/html/` mount is a vendored WP for the
    dev stack, not the repo itself. Surface as unresolved so the cache can
    fulfill it (WooPayments-style setup)."""
    repo = tmp_path / "woocommerce-payments"
    repo.mkdir()
    (repo / "docker" / "wordpress").mkdir(parents=True)
    _write_compose(repo, "docker-compose.yml", """\
        services:
          wordpress:
            volumes:
              - ./docker/wordpress:/var/www/html
    """)
    result = DockerComposeResolver().resolve(str(repo))
    assert result.entries == []
    assert len(result.unresolved) == 1
    item = result.unresolved[0]
    assert item["name"] == "wordpress"
    assert item["reason"] == "vendored_self_mount"
    assert item["source"] == "docker-compose"


def test_core_mount_with_source_eq_repo_root_silent_skips(tmp_path):
    """When the WP repo itself is mounted as core (`.:/var/www/html`), the
    repo IS WordPress — silent skip, no unresolved entry."""
    repo = tmp_path / "wordpress-develop"
    repo.mkdir()
    _write_compose(repo, "docker-compose.yml", """\
        services:
          wordpress:
            volumes:
              - .:/var/www/html
    """)
    result = DockerComposeResolver().resolve(str(repo))
    assert result.entries == []
    assert result.unresolved == []


def test_theme_mount_classified_as_theme(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    theme = tmp_path / "my-theme"
    theme.mkdir()
    _write_compose(repo, "docker-compose.yml", f"""\
        services:
          wordpress:
            volumes:
              - {theme}:/var/www/html/wp-content/themes/my-theme
    """)
    result = DockerComposeResolver().resolve(str(repo))
    assert len(result.entries) == 1
    e = result.entries[0]
    assert e.name == "my-theme"
    assert e.notes.get("wp_kind") == "theme"


def test_unrelated_volume_skipped(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_compose(repo, "docker-compose.yml", """\
        services:
          db:
            volumes:
              - /tmp/mysql-data:/var/lib/mysql
    """)
    result = DockerComposeResolver().resolve(str(repo))
    assert result.entries == []


def test_malformed_yaml_returns_empty_with_note(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yml").write_text("this is: not\n  valid yaml:::")
    result = DockerComposeResolver().resolve(str(repo))
    assert result.entries == []
    assert "parse_error" in result.notes


def test_missing_source_path_produces_unresolved(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_compose(repo, "docker-compose.yml", """\
        services:
          wordpress:
            volumes:
              - /nonexistent/path:/var/www/html/wp-content/plugins/ghost
    """)
    result = DockerComposeResolver().resolve(str(repo))
    assert result.entries == []
    assert len(result.unresolved) == 1
    assert result.unresolved[0]["reason"] == "path_missing"


def test_symlinked_mount_resolving_into_repo_is_self_owned(tmp_path):
    """A compose mount source spelled as an outside path can be a symlink
    resolving back into the reviewed repo — classifying it as upstream
    would report the PR's own code as an independent runtime host.
    Behavioral pin for any containment re-derivation, in any spelling."""
    repo = tmp_path / "repo"
    (repo / "embedded-plugin").mkdir(parents=True)
    os.symlink(str(repo / "embedded-plugin"), str(tmp_path / "plugin-link"))
    _write_compose(repo, "docker-compose.override.yml", """\
        services:
          wordpress:
            volumes:
              - ../plugin-link:/var/www/html/wp-content/plugins/foo
    """)

    result = DockerComposeResolver().resolve(str(repo))

    assert result.entries == []
    assert result.unresolved == []
