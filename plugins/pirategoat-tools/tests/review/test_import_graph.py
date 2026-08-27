"""The review package's import graph, asserted against the real modules.

Three cycles used to be held open by imports placed inside function
bodies, each with a comment explaining why a module-level one would not
work: `agent/output.py` reached `critic_adjustments` to render a ledger,
`reviewer_lifecycle` reached `agent/output.py` to repair a finalized
review, and `agent/output.py` re-loaded `telemetry.py` by file location
under a synthetic module name, bypassing cycle detection entirely.

Comments do not fail. This does — and it fails on the shape, not on any
one of the three, so the next lazy import written for the next cycle is
rejected before its comment is written.
"""

import ast
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
REVIEW_DIR = SCRIPTS_DIR / "review"
sys.path.insert(0, str(SCRIPTS_DIR))

# The one import a module body may still perform. `agent/output.py`'s
# builder logs telemetry, and `telemetry.py` reaches `critic_adjustments`
# -> `findings_ledger` -> `agent/output.py`, so a module-level import
# closes a real cycle. Local keeps the reviewer heredoc's import of
# ReviewOutputBuilder light as well.
ALLOWED_LOCAL_IMPORTS = frozenset({("agent/output.py", "telemetry")})

LEAF_MODULES = ("review_document.py", "verdict_rules.py")
LEAF_ALLOWED = frozenset({"review_document", "verdict_rules"})


def _review_modules():
    return sorted(REVIEW_DIR.rglob("*.py"))


def _targets(path, node):
    """The `review`-relative modules one import node reaches.

    Every module here carries the same two spellings — a relative import
    under `try:` and a `review.`-absolute one in the `except ImportError`
    arm — so both resolve to the same answer.
    """
    package = list(path.relative_to(REVIEW_DIR).parts[:-1])
    if isinstance(node, ast.ImportFrom):
        if node.level:
            anchor = package[: len(package) - (node.level - 1)]
        elif node.module == "review" or (node.module or "").startswith(
            "review."
        ):
            anchor = []
        else:
            return set()
        base = (node.module or "")
        if not node.level:
            base = base[len("review"):].lstrip(".")
        parts = anchor + (base.split(".") if base else [])
        if base:
            return {".".join(parts)}
        return {".".join(parts + [alias.name]) for alias in node.names}
    if isinstance(node, ast.Import):
        found = set()
        for alias in node.names:
            if alias.name == "review" or alias.name.startswith("review."):
                stripped = alias.name[len("review"):].lstrip(".")
                if stripped:
                    found.add(stripped)
        return found
    return set()


def _all_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for target in _targets(path, node):
                yield target


def _local_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, (ast.Import, ast.ImportFrom)):
                for target in _targets(path, inner):
                    yield target


class TestLeafModules:
    def test_the_leaves_depend_only_on_each_other(self):
        offenders = {}
        for name in LEAF_MODULES:
            path = REVIEW_DIR / name
            reached = set(_all_imports(path)) - LEAF_ALLOWED
            if reached:
                offenders[name] = sorted(reached)

        assert offenders == {}


class TestNoCycles:
    def test_output_and_critic_adjustments_do_not_import_each_other(self):
        """The pair the lazy render import held apart: critic_adjustments
        owns the post-critic ledger schema, output.py owns the builder, and
        each used to want a name from the other."""
        output_reaches = set(_all_imports(REVIEW_DIR / "agent" / "output.py"))
        critic_reaches = set(
            _all_imports(REVIEW_DIR / "critic_adjustments.py")
        )

        assert "critic_adjustments" not in output_reaches
        assert not [
            target for target in critic_reaches
            if target == "agent.output" or target.startswith("agent.output.")
        ]

    def test_reviewer_lifecycle_does_not_reach_the_builder(self):
        reached = set(_all_imports(REVIEW_DIR / "reviewer_lifecycle.py"))

        assert not [
            target for target in reached if target.startswith("agent.output")
        ]


class TestNoFunctionBodyImports:
    """One blind spot, stated rather than papered over: this walks import
    statements, so a module loaded through
    `importlib.util.spec_from_file_location` is invisible here. Those
    loaders are tracked separately — by the tests of the modules that own
    them — and widening this scan to guess at them would make it assert
    on string arguments rather than on the import graph."""

    def test_only_the_documented_telemetry_import_is_local(self):
        offenders = set()
        for path in _review_modules():
            relative = str(path.relative_to(REVIEW_DIR))
            for target in _local_imports(path):
                if (relative, target) not in ALLOWED_LOCAL_IMPORTS:
                    offenders.add((relative, target))

        assert offenders == set()
