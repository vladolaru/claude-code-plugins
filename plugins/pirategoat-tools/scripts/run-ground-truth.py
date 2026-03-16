#!/usr/bin/env python3
"""
Ground Truth Collection — run configured static analysis tools and collect findings.

Executes tool commands from a JSON config file (produced by the LLM from the
project's CLAUDE.md) and parses their output using existing parser scripts
(parse-linter-results.py, parse-security-results.py, parse-test-results.py,
parse-coverage-results.py).

Usage:
    python3 run-ground-truth.py --output-dir /tmp/pr-review-42 \
        --changed-files "src/app.php,src/utils.js" \
        --tool-config /tmp/pr-review-42/tool-config.json

    python3 run-ground-truth.py --output-dir /tmp/pr-review-42 \
        --changed-files-file /tmp/files.txt \
        --tool-config /tmp/pr-review-42/tool-config.json

Without --tool-config, all tools are marked as not_configured and an empty
summary is written (exit 0).

Exit code: always 0 (ground truth is additive, not a gate).

Zero external dependencies (stdlib + existing parser scripts).
"""

import argparse
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPTS_DIR = Path(__file__).resolve().parent

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
    tool_config: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Run configured tools and collect ground truth findings.

    Args:
        changed_files: List of changed file paths.
        output_dir: Directory to write results to.
        tool_config: Mapping of tool name -> command template. If None or empty,
                     all tools are marked as not_configured.
        timeout: Per-tool timeout in seconds.

    Returns the ground-truth summary dict (also written to output_dir).
    """
    os.makedirs(output_dir, exist_ok=True)

    if tool_config is None:
        tool_config = {}

    changed_set = frozenset(changed_files)
    tools_run: List[str] = []
    tools_failed: List[str] = []
    tools_not_configured = sorted(KNOWN_TOOLS - set(tool_config.keys()))
    all_findings: List[Dict[str, Any]] = []
    test_results: Optional[Dict[str, Any]] = None
    coverage: Optional[Dict[str, Any]] = None

    # --- Run configured tools in parallel ---
    with ThreadPoolExecutor(max_workers=len(tool_config) or 1) as pool:
        futures = {
            pool.submit(run_configured_tool, name, cmd, output_dir, changed_files, timeout): name
            for name, cmd in tool_config.items()
        }
        for future in as_completed(futures):
            tool_name = futures[future]
            ok, err = future.result()
            if ok:
                tools_run.append(tool_name)
            else:
                tools_failed.append(tool_name)
                if err:
                    print(f"  {err}", file=sys.stderr)

    # --- Parse results by output file existence (not tool name) ---
    # This supports merged runs where e.g. jest --coverage produces both
    # test results and coverage files in a single invocation.

    # Linters
    if (os.path.exists(os.path.join(output_dir, "eslint-results.json"))
            or os.path.exists(os.path.join(output_dir, "phpcs-results.json"))):
        all_findings.extend(parse_linter_findings(output_dir, changed_set))

    # Security
    if os.path.exists(os.path.join(output_dir, "semgrep-results.json")):
        all_findings.extend(parse_security_findings(output_dir, changed_set))

    # Tests
    if (os.path.exists(os.path.join(output_dir, "jest-results.json"))
            or os.path.exists(os.path.join(output_dir, "phpunit-results.json"))):
        test_results = parse_test_results(output_dir)

    # Coverage
    if (os.path.exists(os.path.join(output_dir, "jest-coverage-summary.json"))
            or os.path.exists(os.path.join(output_dir, "phpunit-coverage.xml"))):
        coverage = parse_coverage_results(output_dir, changed_set)

    # --- Build summary ---
    summary: Dict[str, Any] = {
        "tools_run": tools_run,
        "tools_failed": tools_failed,
        "tools_not_configured": tools_not_configured,
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
        "--tool-config",
        default=None,
        help="Path to tool-config.json with tool commands.",
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

    # Load tool config
    tool_config: Dict[str, str] = {}
    if args.tool_config and os.path.exists(args.tool_config):
        try:
            tool_config = load_tool_config(args.tool_config)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  Warning: could not load tool config: {e}", file=sys.stderr)

    summary = collect_ground_truth(
        changed_files=changed_files,
        output_dir=args.output_dir,
        tool_config=tool_config,
        timeout=args.timeout,
    )

    # Print summary to stdout for callers
    print(json.dumps(summary, indent=2))

    # Always exit 0 — ground truth is additive, not a gate
    sys.exit(0)


if __name__ == "__main__":
    main()
