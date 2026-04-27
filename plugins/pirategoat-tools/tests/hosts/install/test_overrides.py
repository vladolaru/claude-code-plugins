"""Tests for overrides parsing."""

import json
import pytest
from pathlib import Path

from hosts.install.overrides import parse_overrides, InstallOverrides


def test_no_overrides_returns_defaults():
    o = parse_overrides(inline_json=None, file_path=None)
    assert o.skip_install is False
    assert o.php_args == []
    assert o.js_args == []
    assert o.js_manager_override is None
    assert o.env == {}


def test_inline_json_parsed():
    o = parse_overrides(
        inline_json='{"js": {"manager": "pnpm", "args": ["--frozen-lockfile"]}}',
        file_path=None,
    )
    assert o.js_manager_override == "pnpm"
    assert o.js_args == ["--frozen-lockfile"]


def test_file_path_parsed(tmp_path):
    f = tmp_path / "o.json"
    f.write_text(json.dumps({
        "skip_install": True,
        "php": {"args": ["--no-dev"]},
    }))
    o = parse_overrides(inline_json=None, file_path=str(f))
    assert o.skip_install is True
    assert o.php_args == ["--no-dev"]


def test_inline_and_file_both_provided_is_error():
    with pytest.raises(ValueError, match="exactly one"):
        parse_overrides(inline_json="{}", file_path="/x")


def test_malformed_json_raises():
    with pytest.raises(ValueError, match="JSON"):
        parse_overrides(inline_json="{not json", file_path=None)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("[]", "root"),
        ('{"php": ["--no-dev"]}', "php"),
        ('{"js": "bad"}', "js"),
    ],
)
def test_parse_overrides_rejects_non_object_sections(payload, message):
    with pytest.raises(ValueError, match=message):
        parse_overrides(payload, None)


def test_env_parsed_when_present():
    o = parse_overrides(
        inline_json='{"env": {"NPM_CONFIG_REGISTRY": "https://registry.example.test"}}',
        file_path=None,
    )
    assert o.env == {"NPM_CONFIG_REGISTRY": "https://registry.example.test"}


def test_parse_overrides_rejects_pre_install():
    with pytest.raises(ValueError, match="pre_install"):
        parse_overrides('{"pre_install": ["echo hi"]}', None)


def test_parse_overrides_rejects_post_install():
    with pytest.raises(ValueError, match="post_install"):
        parse_overrides('{"post_install": ["echo hi"]}', None)


def test_parse_overrides_accepts_env_with_allowed_keys():
    result = parse_overrides(
        '{"env": {"COMPOSER_AUTH": "...", "NPM_CONFIG_REGISTRY": "https://x"}}',
        None,
    )
    assert result.env == {
        "COMPOSER_AUTH": "...",
        "NPM_CONFIG_REGISTRY": "https://x",
    }


def test_parse_overrides_rejects_env_with_disallowed_keys():
    with pytest.raises(ValueError, match="Disallowed env keys"):
        parse_overrides('{"env": {"LD_PRELOAD": "/tmp/x"}}', None)
    with pytest.raises(ValueError, match="Disallowed env keys"):
        parse_overrides('{"env": {"PATH": "/evil/bin"}}', None)


def test_parse_overrides_rejects_node_options():
    with pytest.raises(ValueError, match="Disallowed env keys"):
        parse_overrides('{"env": {"NODE_OPTIONS": "--require=/tmp/evil.js"}}', None)


def test_parse_overrides_raises_valueerror_on_missing_file(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(ValueError, match="Cannot read overrides file"):
        parse_overrides(None, str(missing))


def test_parse_overrides_raises_valueerror_on_malformed_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    with pytest.raises(ValueError, match="Cannot read overrides file"):
        parse_overrides(None, str(bad))


def test_parse_overrides_rejects_unknown_js_manager():
    payload = '{"js": {"manager": "evil-cmd"}}'
    with pytest.raises(ValueError, match="Unknown JS manager"):
        parse_overrides(payload, None)


def test_parse_overrides_rejects_non_string_js_manager():
    with pytest.raises(ValueError, match="js.manager must be a string"):
        parse_overrides('{"js": {"manager": []}}', None)


@pytest.mark.parametrize("ecosystem", ["php", "js"])
def test_parse_overrides_rejects_disallowed_install_args(ecosystem):
    payload = json.dumps({ecosystem: {"args": ["--"]}})
    with pytest.raises(ValueError, match="Disallowed install argument"):
        parse_overrides(payload, None)


@pytest.mark.parametrize(
    "payload",
    [
        '{"php": {"args": "--no-dev"}}',
        '{"js": {"args": "--frozen-lockfile"}}',
        '{"php": {"args": [123]}}',
        '{"js": {"args": ["--prefer-offline", false]}}',
    ],
)
def test_parse_overrides_rejects_args_that_are_not_string_arrays(payload):
    with pytest.raises(ValueError, match="args.*array of strings"):
        parse_overrides(payload, None)


def test_parse_overrides_accepts_known_js_managers():
    for name in ("npm", "pnpm", "yarn"):
        result = parse_overrides('{"js": {"manager": "' + name + '"}}', None)
        assert result.js_manager_override == name
