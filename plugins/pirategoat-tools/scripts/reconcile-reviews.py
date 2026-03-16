#!/usr/bin/env python3
"""
Reconcile Reviews — Deterministic deduplication and merging of review findings.

Reads agent output JSON files from the review output directory, deduplicates
findings across agents, and produces a structured reconciliation file.

Usage:
    python3 reconcile-reviews.py \\
      --output-dir "/tmp/branch-review-feature-x" \\
      --agent-signals "pr-reviewer: STATUS=DISPATCH, security-reviewer: STATUS=DISPATCH"

Output:
    Writes reconciled-structured.json to the output directory.

Exit codes:
    0  Success
    1  Error (invalid arguments, I/O failure)

Zero external dependencies (stdlib only).
"""

import argparse
import glob
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple


# =============================================================================
# Import DOMAIN_CATALOG from review-scope.py (sibling script)
# =============================================================================

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REVIEW_SCOPE_PATH = os.path.join(_SCRIPTS_DIR, "review-scope.py")

import importlib.util as _importlib_util

_scope_spec = _importlib_util.spec_from_file_location("review_scope", _REVIEW_SCOPE_PATH)
_scope_mod = _importlib_util.module_from_spec(_scope_spec)
_scope_spec.loader.exec_module(_scope_mod)
DOMAIN_CATALOG = _scope_mod.DOMAIN_CATALOG


# =============================================================================
# Constants
# =============================================================================

# Domain classification for test gap detection
PRODUCTION_DOMAINS = [
    "code", "security", "performance", "architecture",
    "wp-architecture", "dead-code", "patterns", "a11y",
    "config-ops",
]
TEST_DOMAINS = ["php-tests", "js-tests", "e2e-tests", "go-tests"]

SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

CONFIDENCE_ORDER = {
    "confirmed": 0,
    "likely": 1,
    "possible": 2,
}

# Required fields for a valid finding
FINDING_REQUIRED_FIELDS = {"title", "file", "severity"}

# Line proximity threshold for near-dedup
LINE_PROXIMITY = 5

# Title similarity threshold for near-dedup (Jaccard on words)
TITLE_SIMILARITY_THRESHOLD = 0.7


# =============================================================================
# Similarity
# =============================================================================


def title_similarity(a: str, b: str) -> float:
    """Jaccard similarity on lowercased word sets.

    Returns 0.0 for empty strings, 1.0 for identical word sets.
    """
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


# =============================================================================
# Schema helpers
# =============================================================================


def _validate_agent_output(data: Any) -> bool:
    """Check that an agent output dict has the required structure."""
    if not isinstance(data, dict):
        return False
    if "issues" not in data:
        return False
    if not isinstance(data["issues"], list):
        return False
    return True


def _validate_finding(finding: dict) -> bool:
    """Check that a finding has the minimum required fields."""
    return FINDING_REQUIRED_FIELDS.issubset(finding.keys())


def _extract_reviewer_name(filename: str) -> str:
    """Extract the reviewer name from a filename like 'security-review.json'."""
    base = os.path.basename(filename)
    # Strip '-review.json' suffix
    if base.endswith("-review.json"):
        return base[: -len("-review.json")]
    return base


# =============================================================================
# Agent signals parsing
# =============================================================================


def _parse_skipped_agents(agent_signals: str) -> List[str]:
    """Parse agent signals string and return list of skipped agent names."""
    skipped = []
    if not agent_signals:
        return skipped

    # Match patterns like: "dead-code-reviewer: STATUS=SKIPPED (...)"
    # or "a11y-reviewer: STATUS=SKIPPED_TRIAGE (...)"
    pattern = re.compile(r"([\w-]+):\s*STATUS=SKIPPED(?:_TRIAGE)?")
    for match in pattern.finditer(agent_signals):
        skipped.append(match.group(1))

    return skipped


def discover_agent_signals(output_dir: str, dispatch_plan_path: str) -> str:
    """Build agent signal text from dispatch plan + review files on disk."""
    with open(dispatch_plan_path) as f:
        plan = json.load(f)

    signals = []
    for agent in plan.get("agents", []):
        name = agent["name"]
        status = agent.get("status", "SKIP")

        if status == "SKIP" or status == "SKIPPED" or status == "SKIPPED_TRIAGE":
            reason = agent.get("reason", "not in scope")
            signals.append(f"{name}: STATUS={status} ({reason})")
            continue

        # Check if the agent wrote a review file
        reviewer_base = name[: -len("-reviewer")] if name.endswith("-reviewer") else name
        review_json = os.path.join(output_dir, f"{reviewer_base}-review.json")
        if os.path.isfile(review_json):
            try:
                with open(review_json) as f:
                    review = json.load(f)
                # Extract severity counts from the review JSON
                issues = review.get("issues", [])
                counts = {}
                for finding in issues:
                    sev = finding.get("severity", "medium").lower()
                    counts[sev] = counts.get(sev, 0) + 1
                count_str = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
                verdict = review.get("verdict", "UNKNOWN")
                signals.append(f"{name}: STATUS=FINISHED, {count_str}, VERDICT={verdict}")
            except (json.JSONDecodeError, KeyError):
                signals.append(f"{name}: STATUS=FINISHED (output malformed)")
        else:
            signals.append(f"{name}: STATUS=NOT_RUN (no review file — agent was not dispatched)")

    signal_text = "\n".join(signals)

    # Write to file for the reconciliator agent prompt
    signal_path = os.path.join(output_dir, "agent-signals.txt")
    with open(signal_path, "w") as f:
        f.write(signal_text)

    return signal_text


# =============================================================================
# Test gap detection
# =============================================================================


def detect_test_gap(changed_files: list, domain_catalog: dict) -> Optional[dict]:
    """Detect if production code changed without corresponding test changes.

    Args:
        changed_files: List of changed file paths.
        domain_catalog: Domain catalog dict (from review-scope.py).

    Returns:
        Advisory dict if production files changed without tests, None otherwise.
    """
    if not changed_files:
        return None

    # Check if reliability domain exists in catalog
    all_production = list(PRODUCTION_DOMAINS)
    if "reliability" in domain_catalog:
        all_production.append("reliability")

    def _matches_domain(filepath: str, domain_name: str) -> bool:
        """Check if a file matches a domain's include pattern and not its exclude."""
        if domain_name not in domain_catalog:
            return False
        spec = domain_catalog[domain_name]
        if not re.search(spec["include"], filepath):
            return False
        if spec.get("exclude") and re.search(spec["exclude"], filepath):
            return False
        return True

    def _is_production_file(filepath: str) -> bool:
        return any(_matches_domain(filepath, d) for d in all_production)

    def _is_test_file(filepath: str) -> bool:
        return any(
            d in domain_catalog and re.search(domain_catalog[d]["include"], filepath)
            for d in TEST_DOMAINS
        )

    production_changed = any(_is_production_file(f) for f in changed_files)
    tests_changed = any(_is_test_file(f) for f in changed_files)

    if production_changed and not tests_changed:
        prod_files = [f for f in changed_files if _is_production_file(f)]
        return {
            "type": "advisory",
            "severity": "info",
            "title": "Production code changed without corresponding tests",
            "description": (
                f"{len(prod_files)} production file(s) changed but no test "
                f"files were modified."
            ),
            "production_files": prod_files,
        }

    return None


# =============================================================================
# Clustering
# =============================================================================


def _lines_overlap(line_a: Optional[int], line_b: Optional[int]) -> bool:
    """Check if two line numbers are within LINE_PROXIMITY of each other.

    If either line is None, they are considered overlapping (file-level match).
    """
    if line_a is None or line_b is None:
        return True
    return abs(line_a - line_b) <= LINE_PROXIMITY


def _should_merge(finding_a: dict, finding_b: dict) -> bool:
    """Determine if two findings should be merged into the same cluster.

    Criteria:
    1. Same file path
    2. Lines overlap (within LINE_PROXIMITY) or either is None
    3. Title similarity above TITLE_SIMILARITY_THRESHOLD
    """
    if finding_a["file"] != finding_b["file"]:
        return False
    if not _lines_overlap(finding_a.get("line"), finding_b.get("line")):
        return False
    sim = title_similarity(finding_a["title"], finding_b["title"])
    return sim >= TITLE_SIMILARITY_THRESHOLD


def _pick_canonical(findings: List[dict]) -> dict:
    """Compose the canonical finding from the best attributes in the cluster.

    Takes the best of each attribute across all findings:
    - severity: highest (lowest SEVERITY_ORDER value)
    - confidence: highest numeric value
    - description: longest text
    - Other fields (title, file, line, category): from the finding with highest severity
    """
    # Pick the primary finding (highest severity, then confidence, then description)
    def sort_key(f):
        sev = SEVERITY_ORDER.get(f.get("severity", "info"), 99)
        conf = -(f.get("confidence", 0.0) if isinstance(f.get("confidence"), (int, float)) else 0.0)
        desc_len = -(len(f.get("description", "")))
        return (sev, conf, desc_len)

    primary = min(findings, key=sort_key)

    # Compose: take highest severity, highest confidence, longest description,
    # longest recommendation
    best_severity = min(
        (f.get("severity", "info") for f in findings),
        key=lambda s: SEVERITY_ORDER.get(s, 99),
    )
    best_confidence = max(
        (f.get("confidence", 0.0) if isinstance(f.get("confidence"), (int, float)) else 0.0
         for f in findings),
    )
    best_description = max(
        (f.get("description", "") for f in findings),
        key=len,
    )
    best_recommendation = max(
        (f.get("recommendation", "") for f in findings),
        key=len,
    )

    # Build composed finding based on primary, overriding with best attributes
    result = dict(primary)
    result["severity"] = best_severity
    result["confidence"] = best_confidence
    result["description"] = best_description
    result["recommendation"] = best_recommendation
    return result


def _cluster_findings(all_findings: List[dict]) -> List[List[dict]]:
    """Group findings into clusters using union-find on merge criteria.

    Groups by file path first, then compares pairs within each file group.
    """
    # Group by file
    by_file: Dict[str, List[int]] = {}
    for i, f in enumerate(all_findings):
        fp = f["file"]
        by_file.setdefault(fp, []).append(i)

    # Union-Find
    parent = list(range(len(all_findings)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Compare pairs within each file group
    for indices in by_file.values():
        for i_idx in range(len(indices)):
            for j_idx in range(i_idx + 1, len(indices)):
                a, b = indices[i_idx], indices[j_idx]
                if _should_merge(all_findings[a], all_findings[b]):
                    union(a, b)

    # Collect clusters
    clusters_map: Dict[int, List[int]] = {}
    for i in range(len(all_findings)):
        root = find(i)
        clusters_map.setdefault(root, []).append(i)

    return [
        [all_findings[i] for i in indices]
        for indices in clusters_map.values()
    ]


# =============================================================================
# Main reconciliation
# =============================================================================


def reconcile(
    output_dir: str,
    agent_signals: str = "",
    write_output: bool = False,
    changed_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run the full reconciliation pipeline.

    Args:
        output_dir: Directory containing *-review.json agent output files.
        agent_signals: Raw agent signals string (for skipped agent detection).
        write_output: If True, write reconciled-structured.json to output_dir.
        changed_files: Optional list of changed file paths for test gap detection.

    Returns:
        Reconciliation result dict matching the output schema.
    """
    # 1. Discover agent output files
    pattern = os.path.join(output_dir, "*-review.json")
    review_files = sorted(glob.glob(pattern))

    # Filter out reconciled-structured.json and reconciled.json if present
    review_files = [
        f for f in review_files
        if os.path.basename(f) not in ("reconciled-structured.json", "reconciled.json")
    ]

    # 2. Parse skipped agents from signals
    skipped_agents = _parse_skipped_agents(agent_signals)

    # 3. Read and validate each agent output
    all_findings: List[dict] = []
    agent_finding_counts: Dict[str, int] = {}  # agent -> total findings loaded

    for filepath in review_files:
        reviewer_name = _extract_reviewer_name(filepath)

        try:
            with open(filepath) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            skipped_agents.append(reviewer_name)
            continue

        if not _validate_agent_output(data):
            skipped_agents.append(reviewer_name)
            continue

        # Extract valid findings
        count = 0
        for issue in data["issues"]:
            if not _validate_finding(issue):
                continue
            # Annotate with source
            finding = dict(issue)
            finding["_source_agent"] = reviewer_name
            finding["_source_file"] = os.path.basename(filepath)
            all_findings.append(finding)
            count += 1

        agent_finding_counts[reviewer_name] = count

    # 4. Cluster findings
    clusters_raw = _cluster_findings(all_findings)

    # 5. Build output clusters
    clusters_output = []
    severity_disagreements = []
    # Track which findings got merged (for agent stats)
    finding_merged: Dict[int, bool] = {}  # index in all_findings -> merged?

    for cluster_idx, cluster_findings in enumerate(clusters_raw):
        cluster_id = f"C{cluster_idx + 1}"

        # Build finding references: "reviewer-review:id"
        finding_refs = []
        for cf in cluster_findings:
            source_file = cf.get("_source_file", "unknown")
            # Strip .json from source file for reference
            source_base = source_file.replace(".json", "")
            fid = cf.get("id", "unknown")
            finding_refs.append(f"{source_base}:{fid}")

        # Check for severity disagreements
        severities = set(cf["severity"] for cf in cluster_findings)
        if len(severities) > 1 and len(cluster_findings) > 1:
            agents_in_cluster = list(set(cf["_source_agent"] for cf in cluster_findings))
            sev_by_agent = {
                cf["_source_agent"]: cf["severity"] for cf in cluster_findings
            }
            severity_disagreements.append({
                "cluster_id": cluster_id,
                "agents": sev_by_agent,
                "resolved_to": min(severities, key=lambda s: SEVERITY_ORDER.get(s, 99)),
            })

        # Pick canonical finding
        canonical_raw = _pick_canonical(cluster_findings)
        source_agents = sorted(set(cf["_source_agent"] for cf in cluster_findings))

        canonical = {
            "title": canonical_raw["title"],
            "file": canonical_raw["file"],
            "line": canonical_raw.get("line"),
            "severity": canonical_raw["severity"],
            "confidence": canonical_raw.get("confidence", 0.9),
            "source_agents": source_agents,
            "description": canonical_raw.get("description", ""),
            "recommendation": canonical_raw.get("recommendation", ""),
            "category": canonical_raw.get("category", "general"),
        }

        clusters_output.append({
            "cluster_id": cluster_id,
            "findings": finding_refs,
            "canonical": canonical,
        })

    # 6. Compute agent stats
    # For each agent, count: total findings, unique (in single-finding clusters),
    # duplicated (in multi-finding clusters)
    agent_stats: Dict[str, Dict[str, int]] = {}
    for agent_name, total in agent_finding_counts.items():
        unique = 0
        duplicated = 0
        for cluster_findings in clusters_raw:
            agent_findings_in_cluster = [
                f for f in cluster_findings if f["_source_agent"] == agent_name
            ]
            if not agent_findings_in_cluster:
                continue
            if len(cluster_findings) == 1:
                unique += len(agent_findings_in_cluster)
            else:
                duplicated += len(agent_findings_in_cluster)

        agent_stats[agent_name] = {
            "findings": total,
            "unique": unique,
            "duplicated": duplicated,
        }

    # 7. Deduplicate the skipped_agents list
    skipped_agents = sorted(set(skipped_agents))

    # 8. Build result
    result = {
        "total_findings": len(all_findings),
        "deduplicated_findings": len(clusters_output),
        "clusters": clusters_output,
        "severity_disagreements": severity_disagreements,
        "skipped_agents": skipped_agents,
        "agent_stats": agent_stats,
    }

    # 8.5. Flat issues list (for downstream consumers)
    result["issues"] = [c["canonical"] for c in clusters_output]

    # 8.6. Test gap detection (only when changed_files is provided)
    if changed_files is not None:
        advisories = []
        test_gap = detect_test_gap(changed_files, DOMAIN_CATALOG)
        if test_gap is not None:
            advisories.append(test_gap)
        result["advisories"] = advisories

    # 9. Write output file if requested
    if write_output:
        output_path = os.path.join(output_dir, "reconciled-structured.json")
        os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

    return result


# =============================================================================
# CLI entry point
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Reconcile review agent outputs: deduplicate, merge, and structure.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory containing *-review.json agent output files.",
    )
    parser.add_argument(
        "--agent-signals",
        default="",
        help=(
            "Single string or newline-joined text block of agent signals "
            "(pass it as one quoted shell argument)."
        ),
    )
    parser.add_argument(
        "--dispatch-plan",
        default=None,
        help=(
            "Path to dispatch-plan.json. When provided, agent signals are "
            "discovered from the dispatch plan + review files in --output-dir. "
            "Replaces --agent-signals."
        ),
    )
    parser.add_argument(
        "--changed-files",
        default=None,
        help="Comma-separated list of changed file paths for test gap detection.",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.output_dir):
        print(f"ERROR: Output directory does not exist: {args.output_dir}", file=sys.stderr)
        sys.exit(1)

    # Parse changed files from comma-separated string
    changed_files = None
    if args.changed_files:
        changed_files = [f.strip() for f in args.changed_files.split(",") if f.strip()]

    # Determine agent signals: --dispatch-plan takes precedence over --agent-signals
    if args.dispatch_plan:
        agent_signals = discover_agent_signals(args.output_dir, args.dispatch_plan)
    else:
        agent_signals = args.agent_signals

    result = reconcile(
        output_dir=args.output_dir,
        agent_signals=agent_signals,
        write_output=True,
        changed_files=changed_files,
    )

    # Print summary
    print(f"RECONCILIATION COMPLETE")
    print(f"Total findings: {result['total_findings']}")
    print(f"Deduplicated findings: {result['deduplicated_findings']}")
    print(f"Clusters: {len(result['clusters'])}")
    if result["severity_disagreements"]:
        print(f"Severity disagreements: {len(result['severity_disagreements'])}")
    if result["skipped_agents"]:
        print(f"Skipped agents: {', '.join(result['skipped_agents'])}")
    if result.get("advisories"):
        print(f"Advisories: {len(result['advisories'])}")
        for adv in result["advisories"]:
            print(f"  - [{adv['severity'].upper()}] {adv['title']}")
    print(f"Output: {os.path.join(args.output_dir, 'reconciled-structured.json')}")


if __name__ == "__main__":
    main()
