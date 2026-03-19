#!/usr/bin/env python3
"""
Bootstrap Reviewer — Single-command setup for all reviewer agents.

Consolidates plugin root discovery, protocol extraction, scope discovery,
and output instructions into one structured prompt block. Agents run this
script as their first action and get everything they need.

Usage:
    python3 bootstrap-reviewer.py --agent security-reviewer
    python3 bootstrap-reviewer.py --agent php-tests-reviewer --range main..feature
    python3 bootstrap-reviewer.py --agent patterns-reviewer --output-dir /tmp/pr-review-42

Exit codes:
    0  Success (scope may be OK or NO_DOMAIN_FILES)
    1  Error (plugin root not found, unknown agent, scope discovery failed)

Zero external dependencies (stdlib only).
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Import telemetry (sibling script, best-effort)
try:
    _telemetry_spec = importlib.util.spec_from_file_location(
        "review_telemetry",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "review-telemetry.py"),
    )
    _telemetry_mod = importlib.util.module_from_spec(_telemetry_spec)
    _telemetry_spec.loader.exec_module(_telemetry_mod)
    ReviewTelemetry = _telemetry_mod.ReviewTelemetry
except Exception:
    ReviewTelemetry = None

# =============================================================================
# Agent Configuration — loaded from agent-registry.json
# =============================================================================


def load_agent_config() -> Dict[str, dict]:
    """Load agent configuration from agent-registry.json.

    The registry is the single source of truth for agent configuration.
    Returns a dict keyed by agent name, compatible with the rest of this module.
    """
    registry_path = Path(__file__).parent / "agent-registry.json"
    with open(registry_path) as f:
        registry = json.load(f)
    return registry["agents"]


AGENT_CONFIG = load_agent_config()

# Maximum inline scope size before capping (in characters).
# Beyond this, the full scope is written to a file and only a summary is inlined.
# Prevents Claude Code's output persistence cascade for large PRs.
SCOPE_INLINE_CAP = 15 * 1024  # 15KB

# Sections to SKIP from reviewer-protocol.md.
# Everything else is included automatically (safe default for new sections).
# - Setup sections: bootstrap already performed these steps
# - Operational sections: bootstrap's OUTPUT INSTRUCTIONS provides concrete values
REVIEWER_PROTOCOL_SKIP_SECTIONS = [
    "## Step 0",            # Locate Plugin Root — bootstrap did this
    "## Scope Discovery",   # review-scope.py instructions — bootstrap did this
    "## Output Directory",  # bootstrap resolves to concrete OUTPUT_DIR
    "## ReviewOutputBuilder API",  # bootstrap provides pre-filled snippet
    "## File-Based Output", # bootstrap provides concrete file paths
]


def run_cmd(cmd: List[str], timeout: int = 30) -> Tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except FileNotFoundError:
        return 1, "", f"Command not found: {cmd[0]}"


def find_plugin_root() -> Optional[str]:
    """Find the pirategoat-tools plugin root directory."""
    # Method 1: cached value from hook
    cache_file = "/tmp/.pirategoat-tools-root"
    if os.path.isfile(cache_file):
        try:
            with open(cache_file) as f:
                root = f.read().strip()
            if root and os.path.isdir(root):
                return root
        except OSError:
            pass

    # Method 2: derive from own location (most reliable when running from plugin)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.dirname(script_dir)  # scripts/ -> plugin root
    if os.path.isfile(os.path.join(candidate, "scripts", "review-scope.py")):
        return candidate

    # Method 3: find command fallback
    rc, stdout, _ = run_cmd([
        "find", os.path.expanduser("~/.claude"),
        "-path", "*/pirategoat-tools/*/scripts/bootstrap-reviewer.py",
        "-type", "f",
    ])
    if rc == 0 and stdout:
        # Take the last (most recent version) path
        paths = stdout.strip().splitlines()
        if paths:
            # Sort for version ordering, take last
            paths.sort()
            script_path = paths[-1]
            return str(Path(script_path).parent.parent)

    return None


def read_file(path: str) -> Optional[str]:
    """Read a file and return its contents, or None on failure."""
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def extract_protocol_sections(content: str, skip_prefixes: List[str]) -> str:
    """Extract all sections from a markdown file EXCEPT those matching skip prefixes.

    Uses a skip-list so new sections added to the protocol are included automatically.
    Only setup/operational sections that the bootstrap replaces are skipped.
    Also strips the file's title heading (# level 1) since the bootstrap provides its own.
    """
    lines = content.splitlines()
    extracted = []
    skipping = False
    skip_level = 0
    in_code_fence = False

    for line in lines:
        # Track fenced code blocks — don't parse headings inside them
        if line.startswith("```"):
            in_code_fence = not in_code_fence
            if not skipping:
                extracted.append(line)
            continue

        if in_code_fence:
            if not skipping:
                extracted.append(line)
            continue

        # Check if this line is a markdown heading
        heading_match = re.match(r'^(#{1,6})\s', line)
        if heading_match:
            level = len(heading_match.group(1))
            stripped = line.strip()

            # Skip the file title (# level 1)
            if level == 1:
                continue

            # Check if this heading should be skipped
            should_skip = any(stripped.startswith(prefix) for prefix in skip_prefixes)

            if should_skip:
                skipping = True
                skip_level = level
                continue

            # If we were skipping and hit a heading of same/higher level, stop skipping
            if skipping and level <= skip_level:
                skipping = False

        if not skipping:
            extracted.append(line)

    return "\n".join(extracted).strip()


def run_scope_discovery(
    plugin_root: str,
    domain: str,
    extra_flags: List[str],
    git_range: Optional[str],
    output_dir: Optional[str] = None,
) -> Tuple[int, str]:
    """Run review-scope.py and return (exit_code, output)."""
    script = os.path.join(plugin_root, "scripts", "review-scope.py")
    if not os.path.isfile(script):
        return 1, f"ERROR: review-scope.py not found at {script}"

    cmd = [sys.executable, script, "--domain", domain] + extra_flags
    if git_range:
        cmd.extend(["--range", git_range])
    if output_dir:
        cmd.extend(["--output-dir", output_dir])

    rc, stdout, stderr = run_cmd(cmd, timeout=60)
    # Script outputs to stdout for agent consumption
    output = stdout if stdout else stderr
    return rc, output


def derive_reviewer_name(agent_name: str) -> str:
    """Derive the reviewer output name from agent name.

    Removes '-reviewer' suffix for output file naming.
    e.g. 'security-reviewer' -> 'security', 'pr-reviewer' -> 'pr'
    """
    if agent_name.endswith("-reviewer"):
        return agent_name[: -len("-reviewer")]
    return agent_name


def extract_pr_number(scope_output: str) -> Optional[str]:
    """Extract PR_NUMBER from scope discovery output."""
    match = re.search(r"PR_NUMBER:\s*(\d+)", scope_output)
    return match.group(1) if match else None


def extract_output_dir(scope_output: str) -> Optional[str]:
    """Extract OUTPUT_DIR from scope discovery output."""
    match = re.search(r"OUTPUT_DIR:\s*(.+)", scope_output)
    return match.group(1).strip() if match else None


def extract_status(scope_output: str) -> Optional[str]:
    """Extract STATUS from scope discovery output."""
    match = re.search(r"STATUS:\s*(\S+)", scope_output)
    return match.group(1).strip() if match else None


def get_file_history(files: List[str], max_commits: int = 15) -> str:
    """Get recent commit history for each changed file.

    Returns structured text with last N commits per file.
    Fast: one git log per file, limited output.
    File paths from scope output are git-root-relative, so we use
    git -C <root> to ensure correct path resolution.
    """
    if not files:
        return ""

    # Detect git root so paths resolve correctly regardless of cwd
    rc, git_root, _ = run_cmd(["git", "rev-parse", "--show-toplevel"])
    if rc != 0 or not git_root:
        return ""

    lines = [f"=== FILE HISTORY ==="]
    lines.append(f"Last {max_commits} commits per changed file:")
    lines.append("")

    for filepath in files[:20]:  # Cap at 20 files to avoid runaway
        rc, stdout, _ = run_cmd(
            ["git", "-C", git_root, "log", "--oneline", "--follow",
             "--since=12 months ago", "--", filepath],
            timeout=10,
        )
        if rc == 0 and stdout:
            # Limit to max_commits lines
            commit_lines = stdout.strip().splitlines()[:max_commits]
            lines.append(f"--- {filepath} ---")
            for cl in commit_lines:
                lines.append(cl)
            lines.append("")

    if len(lines) <= 3:
        # No history found for any file
        return ""

    return "\n".join(lines)


def extract_scope_files(scope_output: str) -> List[str]:
    """Extract file paths from the === FILES === section of scope output."""
    files = []
    in_files = False
    for line in scope_output.splitlines():
        if line.startswith("=== FILES ==="):
            in_files = True
            continue
        if in_files and line.startswith("==="):
            break
        if in_files and line.strip():
            # File line format: "path/to/file  (+N -M)"
            file_path = line.split("  ")[0].strip()
            if file_path:
                files.append(file_path)
    return files


def extract_scope_line_count(scope_output: str) -> int:
    """Extract total changed lines from the === FILES === section.

    Parses (+N -M) stats per file and sums additions + deletions.
    """
    total = 0
    in_files = False
    for line in scope_output.splitlines():
        if line.startswith("=== FILES ==="):
            in_files = True
            continue
        if in_files and line.startswith("==="):
            break
        if in_files and line.strip():
            # Parse "(+N -M)" from "path/to/file  (+N -M)"
            match = re.search(r'\(\+(\d+)\s+-(\d+)\)', line)
            if match:
                total += int(match.group(1)) + int(match.group(2))
    return total


def compute_review_budget(changed_lines: int, file_count: int) -> int:
    """Compute a tool call budget proportionate to PR scope.

    Formula: base 15 + 1 call per 10 changed lines, capped at 80.
    The budget is a calibration hint, not a hard cap.
    """
    budget = 15 + (changed_lines // 10)
    budget = max(budget, 15)  # minimum viable budget
    budget = min(budget, 80)  # cap for even the largest PRs
    return budget


def load_pr_intent(output_dir: str) -> Optional[str]:
    """Load PR intent from review-context.json in the output directory.

    Extracts PR title, body, and linked issues to build a concise intent
    block that helps specialist reviewers calibrate severity.

    Returns formatted intent string, or None if no context is available.
    """
    ctx_path = os.path.join(output_dir, "review-context.json")
    if not os.path.isfile(ctx_path):
        return None

    try:
        with open(ctx_path) as f:
            ctx = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    pr = ctx.get("pr", {})
    title = pr.get("title", "").strip()
    body = pr.get("body", "").strip()
    author = pr.get("author", "").strip()
    linked_issues = ctx.get("linked_issues", [])

    # Need at least a title to be useful
    if not title:
        return None

    parts = []
    parts.append(f"PR Title: {title}")
    if author:
        parts.append(f"PR Author: {author}")
    if body:
        # Truncate long bodies to keep the intent section concise
        if len(body) > 500:
            body = body[:500] + "..."
        parts.append(f"PR Description: {body}")
    if linked_issues:
        parts.append(f"Linked Issues: {', '.join(linked_issues)}")

    return "\n".join(parts)


def load_pr_number_from_context(output_dir: str) -> Optional[str]:
    """Load PR number from review-context.json in the output directory.

    Returns PR number as a string, or None if unavailable.
    """
    ctx_path = os.path.join(output_dir, "review-context.json")
    if not os.path.isfile(ctx_path):
        return None
    try:
        with open(ctx_path) as f:
            ctx = json.load(f)
        number = ctx.get("pr", {}).get("number")
        return str(number) if number else None
    except (json.JSONDecodeError, OSError):
        return None


def load_change_purpose(output_dir: str) -> Optional[str]:
    """Load the main session's change-purpose synthesis from the output directory.

    change-purpose.md is written by the main session at step 3/4 as a distilled
    summary of what changed, why, and what to focus on during review. It provides
    richer context than the raw PR metadata in review-context.json.

    Returns the file content stripped, or None if not available.
    """
    cp_path = os.path.join(output_dir, "change-purpose.md")
    if not os.path.isfile(cp_path):
        return None

    try:
        with open(cp_path) as f:
            content = f.read().strip()
        return content if content else None
    except OSError:
        return None


def build_output(
    agent_name: str,
    plugin_root: str,
    status: str,
    review_rules: str,
    domain_rules: Optional[str],
    scope_output: str,
    exploration_scope: Optional[str],
    output_dir: str,
    pr_number: Optional[str],
    reviewer_name: str,
    file_history: Optional[str] = None,
    pr_intent: Optional[str] = None,
    change_purpose: Optional[str] = None,
    review_budget: Optional[int] = None,
) -> str:
    """Build the structured bootstrap output block."""
    lines = []

    # Header
    lines.append(f"=== BOOTSTRAP: {agent_name} ===")
    lines.append(f"PLUGIN_ROOT: {plugin_root}")
    lines.append(f"STATUS: {status}")
    lines.append("")

    # Section 1: Review Rules (top position — primacy effect)
    lines.append("--- Section 1: REVIEW RULES (behavioral steering) ---")
    lines.append("")
    lines.append("=== REVIEW RULES ===")
    lines.append(review_rules)
    lines.append("")

    if domain_rules:
        lines.append("=== DOMAIN RULES ===")
        lines.append(domain_rules)
        lines.append("")

    # PR Intent — injected between rules and content so reviewers
    # understand the PR's purpose before reading the diff.
    if pr_intent:
        lines.append("=== PR INTENT ===")
        lines.append("Use this context to calibrate severity — issues on the PR's")
        lines.append("critical path deserve higher severity than issues on")
        lines.append("tangentially touched code.")
        lines.append("")
        lines.append(pr_intent)
        lines.append("")

    # Review Focus — the main session's distilled understanding of the change.
    # Supplements PR INTENT (author's raw metadata) with richer synthesis:
    # what changed, why, and what to focus on during review.
    if change_purpose:
        lines.append("=== REVIEW FOCUS (pipeline synthesis) ===")
        lines.append("The review pipeline analyzed the full PR context and produced")
        lines.append("this summary. Use it alongside PR INTENT to focus your review.")
        lines.append("")
        lines.append(change_purpose)
        lines.append("")

    # Review Budget — scope-proportionate tool call calibration
    if review_budget is not None:
        lines.append("=== REVIEW BUDGET ===")
        lines.append(f"Target: ~{review_budget} tool calls for this review.")
        lines.append("")
        lines.append("This budget is calibrated to the PR's size. It matters because:")
        lines.append("- **You are on the critical path.** Other agents may finish faster;")
        lines.append("  the pipeline waits for the slowest agent before reconciliation.")
        lines.append("- **Diminishing returns are real.** After the first 15-20 calls on a")
        lines.append("  small PR, each additional call is less likely to surface new findings.")
        lines.append("- **Depth should match complexity.** A thorough review of a simple change")
        lines.append("  is not more valuable — it just takes longer.")
        lines.append("")
        lines.append("If you hit the budget without findings, wrap up. If you're on a genuine")
        lines.append("lead, continue — but check: am I exploring new territory or recycling")
        lines.append("the same searches?")
        lines.append("")

    # Section 2: Review Content (middle position — processing zone)
    lines.append("--- Section 2: REVIEW CONTENT (what to review) ---")
    lines.append("")
    lines.append("=== REVIEW SCOPE ===")

    if len(scope_output) > SCOPE_INLINE_CAP:
        # Write full scope to file to avoid output persistence cascade
        os.makedirs(output_dir, exist_ok=True)
        scope_file = os.path.join(output_dir, "scoped-diff.patch")
        with open(scope_file, 'w') as f:
            f.write(
                "# WARNING: When you read this file, the Read tool adds display line numbers\n"
                "# (e.g., 227→...). These are line numbers WITHIN THIS PATCH FILE, NOT source\n"
                "# file line numbers. For add_issue(line=...), use the source file line numbers\n"
                "# from the @@ hunk headers (e.g., @@ -0,0 +1,116 @@ means source starts at line 1).\n"
                "#\n"
            )
            f.write(scope_output)
        # Show first ~200 lines inline, capped at SCOPE_INLINE_CAP characters
        scope_lines = scope_output.splitlines()
        truncated_lines = []
        char_count = 0
        for sl in scope_lines[:200]:
            if char_count + len(sl) > SCOPE_INLINE_CAP:
                break
            truncated_lines.append(sl)
            char_count += len(sl) + 1  # +1 for newline
        lines.append("\n".join(truncated_lines))
        lines.append("")
        # Estimate tokens (~4 chars/token) for Read tool limit guidance
        total_lines = len(scope_lines)
        estimated_tokens = len(scope_output) // 4
        lines.append(f"... SCOPE TRUNCATED ({total_lines} total lines, ~{estimated_tokens:,} tokens) ...")
        lines.append(f"Full scope written to: {scope_file}")
        if estimated_tokens > 20000:
            lines.append(
                f"WARNING: This file (~{estimated_tokens:,} tokens) will EXCEED the Read tool's 25,000 token limit."
            )
            lines.append(
                f"You MUST read it in chunks: use offset/limit (e.g., offset=0 limit=300, then offset=300 limit=300)."
            )
            lines.append(
                "Interleave diff chunks with source file reads to keep context adjacent."
            )
        else:
            lines.append("Read it with offset/limit parameters (e.g., offset=200, limit=200) to avoid re-truncation.")
    else:
        lines.append(scope_output)

    lines.append("")

    if exploration_scope:
        lines.append("=== EXPLORATION SCOPE ===")
        lines.append(exploration_scope)
        lines.append("")

    if file_history:
        lines.append(file_history)
        lines.append("")

    # Inject DYNAMIC_DISPATCH_RISK for dead-code-reviewer
    if agent_name == "dead-code-reviewer":
        # Check if any PHP files are in the scope
        has_php = any(
            line.strip().split("  ")[0].strip().endswith(".php")
            for line in scope_output.splitlines()
            if line.strip() and not line.startswith("===")
        )
        risk = "high (PHP files in scope — check for hooks, filters, callbacks)" if has_php else "low (0 PHP files in scope — skip Step 0)"
        lines.append(f"DYNAMIC_DISPATCH_RISK: {risk}")
        lines.append("")

    # Section 3: Output Instructions (bottom position — recency effect)
    lines.append("--- Section 3: OUTPUT INSTRUCTIONS (operational) ---")
    lines.append("")
    lines.append("=== OUTPUT INSTRUCTIONS ===")
    lines.append(f"OUTPUT_DIR: {output_dir}")
    lines.append(f"REVIEWER_NAME: {reviewer_name}")
    lines.append("OUTPUT_FILES:")
    lines.append(f"  - {output_dir}/{reviewer_name}-review.json")
    lines.append(f"  - {output_dir}/{reviewer_name}-review.md")
    lines.append("")
    lines.append("ReviewOutputBuilder:")
    lines.append("  import sys, os")
    lines.append(f"  sys.path.insert(0, '{plugin_root}/scripts')")
    lines.append("  from review_output_simple import ReviewOutputBuilder")
    pr_id_str = pr_number if pr_number else "0"
    lines.append(
        f'  builder = ReviewOutputBuilder(pr_id={pr_id_str}, reviewer="{reviewer_name}")'
    )
    lines.append(f'  builder.add_issue(severity="high", title="Issue title", file="path/to/file.py",')
    lines.append(f'      description="What is wrong", recommendation="How to fix",')
    lines.append(f'      category="category-name", line=42, confidence=0.9)')
    lines.append(f"")
    lines.append(f"  IMPORTANT: line= must be the SOURCE FILE line number, not the patch file line number.")
    lines.append(f"  If you read a diff/patch file, the Read tool's display line numbers (e.g., 227→) are")
    lines.append(f"  positions within the patch, not the source. Use @@ hunk headers to find source lines.")
    lines.append(f'  builder.add_positive("Positive observation text")')
    lines.append(f'  builder.set_files_reviewed(N)')
    lines.append(f'  builder.set_confidence(0.85)')
    lines.append(f'  result = builder.save("{output_dir}")  # returns {{"json": path, "markdown": path}}')
    lines.append(f"")
    lines.append(f"  IMPORTANT: save() confirms success via its return value.")
    lines.append(f"  Do NOT read the output files back to verify — proceed directly to the STATUS signal.")
    lines.append("")
    lines.append("Return signal format:")
    lines.append("  STATUS: FINISHED")
    lines.append(f"  OUTPUT_FILES:")
    lines.append(f"    - {output_dir}/{reviewer_name}-review.json")
    lines.append(f"    - {output_dir}/{reviewer_name}-review.md")
    lines.append("  COUNTS: critical: N, high: N, medium: N")
    lines.append("  VERDICT: <APPROVE|COMMENT|REQUEST_CHANGES|BLOCK>")
    lines.append("  SUMMARY: <one sentence>")
    lines.append("")
    lines.append(f"PLUGIN_ROOT: {plugin_root}")
    lines.append(
        f"  (for manual reads: $PLUGIN_ROOT/scripts/review-scope.py,"
    )
    lines.append(f"   $PLUGIN_ROOT/skills/*/references/*.md)")

    return "\n".join(lines)


def build_error_output(agent_name: str, error_msg: str, plugin_root: str = "UNKNOWN") -> str:
    """Build structured error output."""
    return (
        f"=== BOOTSTRAP: {agent_name} ===\n"
        f"PLUGIN_ROOT: {plugin_root}\n"
        f"STATUS: ERROR\n"
        f"\n"
        f"ERROR: {error_msg}\n"
        f"ACTION: Report this error to the caller. Do NOT proceed with review.\n"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap Reviewer — single-command setup for reviewer agents.",
    )
    parser.add_argument(
        "--agent",
        required=True,
        help=f"Agent name. Available: {', '.join(sorted(AGENT_CONFIG.keys()))}",
    )
    parser.add_argument(
        "--range",
        default=None,
        help="Git range for scope discovery (e.g., 'main..HEAD'). Auto-detected if omitted.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Override output directory. Auto-detected if omitted.",
    )
    args = parser.parse_args()

    # Step 1: Validate agent name
    if args.agent not in AGENT_CONFIG:
        print(build_error_output(
            args.agent,
            f"Unknown agent '{args.agent}'. "
            f"Available: {', '.join(sorted(AGENT_CONFIG.keys()))}",
        ))
        sys.exit(1)

    config = AGENT_CONFIG[args.agent]

    # Step 2: Find plugin root
    plugin_root = find_plugin_root()
    if not plugin_root:
        print(build_error_output(
            args.agent,
            "Could not find pirategoat-tools plugin root. "
            "Ensure the plugin is installed or /tmp/.pirategoat-tools-root is set.",
        ))
        sys.exit(1)

    # Step 3: Read and extract protocol rules
    protocol_path = os.path.join(
        plugin_root, "agents", "shared", "reviewer-protocol.md"
    )
    protocol_content = read_file(protocol_path)
    if not protocol_content:
        print(build_error_output(
            args.agent,
            f"Could not read reviewer protocol at {protocol_path}",
            plugin_root,
        ))
        sys.exit(1)

    review_rules = extract_protocol_sections(
        protocol_content, REVIEWER_PROTOCOL_SKIP_SECTIONS
    )

    # Read domain-specific protocol for test agents
    domain_rules = None
    if "tests-reviewer" in config["protocols"]:
        tests_protocol_path = os.path.join(
            plugin_root, "agents", "shared", "tests-reviewer-protocol.md"
        )
        tests_content = read_file(tests_protocol_path)
        if tests_content:
            # Strip YAML frontmatter if present
            if tests_content.startswith("---"):
                end = tests_content.find("---", 3)
                if end != -1:
                    tests_content = tests_content[end + 3 :].strip()
            domain_rules = tests_content

    # Step 4: Run scope discovery (skip for agents with no domain)
    scope_output = ""
    scope_status = "OK"
    output_dir = args.output_dir or "/tmp"
    pr_number = None
    exploration_scope = None

    if config["domain"] is not None:
        # Run primary scope discovery
        scope_flags = list(config.get("scope_flags", []))
        if config.get("no_semantic_filter", False):
            scope_flags.append("--no-semantic-filter")
        rc, scope_output = run_scope_discovery(
            plugin_root, config["domain"], scope_flags, args.range,
            output_dir=args.output_dir,
        )

        if rc != 0 and rc != 2:
            # rc=2 means no changes, which is still structured output
            scope_status = "ERROR"

        # Parse status, output_dir, pr_number from scope output
        parsed_status = extract_status(scope_output)
        if parsed_status:
            scope_status = parsed_status

        parsed_dir = extract_output_dir(scope_output)
        if parsed_dir and not args.output_dir:
            output_dir = parsed_dir

        pr_number = extract_pr_number(scope_output)

        # Fallback: read PR number from review-context.json
        if not pr_number:
            pr_number = load_pr_number_from_context(output_dir)

        # Run extra scope for patterns-reviewer (exploration scope)
        if "extra_scope" in config:
            extra_flags = config["extra_scope"]
            _, exploration_scope = run_scope_discovery(
                plugin_root, config["domain"], extra_flags, args.range,
                output_dir=args.output_dir,
            )

        # Run secondary domain scope discovery (e.g., config-ops for security/architecture)
        for sec_domain in config.get("secondary_domains", []):
            sec_flags = list(config.get("scope_flags", []))
            if config.get("no_semantic_filter", False):
                sec_flags.append("--no-semantic-filter")
            sec_rc, sec_output = run_scope_discovery(
                plugin_root, sec_domain, sec_flags, args.range,
                output_dir=args.output_dir,
            )
            sec_status = extract_status(sec_output)
            if sec_status and sec_status == "OK":
                scope_output += f"\n\n=== SECONDARY SCOPE: {sec_domain} ===\n"
                scope_output += sec_output
    else:
        # No domain (tests-mutation-reviewer) — detect output dir manually
        scope_output = "(No scope discovery — this agent does not use domain-based scope)"
        # Still try to detect PR number and output dir
        detect_script = os.path.join(plugin_root, "scripts", "review-scope.py")
        if os.path.isfile(detect_script):
            # Use a dummy domain just to get output dir detection, but we won't use the scope
            # Instead, just detect PR number via gh/ghe directly
            pass

        # Try gh/ghe for PR number
        for cli in ["gh", "ghe"]:
            rc, stdout, _ = run_cmd(
                [cli, "pr", "view", "--json", "number", "-q", ".number"]
            )
            if rc == 0 and stdout and stdout.isdigit():
                pr_number = stdout
                if not args.output_dir:
                    output_dir = f"/tmp/pr-review-{pr_number}"
                    os.makedirs(output_dir, exist_ok=True)
                break

        # Fallback: read PR number from review-context.json
        if not pr_number:
            pr_number = load_pr_number_from_context(output_dir)

    # Apply output dir override
    if args.output_dir:
        output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    # Write started marker — check-reviewer-agent-status.py uses this
    # to distinguish RUNNING from NOT_DISPATCHED
    started_path = os.path.join(output_dir, f"{args.agent}.started")
    with open(started_path, "w") as f:
        from datetime import datetime, timezone
        f.write(datetime.now(timezone.utc).isoformat())

    # Telemetry: log agent start (best-effort)
    if ReviewTelemetry is not None:
        try:
            _scope_files = extract_scope_files(scope_output) if scope_output else []
            _scope_lines = extract_scope_line_count(scope_output) if scope_output else 0
            _t = ReviewTelemetry(output_dir)
            _t.log_agent_start(
                agent_name=args.agent,
                domain=config.get("domain", ""),
                model_tier=config.get("model_tier", ""),
                scope_files=len(_scope_files),
                scope_lines=_scope_lines,
            )
        except Exception:
            pass

    # Compute review budget from scope metrics
    scope_files_for_budget = extract_scope_files(scope_output) if scope_output else []
    scope_lines_for_budget = extract_scope_line_count(scope_output) if scope_output else 0
    review_budget = compute_review_budget(scope_lines_for_budget, len(scope_files_for_budget))

    # Compute file history for agents that request it
    file_history_output = None
    if config.get("file_history") and scope_output:
        file_lines = extract_scope_files(scope_output)
        if file_lines:
            max_commits = config.get("max_history_commits", 15)
            file_history_output = get_file_history(file_lines, max_commits=max_commits)

    # Step 5: Build and output the structured block
    reviewer_name = derive_reviewer_name(args.agent)

    # Load PR intent from review-context.json (if available)
    pr_intent = load_pr_intent(output_dir)

    # Load change-purpose.md (main session's distilled synthesis, if available)
    change_purpose = load_change_purpose(output_dir)

    # Determine overall status
    overall_status = scope_status
    if config["domain"] is None:
        overall_status = "OK"

    output = build_output(
        agent_name=args.agent,
        plugin_root=plugin_root,
        status=overall_status,
        review_rules=review_rules,
        domain_rules=domain_rules,
        scope_output=scope_output,
        exploration_scope=exploration_scope,
        output_dir=output_dir,
        pr_number=pr_number,
        reviewer_name=reviewer_name,
        file_history=file_history_output,
        pr_intent=pr_intent,
        change_purpose=change_purpose,
        review_budget=review_budget,
    )

    print(output)

    # Exit code: 0 for success (including NO_DOMAIN_FILES), 1 for errors
    if overall_status == "ERROR":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
