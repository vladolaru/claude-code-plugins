"""System, database, container, interpreter, and environment rule tests."""

from ._legacy_safety_hook_tests import (
    TestBrewCommands,
    TestCaseInsensitiveDetection,
    TestDatabaseDestructive,
    TestDockerDestructive,
    TestGitHubCICDOps,
    TestInlineHeredoc,
    TestInlineInterpreter,
    TestPermissionChanges,
    TestSuDashC,
    TestTerraformDestructive,
)
