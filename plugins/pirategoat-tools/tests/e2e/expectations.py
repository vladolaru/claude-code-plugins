"""PR expectations for e2e pipeline tests.

Each PR in the test repo has a fixed set of expected pipeline behaviors.
The PRExpectations dataclass drives both mid-run checkpoint assertions
(via StreamMonitor) and post-run final-state assertions.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PRExpectations:
    """Expected pipeline behavior for a test PR."""

    # PR identity.
    pr_number: int
    branch: str
    base_ref: str = "main"

    # Verdict expectations.
    verdict_in: list[str] = field(default_factory=lambda: ["APPROVE", "COMMENT"])

    # Agent dispatch expectations.
    min_dispatched_agents: int = 1
    max_dispatched_agents: Optional[int] = None
    must_dispatch: list[str] = field(default_factory=list)
    must_skip_triage: list[str] = field(default_factory=list)

    # Finding expectations.
    min_critical_findings: int = 0
    max_critical_findings: Optional[int] = None
    min_important_findings: int = 0
    max_important_findings: Optional[int] = None
    must_find_keywords: list[str] = field(default_factory=list)
    history_should_reference: list[str] = field(default_factory=list)

    # Size expectations.
    size_category_in: list[str] = field(
        default_factory=lambda: ["tiny", "small", "medium", "large", "huge", "vlad-sized"]
    )

    # Changed files (if known — for exact-match assertions).
    changed_files: Optional[list[str]] = None
    max_changed_files: Optional[int] = None

    # Context file assertions (field path -> expected value).
    context_assertions: dict[str, str] = field(default_factory=dict)

    # Review state expectations.
    expect_existing_reviews: bool = False
    expect_changes_requested: bool = False
    expect_prior_approval: bool = False

    # Ground truth.
    must_have_ground_truth: bool = False

    # Agent output file count.
    min_agent_review_files: Optional[int] = None


# =========================================================================
# PR-specific expectations — match the test repo's 4 permanent PRs.
# =========================================================================

PR1_CLEAN_SMALL = PRExpectations(
    pr_number=1,
    branch="feat/currency-conversion",
    base_ref="main",
    verdict_in=["APPROVE", "COMMENT"],
    min_dispatched_agents=2,
    must_dispatch=["pr-reviewer"],
    must_skip_triage=["a11y-reviewer", "e2e-tests-reviewer"],
    max_critical_findings=0,
    max_important_findings=1,
    changed_files=[
        "src/CurrencyConverter.php",
        "tests/php/test-currency-converter.php",
        "src/DoubloonsGateway.php",
    ],
)

PR2_BUGGY_MEDIUM = PRExpectations(
    pr_number=2,
    branch="feat/treasure-map-admin",
    base_ref="main",
    verdict_in=["REQUEST_CHANGES"],
    min_dispatched_agents=6,
    must_dispatch=[
        "pr-reviewer",
        "security-reviewer",
        "performance-reviewer",
        "a11y-reviewer",
        "wp-architecture-reviewer",
        "history-insights-reviewer",
    ],
    min_critical_findings=1,
    must_find_keywords=["prepare()", "esc_html", "SQL"],
    history_should_reference=["escape output", "nonce check", "prepare()"],
    expect_existing_reviews=True,
    expect_changes_requested=True,
)

PR3_LARGE = PRExpectations(
    pr_number=3,
    branch="feat/recurring-billing",
    base_ref="main",
    verdict_in=["APPROVE", "COMMENT", "REQUEST_CHANGES"],
    min_dispatched_agents=10,
    size_category_in=["large", "huge"],
    must_have_ground_truth=False,
    min_agent_review_files=8,
    expect_existing_reviews=True,
    expect_prior_approval=True,
)

PR4_NON_DEFAULT_BRANCH = PRExpectations(
    pr_number=4,
    branch="fix/rounding-error",
    base_ref="release/v1",
    verdict_in=["APPROVE", "COMMENT"],
    context_assertions={
        "git.base_ref": "release/v1",
        "pr.base_ref_name": "release/v1",
    },
    max_changed_files=3,
)

ALL_PR_EXPECTATIONS = [
    PR1_CLEAN_SMALL,
    PR2_BUGGY_MEDIUM,
    PR3_LARGE,
    PR4_NON_DEFAULT_BRANCH,
]
