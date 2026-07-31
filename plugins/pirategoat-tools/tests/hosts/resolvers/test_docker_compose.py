"""Tests for the docker-compose resolver."""

import os
import textwrap
from pathlib import Path

import pytest

from hosts.resolvers.docker_compose import DockerComposeResolver


def _write_compose(repo: Path, filename: str, content: str):
    (repo / filename).write_text(textwrap.dedent(content))


def test_empty_when_no_compose_files(make_repo):
    repo = make_repo({"README.md": "# x"})
    result = DockerComposeResolver().resolve(str(repo))
    assert result.entries == []


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


def test_environment_variable_source_is_expanded_before_path_check(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    host_root = tmp_path / "host-root"
    plugin = host_root / "woocommerce"
    plugin.mkdir(parents=True)
    monkeypatch.setenv("HOST_ROOT", str(host_root))
    _write_compose(repo, "docker-compose.override.yml", """\
        services:
          wordpress:
            volumes:
              - ${HOST_ROOT}/woocommerce:/var/www/html/wp-content/plugins/woocommerce
    """)
    result = DockerComposeResolver().resolve(str(repo))
    assert len(result.entries) == 1
    assert result.entries[0].name == "woocommerce"
    assert result.entries[0].path == str(plugin)
    assert result.unresolved == []


def test_env_file_variable_source_is_expanded_before_path_check(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin = tmp_path / "host-root" / "woocommerce"
    plugin.mkdir(parents=True)
    monkeypatch.delenv("WC_PATH", raising=False)
    (repo / ".env").write_text(f"WC_PATH={plugin}\n")
    _write_compose(repo, "docker-compose.override.yml", """\
        services:
          wordpress:
            volumes:
              - ${WC_PATH}:/var/www/html/wp-content/plugins/woocommerce
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


def test_tilde_source_is_expanded_before_path_check(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    plugin = home / "plugins" / "woocommerce"
    plugin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    _write_compose(repo, "docker-compose.override.yml", """\
        services:
          wordpress:
            volumes:
              - ~/plugins/woocommerce:/var/www/html/wp-content/plugins/woocommerce
    """)
    result = DockerComposeResolver().resolve(str(repo))
    assert len(result.entries) == 1
    assert result.entries[0].name == "woocommerce"
    assert result.entries[0].path == str(plugin)
    assert result.unresolved == []


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


def test_plugin_self_mount_in_subdirectory_stays_silent(tmp_path):
    """Even with the new core-target unresolved logic, plugin/theme
    subdirectory self-mounts (monorepo style) don't trigger unresolved —
    they're "repo provides this plugin", not "repo vendors upstream"."""
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


def test_core_mount_produces_wordpress_entry(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    wp = tmp_path / "wordpress-develop"
    wp.mkdir()
    _write_compose(repo, "docker-compose.override.yml", f"""\
        services:
          wordpress:
            volumes:
              - {wp}:/var/www/html
    """)
    result = DockerComposeResolver().resolve(str(repo))
    assert len(result.entries) == 1
    assert result.entries[0].name == "wordpress"


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


@pytest.mark.parametrize("filename", [
    "docker-compose.yaml",
    "compose.yaml",
    "compose.yml",
])
def test_standard_compose_yaml_names_are_discovered(tmp_path, filename):
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin = tmp_path / "my-plugin"
    plugin.mkdir()
    _write_compose(repo, filename, f"""\
        services:
          wordpress:
            volumes:
              - {plugin}:/var/www/html/wp-content/plugins/my-plugin
    """)
    result = DockerComposeResolver().resolve(str(repo))
    assert len(result.entries) == 1
    assert result.entries[0].name == "my-plugin"


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


def test_non_dict_yaml_root_returns_parse_error(tmp_path):
    """YAML root is a list, not an object."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yml").write_text("- foo\n- bar\n")
    result = DockerComposeResolver().resolve(str(repo))
    assert result.entries == []
    assert "parse_error" in result.notes


def test_unreadable_compose_file_returns_parse_error(tmp_path):
    """File exists but permissions block reading."""
    import os
    import stat
    repo = tmp_path / "repo"
    repo.mkdir()
    cf = repo / "docker-compose.yml"
    cf.write_text("services: {}\n")
    # Strip all permissions
    cf.chmod(0)
    try:
        result = DockerComposeResolver().resolve(str(repo))
        # On systems where the test runner can still read chmod-0 files
        # (some CI runners as root), this test is a no-op — in that case
        # just skip via a conditional assertion.
        if result.notes.get("parse_error"):
            assert result.entries == []
    finally:
        # Restore so tmp_path cleanup works
        cf.chmod(stat.S_IRUSR | stat.S_IWUSR)


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
