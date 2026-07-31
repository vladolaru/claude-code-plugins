#!/usr/bin/env python3
"""
Agent compliance eval runner.

Two modes:
  --grade-only <output_dir>   Grade existing review output files
  --dispatch --agent <name>   Full eval: temp repo -> bootstrap -> dispatch agent -> grade
  --dispatch --all            Run full eval for all 11 agents

Scenarios define: agent name, setup (git state), expected outcome.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Path setup
TESTS_DIR = Path(__file__).resolve().parent.parent  # grading/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
BOOTSTRAP_SCRIPT = SCRIPTS_DIR / "review" / "agent" / "bootstrap.py"
OUTPUT_MODULE = SCRIPTS_DIR / "review" / "agent" / "output.py"
FIXTURES_DIR = TESTS_DIR / "fixtures"

sys.path.insert(0, str(TESTS_DIR))
from helpers.graders import (
    GradeResult,
    grade_output_pair,
    grade_review_json,
    grade_review_markdown,
    grade_no_domain_files,
    grade_error_exit,
    grade_signal_format,
)

# Import agent config
import importlib.util

_spec = importlib.util.spec_from_file_location("bootstrap_reviewer", str(BOOTSTRAP_SCRIPT))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
AGENT_CONFIG = _mod.AGENT_CONFIG
ALL_AGENTS = sorted(AGENT_CONFIG.keys())


# =============================================================================
# Scenario Definitions
# =============================================================================


def setup_temp_git_repo(diff_file: str = None) -> str:
    """Create a temp git repo, optionally applying a diff."""
    tmp = tempfile.mkdtemp(prefix="eval-reviewer-")
    subprocess.run(["git", "init"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp, capture_output=True)

    # Create initial commit
    readme = os.path.join(tmp, "README.md")
    with open(readme, "w") as f:
        f.write("# Test Project\n")
    subprocess.run(["git", "add", "."], cwd=tmp, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp, capture_output=True)

    if diff_file and os.path.isfile(diff_file):
        result = subprocess.run(
            ["git", "apply", str(diff_file)],
            cwd=tmp, capture_output=True, text=True,
        )
        if result.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(
                f"git apply failed for {diff_file}: {result.stderr.strip()}"
            )
        subprocess.run(["git", "add", "."], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-m", "changes"], cwd=tmp, capture_output=True)

    return tmp


SCENARIOS = {
    "no_domain_files_approve": {
        "description": "Docs-only changes should yield APPROVE with zero findings",
        "agents": ALL_AGENTS,
        "diff": str(FIXTURES_DIR / "no-code-changes.diff"),
        "grader": "no_domain_files",
    },
    "standard_review": {
        "description": "Security vulnerability should produce findings",
        "agents": ["security-reviewer"],
        "diff": str(PLUGIN_ROOT / "test-samples" / "json-output-test" / "test-pr-security.diff"),
        "grader": "output_pair",
    },
    "error_no_git_repo": {
        "description": "Running outside a git repo should produce error",
        "agents": ["security-reviewer"],
        "diff": None,  # No git repo
        "grader": "error_exit",
        "no_git": True,
    },
    "php_source_review": {
        "description": "PHP source with SQL injection and tight coupling",
        "agents": ["security-reviewer", "architecture-reviewer", "performance-reviewer"],
        "diff": str(FIXTURES_DIR / "php-source.diff"),
        "grader": "output_pair",
    },
    "js_source_review": {
        "description": "JS/TS source with XSS and hardcoded API key",
        "agents": ["security-reviewer"],
        "diff": str(FIXTURES_DIR / "js-ts-source.diff"),
        "grader": "output_pair",
    },
    "php_tests_review": {
        "description": "PHP test files with missing assertions and over-mocking",
        "agents": ["php-tests-reviewer"],
        "diff": str(FIXTURES_DIR / "php-test-only.diff"),
        "grader": "output_pair",
    },
    "e2e_tests_review": {
        "description": "E2E test files with hard-coded waits",
        "agents": ["e2e-tests-reviewer"],
        "diff": str(FIXTURES_DIR / "e2e-test-only.diff"),
        "grader": "output_pair",
    },
    "wp_specific_review": {
        "description": "WP plugin with hooks, i18n, escaping, and wpdb",
        "agents": ["wp-architecture-reviewer", "security-reviewer"],
        "diff": str(FIXTURES_DIR / "wp-hooks-and-i18n.diff"),
        "grader": "output_pair",
    },
    "realistic_multi_file": {
        "description": "Realistic multi-file PR touching all domains",
        "agents": ALL_AGENTS,
        "diff": str(FIXTURES_DIR / "multi-file-realistic.diff"),
        "grader": "output_pair",
    },
}


# =============================================================================
# Grade-Only Mode
# =============================================================================


def _materialize_missing_markdown(output_dir: str) -> None:
    """Render md for any *-review.json lacking one — save() publishes the
    JSON only; Markdown is a derived artifact (see review/agent/output.py)."""
    spec = importlib.util.spec_from_file_location(
        "_pirategoat_review_output", str(OUTPUT_MODULE),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.materialize_markdown(output_dir)


def run_grade_only(output_dir: str) -> dict:
    """Scan output dir for review files and grade them."""
    results = {}

    if not os.path.isdir(output_dir):
        print(f"ERROR: Directory does not exist: {output_dir}")
        return results

    _materialize_missing_markdown(output_dir)

    # Find all *-review.json files
    for filename in sorted(os.listdir(output_dir)):
        if filename.endswith("-review.json"):
            reviewer_name = filename.replace("-review.json", "")
            result = grade_output_pair(output_dir, reviewer_name)
            results[reviewer_name] = result

    return results


# =============================================================================
# Dispatch Mode
# =============================================================================


def run_bootstrap_for_agent(agent_name: str, cwd: str, output_dir: str) -> tuple:
    """Run review/agent/bootstrap.py and return (exit_code, stdout)."""
    cmd = [
        sys.executable,
        str(BOOTSTRAP_SCRIPT),
        "--agent", agent_name,
        "--output-dir", output_dir,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=cwd)
    return result.returncode, result.stdout


def dispatch_agent(agent_name: str, bootstrap_output: str, agent_def: str, cwd: str) -> tuple:
    """Dispatch an agent via claude -p subprocess.

    Returns (exit_code, stdout).
    """
    prompt = (
        f"You are the {agent_name} agent. Here is your bootstrap output:\n\n"
        f"{bootstrap_output}\n\n"
        f"Now perform your review. Write output files as specified in the bootstrap output."
    )

    # Check if claude CLI is available
    claude_path = shutil.which("claude")
    if not claude_path:
        return 1, "ERROR: claude CLI not found in PATH"

    cmd = [claude_path, "-p", prompt]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=cwd,
        )
        return result.returncode, result.stdout
    except subprocess.TimeoutExpired:
        return 1, "ERROR: Agent dispatch timed out after 300s"
    except FileNotFoundError:
        return 1, "ERROR: claude CLI not found"


def run_dispatch_scenario(scenario_name: str, scenario: dict, agent_name: str) -> GradeResult:
    """Run a single scenario for a single agent."""
    output_dir = tempfile.mkdtemp(prefix=f"eval-output-{agent_name}-")

    # Setup
    if scenario.get("no_git"):
        cwd = tempfile.mkdtemp(prefix="eval-nogit-")
    else:
        cwd = setup_temp_git_repo(scenario.get("diff"))

    try:
        # Bootstrap
        rc, bootstrap_out = run_bootstrap_for_agent(agent_name, cwd, output_dir)

        if scenario["grader"] == "error_exit":
            return grade_error_exit(bootstrap_out)

        if rc != 0:
            return GradeResult(
                passed=False, score=0.0,
                failures=[f"Bootstrap failed (exit {rc}): {bootstrap_out[:200]}"],
                checks_run=1, checks_passed=0,
            )

        if scenario["grader"] == "no_domain_files":
            # Check if bootstrap output says NO_DOMAIN_FILES
            if "NO_DOMAIN_FILES" in bootstrap_out:
                return grade_no_domain_files(bootstrap_out)
            # If scope was OK (no domain filtering at bootstrap level), we need dispatch
            # For dispatch mode, we'd send to agent and grade output
            # In non-dispatch bootstrap-only mode, just check the bootstrap succeeded
            return GradeResult(
                passed=True, score=1.0, failures=[],
                checks_run=1, checks_passed=1,
            )

        # Read agent definition
        agent_def_path = PLUGIN_ROOT / "agents" / f"{agent_name}.md"
        agent_def = ""
        if agent_def_path.is_file():
            agent_def = agent_def_path.read_text()

        # Dispatch agent
        rc, agent_output = dispatch_agent(agent_name, bootstrap_out, agent_def, cwd)

        # Grade
        if scenario["grader"] == "output_pair":
            return grade_output_pair(output_dir, _mod.derive_reviewer_name(agent_name))
        elif scenario["grader"] == "signal_format":
            return grade_signal_format(agent_output)
        else:
            return GradeResult(
                passed=False, score=0.0,
                failures=[f"Unknown grader: {scenario['grader']}"],
                checks_run=1, checks_passed=0,
            )
    finally:
        # Cleanup
        if os.path.isdir(cwd) and cwd.startswith(tempfile.gettempdir()):
            shutil.rmtree(cwd, ignore_errors=True)


# =============================================================================
# Output Formatting
# =============================================================================


def print_results(all_results: dict):
    """Print formatted eval results."""
    print("\n=== EVAL RESULTS ===")
    total_passed = 0
    total_checks = 0

    for scenario_name, agent_results in all_results.items():
        print(f"\nScenario: {scenario_name}")
        for agent_name, result in agent_results.items():
            status = "PASS" if result.passed else "FAIL"
            print(f"  {agent_name:30s} {status} ({result.checks_passed}/{result.checks_run} checks)")
            if not result.passed:
                for failure in result.failures[:3]:
                    print(f"    - {failure}")
            total_passed += result.checks_passed
            total_checks += result.checks_run

    pct = (total_passed / total_checks * 100) if total_checks > 0 else 0
    print(f"\nTOTAL: {total_passed}/{total_checks} passed ({pct:.0f}%)")


# =============================================================================
# CLI
# =============================================================================


def main():
    parser = argparse.ArgumentParser(description="Agent compliance eval runner.")
    parser.add_argument(
        "--grade-only",
        metavar="OUTPUT_DIR",
        help="Grade existing output files in the given directory",
    )
    parser.add_argument(
        "--dispatch",
        action="store_true",
        help="Full eval: setup temp repo, bootstrap, dispatch agent, grade",
    )
    parser.add_argument(
        "--agent",
        help="Agent name for --dispatch mode",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run dispatch for all agents",
    )
    parser.add_argument(
        "--scenario",
        help="Run only a specific scenario (default: all)",
    )

    args = parser.parse_args()

    if args.grade_only:
        results = run_grade_only(args.grade_only)
        all_results = {"grade_existing": results}
        print_results(all_results)
        return

    if args.dispatch:
        agents = ALL_AGENTS if args.all else ([args.agent] if args.agent else ALL_AGENTS)
        scenarios = SCENARIOS
        if args.scenario:
            if args.scenario not in SCENARIOS:
                print(f"ERROR: Unknown scenario '{args.scenario}'. Available: {list(SCENARIOS.keys())}")
                sys.exit(1)
            scenarios = {args.scenario: SCENARIOS[args.scenario]}

        all_results = {}
        for scenario_name, scenario in scenarios.items():
            agent_results = {}
            scenario_agents = [a for a in agents if a in scenario["agents"]]
            for agent_name in scenario_agents:
                print(f"Running: {scenario_name} / {agent_name}...", flush=True)
                result = run_dispatch_scenario(scenario_name, scenario, agent_name)
                agent_results[agent_name] = result
            if agent_results:
                all_results[scenario_name] = agent_results

        print_results(all_results)
        # Exit with failure if any eval failed
        any_failed = any(
            not r.passed
            for scenario_results in all_results.values()
            for r in scenario_results.values()
        )
        sys.exit(1 if any_failed else 0)

    parser.print_help()


if __name__ == "__main__":
    main()
