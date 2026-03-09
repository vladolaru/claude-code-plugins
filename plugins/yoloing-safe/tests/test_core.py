"""Core hook compatibility and configuration tests."""

from ._legacy_safety_hook_tests import (
    TestAllowlist,
    TestDisableRules,
    TestGitGlobalOptionBypass,
    TestIsAllowlistedDisabledRules,
    TestLoadConfig,
    TestNonDisableableRules,
    TestNormalizeCommand,
    TestNpmOptionBypass,
    TestCredentialPatterns,
    TestStripWriterHeredocs,
)
