"""The orchestrator's dispatch adjustments go through one validating, atomic,
self-describing entry point instead of a per-run throwaway script."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from review import dispatch_adjust  # noqa: E402
from review.dispatch_status import (  # noqa: E402
    DISPATCH, DISPATCH_OVERRIDE, SKIPPED, SKIPPED_OVERRIDE, SKIPPED_TRIAGE,
)
from review.run_paths import artifact_path  # noqa: E402
from helpers.review_fixtures import write_artifact  # noqa: E402

CLI = SCRIPTS_DIR / "review" / "dispatch_adjust.py"


def _plan(tmp_path, agents=None, changed_files=None):
    plan = {"agents": agents or [
        {"name": "a11y-reviewer", "status": DISPATCH, "reason": "conditional (domain has files, no triage signal to skip)", "signal": "default"},
        {"name": "code-reviewer", "status": DISPATCH, "reason": "always dispatch (domain has files)", "signal": "always"},
        {"name": "php-tests-reviewer", "status": SKIPPED, "reason": "no files in php-tests domain", "signal": "no_domain_files"},
        {"name": "woo-regression-reviewer", "status": SKIPPED_TRIAGE, "reason": "requires PHP source file", "signal": "source_gate"},
    ]}
    if changed_files is not None:
        plan["changed_files"] = list(changed_files)
    write_artifact(tmp_path, "dispatch_plan_initial", plan)
    return write_artifact(tmp_path, "dispatch_plan", plan)


CHANGELOG_FILE = "plugins/woocommerce/changelog/35520-fix-stale-coupon-code-cache-on-unpublish"
DOMAIN_AGENTS = [
    {"name": "docs-drift-reviewer", "status": DISPATCH, "domain": "docs-drift", "reason": "keywords matched", "signal": "keyword"},
    {"name": "code-reviewer", "status": DISPATCH, "domain": "code", "reason": "always dispatch (domain has files)", "signal": "always"},
]


def _row(name, scope_domains, status=DISPATCH, domain="", **extra):
    """A plan row as the planner stamps it: `scope_domains` states the scope."""
    return {"name": name, "status": status, "domain": scope_domains[0] if domain == "" else domain,
            "scope_domains": scope_domains, "reason": "r", "signal": "always", **extra}


class TestOverrideOrphans:
    """Run 4dfe (PR #66900): the orchestrator skipped docs-drift with a
    sound reason and the changelog fragment, which only that domain
    matches, was reviewed by no one; the report then called it domainless.
    The consequence of a skip is a fact the script states at decision time
    and the plan carries."""

    def test_a_skip_records_the_files_no_dispatched_scope_covers(self, tmp_path):
        path = _plan(tmp_path, agents=DOMAIN_AGENTS, changed_files=[CHANGELOG_FILE, "src/a.php"])
        result = dispatch_adjust.adjust_dispatch_plan(
            str(tmp_path), skips=[("docs-drift-reviewer", "no docs mention the cache")],
        )
        agents = _read(path)
        assert agents["docs-drift-reviewer"]["orphaned_files"] == [CHANGELOG_FILE]
        assert "orphaned_files" not in agents["code-reviewer"]
        assert result["adjustments"][0]["orphaned_files"] == [CHANGELOG_FILE]
        lines = dispatch_adjust.render_adjustments(result["adjustments"])
        assert any(f"`{CHANGELOG_FILE}`" in line for line in lines)

    @pytest.mark.parametrize("agents, changed_files, skip, expected", [
        # Nothing orphaned: no changed file matched the skipped scope alone.
        (DOMAIN_AGENTS, ["src/a.php"], "docs-drift-reviewer", []),
        # A dispatched agent's secondary domain covers the file (security
        # also receives config-ops), so skipping toolchain orphans nothing.
        ([_row("toolchain-reviewer", ["toolchain"]), _row("security-reviewer", ["security", "config-ops"])],
         ["Dockerfile"], "toolchain-reviewer", []),
        # The skipped agent's own secondary domain counts as its coverage.
        ([_row("toolchain-reviewer", ["toolchain"], SKIPPED), _row("security-reviewer", ["security", "config-ops"])],
         ["Dockerfile"], "security-reviewer", ["Dockerfile"]),
        # A repo reviewer's declared scope domains cover files.
        ([_row("code-reviewer", ["code"]), _row("repo-renewals-reviewer", ["code"], domain=None)],
         ["src/a.php"], "code-reviewer", []),
        # A repo reviewer's `applies_to.paths` globs reach beyond its domains.
        ([_row("code-reviewer", ["code"]),
          _row("repo-contracts-reviewer", ["code"], domain=None, include_paths=["contracts/*.contract"])],
         ["contracts/payment.contract", "src/a.php"], "repo-contracts-reviewer", ["contracts/payment.contract"]),
    ], ids=["nothing", "secondary-covers", "own-secondary", "adapter-domains", "adapter-globs"])
    def test_orphans_are_measured_over_each_rows_declared_scope(self, tmp_path, agents, changed_files, skip, expected):
        path = _plan(tmp_path, agents=agents, changed_files=changed_files)
        dispatch_adjust.adjust_dispatch_plan(str(tmp_path), skips=[(skip, "r")])
        assert _read(path)[skip]["orphaned_files"] == expected

    def test_orphans_are_recomputed_on_every_call(self, tmp_path):
        """Re-dispatching the skipped agent clears the record; the list is
        the plan's current truth, not the first call's."""
        path = _plan(tmp_path, agents=DOMAIN_AGENTS, changed_files=[CHANGELOG_FILE, "src/a.php"])
        dispatch_adjust.adjust_dispatch_plan(str(tmp_path), skips=[("docs-drift-reviewer", "r")])
        dispatch_adjust.adjust_dispatch_plan(str(tmp_path), dispatches=[("docs-drift-reviewer", "on reflection")])
        assert "orphaned_files" not in _read(path)["docs-drift-reviewer"]

    def test_without_a_changed_file_list_orphans_are_unmeasured(self, tmp_path):
        path = _plan(tmp_path, agents=DOMAIN_AGENTS)
        result = dispatch_adjust.adjust_dispatch_plan(str(tmp_path), skips=[("docs-drift-reviewer", "r")])
        assert "orphaned_files" not in _read(path)["docs-drift-reviewer"]
        assert result["adjustments"][0]["orphaned_files"] is None


def _read(path):
    return {a["name"]: a for a in json.loads(path.read_text())["agents"]}


class TestAdjustDispatchPlan:
    def test_skips_and_dispatches_in_one_atomic_write(self, tmp_path):
        path = _plan(tmp_path)
        result = dispatch_adjust.adjust_dispatch_plan(
            str(tmp_path),
            skips=[("a11y-reviewer", "no markup, ARIA or focus call in the diff")],
            dispatches=[("php-tests-reviewer", "the fixtures are PHP")],
        )
        agents = _read(path)
        assert agents["a11y-reviewer"]["status"] == SKIPPED_OVERRIDE
        assert agents["a11y-reviewer"]["override_reason"] == "no markup, ARIA or focus call in the diff"
        assert agents["a11y-reviewer"]["reason"].startswith("conditional")  # the planner's reason stays
        assert agents["php-tests-reviewer"]["status"] == DISPATCH_OVERRIDE
        assert result["written"] is True
        assert result["dispatching"] == 2 and result["skipped"] == 2
        assert [(row["name"], row["from"], row["to"], row["changed"]) for row in result["adjustments"]] == [
            ("a11y-reviewer", DISPATCH, SKIPPED_OVERRIDE, True),
            ("php-tests-reviewer", SKIPPED, DISPATCH_OVERRIDE, True),
        ]
        # The planner's baseline is never touched.
        initial = _read(artifact_path(str(tmp_path), "dispatch_plan_initial"))
        assert initial["a11y-reviewer"]["status"] == DISPATCH

    def test_a_repeated_adjustment_is_idempotent(self, tmp_path):
        path = _plan(tmp_path)
        dispatch_adjust.adjust_dispatch_plan(str(tmp_path), skips=[("a11y-reviewer", "r")])
        before = path.read_bytes()
        result = dispatch_adjust.adjust_dispatch_plan(str(tmp_path), skips=[("a11y-reviewer", "r")])
        assert result["adjustments"][0]["changed"] is False
        assert result["written"] is False
        assert path.read_bytes() == before

    def test_an_override_can_be_reversed(self, tmp_path):
        path = _plan(tmp_path)
        dispatch_adjust.adjust_dispatch_plan(str(tmp_path), skips=[("a11y-reviewer", "r")])
        dispatch_adjust.adjust_dispatch_plan(str(tmp_path), dispatches=[("a11y-reviewer", "second thoughts")])
        assert _read(path)["a11y-reviewer"]["status"] == DISPATCH_OVERRIDE

    @pytest.mark.parametrize("skips, dispatches, fragment", [
        ([("nobody-reviewer", "r")], [], "unknown agent 'nobody-reviewer'; the plan names: a11y-reviewer, code-reviewer"),
        ([("a11y-reviewer", "  ")], [], "--skip a11y-reviewer reason"),
        ([("a11y-reviewer", "r")], [("a11y-reviewer", "r")], "'a11y-reviewer' is named twice; one adjustment per agent"),
        ([], [], "nothing to adjust: pass --skip NAME REASON and/or --dispatch NAME REASON"),
    ])
    def test_a_refused_request_names_the_fix_and_writes_nothing(self, tmp_path, skips, dispatches, fragment):
        path = _plan(tmp_path)
        before = path.read_bytes()
        with pytest.raises(dispatch_adjust.DispatchAdjustmentError) as excinfo:
            dispatch_adjust.adjust_dispatch_plan(str(tmp_path), skips=skips, dispatches=dispatches)
        assert any(fragment in problem for problem in excinfo.value.problems), excinfo.value.problems
        assert path.read_bytes() == before

    def test_every_problem_is_reported_at_once(self, tmp_path):
        _plan(tmp_path)
        with pytest.raises(dispatch_adjust.DispatchAdjustmentError) as excinfo:
            dispatch_adjust.adjust_dispatch_plan(
                str(tmp_path), skips=[("nobody-reviewer", "r"), ("ghost-reviewer", "r")],
            )
        assert len(excinfo.value.problems) == 2

    def test_an_agent_already_in_the_requested_family_is_a_reported_no_op(self, tmp_path):
        """`--skip` of a planner-skipped agent (or `--dispatch` of a dispatched
        one) is what the orchestrator meant; refusing it with "use the other
        flag" would send it the wrong way."""
        path = _plan(tmp_path)
        before = path.read_bytes()
        result = dispatch_adjust.adjust_dispatch_plan(
            str(tmp_path),
            skips=[("php-tests-reviewer", "r")], dispatches=[("code-reviewer", "r")],
        )
        assert [(row["name"], row["from"], row["to"], row["changed"]) for row in result["adjustments"]] == [
            ("php-tests-reviewer", SKIPPED, SKIPPED, False),
            ("code-reviewer", DISPATCH, DISPATCH, False),
        ]
        assert result["written"] is False
        assert path.read_bytes() == before
        assert dispatch_adjust.render_adjustments(result["adjustments"]) == [
            "UNCHANGED php-tests-reviewer — already skipped by the planner (SKIPPED)",
            "UNCHANGED code-reviewer — already dispatched by the planner (DISPATCH)",
        ]

    def test_a_malformed_plan_names_the_file(self, tmp_path):
        path = _plan(tmp_path)
        path.write_text("{not json")
        with pytest.raises(ValueError, match=r"dispatch-plan\.json is not valid JSON"):
            dispatch_adjust.adjust_dispatch_plan(str(tmp_path), skips=[("x", "r")])

    def test_dry_run_validates_and_writes_nothing(self, tmp_path):
        path = _plan(tmp_path)
        before = path.read_bytes()
        result = dispatch_adjust.adjust_dispatch_plan(
            str(tmp_path), skips=[("a11y-reviewer", "r")], dry_run=True,
        )
        assert result["written"] is False
        assert result["adjustments"][0]["changed"] is True
        assert path.read_bytes() == before

    def test_an_invalid_plan_is_refused(self, tmp_path):
        path = _plan(tmp_path)
        path.write_text('{"agents": [{"name": "x", "status": "WHATEVER"}]}')
        with pytest.raises(ValueError):
            dispatch_adjust.adjust_dispatch_plan(str(tmp_path), skips=[("x", "r")])


class TestCli:
    def _run(self, tmp_path, *args):
        return subprocess.run(
            [sys.executable, str(CLI), "--output-dir", str(tmp_path), *args],
            capture_output=True, text=True, cwd=tmp_path,
        )

    def test_a_missing_plan_names_the_step_that_writes_it(self, tmp_path):
        completed = self._run(tmp_path, "--skip", "a11y-reviewer", "r")
        assert completed.returncode == 1
        assert completed.stdout.startswith(f"REJECTED: no dispatch plan under {tmp_path}: run pipeline step 5 first")

    def test_prints_one_line_per_adjustment_and_a_summary(self, tmp_path):
        _plan(tmp_path)
        completed = self._run(
            tmp_path,
            "--skip", "a11y-reviewer", "no markup in the diff",
            "--skip", "code-reviewer", "nothing to review",
            "--dispatch", "woo-regression-reviewer", "the fixtures are PHP",
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert completed.stdout.splitlines() == [
            "SKIPPED_OVERRIDE a11y-reviewer — no markup in the diff",
            "SKIPPED_OVERRIDE code-reviewer — nothing to review",
            "DISPATCH_OVERRIDE woo-regression-reviewer — the fixtures are PHP",
            "ADJUSTED: 3 | DISPATCHING: 1 | SKIPPED: 3",
        ]

    def test_refusals_exit_one_with_the_fix_named(self, tmp_path):
        _plan(tmp_path)
        completed = self._run(tmp_path, "--skip", "nobody-reviewer", "r")
        assert completed.returncode == 1
        assert completed.stdout.startswith("REJECTED: unknown agent 'nobody-reviewer'; the plan names: a11y-reviewer, code-reviewer")

    def test_dry_run_says_would(self, tmp_path):
        path = _plan(tmp_path)
        completed = self._run(tmp_path, "--dry-run", "--skip", "a11y-reviewer", "r")
        assert completed.returncode == 0
        assert completed.stdout.splitlines() == [
            "WOULD SKIPPED_OVERRIDE a11y-reviewer — r",
            # The counts describe the plan as it would stand after the adjustment.
            "WOULD ADJUST: 1 | DISPATCHING: 1 | SKIPPED: 3",
        ]
        assert _read(path)["a11y-reviewer"]["status"] == DISPATCH
