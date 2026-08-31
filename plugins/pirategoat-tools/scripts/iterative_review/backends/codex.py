"""Codex CLI backend — invocation, output parsing, developer_instructions composition.

This is the ONLY file that knows about `codex exec review`, `--base`,
`-c developer_instructions`, or Codex's structured JSON output format.
"""

import json
import os
import subprocess

from ..paths import round_artifact_path

TIMEOUT = 1800  # 30 minutes — used by invoke_review and timeout briefings
TIMEOUT_SENTINEL = "__CODEX_TIMEOUT__"

# Patterns that indicate quota/rate-limit exhaustion in Codex stderr.
# When detected, the orchestrator can fall back to Claude immediately
# instead of treating it as a generic failure.
_RATE_LIMIT_PATTERNS = [
    "rate_limit_exceeded",
    "rate limit reached",
    "you've hit your usage limit",
    "you've exceeded the rate limit",
    "quota exceeded",
    "429 too many requests",
    "usage limit",
]


def detect_failure_reason(stderr):
    """Classify a Codex failure from its stderr output.

    Returns a reason string for telemetry:
    - "rate_limit" if quota/rate-limit exhaustion detected
    - "unknown" otherwise
    """
    if not stderr:
        return "unknown"
    lower = stderr.lower()
    for pattern in _RATE_LIMIT_PATTERNS:
        if pattern in lower:
            return "rate_limit"
    return "unknown"


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


def parse_output(raw_output, round_num):
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

def check_auth():
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
                      prior_analysis_path=None, deferred_items=None):
    """Compose and write the review prompt file for codex exec.

    The prompt is ordered for optimal server-side prompt caching:
    static content first (rubric, context, task), dynamic content last
    (pushback log, analysis paths). OpenAI caches the longest matching
    prefix across API calls — keeping the stable prefix long maximizes
    cache hits on rounds 2+.

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
            "Treat rejected items as resolved. Treat deferred items as "
            "out-of-scope — they were acknowledged and will be tracked as "
            "follow-ups. Do not re-raise deferred items.\n"
        )

    # Previously deferred items (round 2+)
    if deferred_items:
        parts.append("\n## Previously Deferred Items\n")
        parts.append(
            "The following issues were identified in prior rounds, acknowledged "
            "as valid, and deferred as out-of-scope for this branch. They will "
            "be tracked as follow-ups.\n"
        )
        for item in deferred_items:
            sev = item.get("severity", "?")
            title = item.get("title", "Untitled")
            loc = item.get("location", "unknown")
            parts.append(f'- [{sev}] "{title}" ({loc})')
        parts.append(
            "\nFocus your review on NEW issues not covered above. "
            "Do not re-raise deferred items — their scope decision has been made.\n"
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

    path = round_artifact_path(output_dir, round_num, "prompt")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(parts))
    return str(path)


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


def invoke_review(prompt_file, schema_file, timeout=TIMEOUT, effort=None,
                  **kwargs):
    """Invoke `codex exec` with a custom review prompt and structured output.

    Uses `codex exec` (NOT `codex exec review`) with --output-schema for
    guaranteed structured JSON. The prompt file is piped via stdin.

    Accepts optional output_file= kwarg. When omitted, a temp file is
    created automatically. This keeps the common signature compatible
    with the Claude backend (which has no output_file parameter) while
    preserving the caller's ability to control the output path.

    Args:
        prompt_file: Path to the review prompt markdown file
        schema_file: Path to the JSON Schema for structured output
        timeout: Seconds before killing (default 1800 = 30 min)
        effort: Optional reasoning effort level (e.g. 'high', 'xhigh').
                When set, injects -c model_reasoning_effort="<effort>"
                into the codex exec command. When None, behavior is unchanged.
        **kwargs: output_file= path for -o flag (auto-created if omitted)

    Returns:
        (output_string, success_bool)
    """
    output_file = kwargs.get("output_file")
    if output_file is None:
        import tempfile
        fd, output_file = tempfile.mkstemp(suffix="-review-output.json")
        os.close(fd)

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
    ]

    # Inject reasoning effort override before the stdin marker
    if effort:
        cmd.extend(["-c", f'model_reasoning_effort="{effort}"'])
        # Activate fast mode for high/xhigh to keep throughput manageable
        if effort in ("high", "xhigh"):
            cmd.extend(["-c", 'service_tier="fast"'])

    cmd.append("-")  # read prompt from stdin

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
        # Store stderr for post-failure diagnosis (rate limits, etc.)
        invoke_review.last_stderr = result.stderr
        # Read from -o output file
        if os.path.isfile(output_file):
            with open(output_file) as f:
                content = f.read()
                if content.strip():
                    return content, result.returncode == 0
        # Fall back to stdout (structured output also goes to stdout)
        return result.stdout, result.returncode == 0
    except subprocess.TimeoutExpired:
        invoke_review.last_stderr = ""
        return TIMEOUT_SENTINEL, False
    except FileNotFoundError:
        invoke_review.last_stderr = ""
        return "", False
