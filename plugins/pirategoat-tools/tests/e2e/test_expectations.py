"""Tests for PRExpectations dataclass."""

from expectations import (
    PRExpectations,
    PR1_CLEAN_SMALL,
    PR2_BUGGY_MEDIUM,
    PR3_LARGE,
    PR4_NON_DEFAULT_BRANCH,
    ALL_PR_EXPECTATIONS,
)


class TestPRExpectations:
    def test_pr1_base_ref_is_main(self):
        assert PR1_CLEAN_SMALL.base_ref == "main"

    def test_pr2_expects_request_changes(self):
        assert "REQUEST_CHANGES" in PR2_BUGGY_MEDIUM.verdict_in

    def test_pr2_must_dispatch_security(self):
        assert "security-reviewer" in PR2_BUGGY_MEDIUM.must_dispatch

    def test_pr3_expects_large_size(self):
        assert "large" in PR3_LARGE.size_category_in

    def test_pr4_targets_release_branch(self):
        assert PR4_NON_DEFAULT_BRANCH.base_ref == "release/v1"

    def test_pr4_context_assertions(self):
        assert PR4_NON_DEFAULT_BRANCH.context_assertions["git.base_ref"] == "release/v1"

    def test_all_prs_have_unique_numbers(self):
        numbers = [e.pr_number for e in ALL_PR_EXPECTATIONS]
        assert len(numbers) == len(set(numbers))

    def test_all_prs_have_at_least_one_verdict(self):
        for e in ALL_PR_EXPECTATIONS:
            assert len(e.verdict_in) >= 1

    def test_must_dispatch_is_subset_of_known_agents(self):
        known = {
            "pr-reviewer", "security-reviewer", "performance-reviewer",
            "architecture-reviewer", "wp-architecture-reviewer",
            "patterns-reviewer", "history-insights-reviewer",
            "php-tests-reviewer", "js-tests-reviewer", "e2e-tests-reviewer",
            "go-tests-reviewer", "dead-code-reviewer", "a11y-reviewer",
            "reliability-reviewer",
        }
        for e in ALL_PR_EXPECTATIONS:
            for agent in e.must_dispatch:
                assert agent in known, f"PR{e.pr_number}: unknown agent {agent}"
