#!/usr/bin/env python3
"""
Ground Truth Collection — run available static analysis tools and collect findings.

Orchestrates linters, security scanners, and test runners against changed files.
Uses existing parser scripts (parse-linter-results.py, parse-security-results.py,
parse-test-results.py, parse-coverage-results.py) for output normalization.

Usage:
    python3 run-ground-truth.py --output-dir /tmp/pr-review-42 --changed-files "src/app.php,src/utils.js"
    python3 run-ground-truth.py --output-dir /tmp/pr-review-42 --changed-files-file /tmp/files.txt

Exit code: always 0 (ground truth is additive, not a gate).

Zero external dependencies (stdlib + existing parser scripts).
"""

import argparse
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPTS_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# File type classification
# ---------------------------------------------------------------------------

PHP_EXTENSIONS = frozenset({".php"})
JS_EXTENSIONS = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"})
TEST_PATTERNS = ("test.", "spec.", "__tests__", "tests/", "test/")

DEFAULT_TIMEOUT = 60  # seconds per tool

# ---------------------------------------------------------------------------
# Tool configuration
# ---------------------------------------------------------------------------

KNOWN_TOOLS = frozenset({
    "eslint", "phpcs", "semgrep",
    "jest", "jest_coverage",
    "phpunit", "phpunit_coverage",
})

TOOL_OUTPUT_FILES = {
    "eslint": "eslint-results.json",
    "phpcs": "phpcs-results.json",
    "semgrep": "semgrep-results.json",
    "jest": "jest-results.json",
    "jest_coverage": "jest-coverage-summary.json",
    "phpunit": "phpunit-results.json",
    "phpunit_coverage": "phpunit-coverage.xml",
}


def load_tool_config(config_path: str) -> Dict[str, str]:
    """Load tool configuration from a JSON file.

    Returns a dict mapping tool name -> command template.
    Unknown tool names and invalid entries are skipped with warnings.
    """
    with open(config_path) as f:
        raw = json.load(f)

    config: Dict[str, str] = {}
    for tool_name, entry in raw.items():
        if tool_name not in KNOWN_TOOLS:
            print(f"  Warning: unknown tool '{tool_name}', skipping", file=sys.stderr)
            continue
        if not isinstance(entry, dict) or "cmd" not in entry:
            print(f"  Warning: tool '{tool_name}' missing 'cmd', skipping", file=sys.stderr)
            continue
        cmd = entry["cmd"].strip()
        if not cmd:
            continue
        config[tool_name] = cmd

    return config


def run_configured_tool(
    tool_name: str,
    cmd_template: str,
    output_dir: str,
    changed_files: List[str],
    timeout: int,
) -> Tuple[bool, str]:
    """Run a tool from its command template.

    Substitutes placeholders:
      {output_file} -> output_dir/TOOL_OUTPUT_FILES[tool_name]
      {output_dir}  -> output_dir
      {files}       -> shell-quoted space-separated changed files

    Returns (success, error_message). Success means the expected output file exists.
    """
    output_file = os.path.join(output_dir, TOOL_OUTPUT_FILES[tool_name])
    files_str = " ".join(shlex.quote(f) for f in changed_files)

    cmd_str = cmd_template.replace("{output_file}", output_file)
    cmd_str = cmd_str.replace("{output_dir}", output_dir)
    cmd_str = cmd_str.replace("{files}", files_str)

    try:
        subprocess.run(
            cmd_str, shell=True, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return False, f"{tool_name}: timed out after {timeout}s"
    except OSError as e:
        return False, f"{tool_name}: {e}"

    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        return True, ""

    return False, f"{tool_name}: command ran but produced no output file"


def is_test_file(filepath: str) -> bool:
    """Check if a file is a test file."""
    lower = filepath.lower()
    return any(pat in lower for pat in TEST_PATTERNS)


def classify_changed_files(changed_files: List[str]) -> Dict[str, List[str]]:
    """Classify changed files by type.

    Returns dict with keys: 'php', 'js', 'all', 'has_production_code'.
    """
    php_files: List[str] = []
    js_files: List[str] = []
    has_production = False

    for f in changed_files:
        ext = os.path.splitext(f)[1].lower()
        if ext in PHP_EXTENSIONS:
            php_files.append(f)
        elif ext in JS_EXTENSIONS:
            js_files.append(f)

        if not is_test_file(f):
            has_production = True

    return {
        "php": php_files,
        "js": js_files,
        "all": changed_files,
        "has_production_code": has_production,
    }


# ---------------------------------------------------------------------------
# Tool detection
# ---------------------------------------------------------------------------


def detect_eslint() -> bool:
    """Check if ESLint is available in the project."""
    # Check for ESLint config files
    config_files = [
        ".eslintrc.js",
        ".eslintrc.json",
        ".eslintrc.yml",
        ".eslintrc.yaml",
        ".eslintrc",
        "eslint.config.js",
        "eslint.config.mjs",
        "eslint.config.cjs",
    ]
    if any(os.path.exists(cf) for cf in config_files):
        return shutil.which("npx") is not None

    # Check package.json for eslint dependency
    if os.path.exists("package.json"):
        try:
            with open("package.json") as f:
                pkg = json.load(f)
            deps = {
                **pkg.get("dependencies", {}),
                **pkg.get("devDependencies", {}),
            }
            if "eslint" in deps:
                return shutil.which("npx") is not None
        except (json.JSONDecodeError, OSError):
            pass

    return False


def detect_phpcs() -> bool:
    """Check if PHPCS is available."""
    return shutil.which("phpcs") is not None


def detect_semgrep() -> bool:
    """Check if Semgrep is available."""
    return shutil.which("semgrep") is not None


def detect_jest() -> bool:
    """Check if Jest is available in the project."""
    if not os.path.exists("package.json"):
        return False
    try:
        with open("package.json") as f:
            pkg = json.load(f)
        deps = {
            **pkg.get("dependencies", {}),
            **pkg.get("devDependencies", {}),
        }
        return "jest" in deps and shutil.which("npx") is not None
    except (json.JSONDecodeError, OSError):
        return False


def detect_phpunit() -> bool:
    """Check if PHPUnit is available."""
    return (
        os.path.exists("phpunit.xml") or os.path.exists("phpunit.xml.dist")
    ) and shutil.which("phpunit") is not None


def detect_tools() -> Dict[str, bool]:
    """Detect all available tools."""
    return {
        "eslint": detect_eslint(),
        "phpcs": detect_phpcs(),
        "semgrep": detect_semgrep(),
        "jest": detect_jest(),
        "phpunit": detect_phpunit(),
    }


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


def run_tool(
    cmd: List[str], timeout: int, label: str
) -> Tuple[bool, str, str]:
    """Run a tool with timeout. Returns (success, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        # Many tools exit non-zero when findings exist — that's still success
        return True, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"{label} timed out after {timeout}s"
    except FileNotFoundError:
        return False, "", f"{label} command not found"
    except OSError as e:
        return False, "", f"{label} error: {e}"


def run_eslint(
    files: List[str], output_dir: str, timeout: int
) -> Optional[str]:
    """Run ESLint on specific files. Returns path to results file or None."""
    if not files:
        return None

    output_file = os.path.join(output_dir, "eslint-results.json")
    cmd = ["npx", "eslint", "--format", "json", "--output-file", output_file]
    cmd.extend(files)

    ok, _, stderr = run_tool(cmd, timeout, "ESLint")
    # ESLint writes to output file even on violations (exit 1)
    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        return output_file
    if not ok:
        print(f"  ESLint: {stderr}", file=sys.stderr)
    return None


def run_phpcs(
    files: List[str], output_dir: str, timeout: int
) -> Optional[str]:
    """Run PHPCS on specific files. Returns path to results file or None."""
    if not files:
        return None

    output_file = os.path.join(output_dir, "phpcs-results.json")

    # Try WordPress-Extra first, fall back to PSR12
    for standard in ("WordPress-Extra", "PSR12"):
        cmd = [
            "phpcs",
            f"--standard={standard}",
            "--report=json",
            f"--report-file={output_file}",
        ]
        cmd.extend(files)

        ok, _, stderr = run_tool(cmd, timeout, f"PHPCS ({standard})")
        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            return output_file
        # If standard not found, try next
        if "standard" in stderr.lower() and "not found" in stderr.lower():
            continue
        break

    return None


def run_semgrep(
    files: List[str], output_dir: str, timeout: int
) -> Optional[str]:
    """Run Semgrep on specific files. Returns path to results file or None."""
    if not files:
        return None

    output_file = os.path.join(output_dir, "semgrep-results.json")
    cmd = [
        "semgrep",
        "--config=auto",
        "--json",
        f"--output={output_file}",
    ]
    cmd.extend(files)

    ok, _, stderr = run_tool(cmd, timeout, "Semgrep")
    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        return output_file
    if not ok:
        print(f"  Semgrep: {stderr}", file=sys.stderr)
    return None


def run_jest(output_dir: str, timeout: int) -> Optional[str]:
    """Run Jest test suite. Returns path to results file or None."""
    output_file = os.path.join(output_dir, "jest-results.json")
    cmd = [
        "npx",
        "jest",
        "--json",
        f"--outputFile={output_file}",
        "--forceExit",
    ]

    ok, _, stderr = run_tool(cmd, timeout, "Jest")
    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        return output_file
    if not ok:
        print(f"  Jest: {stderr}", file=sys.stderr)
    return None


def run_phpunit(output_dir: str, timeout: int) -> Optional[str]:
    """Run PHPUnit test suite. Returns path to results file or None."""
    output_file = os.path.join(output_dir, "phpunit-results.json")
    cmd = ["phpunit", f"--log-json={output_file}"]

    ok, _, stderr = run_tool(cmd, timeout, "PHPUnit")
    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        return output_file
    if not ok:
        print(f"  PHPUnit: {stderr}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Parser integration — import functions from existing parser scripts
# ---------------------------------------------------------------------------


def _load_parser_module(name: str):
    """Import a parser script as a module."""
    script_path = SCRIPTS_DIR / f"parse-{name}.py"
    if not script_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(
        f"parse_{name.replace('-', '_')}", str(script_path)
    )
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_linter_findings(
    output_dir: str, changed_files_set: frozenset
) -> List[Dict[str, Any]]:
    """Parse linter results and filter to changed files."""
    mod = _load_parser_module("linter-results")
    if mod is None:
        return []

    results = []
    eslint_file = os.path.join(output_dir, "eslint-results.json")
    phpcs_file = os.path.join(output_dir, "phpcs-results.json")

    if os.path.exists(eslint_file):
        try:
            results.append(mod.parse_eslint_results(eslint_file))
        except Exception:
            pass

    if os.path.exists(phpcs_file):
        try:
            results.append(mod.parse_phpcs_results(phpcs_file))
        except Exception:
            pass

    if not results:
        return []

    unified = mod.unify_results(results)
    findings = []
    for v in unified.get("all_violations", []):
        # Normalize file path and filter to changed files
        vfile = _normalize_path(v.get("file", ""))
        if not _file_in_changeset(vfile, changed_files_set):
            continue
        findings.append(
            {
                "tool": v.get("linter", "unknown").lower(),
                "category": "lint",
                "file": vfile,
                "line": v.get("line", 0),
                "rule": v.get("rule", "unknown"),
                "severity": v.get("severity", "warning"),
                "message": v.get("message", ""),
            }
        )

    return findings


def parse_security_findings(
    output_dir: str, changed_files_set: frozenset
) -> List[Dict[str, Any]]:
    """Parse security scanner results and filter to changed files."""
    mod = _load_parser_module("security-results")
    if mod is None:
        return []

    results = []
    semgrep_result = mod.parse_semgrep_results(output_dir)
    if semgrep_result:
        results.append(semgrep_result)

    bandit_result = mod.parse_bandit_results(output_dir)
    if bandit_result:
        results.append(bandit_result)

    if not results:
        return []

    unified = mod.unify_results(results)
    findings = []
    for f in unified.get("all_findings", []):
        # Filter out info-level findings
        if f.get("severity") == "info":
            continue
        ffile = _normalize_path(f.get("file", ""))
        if not _file_in_changeset(ffile, changed_files_set):
            continue
        findings.append(
            {
                "tool": f.get("scanner", "unknown").lower(),
                "category": "security",
                "file": ffile,
                "line": f.get("line", 0),
                "rule": f.get("rule", "unknown"),
                "severity": f.get("severity", "medium"),
                "message": f.get("message", ""),
            }
        )

    return findings


def parse_test_results(output_dir: str) -> Optional[Dict[str, Any]]:
    """Parse test results into summary."""
    mod = _load_parser_module("test-results")
    if mod is None:
        return None

    results = []
    for name, parser_fn in [
        ("jest", mod.parse_jest_results),
        ("phpunit", mod.parse_phpunit_results),
    ]:
        result_file = os.path.join(output_dir, f"{name}-results.json")
        if os.path.exists(result_file):
            try:
                results.append(parser_fn(result_file))
            except Exception:
                pass

    if not results:
        return None

    unified = mod.unify_results(results)
    return {
        "passed": unified["summary"]["passed"],
        "failed": unified["summary"]["failed"],
        "total": unified["summary"]["total"],
        "failures": [
            {
                "test": f.get("test", ""),
                "framework": f.get("framework", ""),
                "location": f.get("location", ""),
                "message": f.get("message", "")[:500],
            }
            for f in unified.get("all_failures", [])
        ],
    }


def parse_coverage_results(
    output_dir: str, changed_files_set: frozenset
) -> Optional[Dict[str, Any]]:
    """Parse coverage results, filtered to changed files."""
    mod = _load_parser_module("coverage-results")
    if mod is None:
        return None

    results = []
    jest_result = mod.parse_jest_coverage(output_dir)
    if jest_result:
        results.append(jest_result)
    phpunit_result = mod.parse_phpunit_coverage(output_dir)
    if phpunit_result:
        results.append(phpunit_result)

    if not results:
        return None

    unified = mod.unify_results(results)
    # Filter to changed files
    filtered_gaps = [
        f
        for f in unified.get("all_files_below_threshold", [])
        if _file_in_changeset(
            _normalize_path(f.get("file", "")), changed_files_set
        )
    ]

    return {
        "overall_line": unified.get("overall_coverage", 0),
        "files_below_threshold": filtered_gaps,
    }


# ---------------------------------------------------------------------------
# Path normalization helpers
# ---------------------------------------------------------------------------


def _normalize_path(filepath: str) -> str:
    """Normalize a file path for comparison.

    Tools may return absolute paths — convert to relative if possible.
    """
    if not filepath:
        return filepath

    # Try making relative to CWD
    try:
        return str(Path(filepath).relative_to(Path.cwd()))
    except ValueError:
        pass

    # Return basename match-friendly version
    return filepath


def _file_in_changeset(filepath: str, changed_files_set: frozenset) -> bool:
    """Check if a file path matches any file in the changeset.

    Handles tool output using absolute paths vs changeset using relative paths.
    """
    if not filepath or not changed_files_set:
        return True  # No filter = include all

    # Direct match
    if filepath in changed_files_set:
        return True

    # Basename-suffix match (tool returns /abs/path/src/app.js, changeset has src/app.js)
    for cf in changed_files_set:
        if filepath.endswith(cf) or cf.endswith(filepath):
            return True

    return False


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def collect_ground_truth(
    changed_files: List[str],
    output_dir: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Run all available tools and collect ground truth findings.

    Returns the ground-truth summary dict (also written to output_dir).
    """
    os.makedirs(output_dir, exist_ok=True)

    classified = classify_changed_files(changed_files)
    available = detect_tools()
    changed_set = frozenset(changed_files)

    tools_run: List[str] = []
    tools_skipped: List[str] = []
    tools_unavailable: List[str] = []
    all_findings: List[Dict[str, Any]] = []
    test_results: Optional[Dict[str, Any]] = None
    coverage: Optional[Dict[str, Any]] = None

    # --- Linters (run all, then parse once) ---

    # ESLint (JS/TS files)
    if classified["js"]:
        if available["eslint"]:
            if run_eslint(classified["js"], output_dir, timeout):
                tools_run.append("eslint")
            else:
                tools_skipped.append("eslint")
        else:
            tools_unavailable.append("eslint")

    # PHPCS (PHP files)
    if classified["php"]:
        if available["phpcs"]:
            if run_phpcs(classified["php"], output_dir, timeout):
                tools_run.append("phpcs")
            else:
                tools_skipped.append("phpcs")
        else:
            tools_unavailable.append("phpcs")

    # Parse all linter results at once (handles both eslint + phpcs)
    if "eslint" in tools_run or "phpcs" in tools_run:
        all_findings.extend(parse_linter_findings(output_dir, changed_set))

    # --- Security ---

    # Semgrep (all file types)
    if classified["all"]:
        if available["semgrep"]:
            result_file = run_semgrep(
                classified["all"], output_dir, timeout
            )
            if result_file:
                tools_run.append("semgrep")
                all_findings.extend(
                    parse_security_findings(output_dir, changed_set)
                )
            else:
                tools_skipped.append("semgrep")
        else:
            tools_unavailable.append("semgrep")

    # --- Tests (only if production code changed) ---

    if classified["has_production_code"]:
        # Jest
        if available["jest"]:
            result_file = run_jest(output_dir, timeout)
            if result_file:
                tools_run.append("jest")
            else:
                tools_skipped.append("jest")
        else:
            tools_unavailable.append("jest")

        # PHPUnit
        if available["phpunit"]:
            result_file = run_phpunit(output_dir, timeout)
            if result_file:
                tools_run.append("phpunit")
            else:
                tools_skipped.append("phpunit")
        else:
            tools_unavailable.append("phpunit")

        # Parse test results (covers all frameworks)
        test_results = parse_test_results(output_dir)

    # --- Build summary ---

    summary: Dict[str, Any] = {
        "tools_run": tools_run,
        "tools_skipped": tools_skipped,
        "tools_unavailable": tools_unavailable,
        "findings": all_findings,
    }

    if test_results is not None:
        summary["test_results"] = test_results

    if coverage is not None:
        summary["coverage"] = coverage

    # Write summary
    summary_path = os.path.join(output_dir, "ground-truth-summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Collect ground truth from static analysis tools.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write results to.",
    )
    parser.add_argument(
        "--changed-files",
        default="",
        help="Comma-separated list of changed files.",
    )
    parser.add_argument(
        "--changed-files-file",
        default=None,
        help="Path to file containing changed files (one per line).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Per-tool timeout in seconds (default: {DEFAULT_TIMEOUT}).",
    )

    args = parser.parse_args()

    # Resolve changed files
    changed_files: List[str] = []
    if args.changed_files:
        changed_files = [
            f.strip() for f in args.changed_files.split(",") if f.strip()
        ]
    if args.changed_files_file and os.path.exists(args.changed_files_file):
        with open(args.changed_files_file) as f:
            changed_files.extend(
                line.strip() for line in f if line.strip()
            )

    if not changed_files:
        print("No changed files provided. Nothing to check.", file=sys.stderr)
        # Write empty summary
        os.makedirs(args.output_dir, exist_ok=True)
        summary = {
            "tools_run": [],
            "tools_skipped": [],
            "tools_unavailable": [],
            "findings": [],
        }
        with open(
            os.path.join(args.output_dir, "ground-truth-summary.json"), "w"
        ) as f:
            json.dump(summary, f, indent=2)
        print(json.dumps(summary, indent=2))
        sys.exit(0)

    summary = collect_ground_truth(
        changed_files=changed_files,
        output_dir=args.output_dir,
        timeout=args.timeout,
    )

    # Print summary to stdout for callers
    print(json.dumps(summary, indent=2))

    # Always exit 0 — ground truth is additive, not a gate
    sys.exit(0)


if __name__ == "__main__":
    main()
