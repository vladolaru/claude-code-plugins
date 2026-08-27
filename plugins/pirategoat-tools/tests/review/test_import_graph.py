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


def _module_level_imports(path):
    """The `review`-relative modules one file imports at module level.

    Function-body imports are excluded on purpose: they are what the
    cycle check is measuring the absence of a need for, and
    `TestNoFunctionBodyImports` below is the gate on them.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found |= _targets(path, node)
            continue
        for field in ("body", "orelse", "finalbody", "handlers"):
            stack.extend(getattr(node, field, []) or [])
    return found


def _module_name(path):
    return ".".join(path.relative_to(REVIEW_DIR).with_suffix("").parts)


def _import_graph():
    """Every module in `scripts/review/` mapped to what it imports.

    A target is a dotted path that may name a module (`agent.output`) or
    a name inside one (`agent.output.ReviewOutputBuilder`); both resolve
    to the module, and a package name resolves to its `__init__`.
    """
    modules = {_module_name(path): path for path in _review_modules()}

    def resolve(target):
        for candidate in (target, f"{target}.__init__"):
            if candidate in modules:
                return candidate
        parent = target.rsplit(".", 1)[0]
        for candidate in (parent, f"{parent}.__init__"):
            if candidate in modules:
                return candidate
        return None

    graph = {}
    for name, path in modules.items():
        edges = set()
        for target in _module_level_imports(path):
            resolved = resolve(target)
            if resolved is not None and resolved != name:
                edges.add(resolved)
        graph[name] = edges
    return graph


def _find_cycle(graph):
    """One cycle as a module list, or None. Depth-first, colour-marked."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(graph, WHITE)

    def visit(node, trail):
        colour[node] = GREY
        for target in sorted(graph[node]):
            if colour[target] == GREY:
                return trail[trail.index(target):] + [target]
            if colour[target] == WHITE:
                found = visit(target, trail + [target])
                if found:
                    return found
        colour[node] = BLACK
        return None

    for node in sorted(graph):
        if colour[node] == WHITE:
            found = visit(node, [node])
            if found:
                return found
    return None


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
    def test_the_module_level_import_graph_is_acyclic(self):
        """Every module under `scripts/review/`, not a named few.

        Three cycles were once held open by function-body imports, and
        each pair that could close one was pinned by name — which pins
        only the pairs someone thought of. A cycle through a fourth
        module went unasserted. This walks the whole graph instead, so
        the next one is rejected without a new test being written for it.

        The one import that would close a cycle today lives in a
        function body for exactly that reason (`agent/output.py` ->
        `telemetry`), which is why it is invisible here and gated by
        `TestNoFunctionBodyImports` instead.
        """
        assert _find_cycle(_import_graph()) is None


class TestLayering:
    """Two directions the cycle check cannot see, because taking either
    one would not (yet) close a loop — but both would put a module on
    the wrong side of a boundary that exists for a reason."""

    def test_the_post_critic_ledger_does_not_reach_the_builder(self):
        """`critic_adjustments` owns the post-critic ledger schema,
        `agent/output.py` owns the builder, and each used to want a name
        from the other. `findings_ledger` already bridges them in one
        direction, so the return edge would be acyclic and silent."""
        reached = set(_all_imports(REVIEW_DIR / "critic_adjustments.py"))

        assert not [
            target for target in reached
            if target == "agent.output" or target.startswith("agent.output.")
        ]

    def test_review_markdown_does_not_reach_the_builder(self):
        """Rendering reads documents; it does not build them.

        The renderer imported one text coercer from `agent/output.py` and
        got the whole builder — plus its lifecycle, assignment, and
        atomic-write dependencies — as the price. The coercer is a
        question about the document's shape, so it lives in
        `review_document.py` with the rest of them.
        """
        reached = set(_all_imports(REVIEW_DIR / "review_markdown.py"))

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
