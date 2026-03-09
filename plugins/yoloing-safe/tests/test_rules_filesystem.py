"""Filesystem, credentials, self-protection, and path rule tests."""

from ._legacy_safety_hook_tests import (
    TestAlternativeDeletion,
    TestCredentialAccess,
    TestCredentialFalsePositives,
    TestDestructiveDeletion,
    TestDiskFormatting,
    TestFindDeleteTraversal,
    TestSensitiveWriteTarget,
    TestSelfProtectionInterpreterWrite,
    TestSelfProtectionSymlinkToctou,
    TestZeroAccessHomeBypasses,
    TestZeroAccessPaths,
)
