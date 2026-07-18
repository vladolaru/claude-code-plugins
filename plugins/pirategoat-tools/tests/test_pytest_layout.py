"""Repo-wide pytest layout guards — keep multi-plugin test runs collectable.

Every plugin ships a top-level `tests/` directory. If two of them are
importable as the same package name, running their suites in ONE pytest
session fails at collection:

- `plugins/*/tests/__init__.py` makes each suite the top-level package
  `tests` (the plugin directories above are not packages — most have
  hyphenated, non-importable names), so the second suite's modules raise
  ``ModuleNotFoundError: No module named 'tests.test_...'`` and conftests
  collide as `tests.conftest` ("Plugin already registered under a
  different name") even under importlib mode, because the dotted name IS
  importable and the first import wins in sys.modules.

Subdirectory `__init__.py` files are just as harmful: test trees mirror
the scripts layout (`tests/hosts/` alongside `scripts/hosts/`), so a
`tests/hosts/__init__.py` makes the TEST tree importable as `hosts`,
shadowing the production package the tests themselves import
(``ModuleNotFoundError: No module named 'hosts.cache.manager'``).
Without `__init__.py`, a bare test directory is at most a namespace
portion, and Python always prefers the scripts' regular package.

The fix this file pins: NO `__init__.py` anywhere under `plugins/*/tests`,
plus `--import-mode=importlib` in the root pytest.ini so pytest derives
unique module names from paths instead of requiring unique basenames
across (or within) plugins. Intra-suite imports (`helpers.graders`,
`from conftest import ...`) keep working as namespace-package/plain-module
imports via the sys.path inserts the test files already do.

These guards live in pirategoat-tools (the largest suite, run most often)
because the repo has no root-level test home; the invariant is repo-wide.
"""

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent  # tests/ -> pirategoat-tools/
REPO_ROOT = PLUGIN_ROOT.parent.parent  # plugins/ -> repo root
PLUGINS_DIR = REPO_ROOT / "plugins"


class TestMultiPluginCollection:
    def test_no_init_py_anywhere_under_plugin_test_trees(self):
        """No `__init__.py` under any `plugins/*/tests/` — suite roots
        collide across plugins as the package name `tests`; subdirectories
        shadow same-named production packages (`tests/hosts/` vs
        `scripts/hosts/`)."""
        offenders = sorted(
            str(p.relative_to(REPO_ROOT))
            for p in PLUGINS_DIR.glob("*/tests/**/__init__.py")
        )
        assert offenders == [], (
            "__init__.py files under plugin test trees break multi-suite "
            f"collection or shadow production packages: {offenders}. Delete "
            "them — importlib import mode (root pytest.ini) plus namespace "
            "packages cover every import these trees do."
        )

    def test_root_pytest_ini_pins_importlib_import_mode(self):
        """The root pytest.ini must keep --import-mode=importlib so test
        modules with duplicate basenames across plugins get unique,
        path-derived module names."""
        pytest_ini = REPO_ROOT / "pytest.ini"
        assert pytest_ini.is_file(), (
            "Root pytest.ini is missing — without it, pytest falls back to "
            "prepend import mode and multi-plugin runs collide on duplicate "
            "test module basenames."
        )
        content = pytest_ini.read_text()
        assert "--import-mode=importlib" in content
