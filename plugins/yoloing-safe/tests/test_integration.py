"""Integration and regression tests for end-to-end hook behavior."""

from ._legacy_safety_hook_tests import (
    TestCompoundAllowlist,
    TestHeredocStrippingIntegration,
    TestIntegrationAllow,
    TestIntegrationAllowlistChainBypass,
    TestIntegrationAsk,
    TestIntegrationBlock,
    TestIntegrationFailOpen,
    TestIntegrationNewAskRules,
    TestIntegrationSelfProtection,
    TestMultiTargetAllowlistBypass,
    TestPipeAndBackgroundSplitting,
)
