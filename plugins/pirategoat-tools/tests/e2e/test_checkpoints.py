"""Tests for checkpoint builders."""

import json
from pathlib import Path

import pytest

from checkpoints import build_checkpoints
from expectations import PR1_CLEAN_SMALL, PR2_BUGGY_MEDIUM, PR4_NON_DEFAULT_BRANCH


class TestBuildCheckpoints:
    def test_produces_checkpoints_for_pr1(self, tmp_path):
        cps = build_checkpoints(PR1_CLEAN_SMALL, str(tmp_path))
        assert len(cps) > 0
        names = [cp.name for cp in cps]
        assert "step_2_context_file" in names
        assert "step_10_dispatch_plan" in names

    def test_pr4_has_base_ref_checkpoint(self, tmp_path):
        cps = build_checkpoints(PR4_NON_DEFAULT_BRANCH, str(tmp_path))
        names = [cp.name for cp in cps]
        assert "step_2_context_git_base_ref" in names

    def test_pr2_has_agent_started_checkpoints(self, tmp_path):
        cps = build_checkpoints(PR2_BUGGY_MEDIUM, str(tmp_path))
        names = [cp.name for cp in cps]
        assert "agent_started_security-reviewer" in names

    def test_all_checkpoints_have_assertions(self, tmp_path):
        cps = build_checkpoints(PR1_CLEAN_SMALL, str(tmp_path))
        for cp in cps:
            assert cp.assertion is not None, f"Checkpoint {cp.name} has no assertion"
            assert callable(cp.assertion), f"Checkpoint {cp.name} assertion not callable"

    def test_step_triggers_are_in_order(self, tmp_path):
        cps = build_checkpoints(PR1_CLEAN_SMALL, str(tmp_path))
        step_cps = [cp for cp in cps if cp.trigger_step is not None]
        steps = [cp.trigger_step for cp in step_cps]
        assert steps == sorted(steps), f"Step triggers not in order: {steps}"
