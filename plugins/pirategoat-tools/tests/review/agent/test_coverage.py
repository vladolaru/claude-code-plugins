"""Tests for authoritative deferred-coverage derivation."""

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review.agent.coverage import CoverageError, derive_deferred_coverage


def _sidecar(**overrides):
    payload = {
        "schema": 2,
        "agent_name": "code-reviewer",
        "diffed_count": 2,
        "in_scope_count": 5,
        "deferred_files": ["src/b.py", "src/c.py", "src/d.py"],
    }
    payload.update(overrides)
    return payload


def test_derives_gaps_and_counts_from_positive_claims():
    coverage = derive_deferred_coverage(
        _sidecar(), ["./src/d.py", "src/b.py", "src/b.py"]
    )

    assert coverage.deferred_reviewed == ("src/b.py", "src/d.py")
    assert coverage.unreviewed == ("src/c.py",)
    assert coverage.files_reviewed == 4
    assert coverage.in_scope_count == 5


def test_rejects_a_claim_outside_the_authoritative_deferred_set():
    with pytest.raises(CoverageError, match="not deferred files"):
        derive_deferred_coverage(_sidecar(deferred_files=["src/b.py"], diffed_count=1, in_scope_count=2), ["src/other.py"])


def test_uses_the_sidecar_order_and_preserves_the_exact_dispatch_identity():
    coverage = derive_deferred_coverage(
        _sidecar(agent_name="repo-renewals-reviewer"), ["src/d.py", "src/b.py"]
    )

    assert coverage.agent_name == "repo-renewals-reviewer"
    assert coverage.deferred_reviewed == ("src/b.py", "src/d.py")
    assert coverage.unreviewed == ("src/c.py",)


def test_empty_deferred_set_keeps_all_inline_coverage():
    coverage = derive_deferred_coverage(
        _sidecar(deferred_files=[], diffed_count=3, in_scope_count=3), []
    )

    assert coverage.deferred_reviewed == ()
    assert coverage.unreviewed == ()
    assert coverage.files_reviewed == 3


@pytest.mark.parametrize(
    "sidecar",
    [
        {},
        _sidecar(schema=1),
        _sidecar(agent_name=""),
        _sidecar(deferred_files=["src/b.py", 3]),
        _sidecar(diffed_count=-1),
        _sidecar(in_scope_count=True),
        _sidecar(diffed_count=1, in_scope_count=5),
    ],
)
def test_rejects_missing_or_malformed_sidecars(sidecar):
    with pytest.raises(CoverageError):
        derive_deferred_coverage(sidecar, [])


@pytest.mark.parametrize("claim", ["/etc/passwd", "../src/b.py", "C:\\src\\b.py", "."])
def test_rejects_claims_outside_the_repository_relative_path_grammar(claim):
    with pytest.raises(CoverageError):
        derive_deferred_coverage(_sidecar(), [claim])
