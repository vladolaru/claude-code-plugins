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
from helpers.graders import SEVERITY_RANK, VALID_VERDICTS

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
            tol = spec.get("line_tolerance", 2)
            assert line + tol <= new_files[spec["file"]], (
                f"{name}/{agent}/{spec_id}: line {line} + tolerance {tol} exceeds "
                f"{new_files[spec['file']]}-line span of {spec['file']}"
            )
        assert spec.get("match_any"), f"{name}/{agent}/{spec_id}: empty match_any"
        for pattern in spec["match_any"]:
            re.compile(pattern)
