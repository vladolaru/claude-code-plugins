#!/usr/bin/env python3
"""
Agent compliance eval runner.

Two modes:
  --grade-only <output_dir>   Grade existing review output files
  --dispatch --agent <name>   Full eval: temp repo -> bootstrap -> dispatch agent -> grade
  --dispatch --all            Run full eval for all dispatchable agents (EVAL_AGENTS)

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
sys.path.insert(0, str(SCRIPTS_DIR))
from helpers.graders import (
    GradeResult,
    grade_detection,
    grade_output_pair,
    grade_review_json,
    grade_error_exit,
    grade_signal_format,
    merge_grades,
    aggregate_detection_trials,
)
from review.agent.output import load_review_document

# Import agent config
import importlib.util

_spec = importlib.util.spec_from_file_location("bootstrap_reviewer", str(BOOTSTRAP_SCRIPT))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
AGENT_CONFIG = _mod.AGENT_CONFIG
ALL_AGENTS = sorted(AGENT_CONFIG.keys())

# Agents the production pipeline can actually dispatch as reviewers. The
# registry also carries special orchestration agents (decision-reviewer,
# repo-reviewer-adapter — needs ref-mode args this harness never passes) and
# manual-only agents (tests-mutation-reviewer — must run SOLO); dispatching
# those burns model calls on runs that cannot produce a gradeable review and
# pollutes the benchmark denominator.
EVAL_AGENTS = sorted(
    name for name, cfg in AGENT_CONFIG.items()
    if cfg.get("dispatch_class") in ("always", "conditional")
)


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

    if diff_file and not os.path.isfile(diff_file):
        # A missing fixture must fail loudly: silently skipping the apply
        # yields a repo where main == HEAD, every agent sees NO_CHANGES, and
        # the scenario measures nothing while reporting normally.
        shutil.rmtree(tmp, ignore_errors=True)
        raise FileNotFoundError(f"scenario fixture missing: {diff_file}")
    if diff_file:
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
# matcher's claimed-set rule (one finding satisfies at most one spec).
SCENARIOS = {
    "no_domain_files_approve": {
        "description": "Docs-only changes: every non-docs reviewer must short-circuit",
        # docs-drift-reviewer legitimately owns docs-only diffs (its domain
        # HAS files here), so the short-circuit assertion cannot apply to it.
        "agents": [a for a in EVAL_AGENTS if a != "docs-drift-reviewer"],
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
                # security-reviewer.md classifies SQL injection as CRITICAL,
                # which the builder maps to block — accepting a softer
                # verdict or severity would reward under-classification.
                "verdict_in": ["block"],
                "required_findings": [
                    {"id": "sql-injection", "file": "src/UserHandler.php", "line": 6,
                     "min_severity": "critical",
                     "severity_basis": "doctrine",
                     "rationale": "security-reviewer.md -> SQL Injection = CRITICAL.",
                     "match_any": [r"sql[\s-]*inject", r"\bprepare\b", r"interpolat"]},
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
                # SQL injection is CRITICAL per security-reviewer.md, which
                # the builder maps to block.
                "verdict_in": ["block"],
                "required_findings": [
                    # Injection technique/sink evidence only — no standalone
                    # source token (\$_GET) and no generic input-handling
                    # vocabulary (unsanitiz), or an IDOR/access-control
                    # finding at the same line would satisfy this spec
                    # without any injection having been reported.
                    {"id": "sql-injection-get", "file": "src/PaymentHandler.php", "line": 13,
                     "min_severity": "critical",
                     "severity_basis": "doctrine",
                     "rationale": "security-reviewer.md -> SQL Injection = CRITICAL.",
                     "match_any": [r"sql[\s-]*inject", r"concatenat", r"\bprepare\b"]},
                ],
                "acceptable_findings": [
                    # Disjoint from the required spec's patterns (mutual
                    # exclusivity rule in match_findings) — prepare/inject
                    # belong to the required injection spec.
                    {"id": "sql-injection-insert", "file": "src/PaymentHandler.php",
                     "match_any": [r"interpolat", r"\bINSERT\b"]},
                    {"id": "idor-access-control", "file": "src/PaymentHandler.php",
                     "match_any": [r"access control", r"authoriz", r"\bIDOR\b", r"ownership"]},
                    {"id": "unvalidated-order", "file": "src/OrderProcessor.php",
                     "match_any": [r"validat", r"untrusted", r"authoriz"]},
                ],
            },
            "architecture-reviewer": {
                # architecture-reviewer.md "Mixed Abstraction Levels" mandates
                # flagging a method that interleaves orchestration with raw
                # SQL assembly, with concrete symptoms — process_payment is
                # its exact example, which earns the no-approve verdict gate.
                "verdict_in": ["comment", "request_changes", "block"],
                "required_findings": [
                    {"id": "handler-owns-queries", "file": "src/PaymentHandler.php",
                     "match_any": [r"coupl", r"abstraction", r"repositor",
                                   r"separat", r"data.?access", r"extract"]},
                ],
                "acceptable_findings": [
                    {"id": "no-failure-handling", "file": "src/OrderProcessor.php",
                     "match_any": [r"error handling", r"failure", r"transaction", r"partial"]},
                ],
            },
            "performance-reviewer": {
                # performance-reviewer.md classifies missing LIMIT in raw
                # queries as CRITICAL (Unbounded Queries). The fixture's
                # separate no-WHERE/no-LIMIT query makes that classification
                # unambiguous.
                "verdict_in": ["comment", "request_changes", "block"],
                "required_findings": [
                    # No select\* token — a SELECT-*-over-fetch finding is a
                    # different genuine defect and must not claim the
                    # unbounded-query recall gate.
                    {"id": "unbounded-query", "file": "src/PaymentHandler.php",
                     "min_severity": "critical",
                     "severity_basis": "doctrine",
                     "rationale": "performance-reviewer.md -> Unbounded Queries "
                                  "(raw query missing LIMIT) = CRITICAL.",
                     "match_any": [r"\bLIMIT\b", r"unbounded", r"paginat"]},
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
                # XSS is CRITICAL per security-reviewer.md → verdict block.
                # The api-key finding is HIGH (Sensitive Data Exposure) —
                # its floor is high accordingly.
                "verdict_in": ["block"],
                "required_findings": [
                    # No line pin: live evidence shows the reviewer anchoring
                    # anywhere in the handler (state hook, handler decl, or
                    # the sink itself); the patterns are sink-specific and
                    # the file holds no competing XSS-adjacent finding.
                    {"id": "dom-xss", "file": "src/components/UserForm.tsx",
                     "min_severity": "critical",
                     "severity_basis": "doctrine",
                     "rationale": "security-reviewer.md -> Cross-Site Scripting "
                                  "(missing context-appropriate escaping) = CRITICAL.",
                     "match_any": [r"\bxss\b", r"innerHTML", r"sanitiz"]},
                    # No line pin: the reviewer may anchor at the declaration
                    # (line 1) or the transmission sink (line 11); the file is
                    # 24 lines and the patterns are already specific.
                    {"id": "hardcoded-api-key", "file": "src/api/client.ts",
                     "min_severity": "high",
                     "severity_basis": "doctrine",
                     "rationale": "security-reviewer.md -> Sensitive Data Exposure "
                                  "(API keys or tokens in source) = HIGH.",
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
                # tests-reviewer-protocol.md classifies zero-assertion tests
                # as CRITICAL, so the builder's auto-verdict is block. The
                # assertNotNull is the protocol's mock-return tautology ONLY
                # if the reviewer can see PaymentHandler's implementation —
                # this fixture is deliberately test-only, so the provable
                # classification caps at weak-assertion/HIGH.
                "verdict_in": ["block"],
                "required_findings": [
                    {"id": "meaningless-assertion", "file": "tests/PaymentHandlerTest.php",
                     "line": 14, "min_severity": "high",
                     "severity_basis": "evidence_capped",
                     "rationale": "tests-reviewer-protocol.md makes mock-return "
                                  "tautologies CRITICAL, but this test-only fixture "
                                  "withholds PaymentHandler::process_payment(), so "
                                  "the reviewer cannot prove $result merely forwards "
                                  "the configured database mock return; the visible "
                                  "weak-assertion evidence supports HIGH.",
                     "match_any": [r"assertNotNull", r"meaning", r"weak assert"]},
                    # Explicit absent-assertion phrasings only — a bare
                    # "assert" token lets the co-located over-mocking finding
                    # claim this recall gate without the assertion gap ever
                    # being reported.
                    {"id": "no-assertions", "file": "tests/OrderProcessorTest.php",
                     "min_severity": "critical",
                     "severity_basis": "doctrine",
                     "rationale": "tests-reviewer-protocol.md -> tests without "
                                  "assertions (False Confidence) = CRITICAL.",
                     "match_any": [r"no\s+assert", r"assert\w*\s+nothing",
                                   r"without\s+(any\s+)?assert", r"missing\s+assert",
                                   r"zero\s+assert", r"lacks\s+assert",
                                   r"doesn'?t\s+assert", r"never\s+assert"]},
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
                # Fixed waits are HIGH per tests-reviewer-protocol.md (flaky
                # tests) — one high already forces request_changes, and the
                # fixture's three wait sites can reach block as 3+ highs.
                "verdict_in": ["request_changes", "block"],
                "required_findings": [
                    {"id": "hardcoded-wait", "file": "e2e/checkout.spec.ts",
                     "min_severity": "high",
                     "severity_basis": "doctrine",
                     "rationale": "tests-reviewer-protocol.md -> flaky/time-dependent "
                                  "tests = HIGH; e2e-tests-reviewer.md identifies "
                                  "page.waitForTimeout() as arbitrary-delay flakiness.",
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
                # Missing context-appropriate escaping is CRITICAL XSS per
                # security-reviewer.md (unqualified by data provenance) →
                # verdict block; a softer classification is a calibration
                # miss the benchmark must measure.
                "verdict_in": ["block"],
                "required_findings": [
                    {"id": "unescaped-output", "file": "includes/class-payment-gateway.php",
                     "line": 47, "line_tolerance": 2, "min_severity": "critical",
                     "severity_basis": "doctrine",
                     "rationale": "security-reviewer.md -> Cross-Site Scripting "
                                  "(missing context-appropriate escaping) = CRITICAL.",
                     "match_any": [r"esc_html", r"escap", r"\bxss\b"]},
                ],
            },
            "wp-architecture-reviewer": {
                # wp-architecture-reviewer.md classifies unprefixed global
                # classes/CPT names as CRITICAL namespace pollution — the
                # fixture's global Payment_Gateway / payment_log make a
                # blocking verdict correct behavior (auto-verdict: any
                # critical → block; live-confirmed).
                "verdict_in": ["block"],
                "required_findings": [
                    # (?<!->) excludes findings that merely quote the
                    # fixture's correct `$wpdb->prefix` usage — a direct-db
                    # api-bypass finding must not claim this recall gate.
                    {"id": "namespace-pollution",
                     "file": "includes/class-payment-gateway.php",
                     "min_severity": "critical",
                     "severity_basis": "doctrine",
                     "rationale": "wp-architecture-reviewer.md -> Global Namespace "
                                  "Pollution from unprefixed global classes = CRITICAL.",
                     "match_any": [r"(?<!->)prefix", r"namespac", r"collision"]},
                ],
            },
        },
    },
    "realistic_multi_file": {
        "description": "Realistic multi-file PR touching all domains",
        "agents": EVAL_AGENTS,
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
                    # NOT False Confidence: that CRITICAL bucket enumerates
                    # tests without assertions, assertions on MOCK return
                    # values (tautology), and disabled assertions. Here the
                    # assertion exists and get_product() runs a real $wpdb
                    # SELECT — nothing is mocked, so the assertion is weak,
                    # not tautological. A value-blind assertion over real
                    # data lands in MEDIUM (Best Practice).
                    # No bare "assert" token — any assertion-adjacent finding
                    # on this file would claim the gate vacuously.
                    {"id": "weak-assertion", "file": "tests/ProductManagerTest.php",
                     "min_severity": "medium",
                     "severity_basis": "doctrine",
                     "rationale": "tests-reviewer-protocol.md -> MEDIUM (Best "
                                  "Practice). Explicitly NOT CRITICAL (False "
                                  "Confidence): that bucket requires a missing, "
                                  "mocked, or disabled assertion, and this test "
                                  "asserts against a real $wpdb read.",
                     "match_any": [r"assertNotNull", r"meaning", r"weak"]},
                ],
            },
            "js-tests-reviewer": {
                "verdict_in": ["block", "request_changes", "comment"],
                "required_findings": [
                    # Asserting a count instead of content is UNDER-assertion,
                    # not the HIGH bucket's implementation-detail
                    # verification (which is over-coupling to internals). The
                    # separate querySelectorAll('li') smell is the
                    # implementation-detail one and is not this gate.
                    # No "name" token — a test-naming finding on the same
                    # file must not claim the assertion-quality gate.
                    {"id": "count-only-assertion",
                     "file": "src/components/__tests__/ProductList.test.tsx",
                     "min_severity": "medium",
                     "severity_basis": "doctrine",
                     "rationale": "tests-reviewer-protocol.md -> MEDIUM (Best "
                                  "Practice). Explicitly NOT HIGH: the count-only "
                                  "assertion verifies too little, whereas HIGH's "
                                  "implementation-detail verification is verifying "
                                  "internals too closely.",
                     "match_any": [r"toHaveLength", r"\bcount\b", r"\bcontent\b", r"\bprice\b"]},
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
            "security-reviewer": {
                "verdict_in": ["approve"], "max_severity": "low",
                "min_check_count": 1,
                "max_unclaimed_review_file_count": 0,
            },
            "performance-reviewer": {
                "verdict_in": ["approve"], "max_severity": "low",
                "min_check_count": 1,
                "max_unclaimed_review_file_count": 0,
            },
        },
    },
    "js_clean_review": {
        "description": "Well-written TS API client (no secrets, encoded params) — false-positive probe",
        "agents": ["security-reviewer"],
        "diff": str(FIXTURES_DIR / "js-clean-source.diff"),
        "grader": "output_pair",
        "expected": {
            "security-reviewer": {
                "verdict_in": ["approve"], "max_severity": "low",
                "min_check_count": 1,
                "max_unclaimed_review_file_count": 0,
            },
        },
    },
}


# =============================================================================
# Grade-Only Mode
# =============================================================================


def _materialize_missing_markdown(output_dir: str) -> None:
    """Render Markdown missing beside finalized canonical review JSON."""
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


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def frontmatter_model(agent_def: str) -> Optional[str]:
    """Return the `model:` value from an agent .md's YAML frontmatter.

    None when the definition has no frontmatter or no model field.
    """
    m = _FRONTMATTER_RE.match(agent_def)
    if not m:
        return None
    model_match = re.search(r"^model:\s*(\S+)\s*$", m.group(1), re.MULTILINE)
    return model_match.group(1) if model_match else None


# Model values the Agent tool accepts as routing shorthands. Frontmatter
# `inherit` (or a missing field) means "use the caller's model".
_DISPATCHABLE_MODELS = {"sonnet", "haiku", "opus"}

# Explicit per-entry outcome vocabulary. Each value is stamped by the code
# path that KNOWS what happened — never inferred from evidence shape.
# "degraded" is aggregate-only: a multi-trial entry where not every trial
# reached "graded". Consumers computing reviewer-behavior pass rates filter
# on status == "graded"; "timed_out" means model calls likely occurred
# (money spent) but produced no gradable evidence — deliberately not
# conflated with never-dispatched.
ENTRY_STATUSES = {
    "graded",            # live run produced a graded artifact
    "bootstrap_only",    # deterministic entry, no model call by design
    "agent_missing",     # pre-dispatch: agent definition file absent
    "routing_drift",     # pre-dispatch: frontmatter/registry mismatch
    "bootstrap_failed",  # pre-dispatch: bootstrap exited nonzero
    "cli_missing",       # claude CLI not found; no model call
    "timed_out",         # dispatch timeout; no gradable evidence
    "dispatch_error",    # non-JSON output, session error, nonzero exit
    "model_mismatch",    # run rejected: wrong model instrument
    "harness_error",     # eval-harness exception or unknown grader
    "degraded",          # aggregate: not every trial reached "graded"
}


def entry_status(result: GradeResult) -> str:
    """Derive a report entry's status from the RESULT's detail only — never
    from the trial list directly — so a harness error raised AFTER trials
    completed cannot masquerade as graded. An aggregate detail
    (per_trial_status present) is "graded" only when every trial reached
    graded, else "degraded": gradability, not spend — a trial that dispatched
    and was then rejected is not a graded trial.

    ENTRY_STATUSES is load-bearing here: an out-of-vocabulary stamp (a typo,
    or a leaked internal sentinel like dispatch_agent's "completed") is a
    harness bug and reports as harness_error instead of flowing into
    status-filtered pass rates as a novel value.
    """
    detail = result.detail or {}
    if "per_trial_status" in detail:
        status = (
            "graded"
            if all(s == "graded" for s in detail["per_trial_status"])
            else "degraded"
        )
    else:
        status = detail.get("status", "harness_error")
    return status if status in ENTRY_STATUSES else "harness_error"


def scenario_key(scenario: dict, agent_name: str) -> Optional[dict]:
    """The detection answer key for this scenario/agent pair, or None.

    The single definition of "keyed" — dispatch trial counts, detection
    grading, and report metadata must never disagree on it.
    """
    return (scenario.get("expected") or {}).get(agent_name)


def check_model_routing(agent_name: str, agent_def: str) -> Optional[str]:
    """Return an error when frontmatter routing diverges from the registry.

    agent_registry.json is the single source of truth for agent
    configuration; the Agent tool routes on the .md frontmatter. When the two
    disagree, dispatching would silently exercise a model production planning
    does not declare — refuse to run rather than measure the wrong tier.
    """
    fm_model = frontmatter_model(agent_def)
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


_PLUGIN_SHIM_DIR = None


def ensure_plugin_shim() -> str:
    """Create (once per process) a loadable plugin dir for the WORKTREE agents.

    The plugin directory itself carries no `.claude-plugin/plugin.json` (the
    repo-root marketplace.json is the canonical manifest, and the CLI loads
    neither form as a --plugin-dir target — verified: --agent fails to
    resolve with either path). Without a resolvable --plugin-dir the agent
    name is answered by the user-scope INSTALLED plugin, so the benchmark
    would grade a stale release instead of the branch under test. The shim
    is a tempdir with a minimal manifest and a symlink to the worktree's
    agents/ — sentinel-verified to make the branch definitions win.
    """
    global _PLUGIN_SHIM_DIR
    if _PLUGIN_SHIM_DIR is None:
        shim = tempfile.mkdtemp(prefix="eval-plugin-shim-")
        os.makedirs(os.path.join(shim, ".claude-plugin"))
        with open(os.path.join(shim, ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({
                "name": "pirategoat-tools",
                "version": "0.0.0-eval-worktree",
                "description": "eval shim exposing the worktree agent definitions",
            }, f)
        os.symlink(str(PLUGIN_ROOT / "agents"), os.path.join(shim, "agents"))
        _PLUGIN_SHIM_DIR = shim
    return _PLUGIN_SHIM_DIR


def build_dispatch_cmd(claude_path: str, agent_name: str, prompt: str) -> list:
    """Assemble the claude -p invocation that runs the configured reviewer.

    --setting-sources project excludes user-scope configuration — the
    installed copy of this plugin (which would silently shadow the worktree
    definitions; verified by a negative-control probe), user hooks that can
    block the reviewer's builder call, and user memory that contaminates the
    context. The temp eval repo carries no project settings, so the session
    is effectively clean and machine-independent.
    """
    return [
        claude_path, "-p", "--dangerously-skip-permissions",
        "--setting-sources", "project",
        "--plugin-dir", ensure_plugin_shim(),
        "--agent", f"pirategoat-tools:{agent_name}",
        "--output-format", "json",
        prompt,
    ]


_MODEL_USAGE_TOKEN_FIELDS = (
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheCreationInputTokens",
)


def _model_identity(model: str, usage: object) -> str:
    if isinstance(usage, dict):
        canonical = usage.get("canonicalModel")
        if isinstance(canonical, str) and canonical:
            return canonical
    return model


def _primary_model(model_usage: dict) -> Optional[str]:
    """Best-effort primary model: the entry with the largest token usage.

    modelUsage is a session-wide accumulator including auxiliary calls
    (verified live: a sonnet reviewer session also reports a haiku entry),
    so membership alone cannot attribute the main loop. Weight each model by
    its token counters; None when no usable weights exist.
    """
    best, best_weight = None, 0
    for model, usage in model_usage.items():
        weight = 0
        if isinstance(usage, dict):
            for field in _MODEL_USAGE_TOKEN_FIELDS:
                value = usage.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    weight += value
        if weight > best_weight:
            best, best_weight = _model_identity(model, usage), weight
    return best


def check_dispatched_models(agent_name: str, model_usage: dict) -> Optional[str]:
    """Return an error when the run's model usage contradicts the registry.

    A routed tier (sonnet/haiku/opus) must match the PRIMARY model — the one
    that did the bulk of the work — not merely appear somewhere in the
    session accumulator, or an auxiliary call in the right family would
    vouch for a main loop that ran elsewhere. Falls back to membership when
    usage carries no numeric weights; `inherit` accepts any model.
    """
    tier = AGENT_CONFIG.get(agent_name, {}).get("model_tier") or "inherit"
    if tier not in _DISPATCHABLE_MODELS:
        return None
    models = sorted(
        _model_identity(model, usage) for model, usage in model_usage.items()
    )
    primary = _primary_model(model_usage)
    if primary is not None:
        if tier not in primary:
            return (
                f"primary dispatched model {primary!r} (of {models}) does not "
                f"match registry tier {tier!r} for {agent_name} — model "
                f"routing was not applied"
            )
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
        return 1, "ERROR: claude CLI not found in PATH", {"status": "cli_missing"}

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
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return 1, "ERROR: Agent dispatch timed out after 900s", {"status": "timed_out"}
    except FileNotFoundError:
        return 1, "ERROR: claude CLI not found", {"status": "cli_missing"}

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return 1, (
            f"ERROR: non-JSON dispatch output:\n{result.stdout[:2000]}"
        ), {"status": "dispatch_error"}

    model_usage = payload.get("modelUsage") or {}
    evidence = {
        "models": sorted(
            _model_identity(model, usage)
            for model, usage in model_usage.items()
        ),
        "primary_model": _primary_model(model_usage),
        "is_error": bool(payload.get("is_error")),
        "session_id": payload.get("session_id"),
        # Under --dangerously-skip-permissions any denial comes from a hook;
        # recorded so a hook-blocked builder call is distinguishable from a
        # reviewer that chose to write nothing.
        "permission_denials": len(payload.get("permission_denials") or []),
    }
    text = payload.get("result") or ""

    rc = 1 if payload.get("is_error") else result.returncode
    if rc != 0:
        evidence["status"] = "dispatch_error"
        return rc, text, evidence

    model_error = check_dispatched_models(agent_name, model_usage)
    if model_error:
        evidence["status"] = "model_mismatch"
        return 1, f"ERROR: {model_error}\n\n{text}", evidence

    # "completed" is internal to dispatch_agent: run_dispatch_scenario
    # upgrades it to "graded" once grading actually runs.
    evidence["status"] = "completed"
    return 0, text, evidence


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
            result = grade_error_exit(bootstrap_out)
            result.detail = dict(result.detail or {}, status="bootstrap_only")
            return result

        if rc != 0:
            return GradeResult(
                passed=False, score=0.0,
                failures=[f"Bootstrap failed (exit {rc}): {bootstrap_out[:200]}"],
                checks_run=1, checks_passed=0,
                detail={"status": "bootstrap_failed"},
            )

        if scenario["grader"] == "no_domain_files":
            # Bootstrap short-circuit: NO_DOMAIN_FILES means no agent runs, so
            # there is no agent output to grade. helpers.graders.grade_no_domain_files
            # targets agent return signals — running it against the full bootstrap
            # prompt false-positives on protocol prose that teaches severity
            # vocabulary. The short-circuit itself is the pass condition —
            # and the ONLY pass condition: an unconditional pass on the
            # fallthrough made this scenario structurally unable to fail,
            # granting free credit to every entry.
            if "NO_DOMAIN_FILES" in bootstrap_out:
                return GradeResult(
                    passed=True, score=1.0, failures=[],
                    checks_run=1, checks_passed=1,
                    detail={"status": "bootstrap_only"},
                )
            return GradeResult(
                passed=False, score=0.0,
                failures=[
                    "bootstrap did not short-circuit on a docs-only diff — "
                    "either scope routing regressed or this agent's domain "
                    "covers docs and it must be excluded from this scenario"
                ],
                checks_run=1, checks_passed=0,
                detail={"status": "bootstrap_only"},
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
                detail={"status": "agent_missing"},
            )
        drift = check_model_routing(agent_name, agent_def_path.read_text())
        if drift:
            return GradeResult(
                passed=False, score=0.0, failures=[drift],
                checks_run=1, checks_passed=0,
                detail={"status": "routing_drift"},
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

        # A dispatch the harness rejected — model-routing violation, session
        # error, timeout, non-JSON output — must never be graded: the
        # reviewer may have written a plausible artifact before the rejection
        # surfaced, and grading it would attribute an untrusted run to the
        # agent. Fail the entry with the rejection as evidence; the status is
        # whatever dispatch_agent stamped on the failing path.
        if rc != 0:
            return GradeResult(
                passed=False, score=0.0,
                failures=[f"dispatch rejected: {agent_output.splitlines()[0][:300] if agent_output else 'no output'}"],
                checks_run=1, checks_passed=0,
                detail={
                    "dispatch_rejected": True,
                    "dispatch_evidence": dispatch_evidence,
                    "output_dir": output_dir,
                    "status": dispatch_evidence.get("status", "dispatch_error"),
                },
            )

        # Grade the reviewer's JSON only. Reviewers publish JSON — Markdown is
        # a derived artifact the HARNESS materializes below for human
        # inspection; grading the pair would count 5 markdown checks that
        # render_markdown() structurally guarantees, inflating every
        # compliance score with tautological credit.
        if scenario["grader"] == "output_pair":
            _materialize_missing_markdown(output_dir)
            reviewer_name = _mod.derive_reviewer_name(agent_name)
            review_path = os.path.join(output_dir, f"{reviewer_name}-review.json")
            compliance = grade_review_json(
                review_path, expected_reviewer=reviewer_name,
            )

            key = scenario_key(scenario, agent_name)
            if key is None:
                # Unkeyed entries still leave artifacts (review JSON,
                # dispatch transcript) worth pointing at from the report.
                compliance.detail = dict(
                    compliance.detail or {},
                    output_dir=output_dir,
                    status="graded",
                )
                return compliance

            try:
                review = load_review_document(
                    review_path, reviewer_name
                )
            except ValueError as exc:
                detection = GradeResult(
                    passed=False, score=0.0,
                    failures=[f"review JSON unreadable for detection grading: {exc}"],
                    checks_run=1, checks_passed=0,
                    detail={"verdict": None, "match": None},
                )
            else:
                detection = grade_detection(review, key, repo_root=cwd)

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
                status="graded",
            )
            return merge_grades(compliance, detection, detection_label="detection")
        elif scenario["grader"] == "signal_format":
            result = grade_signal_format(agent_output)
            result.detail = dict(result.detail or {}, status="graded")
            return result
        else:
            # A grader name the harness does not know is a harness/config
            # bug, not reviewer behavior.
            return GradeResult(
                passed=False, score=0.0,
                failures=[f"Unknown grader: {scenario['grader']}"],
                checks_run=1, checks_passed=0,
                detail={"status": "harness_error"},
            )
    finally:
        # Cleanup
        if os.path.isdir(cwd) and cwd.startswith(tempfile.gettempdir()):
            shutil.rmtree(cwd, ignore_errors=True)


# =============================================================================
# Output Formatting
# =============================================================================


def print_results(all_results: dict):
    """Print formatted eval results.

    The headline metric is ENTRIES passed — check counts vary with reviewer
    verbosity (compliance adds checks per schema-valid finding), so a
    check-ratio percentage would score a noisier reviewer higher for
    identical detection performance. Check counts remain per-entry
    diagnostics only.
    """
    print("\n=== EVAL RESULTS ===")
    entries_passed = 0
    entries_total = 0
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
            entries_total += 1
            entries_passed += 1 if result.passed else 0
            total_passed += result.checks_passed
            total_checks += result.checks_run

    print(
        f"\nTOTAL: {entries_passed}/{entries_total} entries passed "
        f"({total_passed}/{total_checks} checks — diagnostic only, not "
        f"comparable across entries)"
    )


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
        default=None,
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

    if args.report_out == "":
        parser.error("--report-out path must not be empty")
    if args.trials is not None and args.trials < 1:
        parser.error(f"--trials must be >= 1, got {args.trials}")
    if args.all and args.agent:
        parser.error("--all and --agent are mutually exclusive")
    dispatch_only_flags = bool(
        args.report_out is not None or args.trials is not None
        or args.scenario or args.agent or args.all
    )
    if args.grade_only and (args.dispatch or dispatch_only_flags):
        parser.error(
            "--grade-only cannot be combined with dispatch-mode flags "
            "(--dispatch/--report-out/--trials/--scenario/--agent/--all) — "
            "they would be silently ignored"
        )
    if not args.dispatch and not args.grade_only and dispatch_only_flags:
        parser.error(
            "--report-out/--trials/--scenario/--agent/--all require "
            "--dispatch — without it they would be silently ignored and "
            "the command would exit 0 having done nothing"
        )

    if args.trials is None:
        args.trials = 1

    if args.grade_only:
        results = run_grade_only(args.grade_only)
        all_results = {"grade_existing": results}
        print_results(all_results)
        return

    if args.dispatch:
        agents = EVAL_AGENTS if args.all else ([args.agent] if args.agent else EVAL_AGENTS)
        scenarios = SCENARIOS
        if args.scenario:
            if args.scenario not in SCENARIOS:
                # Exit 2 like every other configuration error — exit 1 is
                # reserved for "the eval ran and something failed".
                print(f"ERROR: Unknown scenario '{args.scenario}'. Available: {list(SCENARIOS.keys())}")
                sys.exit(2)
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
        if args.report_out is not None:
            report_path = Path(args.report_out)
            try:
                report_path.parent.mkdir(parents=True, exist_ok=True)
                with open(report_path, "a"):
                    pass
            except OSError as exc:
                print(f"ERROR: report path {args.report_out!r} is not writable: {exc}")
                sys.exit(2)

        def harness_error_grade(exc: Exception) -> GradeResult:
            return GradeResult(
                passed=False, score=0.0,
                failures=[f"harness error: {exc}"],
                checks_run=1, checks_passed=0,
                detail={"status": "harness_error"},
            )

        all_results = {}
        for scenario_name, scenario, scenario_agents in selection:
            agent_results = {}
            for agent_name in scenario_agents:
                print(f"Running: {scenario_name} / {agent_name}...", flush=True)
                # Unkeyed agents run once regardless of --trials — there is
                # no detection key to vote on.
                run_trials = (
                    args.trials
                    if scenario_key(scenario, agent_name) is not None
                    else 1
                )
                # One broken entry (bad fixture, bootstrap timeout) must not
                # abort the run and discard every completed paid dispatch —
                # record it as a failed entry and continue.
                try:
                    if run_trials > 1:
                        # Per-trial fault isolation: a trial that raises
                        # (bootstrap timeout, fixture error) becomes a failed
                        # grade — it votes as a miss on every check — while
                        # the completed paid trials keep their evidence and
                        # the remaining trials still run. A shared
                        # list-comprehension would discard everything on the
                        # first raise.
                        trial_grades = []
                        for _ in range(run_trials):
                            try:
                                trial_grades.append(
                                    run_dispatch_scenario(scenario_name, scenario, agent_name)
                                )
                            except Exception as exc:
                                trial_grades.append(harness_error_grade(exc))
                        result = aggregate_detection_trials(trial_grades)
                    else:
                        result = run_dispatch_scenario(scenario_name, scenario, agent_name)
                except Exception as exc:
                    result = harness_error_grade(exc)
                agent_results[agent_name] = result
            if agent_results:
                all_results[scenario_name] = agent_results

        print_results(all_results)

        if args.report_out is not None:
            report = {
                "mode": "dispatch",
                "trials": args.trials,
                "results": [
                    {
                        "scenario": scenario_name,
                        "agent": agent_name,
                        # Unkeyed agents run once regardless of --trials, and
                        # a keyed run can fail before producing detection
                        # detail, so neither the report-level trials field
                        # nor detail alone tells a consumer what kind of
                        # result an entry is.
                        "trials": (
                            args.trials
                            if scenario_key(scenarios[scenario_name], agent_name) is not None
                            else 1
                        ),
                        "keyed": scenario_key(scenarios[scenario_name], agent_name) is not None,
                        "status": entry_status(r),
                        "passed": r.passed,
                        "checks_run": r.checks_run,
                        "checks_passed": r.checks_passed,
                        "failures": r.failures,
                        "detail": r.detail,
                    }
                    for scenario_name, scenario_results in all_results.items()
                    for agent_name, r in scenario_results.items()
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
