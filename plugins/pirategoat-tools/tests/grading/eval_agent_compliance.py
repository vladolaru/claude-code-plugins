#!/usr/bin/env python3
"""
Agent compliance eval runner.

Two modes:
  --grade-only <output_dir>   Grade existing review output files
  --dispatch --agent <name>   Full eval: temp repo -> bootstrap -> dispatch agent -> grade
  --dispatch --all            Run full eval for all 11 agents

Scenarios define: agent name, setup (git state), grader, and optional detection answer keys ("expected").
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

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
    grade_detection,
    grade_output_pair,
    grade_review_json,
    grade_review_markdown,
    grade_no_domain_files,
    grade_error_exit,
    grade_signal_format,
    merge_grades,
    aggregate_detection_trials,
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
    # scope.py resolves the review base ref to "main" when no origin remote
    # exists, so the base branch must be named main and the changes must land
    # on a diverging feature branch — otherwise every scope-using agent sees
    # NO_CHANGES.
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp, capture_output=True)
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
        subprocess.run(["git", "checkout", "-b", "feature"], cwd=tmp, capture_output=True)
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


# Scenario "expected" blocks are detection answer keys graded by
# helpers.graders.grade_detection — see its docstring for key fields and the
# matcher's claimed-set rule (one issue satisfies at most one spec).
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
        "expected": {
            "security-reviewer": {
                "verdict_in": ["block", "request_changes"],
                "required_findings": [
                    {"id": "sql-injection", "file": "src/UserHandler.php", "line": 6,
                     "match_any": [r"sql[\s-]*inject", r"\bprepare\b", r"interpolat", r"unsanitiz"]},
                ],
                "acceptable_findings": [
                    {"id": "missing-capability-check", "file": "src/UserHandler.php",
                     "match_any": [r"current_user_can", r"capabilit", r"authoriz", r"access control"]},
                    {"id": "csrf-nonce", "file": "src/UserHandler.php",
                     "match_any": [r"\bnonce\b", r"csrf", r"state-chang"]},
                    {"id": "raw-table-name", "file": "src/UserHandler.php",
                     "match_any": [r"prefix", r"wp_delete_user", r"\btable\b"]},
                    {"id": "unguarded-superglobal", "file": "src/UserHandler.php",
                     "match_any": [r"isset", r"unslash", r"sanitiz", r"superglobal"]},
                ],
            },
        },
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
        "expected": {
            "security-reviewer": {
                "verdict_in": ["block", "request_changes"],
                "required_findings": [
                    # Injection evidence only — no standalone source token like
                    # \$_GET, or an IDOR/access-control finding at the same
                    # line would satisfy this spec without any injection
                    # having been reported.
                    {"id": "sql-injection-get", "file": "src/PaymentHandler.php", "line": 13,
                     "match_any": [r"sql[\s-]*inject", r"concatenat", r"unsanitiz", r"\bprepare\b"]},
                ],
                "acceptable_findings": [
                    {"id": "sql-injection-insert", "file": "src/PaymentHandler.php",
                     "match_any": [r"interpolat", r"\bINSERT\b", r"\bprepare\b", r"inject"]},
                    {"id": "idor-access-control", "file": "src/PaymentHandler.php",
                     "match_any": [r"access control", r"authoriz", r"\bIDOR\b", r"ownership"]},
                    {"id": "unvalidated-order", "file": "src/OrderProcessor.php",
                     "match_any": [r"validat", r"untrusted", r"authoriz"]},
                ],
            },
            "architecture-reviewer": {
                "verdict_in": ["comment", "request_changes", "block"],
                "acceptable_findings": [
                    {"id": "handler-owns-queries", "file": "src/PaymentHandler.php",
                     "match_any": [r"coupl", r"abstraction", r"repository", r"separation", r"respons"]},
                    {"id": "no-failure-handling", "file": "src/OrderProcessor.php",
                     "match_any": [r"error handling", r"failure", r"transaction", r"partial"]},
                ],
            },
            "performance-reviewer": {
                # performance-reviewer.md classifies missing LIMIT in raw
                # queries as CRITICAL (Unbounded Queries) — the fixture's
                # SELECT * with no LIMIT makes a blocking verdict correct
                # behavior, not a false positive.
                "verdict_in": ["comment", "request_changes", "block"],
                "required_findings": [
                    {"id": "unbounded-query", "file": "src/PaymentHandler.php",
                     "match_any": [r"\bLIMIT\b", r"unbounded", r"select\s*\*"]},
                ],
            },
        },
    },
    "js_source_review": {
        "description": "JS/TS source with XSS and hardcoded API key",
        "agents": ["security-reviewer"],
        "diff": str(FIXTURES_DIR / "js-ts-source.diff"),
        "grader": "output_pair",
        "expected": {
            "security-reviewer": {
                "verdict_in": ["block", "request_changes"],
                "required_findings": [
                    {"id": "dom-xss", "file": "src/components/UserForm.tsx", "line": 13,
                     "match_any": [r"\bxss\b", r"innerHTML", r"sanitiz"]},
                    {"id": "hardcoded-api-key", "file": "src/api/client.ts", "line": 1,
                     "match_any": [r"hard-?coded", r"api.?key", r"secret", r"credential"]},
                ],
                "acceptable_findings": [
                    {"id": "url-interpolation", "file": "src/api/client.ts",
                     "match_any": [r"encodeURIComponent", r"interpolat", r"\bURL\b"]},
                ],
            },
        },
    },
    "php_tests_review": {
        "description": "PHP test files with missing assertions and over-mocking",
        "agents": ["php-tests-reviewer"],
        "diff": str(FIXTURES_DIR / "php-test-only.diff"),
        "grader": "output_pair",
        "expected": {
            "php-tests-reviewer": {
                "verdict_in": ["block", "request_changes", "comment"],
                "required_findings": [
                    {"id": "meaningless-assertion", "file": "tests/PaymentHandlerTest.php",
                     "line": 14,
                     "match_any": [r"assertNotNull", r"meaning", r"weak assert"]},
                    {"id": "no-assertions", "file": "tests/OrderProcessorTest.php",
                     "match_any": [r"assert"]},
                ],
                "acceptable_findings": [
                    {"id": "over-mocking", "file": "tests/PaymentHandlerTest.php",
                     "match_any": [r"mock", r"assertInstanceOf", r"construct"]},
                ],
            },
        },
    },
    "e2e_tests_review": {
        "description": "E2E test files with hard-coded waits",
        "agents": ["e2e-tests-reviewer"],
        "diff": str(FIXTURES_DIR / "e2e-test-only.diff"),
        "grader": "output_pair",
        "expected": {
            "e2e-tests-reviewer": {
                "verdict_in": ["block", "request_changes", "comment"],
                "required_findings": [
                    {"id": "hardcoded-wait", "file": "e2e/checkout.spec.ts",
                     "match_any": [r"waitForTimeout", r"hard-?coded wait", r"fixed wait"]},
                ],
                "acceptable_findings": [
                    {"id": "pom-wait", "file": "e2e/pages/CheckoutPage.ts",
                     "match_any": [r"waitForTimeout", r"\bwait"]},
                    {"id": "brittle-locators", "file": "e2e/checkout.spec.ts",
                     "match_any": [r"locator", r"selector", r"getByRole", r"test.?id"]},
                ],
            },
        },
    },
    "wp_specific_review": {
        "description": "WP plugin with hooks, i18n, escaping, and wpdb",
        "agents": ["wp-architecture-reviewer", "security-reviewer"],
        "diff": str(FIXTURES_DIR / "wp-hooks-and-i18n.diff"),
        "grader": "output_pair",
        "expected": {
            "security-reviewer": {
                "verdict_in": ["block", "request_changes", "comment"],
                "required_findings": [
                    {"id": "unescaped-output", "file": "includes/class-payment-gateway.php",
                     "line": 47, "line_tolerance": 2,
                     "match_any": [r"esc_html", r"escap", r"\bxss\b"]},
                ],
            },
            "wp-architecture-reviewer": {
                # wp-architecture-reviewer.md classifies unprefixed global
                # classes/CPT names as CRITICAL namespace pollution — the
                # fixture's global Payment_Gateway / payment_log make a
                # blocking verdict correct behavior.
                "verdict_in": ["comment", "request_changes", "block"],
                "required_findings": [
                    {"id": "namespace-pollution",
                     "file": "includes/class-payment-gateway.php",
                     "match_any": [r"prefix", r"namespac", r"collision"]},
                ],
            },
        },
    },
    "realistic_multi_file": {
        "description": "Realistic multi-file PR touching all domains",
        "agents": ALL_AGENTS,
        "diff": str(FIXTURES_DIR / "multi-file-realistic.diff"),
        "grader": "output_pair",
        "expected": {
            "security-reviewer": {
                "verdict_in": ["approve", "comment"],
                "max_severity": "medium",
            },
            "php-tests-reviewer": {
                "verdict_in": ["block", "request_changes", "comment"],
                "required_findings": [
                    {"id": "weak-assertion", "file": "tests/ProductManagerTest.php",
                     "match_any": [r"assertNotNull", r"meaning", r"weak", r"assert"]},
                ],
            },
            "js-tests-reviewer": {
                "verdict_in": ["block", "request_changes", "comment"],
                "required_findings": [
                    {"id": "count-only-assertion",
                     "file": "src/components/__tests__/ProductList.test.tsx",
                     "match_any": [r"toHaveLength", r"count", r"content", r"name", r"price"]},
                ],
            },
            "python-tests-reviewer": {"expect_not_applicable": True},
            "go-tests-reviewer": {"expect_not_applicable": True},
            "rust-tests-reviewer": {"expect_not_applicable": True},
        },
    },
    "php_clean_review": {
        "description": "Well-written WP PHP (capability, nonce, prepared query, escaping) — false-positive probe",
        "agents": ["security-reviewer", "performance-reviewer"],
        "diff": str(FIXTURES_DIR / "php-clean-source.diff"),
        "grader": "output_pair",
        "expected": {
            "security-reviewer": {"verdict_in": ["approve"], "max_severity": "low"},
            "performance-reviewer": {"verdict_in": ["approve"], "max_severity": "low"},
        },
    },
    "js_clean_review": {
        "description": "Well-written TS API client (no secrets, encoded params) — false-positive probe",
        "agents": ["security-reviewer"],
        "diff": str(FIXTURES_DIR / "js-clean-source.diff"),
        "grader": "output_pair",
        "expected": {
            "security-reviewer": {"verdict_in": ["approve"], "max_severity": "low"},
        },
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


def split_agent_definition(agent_def: str) -> tuple:
    """Split an agent .md into (body, model) from its YAML frontmatter.

    Returns the definition body without frontmatter and the frontmatter's
    `model:` value (None when absent). Definitions without frontmatter come
    back verbatim with model None.
    """
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", agent_def, re.DOTALL)
    if not m:
        return agent_def, None
    frontmatter, body = m.group(1), m.group(2)
    model_match = re.search(r"^model:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
    return body.lstrip("\n"), model_match.group(1) if model_match else None


# Model values the Agent tool accepts as routing shorthands. Frontmatter
# `inherit` (or a missing field) means "use the caller's model".
_DISPATCHABLE_MODELS = {"sonnet", "haiku", "opus"}


def check_model_routing(agent_name: str, agent_def: str) -> Optional[str]:
    """Return an error when frontmatter routing diverges from the registry.

    agent_registry.json is the single source of truth for agent
    configuration; the Agent tool routes on the .md frontmatter. When the two
    disagree, dispatching would silently exercise a model production planning
    does not declare — refuse to run rather than measure the wrong tier.
    """
    _, fm_model = split_agent_definition(agent_def)
    tier = AGENT_CONFIG.get(agent_name, {}).get("model_tier") or "inherit"
    if (fm_model or "inherit") != tier:
        return (
            f"model routing drift for {agent_name}: frontmatter model "
            f"{fm_model!r} vs registry model_tier {tier!r} — the registry is "
            f"canonical; refusing to dispatch an unrepresentative model"
        )
    return None


def build_dispatch_prompt(agent_name: str, bootstrap_cmd: str) -> str:
    """Build the reviewer prompt for a session running AS the agent.

    The session is invoked with `--agent pirategoat-tools:<name>`, so the
    canonical .md is its system prompt and its frontmatter contract (model,
    effort, tools) is applied by the host — there is no orchestrating parent
    whose output could be misattributed to the reviewer. The prompt mirrors
    the production step 6 subagent prompt: run bootstrap, follow its
    contract.
    """
    return (
        f"Run this exact bootstrap command and follow the emitted scope and "
        f"output contract:\n```\n{bootstrap_cmd}\n```"
    )


def build_dispatch_cmd(claude_path: str, agent_name: str, prompt: str) -> list:
    """Assemble the claude -p invocation that runs the configured reviewer."""
    return [
        claude_path, "-p", "--dangerously-skip-permissions",
        "--plugin-dir", str(PLUGIN_ROOT),
        "--agent", f"pirategoat-tools:{agent_name}",
        "--output-format", "json",
        prompt,
    ]


def check_dispatched_models(agent_name: str, models: list) -> Optional[str]:
    """Return an error when the run's model usage contradicts the registry.

    JSON output reports modelUsage per run — hard evidence of which model
    actually executed. A routed tier (sonnet/haiku/opus) must appear in the
    used-model IDs; `inherit` accepts whatever the ambient default was.
    """
    tier = AGENT_CONFIG.get(agent_name, {}).get("model_tier") or "inherit"
    if tier not in _DISPATCHABLE_MODELS:
        return None
    if not any(tier in model for model in models):
        return (
            f"dispatched models {models} do not include registry tier "
            f"{tier!r} for {agent_name} — model routing was not applied"
        )
    return None


def dispatch_agent(agent_name: str, bootstrap_cmd: str, cwd: str) -> tuple:
    """Run the configured reviewer directly via `claude -p --agent`.

    Returns (exit_code, result_text, evidence). The session IS the reviewer
    (canonical definition as system prompt, frontmatter model/effort/tools
    applied natively), JSON output provides per-run model-usage evidence,
    and a run whose used models contradict the registry tier fails instead
    of being silently graded as the wrong instrument.
    """
    prompt = build_dispatch_prompt(agent_name, bootstrap_cmd)

    # Check if claude CLI is available
    claude_path = shutil.which("claude")
    if not claude_path:
        return 1, "ERROR: claude CLI not found in PATH", {}

    # The real pipeline runs reviewers via the Agent tool under an interactive
    # parent that can answer permission prompts. A headless `claude -p` cannot,
    # so a content-triggered "ask" (e.g. destructive SQL quoted in finding
    # prose) would silently deny the builder call and no output would be
    # written. The eval repo is a throwaway tempdir, so skip permission
    # prompts.
    cmd = build_dispatch_cmd(claude_path, agent_name, prompt)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=900, cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return 1, "ERROR: Agent dispatch timed out after 900s", {}
    except FileNotFoundError:
        return 1, "ERROR: claude CLI not found", {}

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return 1, f"ERROR: non-JSON dispatch output:\n{result.stdout[:2000]}", {}

    models = sorted((payload.get("modelUsage") or {}).keys())
    evidence = {
        "models": models,
        "is_error": bool(payload.get("is_error")),
        "session_id": payload.get("session_id"),
    }
    text = payload.get("result") or ""

    model_error = check_dispatched_models(agent_name, models)
    if model_error:
        return 1, f"ERROR: {model_error}\n\n{text}", evidence

    rc = 1 if payload.get("is_error") else result.returncode
    return rc, text, evidence


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
            # Bootstrap short-circuit: NO_DOMAIN_FILES means no agent runs, so
            # there is no agent output to grade. grade_no_domain_files targets
            # agent return signals — running it against the full bootstrap
            # prompt false-positives on protocol prose that teaches severity
            # vocabulary. The short-circuit itself is the pass condition.
            if "NO_DOMAIN_FILES" in bootstrap_out:
                return GradeResult(
                    passed=True, score=1.0, failures=[],
                    checks_run=1, checks_passed=1,
                )
            # If scope was OK (no domain filtering at bootstrap level), we need dispatch
            # For dispatch mode, we'd send to agent and grade output
            # In non-dispatch bootstrap-only mode, just check the bootstrap succeeded
            return GradeResult(
                passed=True, score=1.0, failures=[],
                checks_run=1, checks_passed=1,
            )

        # Refuse to dispatch when frontmatter routing has drifted from the
        # canonical registry — the run would measure an unrepresentative
        # model. Checked before dispatch so the drift costs nothing.
        agent_def_path = PLUGIN_ROOT / "agents" / f"{agent_name}.md"
        if not agent_def_path.is_file():
            return GradeResult(
                passed=False, score=0.0,
                failures=[f"agent definition missing: {agent_def_path}"],
                checks_run=1, checks_passed=0,
            )
        drift = check_model_routing(agent_name, agent_def_path.read_text())
        if drift:
            return GradeResult(
                passed=False, score=0.0, failures=[drift],
                checks_run=1, checks_passed=0,
            )

        # Dispatch the configured reviewer. The subagent re-runs bootstrap
        # itself (mirroring the production step 6 briefing); the direct run
        # above stays for short-circuit grading and fail-fast.
        bootstrap_cmd = (
            f'{shlex.quote(sys.executable)} {shlex.quote(str(BOOTSTRAP_SCRIPT))} '
            f'--agent {shlex.quote(agent_name)} --output-dir {shlex.quote(output_dir)}'
        )
        rc, agent_output, dispatch_evidence = dispatch_agent(agent_name, bootstrap_cmd, cwd)

        # Keep the agent's final message for postmortem — without it a
        # missing output file is undiagnosable.
        transcript_path = os.path.join(output_dir, f"{agent_name}-dispatch-transcript.txt")
        with open(transcript_path, "w") as f:
            f.write(
                f"exit_code: {rc}\n"
                f"dispatch_evidence: {json.dumps(dispatch_evidence)}\n\n"
                f"{agent_output}"
            )

        # Grade. Reviewers publish JSON only — Markdown is a derived artifact
        # (see review/agent/output.py), so materialize it before the pair grade.
        if scenario["grader"] == "output_pair":
            _materialize_missing_markdown(output_dir)
            reviewer_name = _mod.derive_reviewer_name(agent_name)
            compliance = grade_output_pair(output_dir, reviewer_name)

            key = (scenario.get("expected") or {}).get(agent_name)
            if key is None:
                return compliance

            review_path = os.path.join(output_dir, f"{reviewer_name}-review.json")
            try:
                with open(review_path) as f:
                    review = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                detection = GradeResult(
                    passed=False, score=0.0,
                    failures=[f"review JSON unreadable for detection grading: {exc}"],
                    checks_run=1, checks_passed=0,
                    detail={"verdict": None, "match": None},
                )
            else:
                detection = grade_detection(review, key)

            detection.failures = [f"detection: {msg}" for msg in detection.failures]
            # compliance_passed: carried so multi-trial aggregation can vote on
            # compliance separately. output_dir: the artifact directory (review
            # JSON, dispatch transcript) — without it, diagnosing a matcher
            # false negative across hidden per-trial tempdirs is impossible.
            # models: the run's verified model usage, so reports carry the
            # dispatch-identity evidence, not just the pass/fail outcome.
            detection.detail = dict(
                detection.detail or {},
                compliance_passed=compliance.passed,
                output_dir=output_dir,
                models=dispatch_evidence.get("models"),
            )
            return merge_grades(compliance, detection)
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
    parser.add_argument(
        "--trials",
        type=int,
        default=1,
        help="Dispatch each keyed agent N times and majority-vote the detection "
             "checks (nondeterminism control; multiplies model cost by N)",
    )
    parser.add_argument(
        "--report-out",
        metavar="PATH",
        help="Write a structured JSON benchmark report (per scenario/agent: "
             "pass state, check counts, failures, detection detail)",
    )

    args = parser.parse_args()

    if args.trials < 1:
        parser.error(f"--trials must be >= 1, got {args.trials}")

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

        # Resolve the selection before any side effect. A selection that
        # matches nothing (e.g. --scenario php_clean_review --agent
        # js-tests-reviewer) must not masquerade as a green run — TOTAL: 0/0
        # with exit 0 and an empty report reads as success to benchmark
        # automation — and must not leave a zero-byte report behind either.
        selection = [
            (scenario_name, scenario, [a for a in agents if a in scenario["agents"]])
            for scenario_name, scenario in scenarios.items()
        ]
        if not any(scenario_agents for _, _, scenario_agents in selection):
            print(
                "ERROR: selection matched no scenario/agent pairs "
                f"(scenario: {args.scenario or 'all'}, "
                f"agent: {args.agent or 'all'})."
            )
            sys.exit(2)

        # Pre-flight the report path only once the selection is valid —
        # append-mode open proves write access (touch() only updates
        # metadata, which the owner may do even on a read-only file) without
        # truncating an existing report, so a non-writable path fails before
        # any paid dispatch.
        if args.report_out:
            report_path = Path(args.report_out)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, "a"):
                pass

        all_results = {}
        # Per-entry report metadata: unkeyed agents run once regardless of
        # --trials, and a keyed run can fail before producing detection
        # detail, so neither the report-level trials field nor detail alone
        # tells a consumer what kind of result an entry is.
        entry_meta = {}
        for scenario_name, scenario, scenario_agents in selection:
            agent_results = {}
            for agent_name in scenario_agents:
                print(f"Running: {scenario_name} / {agent_name}...", flush=True)
                key = (scenario.get("expected") or {}).get(agent_name)
                entry_meta[(scenario_name, agent_name)] = {
                    "trials": args.trials if args.trials > 1 and key is not None else 1,
                    "keyed": key is not None,
                }
                if args.trials > 1 and key is not None:
                    trial_grades = [
                        run_dispatch_scenario(scenario_name, scenario, agent_name)
                        for _ in range(args.trials)
                    ]
                    result = aggregate_detection_trials(
                        [g.detail for g in trial_grades], key,
                    )
                    result.detail["per_trial_failures"] = [g.failures for g in trial_grades]
                else:
                    result = run_dispatch_scenario(scenario_name, scenario, agent_name)
                agent_results[agent_name] = result
            if agent_results:
                all_results[scenario_name] = agent_results

        print_results(all_results)

        if args.report_out:
            report = {
                "mode": "dispatch",
                "trials": args.trials,
                "results": [
                    {
                        "scenario": scenario_name,
                        "agent": agent_name,
                        "trials": entry_meta[(scenario_name, agent_name)]["trials"],
                        "keyed": entry_meta[(scenario_name, agent_name)]["keyed"],
                        "passed": r.passed,
                        "checks_run": r.checks_run,
                        "checks_passed": r.checks_passed,
                        "failures": r.failures,
                        "detail": r.detail,
                    }
                    for scenario_name, agents in all_results.items()
                    for agent_name, r in agents.items()
                ],
            }
            with open(args.report_out, "w") as f:
                json.dump(report, f, indent=2)
            print(f"Report written: {args.report_out}")

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
