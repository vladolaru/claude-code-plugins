"""Tests for review/reviewer_names.py — derive_reviewer_name() and its
inverse, agent_name_from_review_stem().

agent_name_from_review_stem() projects a reviewer artifact stem back to its
registry agent name; telemetry, telemetry sharing and the run-metrics
contracts use it. The forward derivation is exercised across every
registered agent in review/agent/test_bootstrap_integration.py.
"""

import pytest

from review.reviewer_names import agent_name_from_review_stem, derive_reviewer_name


class TestDeriveReviewerName:
    def test_mid_string_reviewer_keeps_it_strips_only_trailing_suffix(self):
        """A blanket `.replace("-reviewer", "")` would corrupt an adapter
        instance name carrying "reviewer" mid-string; only the trailing
        occurrence is stripped."""
        assert (
            derive_reviewer_name("repo-api-reviewer-v2-reviewer")
            == "repo-api-reviewer-v2"
        )


class TestAgentNameFromReviewStem:
    @pytest.mark.parametrize(
        ("stem", "expected"),
        [
            ("security-review", "security-reviewer"),
            ("repo-api-reviewer-v2-review", "repo-api-reviewer-v2-reviewer"),
            ("security-reviewer", "security-reviewer"),
            ("tests-mutation-reviewer", "tests-mutation-reviewer"),
            ("review", "review"),
        ],
    )
    def test_maps_stem_to_registry_name(self, stem, expected):
        assert agent_name_from_review_stem(stem) == expected
