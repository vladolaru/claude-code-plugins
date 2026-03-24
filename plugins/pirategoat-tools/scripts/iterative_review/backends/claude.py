"""Claude Code CLI backend — invocation, output parsing, prompt composition.

This is the ONLY file that knows about `claude -p`, `--json-schema`,
`--allowedTools`, or Claude Code's JSON response envelope format.
"""

import json
import os
import subprocess

TIMEOUT = 1800  # 30 minutes — used by invoke_review and timeout briefings
TIMEOUT_SENTINEL = "__CLAUDE_TIMEOUT__"

# Sonnet caps at "high" — no "xhigh" or "max" support
_EFFORT_MAP = {"medium": "medium", "high": "high", "xhigh": "high"}


# ---------------------------------------------------------------------------
# Output Parsing
# ---------------------------------------------------------------------------

def _format_location(code_location):
    """Extract file:line from CC's code_location dict.

    CC uses relative paths via file_path (not absolute_file_path like Codex),
    so no path conversion is needed.
    """
    if not code_location:
        return "unknown"
    path = code_location.get("file_path", "unknown")
    lr = code_location.get("line_range", {})
    start = lr.get("start")
    end = lr.get("end")
    if start and end and start != end:
        return f"{path}:{start}-{end}"
    elif start:
        return f"{path}:{start}"
    return path


def parse_output(raw_output, round_num):
    """Parse Claude Code review output into normalized findings.

    CC returns a JSON envelope on stdout:
    {
      "type": "result",
      "result": "text response",
      "structured_output": { ...findings schema... },
      ...
    }

    This function:
    1. Parses the raw stdout as JSON
    2. Extracts structured_output (schema-validated findings)
    3. Falls back to result field as plain text if structured_output missing

    Returns:
        (findings_list, degraded_bool)
        findings_list: list of dicts with id, severity, title, body, location
        degraded_bool: True if structured output was unavailable (plain text fallback)
    """
    try:
        envelope = json.loads(raw_output)
    except (json.JSONDecodeError, TypeError):
        # Not even valid JSON — full degraded mode
        return [{
            "id": f"r{round_num}_raw",
            "severity": "unknown",
            "title": "Unstructured review output",
            "body": raw_output,
            "location": "unknown",
            "confidence": None,
        }], True

    # Reject error envelopes — these are CLI failures (auth errors, budget
    # exhaustion, etc.), not review findings. Returning empty findings with
    # degraded=True lets the caller handle it as a backend failure.
    if envelope.get("is_error"):
        return [], True

    # Try structured_output first (schema-validated findings)
    data = envelope.get("structured_output")
    if data is None:
        # No structured output — fall back to result field as plain text
        result_text = envelope.get("result", raw_output)
        return [{
            "id": f"r{round_num}_raw",
            "severity": "unknown",
            "title": "Unstructured review output",
            "body": result_text,
            "location": "unknown",
            "confidence": None,
        }], True

    raw_findings = data.get("findings", [])
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

def check_auth():
    """Check that Claude CLI is installed and authenticated.

    Uses `claude auth status` which returns JSON with a `loggedIn` field.
    `claude --version` is NOT sufficient — it exits 0 even when unauthenticated.

    Returns (authenticated, message).
    """
    try:
        result = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return False, result.stderr.strip() or "claude auth status failed"
        try:
            data = json.loads(result.stdout)
            if data.get("loggedIn"):
                return True, result.stdout.strip()
            return False, "claude CLI is not logged in"
        except (json.JSONDecodeError, TypeError):
            return False, f"unexpected auth status output: {result.stdout.strip()}"
    except FileNotFoundError:
        return False, "claude CLI not found in PATH"
    except subprocess.TimeoutExpired:
        return False, "claude auth status timed out"


def write_prompt_file(output_dir, round_num, rubric, merge_base,
                      context, pushback_log, analysis_doc_path,
                      prior_analysis_path=None):
    """Compose and write the review prompt file.

    The prompt is ordered for optimal prompt caching:
    static content first (rubric, context, task), dynamic content last
    (pushback log, analysis paths).

    Returns the file path.
    """
    # --- Static prefix (cacheable across rounds) ---
    parts = [rubric]

    # Original context (investigation report, PR goal) — static across rounds
    if context:
        parts.append("\n## Additional Context\n")
        parts.append(context)

    # Task description — merge_base is static across rounds
    parts.append("\n---\n")
    parts.append("## Your Task\n")
    parts.append(
        f"Review the code changes between merge base `{merge_base}` and HEAD. "
        f"Run `git diff {merge_base}..HEAD` to inspect the changes.\n"
    )

    # --- Dynamic suffix (changes each round) ---

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

    path = os.path.join(output_dir, f"round-{round_num}-prompt.md")
    with open(path, "w") as f:
        f.write("\n".join(parts))
    return path


def get_schema_path():
    """Return the path to the Claude Code review output JSON Schema file."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "claude-review-schema.json")


def get_rubric():
    """Read the review rubric (shared with Codex backend)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "codex-review-rubric.md")
    try:
        with open(path) as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return ""


def invoke_review(prompt_file, schema_file, timeout=TIMEOUT, effort=None,
                  **kwargs):
    """Invoke `claude -p` with a review prompt and structured output schema.

    Uses flag-based isolation (hooks, MCP, skills disabled) instead of
    --bare since bare mode doesn't support OAuth/subscription auth.

    Args:
        prompt_file: Path to the review prompt markdown file
        schema_file: Path to the JSON Schema for structured output
        timeout: Seconds before killing (default 1800 = 30 min)
        effort: Optional reasoning effort level (e.g. 'high', 'xhigh').
                Mapped via _EFFORT_MAP (xhigh -> high for Sonnet capping).
        **kwargs: output_dir= grants CC access to the workspace directory
                  via --add-dir (required when output_dir is outside the repo
                  tree, e.g. /tmp/iterative-review-*). Other kwargs ignored.

    Returns:
        (raw_stdout_string, success_bool)
    """
    prompt_file = os.path.abspath(prompt_file)
    schema_file = os.path.abspath(schema_file)

    # Read schema content for inline passing
    with open(schema_file) as f:
        schema_json = f.read().strip()

    # Read prompt content for input= kwarg
    with open(prompt_file) as f:
        prompt_content = f.read()

    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--json-schema", schema_json,
        "--permission-mode", "dontAsk",
        "--allowedTools",
        "Read,Grep,Glob,Write,Bash(git diff *,git log *,git show *,git blame *)",
        "--settings", '{"disableAllHooks": true}',
        "--mcp-config", '{"mcpServers":{}}',
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--model", "sonnet",
    ]

    # Grant access to the output directory if it's outside the repo tree.
    # Without this, Read/Write to analysis files under /tmp/iterative-review-*
    # are denied by --permission-mode dontAsk.
    output_dir = kwargs.get("output_dir")
    if output_dir:
        cmd.extend(["--add-dir", os.path.abspath(output_dir)])

    if effort:
        cc_effort = _EFFORT_MAP.get(effort, "high")
        cmd.extend(["--effort", cc_effort])

    # Run from repo root so CC can access all project files
    try:
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True
        ).stdout.strip()
    except Exception:
        toplevel = None
    cwd = toplevel if toplevel else None

    try:
        result = subprocess.run(
            cmd, input=prompt_content, capture_output=True, text=True,
            timeout=timeout, cwd=cwd
        )
        return result.stdout, result.returncode == 0
    except subprocess.TimeoutExpired:
        return TIMEOUT_SENTINEL, False
    except FileNotFoundError:
        return "", False
