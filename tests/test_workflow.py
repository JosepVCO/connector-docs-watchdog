"""Static validation of the weekly workflow (it is never executed in the test suite)."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
TEXT = (ROOT / ".github/workflows/watch.yml").read_text()
WF = yaml.safe_load(TEXT)
TRIGGERS = WF.get("on", WF.get(True))  # PyYAML parses a bare `on:` key as True
STEPS = WF["jobs"]["watch"]["steps"]


def step(name_part):
    return next(s for s in STEPS if name_part.lower() in s.get("name", "").lower())


def test_triggers_weekly_cron_and_manual_dispatch():
    crons = [s["cron"] for s in TRIGGERS["schedule"]]
    assert len(crons) == 1
    minute, hour, dom, mon, dow = crons[0].split()
    assert dom == "*" and mon == "*" and dow.isdigit()  # exactly once a week
    assert "workflow_dispatch" in TRIGGERS
    assert "UTC" in TEXT  # timezone is documented where the cron is defined


def test_runs_deterministically_without_secrets_or_llm():
    run = step("Run monitor")["run"]
    assert "--no-llm" in run and "--state-dir" in run
    assert "secrets." not in TEXT and "LLM_API_KEY" not in TEXT and "ANTHROPIC" not in TEXT


def test_state_is_not_cache_based_and_lives_in_a_separate_branch():
    assert "actions/cache" not in TEXT and "cache:" not in TEXT.replace("cache: pip", "")
    assert WF["env"]["DATA_BRANCH"] == "watchdog-data"
    assert step("(data branch)")["with"]["path"] == "state"
    persist = step("Persist")
    assert "always()" in persist["if"] and "HEAD:${DATA_BRANCH}" in persist["run"]
    assert "--force" not in TEXT and "push -f" not in TEXT  # history is never rewritten
    assert "main" not in persist["run"]  # the code branch is never touched


def test_concurrency_serialises_runs_without_cancelling():
    c = WF["concurrency"]
    assert c["group"] and c["cancel-in-progress"] is False


def test_least_privilege_permissions():
    assert WF["permissions"] == {"contents": "read"}
    assert WF["jobs"]["watch"]["permissions"] == {"contents": "write"}
    assert "persist-credentials: false" in TEXT  # code checkout holds no push token


def test_python_version_is_compatible_and_install_is_pinned():
    version = next(s for s in STEPS if str(s.get("uses", "")).startswith("actions/setup-python"))[
        "with"]["python-version"]
    req = re.search(r'requires-python = ">=(\d+)\.(\d+)"', (ROOT / "pyproject.toml").read_text())
    assert tuple(map(int, str(version).split("."))) >= (int(req[1]), int(req[2]))
    assert "-c constraints.txt" in step("Install")["run"] and (ROOT / "constraints.txt").exists()


def test_tests_run_before_the_monitor_and_failures_fail_the_job():
    names = [s.get("name", "") for s in STEPS]
    assert names.index("Tests") < names.index("Run monitor (deterministic mode)")
    assert "continue-on-error" not in TEXT


def test_workflow_has_no_local_paths_or_secrets():
    assert "/Users/" not in TEXT and "/home/" not in TEXT and "ghp_" not in TEXT


def test_constraints_pin_exact_versions():
    lines = [l for l in (ROOT / "constraints.txt").read_text().splitlines() if l and not l.startswith("#")]
    assert lines and all("==" in l for l in lines)
