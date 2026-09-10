"""The history-insights reviewer's exploration is bounded by the REVIEW
BUDGET bootstrap hands it, not by a number written into the agent."""

from pathlib import Path

AGENT = Path(__file__).resolve().parents[3] / "agents" / "history-insights-reviewer.md"


def test_scenario_count_derives_from_the_review_budget():
    content = AGENT.read_text()
    assert "Plan `min(5, max(2, target // 10))` scenarios" in content


def test_parallel_branch_detection_is_one_walk_and_skipped_on_wide_diffs():
    content = AGENT.read_text()
    start = content.index("### Phase 1.5")
    section = content[start:content.index("### Phase 2")]
    assert "one `git log --all` call over every changed file" in section
    assert "more than 10 changed files" in section
