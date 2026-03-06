"""Performance benchmark for the yoloing-safe PreToolUse safety hook.

Measures wall-clock and in-process timing across a representative mix of
tool calls. Uses YOLOING_SAFE_PROFILE=1 to collect phase breakpoints from
the hook's stderr output.

Usage:
    # Run benchmark with summary report
    python3 plugins/yoloing-safe/tests/benchmark_hook.py

    # Run as pytest (includes regression assertion)
    pytest plugins/yoloing-safe/tests/benchmark_hook.py -v

    # Customize iterations (default: 100)
    python3 plugins/yoloing-safe/tests/benchmark_hook.py --iterations 200
"""

import json
import os
import re
import subprocess
import statistics
import sys
import time
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "pre-tool-use-safety.py")
SCENARIOS_FILE = Path(__file__).resolve().parent / "scenarios" / "benchmark.json"

# Regression threshold: median wall-clock per call must stay under this.
# Set conservatively high — the goal is catching regressions (e.g., accidental
# O(n^2) or new blocking I/O), not micro-benchmarking. Adjust based on baseline.
WALL_CLOCK_MEDIAN_THRESHOLD_MS = 80.0

# In-process threshold: median in-process time (rules + config, excludes Python
# startup). Much tighter since it measures only our code.
IN_PROCESS_MEDIAN_THRESHOLD_MS = 10.0

_PROFILE_RE = re.compile(r"^\[yoloing-safe:profile\] (\S+) ([\d.]+)ms$")


def load_benchmark_scenarios():
    """Load and expand weighted scenarios into a flat call list."""
    with open(SCENARIOS_FILE) as f:
        data = json.load(f)
    calls = []
    for s in data["scenarios"]:
        for _ in range(s["weight"]):
            calls.append({
                "tool_name": s["tool_name"],
                "tool_input": s["tool_input"],
                "label": s["label"],
            })
    return calls


def parse_profile(stderr_text):
    """Parse profile marks from stderr into a dict of label -> ms."""
    marks = {}
    for line in stderr_text.splitlines():
        m = _PROFILE_RE.match(line)
        if m:
            marks[m.group(1)] = float(m.group(2))
    return marks


def run_one(scenario):
    """Run the hook once and return (wall_ms, profile_marks, exit_code)."""
    payload = json.dumps({
        "tool_name": scenario["tool_name"],
        "tool_input": scenario["tool_input"],
    })
    env = os.environ.copy()
    env["YOLOING_SAFE_PROFILE"] = "1"
    # Point config at a nonexistent path to avoid user config variance
    env["YOLOING_SAFE_CONFIG_PATH"] = "/tmp/yoloing-safe-benchmark-nonexistent.json"

    t0 = time.monotonic()
    result = subprocess.run(
        ["python3", SCRIPT],
        input=payload, capture_output=True, text=True, timeout=5, env=env,
    )
    wall_ms = (time.monotonic() - t0) * 1000

    marks = parse_profile(result.stderr)
    return wall_ms, marks, result.returncode


def run_benchmark(iterations=100):
    """Run the full benchmark and return structured results."""
    calls = load_benchmark_scenarios()
    # Ensure reproducible ordering
    total_weight = len(calls)

    results = []
    for i in range(iterations):
        scenario = calls[i % total_weight]
        wall_ms, marks, exit_code = run_one(scenario)
        results.append({
            "iteration": i,
            "label": scenario["label"],
            "tool_name": scenario["tool_name"],
            "wall_ms": wall_ms,
            "in_process_ms": marks.get("exit", 0),
            "rules_ms": marks.get("rules_done", 0) - marks.get("rules_start", 0)
                if "rules_done" in marks and "rules_start" in marks else 0,
            "config_ms": marks.get("config_loaded", 0) - marks.get("stdin_parsed", 0)
                if "config_loaded" in marks and "stdin_parsed" in marks else 0,
            "exit_code": exit_code,
            "marks": marks,
        })

    return results, total_weight


def compute_stats(results):
    """Compute summary statistics from benchmark results."""
    wall_times = [r["wall_ms"] for r in results]
    in_process_times = [r["in_process_ms"] for r in results]
    rules_times = [r["rules_ms"] for r in results]

    # Per-tool breakdown
    by_tool = {}
    for r in results:
        tool = r["tool_name"]
        by_tool.setdefault(tool, []).append(r)

    # Per-label breakdown (top 5 by count)
    by_label = {}
    for r in results:
        by_label.setdefault(r["label"], []).append(r)

    stats = {
        "iterations": len(results),
        "wall_ms": {
            "median": statistics.median(wall_times),
            "mean": statistics.mean(wall_times),
            "p95": sorted(wall_times)[int(len(wall_times) * 0.95)],
            "min": min(wall_times),
            "max": max(wall_times),
        },
        "in_process_ms": {
            "median": statistics.median(in_process_times),
            "mean": statistics.mean(in_process_times),
            "p95": sorted(in_process_times)[int(len(in_process_times) * 0.95)],
        },
        "rules_ms": {
            "median": statistics.median(rules_times),
            "mean": statistics.mean(rules_times),
            "p95": sorted(rules_times)[int(len(rules_times) * 0.95)],
        },
        "by_tool": {},
    }

    for tool, tool_results in sorted(by_tool.items()):
        tw = [r["wall_ms"] for r in tool_results]
        tip = [r["in_process_ms"] for r in tool_results]
        tr = [r["rules_ms"] for r in tool_results]
        stats["by_tool"][tool] = {
            "count": len(tool_results),
            "wall_median_ms": statistics.median(tw),
            "in_process_median_ms": statistics.median(tip),
            "rules_median_ms": statistics.median(tr),
        }

    return stats


def print_report(stats):
    """Print a human-readable benchmark report."""
    print("\n" + "=" * 65)
    print("  yoloing-safe Performance Benchmark")
    print("=" * 65)

    print(f"\n  Iterations: {stats['iterations']}")

    print(f"\n  Wall-clock (subprocess startup + in-process):")
    w = stats["wall_ms"]
    print(f"    median: {w['median']:.1f}ms  mean: {w['mean']:.1f}ms  "
          f"p95: {w['p95']:.1f}ms  min: {w['min']:.1f}ms  max: {w['max']:.1f}ms")

    print(f"\n  In-process (our code only, excludes Python startup):")
    ip = stats["in_process_ms"]
    print(f"    median: {ip['median']:.3f}ms  mean: {ip['mean']:.3f}ms  p95: {ip['p95']:.3f}ms")

    print(f"\n  Rule evaluation (allowlist + rule registry scan):")
    r = stats["rules_ms"]
    print(f"    median: {r['median']:.3f}ms  mean: {r['mean']:.3f}ms  p95: {r['p95']:.3f}ms")

    startup_ms = stats["wall_ms"]["median"] - stats["in_process_ms"]["median"]
    total = stats["wall_ms"]["median"]
    if total > 0:
        pct_startup = (startup_ms / total) * 100
        pct_inprocess = (stats["in_process_ms"]["median"] / total) * 100
        print(f"\n  Time budget breakdown (median):")
        print(f"    Python startup: ~{startup_ms:.1f}ms ({pct_startup:.0f}%)")
        print(f"    In-process:      {stats['in_process_ms']['median']:.3f}ms ({pct_inprocess:.1f}%)")

    print(f"\n  By tool:")
    print(f"    {'Tool':<8} {'Count':>5}  {'Wall (med)':>10}  {'In-proc (med)':>13}  {'Rules (med)':>11}")
    print(f"    {'─' * 8} {'─' * 5}  {'─' * 10}  {'─' * 13}  {'─' * 11}")
    for tool, ts in stats["by_tool"].items():
        print(f"    {tool:<8} {ts['count']:>5}  {ts['wall_median_ms']:>9.1f}ms"
              f"  {ts['in_process_median_ms']:>12.3f}ms  {ts['rules_median_ms']:>10.3f}ms")

    print(f"\n  Thresholds:")
    print(f"    Wall-clock median:  {stats['wall_ms']['median']:.1f}ms "
          f"(limit: {WALL_CLOCK_MEDIAN_THRESHOLD_MS}ms) "
          f"{'PASS' if stats['wall_ms']['median'] <= WALL_CLOCK_MEDIAN_THRESHOLD_MS else 'FAIL'}")
    print(f"    In-process median:  {stats['in_process_ms']['median']:.3f}ms "
          f"(limit: {IN_PROCESS_MEDIAN_THRESHOLD_MS}ms) "
          f"{'PASS' if stats['in_process_ms']['median'] <= IN_PROCESS_MEDIAN_THRESHOLD_MS else 'FAIL'}")

    print("\n" + "=" * 65 + "\n")


# ---------------------------------------------------------------------------
# Pytest integration
# ---------------------------------------------------------------------------

def test_performance_regression():
    """Assert hook performance stays within acceptable bounds.

    This test catches regressions like accidental O(n^2) loops, blocking I/O,
    or expensive new imports. It does NOT catch micro-optimizations — the
    thresholds are deliberately generous.

    Run: pytest plugins/yoloing-safe/tests/benchmark_hook.py -v
    """
    results, _ = run_benchmark(iterations=50)
    stats = compute_stats(results)

    wall_median = stats["wall_ms"]["median"]
    inproc_median = stats["in_process_ms"]["median"]

    assert wall_median <= WALL_CLOCK_MEDIAN_THRESHOLD_MS, (
        f"Wall-clock median {wall_median:.1f}ms exceeds threshold "
        f"{WALL_CLOCK_MEDIAN_THRESHOLD_MS}ms — check for regressions"
    )
    assert inproc_median <= IN_PROCESS_MEDIAN_THRESHOLD_MS, (
        f"In-process median {inproc_median:.3f}ms exceeds threshold "
        f"{IN_PROCESS_MEDIAN_THRESHOLD_MS}ms — check for regressions"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    iterations = 100
    if "--iterations" in sys.argv:
        idx = sys.argv.index("--iterations")
        iterations = int(sys.argv[idx + 1])

    print(f"Running {iterations} iterations...")
    results, total_weight = run_benchmark(iterations=iterations)
    stats = compute_stats(results)
    print_report(stats)

    # Exit with failure if thresholds breached
    if stats["wall_ms"]["median"] > WALL_CLOCK_MEDIAN_THRESHOLD_MS:
        sys.exit(1)
    if stats["in_process_ms"]["median"] > IN_PROCESS_MEDIAN_THRESHOLD_MS:
        sys.exit(1)
