"""Tests for install runner and retry table."""

import pytest

from hosts.install.runner import (
    build_install_command, classify_error, should_retry, apply_retry_args,
    validate_extra_args,
)


def test_composer_command_mandates_ignore_scripts():
    cmd = build_install_command("composer", target_cache_dir="/tmp/x")
    assert "composer" in cmd[0]
    assert "--no-scripts" in cmd
    assert "--no-plugins" in cmd


def test_npm_command_mandates_ignore_scripts():
    cmd = build_install_command("npm", target_cache_dir="/tmp/x")
    assert cmd[:2] == ["npm", "ci"]
    assert "--ignore-scripts" in cmd


def test_pnpm_command_mandates_ignore_scripts():
    cmd = build_install_command("pnpm", target_cache_dir="/tmp/x")
    assert cmd[:2] == ["pnpm", "install"]
    assert "--ignore-scripts" in cmd
    assert "--frozen-lockfile" in cmd


def test_yarn_command_mandates_ignore_scripts():
    cmd = build_install_command("yarn", target_cache_dir="/tmp/x")
    assert cmd[:2] == ["yarn", "install"]
    assert "--ignore-scripts" in cmd
    assert "--frozen-lockfile" in cmd


def test_user_cannot_override_ignore_scripts():
    cmd = build_install_command(
        "npm", target_cache_dir="/tmp/x",
        extra_args=["--foreground-scripts"],  # user tries to re-enable
    )
    # --ignore-scripts is present both before AND after user extras
    # (belt-and-suspenders against parser-specific edge cases).
    assert "--ignore-scripts" in cmd
    ignore_positions = [i for i, a in enumerate(cmd) if a == "--ignore-scripts"]
    user_pos = cmd.index("--foreground-scripts")
    assert any(p < user_pos for p in ignore_positions)
    assert any(p > user_pos for p in ignore_positions)


@pytest.mark.parametrize("stderr,expected", [
    ("npm ERR! code EBADENGINE\nnpm ERR! engine Unsupported", "EBADENGINE"),
    ("npm ERR! code ERESOLVE\nERESOLVE could not resolve dependency tree", "ERESOLVE"),
    ("Could not authenticate to https://packagist.example.com", "AUTH_FAILED"),
    ("SSL certificate problem: self signed", "SSL_PROBLEM"),
    ("All good", None),
])
def test_classify_error_recognizes_known_patterns(stderr, expected):
    assert classify_error(stderr) == expected


def test_retry_table_applies_engine_strict_false_for_ebadengine():
    args = apply_retry_args("npm", error_class="EBADENGINE", base_args=[])
    assert "--engine-strict=false" in args


def test_retry_table_applies_legacy_peer_deps_for_eresolve():
    args = apply_retry_args("npm", error_class="ERESOLVE", base_args=[])
    assert "--legacy-peer-deps" in args


def test_should_retry_false_after_one_attempt():
    assert should_retry(attempts=1, error_class="EBADENGINE") is False


def test_should_retry_true_on_first_known_error():
    assert should_retry(attempts=0, error_class="EBADENGINE") is True


def test_should_retry_false_for_unknown_error():
    assert should_retry(attempts=0, error_class=None) is False


def test_build_install_command_places_mandatory_flags_first_and_last():
    cmd = build_install_command("npm", "/tmp/cache", extra_args=["--prefer-offline"])
    assert cmd[0] == "npm"
    assert cmd[1] == "ci"
    # Mandatory flags appear both before and after extra_args
    assert "--ignore-scripts" in cmd[2:5]
    assert "--ignore-scripts" in cmd[-3:]


def test_validate_extra_args_rejects_double_dash_separator():
    with pytest.raises(ValueError, match="'--' separator"):
        validate_extra_args(["--", "--ignore-scripts=false"])


def test_validate_extra_args_rejects_known_dangerous_flags():
    for bad in ("--script-shell", "--run-scripts", "--exec"):
        with pytest.raises(ValueError, match=bad):
            validate_extra_args([bad])


def test_validate_extra_args_accepts_benign_flags():
    # No exception
    validate_extra_args(["--prefer-offline", "--no-audit", "--verbose"])
