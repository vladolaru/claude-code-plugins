"""Codex CLI backend — invocation, output parsing, developer_instructions composition.

This is the ONLY file that knows about `codex exec review`, `--base`,
`-c developer_instructions`, or Codex's structured JSON output format.
"""

import json
import os
import subprocess
import re
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Output Parsing
# ---------------------------------------------------------------------------

def _get_repo_root():
    """Get the repo root for stripping absolute paths. Cached."""
    if not hasattr(_get_repo_root, "_cache"):
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True)
            _get_repo_root._cache = result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            _get_repo_root._cache = ""
    return _get_repo_root._cache


def _to_relative_path(absolute_path):
    """Convert an absolute file path to repo-relative."""
    root = _get_repo_root()
    if root and absolute_path.startswith(root):
        rel = absolute_path[len(root):]
        return rel.lstrip("/")
    return absolute_path


def _format_location(code_location):
    """Extract file:line from Codex's code_location dict, repo-relative."""
    if not code_location:
        return "unknown"
    path = _to_relative_path(code_location.get("absolute_file_path", "unknown"))
    lr = code_location.get("line_range", {})
    start = lr.get("start")
    end = lr.get("end")
    if start and end and start != end:
        return f"{path}:{start}-{end}"
    elif start:
        return f"{path}:{start}"
    return path


def parse_codex_output(raw_output, round_num):
    """Parse Codex review output into normalized findings.

    Returns:
        (findings_list, degraded_bool)
        findings_list: list of dicts with id, severity, title, body, location
        degraded_bool: True if JSON parsing failed (plain text fallback)
    """
    try:
        data = json.loads(raw_output)
        raw_findings = data.get("findings", [])
    except (json.JSONDecodeError, TypeError):
        # Plain text fallback
        return [{
            "id": f"r{round_num}_raw",
            "severity": "unknown",
            "title": "Unstructured review output",
            "body": raw_output,
            "location": "unknown",
            "confidence": None,
        }], True

    findings = []
    for i, f in enumerate(raw_findings, 1):
        findings.append({
            "id": f"r{round_num}_f{i}",
            "severity": f"P{f.get('priority', '?')}",
            "title": f.get("title", "Untitled"),
            "body": f.get("body", ""),
            "location": _format_location(f.get("code_location")),
            "confidence": f.get("confidence_score"),
        })

    return findings, False


# ---------------------------------------------------------------------------
# Prompt Composition & CLI Invocation
# ---------------------------------------------------------------------------

def check_codex_auth():
    """Quick auth check. Returns (authenticated, error_message)."""
    try:
        result = subprocess.run(
            ["codex", "login", "status"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0, result.stderr.strip()
    except FileNotFoundError:
        return False, "codex CLI not found in PATH"
    except subprocess.TimeoutExpired:
        return False, "codex login status timed out"


def write_prompt_file(output_dir, round_num, rubric, merge_base,
                      context, pushback_log, analysis_doc_path,
                      prior_analysis_path=None):
    """Compose and write the review prompt file for codex exec.

    The prompt includes Codex's review rubric verbatim, the diff task,
    and iterative context (pushback log, analysis doc instructions).
    Returns the file path.
    """
    parts = [rubric]

    parts.append("\n---\n")
    parts.append("## Your Task\n")
    parts.append(
        f"Review the code changes between merge base `{merge_base}` and HEAD. "
        f"Run `git diff {merge_base}..HEAD` to inspect the changes.\n"
    )

    # Pushback log from prior rounds
    if pushback_log:
        parts.append("\n## Review History\n")
        parts.append(pushback_log)
        parts.append(
            "\nReview the CURRENT state of the code. Previously fixed issues "
            "should no longer exist. Re-raise a rejected item ONLY if you have "
            "a new technical counter-argument the reviewer has not addressed. "
            "Otherwise, treat rejected items as resolved.\n"
        )

    # Prior analysis doc (round 2+)
    if prior_analysis_path:
        parts.append(
            f"\nRead your previous analysis at `{prior_analysis_path}` to "
            f"understand what you've already reviewed and what was addressed.\n"
        )

    # This round's analysis doc
    parts.append(
        f"\nWrite your detailed analysis to `{analysis_doc_path}` as you "
        f"review. Include what you checked, your reasoning for each finding, "
        f"and what you considered but chose not to flag.\n"
    )

    # Original context (investigation report, PR goal)
    if context:
        parts.append("\n## Additional Context\n")
        parts.append(context)

    path = os.path.join(output_dir, f"round-{round_num}-prompt.md")
    with open(path, "w") as f:
        f.write("\n".join(parts))
    return path


def get_schema_path():
    """Return the path to the review output JSON Schema file."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "codex-review-schema.json")


def get_rubric():
    """Read the review rubric from codex-review-rubric.md."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "codex-review-rubric.md")
    try:
        with open(path) as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return ""


def invoke_codex_review(prompt_file, schema_file, output_file, timeout=1800):
    """Invoke `codex exec` with a custom review prompt and structured output.

    Uses `codex exec` (NOT `codex exec review`) with --output-schema for
    guaranteed structured JSON. The prompt file is piped via stdin.

    Args:
        prompt_file: Path to the review prompt markdown file
        schema_file: Path to the JSON Schema for structured output
        output_file: Path for -o flag (captures structured JSON)
        timeout: Seconds before killing (default 1800 = 30 min)

    Returns:
        (output_string, success_bool)
    """
    # Resolve all paths to absolute before changing cwd to repo root
    prompt_file = os.path.abspath(prompt_file)
    schema_file = os.path.abspath(schema_file)
    output_file = os.path.abspath(output_file)

    cmd = [
        "codex", "exec",
        "--output-schema", schema_file,
        "-o", output_file,
        "--sandbox", "workspace-write",
        "--ephemeral",
        "-",  # read prompt from stdin
    ]

    # Run from repo root so Codex can write analysis docs to output_dir
    try:
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True
        ).stdout.strip()
    except Exception:
        toplevel = None
    cwd = toplevel if toplevel else None

    try:
        with open(prompt_file) as pf:
            result = subprocess.run(
                cmd, stdin=pf, capture_output=True, text=True,
                timeout=timeout, cwd=cwd
            )
        # Read from -o output file
        if os.path.isfile(output_file):
            with open(output_file) as f:
                content = f.read()
                if content.strip():
                    return content, result.returncode == 0
        # Fall back to stdout (structured output also goes to stdout)
        return result.stdout, result.returncode == 0
    except subprocess.TimeoutExpired:
        return "", False
    except FileNotFoundError:
        return "", False
