"""
Deterministic integrity guard for the detection answer keys in
eval_agent_compliance.SCENARIOS.

Keys are trusted at eval runtime; this guard is where their validity is
enforced: every referenced file must exist in the fixture diff, every line
must be inside the file's new-line span, every regex must compile, and every
fixture must actually apply. Runs in pytest with zero model calls.
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # grading/ -> tests/
EVAL_SCRIPT = TESTS_DIR / "grading" / "eval_agent_compliance.py"

sys.path.insert(0, str(TESTS_DIR))
from helpers.graders import DEFAULT_LINE_TOLERANCE, SEVERITY_RANK, VALID_VERDICTS

_spec = importlib.util.spec_from_file_location("_eval_for_keys", str(EVAL_SCRIPT))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
SCENARIOS = _mod.SCENARIOS
ALL_AGENTS = _mod.ALL_AGENTS

KEYED_SCENARIOS = [
    (name, scenario) for name, scenario in SCENARIOS.items() if scenario.get("expected")
]
KEYED_ENTRIES = [
    (name, agent, key)
    for name, scenario in KEYED_SCENARIOS
    for agent, key in scenario["expected"].items()
]

# Keys that expect_not_applicable silently disables in grade_detection's
# short-circuit — their presence alongside it is always an authoring error.
_NA_INCOMPATIBLE = (
    "required_findings", "acceptable_findings", "max_severity", "max_unexpected",
)


def _has_gate(key: dict) -> bool:
    """True when the key asserts at least one graded condition.

    max_unexpected/max_severity gate on presence (0 and "info" are valid
    gates), so truthiness is not enough.
    """
    return bool(
        key.get("verdict_in")
        or key.get("required_findings")
        or key.get("expect_not_applicable")
        or key.get("max_severity") is not None
        or key.get("max_unexpected") is not None
    )


def _diff_new_files(diff_text: str) -> dict:
    """Map each new-file path in a unified diff to its maximum new line number."""
    files: dict = {}
    current = None
    for line in diff_text.splitlines():
        header = re.match(r"^\+\+\+ b/(.+)$", line)
        if header:
            current = header.group(1)
            files.setdefault(current, 0)
            continue
        hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if hunk and current:
            start = int(hunk.group(1))
            count = int(hunk.group(2) or 1)
            files[current] = max(files[current], start + count - 1)
    return files


@pytest.mark.parametrize("name,scenario", KEYED_SCENARIOS, ids=[n for n, _ in KEYED_SCENARIOS])
def test_keyed_scenario_fixture_applies(name, scenario, tmp_path):
    diff = scenario.get("diff")
    assert diff, f"{name}: keyed scenario has no fixture diff"
    assert Path(diff).is_file(), f"{name}: fixture missing: {diff}"
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    check = subprocess.run(
        ["git", "apply", "--check", str(diff)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert check.returncode == 0, f"{name}: fixture does not apply: {check.stderr}"


@pytest.mark.parametrize(
    "name,agent,key", KEYED_ENTRIES, ids=[f"{n}-{a}" for n, a, _ in KEYED_ENTRIES]
)
def test_answer_key_is_well_formed(name, agent, key):
    scenario = SCENARIOS[name]
    assert agent in scenario["agents"], f"{name}: key for undispatched agent {agent}"
    assert agent in ALL_AGENTS, f"{name}: unknown agent {agent}"
    assert _has_gate(key), f"{name}/{agent}: key has no gate — it would pass vacuously"
    if "verdict_in" in key:
        assert key["verdict_in"], f"{name}/{agent}: empty verdict_in silently skips the gate"
    for verdict in key.get("verdict_in", []):
        assert verdict in VALID_VERDICTS, f"{name}/{agent}: invalid verdict {verdict}"
    if key.get("max_severity") is not None:
        assert key["max_severity"] in SEVERITY_RANK, (
            f"{name}/{agent}: invalid max_severity {key['max_severity']!r}"
        )
    max_unexpected = key.get("max_unexpected")
    if max_unexpected is not None:
        # grade_detection compares len(unexpected) <= max_unexpected — a
        # string raises TypeError after paid dispatches, a negative makes
        # the gate unsatisfiable, and bool is an int subclass in disguise.
        assert (
            isinstance(max_unexpected, int)
            and not isinstance(max_unexpected, bool)
            and max_unexpected >= 0
        ), (
            f"{name}/{agent}: max_unexpected must be a non-negative int, "
            f"got {max_unexpected!r}"
        )
    if key.get("expect_not_applicable"):
        for field in _NA_INCOMPATIBLE:
            assert key.get(field) is None, (
                f"{name}/{agent}: expect_not_applicable silently disables {field}"
            )


@pytest.mark.parametrize(
    "name,agent,key", KEYED_ENTRIES, ids=[f"{n}-{a}" for n, a, _ in KEYED_ENTRIES]
)
def test_finding_specs_resolve_against_fixture(name, agent, key):
    specs = list(key.get("required_findings", [])) + list(key.get("acceptable_findings", []))
    if not specs:
        return
    diff_text = Path(SCENARIOS[name]["diff"]).read_text()
    new_files = _diff_new_files(diff_text)
    seen_ids = set()
    for spec in specs:
        spec_id = spec["id"]
        assert spec_id not in seen_ids, f"{name}/{agent}: duplicate spec id {spec_id}"
        seen_ids.add(spec_id)
        assert spec["file"] in new_files, (
            f"{name}/{agent}/{spec_id}: file {spec['file']} not in fixture diff"
        )
        line = spec.get("line")
        if line is not None:
            assert 1 <= line <= new_files[spec["file"]], (
                f"{name}/{agent}/{spec_id}: line {line} outside "
                f"1..{new_files[spec['file']]} of {spec['file']}"
            )
            tol = spec.get("line_tolerance", DEFAULT_LINE_TOLERANCE)
            assert line + tol <= new_files[spec["file"]], (
                f"{name}/{agent}/{spec_id}: line {line} + tolerance {tol} exceeds "
                f"{new_files[spec['file']]}-line span of {spec['file']}"
            )
        assert spec.get("match_any"), f"{name}/{agent}/{spec_id}: empty match_any"
        for pattern in spec["match_any"]:
            re.compile(pattern)


# Fixture-integrity guards below cover EVERY fixture diff, keyed or not.
# git apply silently drops +-lines beyond a hunk header's declared count, so
# an off-by-one header truncates the applied file without any error — the
# resulting syntax break is an extra, unkeyed defect that contaminates
# reviewer findings (found live in ten hand-authored fixtures, 2026-08-06).

ALL_FIXTURE_DIFFS = sorted(_mod.FIXTURES_DIR.glob("*.diff")) + [
    _mod.PLUGIN_ROOT / "test-samples" / "json-output-test" / "test-pr-security.diff",
]

# Balance-checked extensions: code formats whose fixture content never carries
# unbalanced delimiters legitimately. Prose formats (.md, .txt) are excluded —
# list markers and links make delimiter counting meaningless there.
_BALANCED_EXTS = {".php", ".ts", ".tsx", ".js", ".jsx", ".go", ".tf", ".py"}
_PAIRS = {"}": "{", ")": "(", "]": "["}


def _diff_new_file_contents(diff_text: str) -> dict:
    """Map each new-file path in a unified diff to its full content lines."""
    files, current = {}, None
    for line in diff_text.splitlines():
        header = re.match(r"^\+\+\+ b/(.+)$", line)
        if header:
            current = header.group(1)
            files[current] = []
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            files[current].append(line[1:])
    return files


@pytest.mark.parametrize("diff_path", ALL_FIXTURE_DIFFS, ids=[p.name for p in ALL_FIXTURE_DIFFS])
def test_fixture_hunk_counts_are_exact(diff_path):
    """Every hunk's declared new-line count must equal its actual +-lines."""
    current, declared, actual = None, 0, 0

    def check():
        assert declared == actual, (
            f"{diff_path.name}: {current} hunk declares {declared} new lines "
            f"but carries {actual} — git apply silently drops the excess"
        )

    for line in diff_path.read_text().splitlines():
        header = re.match(r"^\+\+\+ b/(.+)$", line)
        hunk = re.match(r"^@@ -\d+(?:,\d+)? \+\d+(?:,(\d+))? @@", line)
        if header:
            check()
            current, declared, actual = header.group(1), 0, 0
        elif hunk:
            check()
            declared, actual = int(hunk.group(1) or 1), 0
        elif line.startswith("+") and not line.startswith("+++"):
            actual += 1
    check()


@pytest.mark.parametrize("diff_path", ALL_FIXTURE_DIFFS, ids=[p.name for p in ALL_FIXTURE_DIFFS])
def test_fixture_sources_are_balanced(diff_path):
    """New source files must close every delimiter they open.

    A truncation tripwire, not a parser: fixture sources are simple enough
    that unbalanced braces/parens/brackets always mean dropped lines.
    """
    for path, lines in _diff_new_file_contents(diff_path.read_text()).items():
        if Path(path).suffix not in _BALANCED_EXTS:
            continue
        stack = []
        for n, line in enumerate(lines, start=1):
            for ch in line:
                if ch in "{([":
                    stack.append((ch, n))
                elif ch in _PAIRS:
                    assert stack and stack[-1][0] == _PAIRS[ch], (
                        f"{diff_path.name}: {path}:{n} closes {ch!r} without opener"
                    )
                    stack.pop()
        assert not stack, (
            f"{diff_path.name}: {path} leaves {stack[-1][0]!r} from line "
            f"{stack[-1][1]} unclosed — likely truncated content"
        )
