"""Shared pytest fixtures and helpers for the yoloing-safe test suite."""

from __future__ import annotations

import json
import os
import subprocess
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "pre-tool-use-safety.py")
SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"


@pytest.fixture
def hook():
    """Import the hook script as a module."""
    spec = spec_from_file_location("safety_hook", SCRIPT)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def get_detect(hook, rule_id):
    """Get the resolved detector function for a rule."""
    return hook.RULES[rule_id]["_detect"]


def run_hook(tool_name, tool_input, *, env=None, timeout=5):
    """Run the hook script via subprocess for integration assertions."""
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    return subprocess.run(
        ["python3", SCRIPT],
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=merged_env,
    )


def load_scenarios(filename):
    """Load one JSON scenario file from the test scenario directory."""
    return json.loads((SCENARIOS_DIR / filename).read_text())
