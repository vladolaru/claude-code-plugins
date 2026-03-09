"""Scenario-driven regression suites for yoloing-safe."""

import json
import os
import subprocess

import pytest
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "pre-tool-use-safety.py")


class TestBlockedScenarios:

    @pytest.fixture
    def scenarios(self):
        path = Path(__file__).resolve().parent / "scenarios" / "blocked.json"
        with open(path) as f:
            return json.load(f)

    def test_all_blocked(self, scenarios):
        for s in scenarios:
            payload = json.dumps({"tool_name": s["tool_name"], "tool_input": s["tool_input"]})
            result = subprocess.run(
                ["python3", SCRIPT],
                input=payload, capture_output=True, text=True, timeout=5
            )
            assert result.returncode == 2, f"NOT blocked ({s['category']}): {s['tool_input']}"


class TestAllowedScenarios:

    @pytest.fixture
    def scenarios(self):
        path = Path(__file__).resolve().parent / "scenarios" / "allowed.json"
        with open(path) as f:
            return json.load(f)

    def test_all_allowed(self, scenarios):
        for s in scenarios:
            payload = json.dumps({"tool_name": s["tool_name"], "tool_input": s["tool_input"]})
            result = subprocess.run(
                ["python3", SCRIPT],
                input=payload, capture_output=True, text=True, timeout=5
            )
            assert result.returncode == 0, f"Incorrectly blocked ({s['category']}): {s['tool_input']}"


class TestAskedScenarios:
    """Ask-tier scenario regression suite loaded from scenarios/asked.json."""

    @pytest.fixture
    def asked_scenarios(self):
        path = Path(__file__).resolve().parent / "scenarios" / "asked.json"
        with open(path) as f:
            return json.load(f)

    def test_all_asked(self, asked_scenarios):
        """Every asked scenario must exit 0 with permissionDecision: ask."""
        for i, scenario in enumerate(asked_scenarios):
            payload = json.dumps(scenario)
            r = subprocess.run(
                ["python3", SCRIPT],
                input=payload, capture_output=True, text=True, timeout=5,
                env={**os.environ, "YOLOING_SAFE_CONFIG_PATH": "/dev/null"},
            )
            assert r.returncode == 0, (
                f"Asked scenario {i} ({scenario.get('category')}) "
                f"expected exit 0 but got {r.returncode}. stderr: {r.stderr}"
            )
            output = json.loads(r.stdout)
            assert output["hookSpecificOutput"]["permissionDecision"] == "ask", (
                f"Asked scenario {i} ({scenario.get('category')}) "
                f"missing permissionDecision: ask. stdout: {r.stdout}"
            )


class TestEvasionSuite:

    @pytest.fixture
    def evasion_scenarios(self):
        path = Path(__file__).resolve().parent / "scenarios" / "evasion.json"
        with open(path) as f:
            return json.load(f)

    def test_all_evasions_caught(self, evasion_scenarios):
        """Every evasion scenario must be blocked or asked (not silently allowed)."""
        for scenario in evasion_scenarios:
            payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": scenario["command"]}})
            result = subprocess.run(
                ["python3", SCRIPT],
                input=payload, capture_output=True, text=True, timeout=5,
                env={**os.environ, "YOLOING_SAFE_CONFIG_PATH": "/dev/null"},
            )
            should = scenario.get("should", "block")
            if should == "block":
                assert result.returncode == 2, (
                    f"Evasion NOT blocked ({scenario['technique']}): {scenario['command']}"
                )
            elif should == "ask_or_block":
                # Must not be silently allowed — either block (rc=2) or ask (rc=0 with JSON)
                if result.returncode == 0:
                    assert result.stdout.strip(), (
                        f"Evasion silently allowed ({scenario['technique']}): {scenario['command']}"
                    )
            else:
                assert False, f"Unknown 'should' value: {should}"
