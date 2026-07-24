"""Coverage for main()'s exit status and for the --commit gate on the PR path.

main() is the only place that turns a command's return value into a process exit
code, and a scheduled workflow's green/red badge is the only signal anyone sees
week to week. These tests stub the commands themselves — the point here is the
wiring, not the commands' own logic.
"""
import json
import sys

import pytest

import dashboard
import readme_forge as rf


@pytest.fixture
def argv(monkeypatch, tmp_path):
    """Run main() with a throwaway config so no real .forge state is touched."""
    conf = tmp_path / "conf.json"
    conf.write_text(json.dumps({"workdir": str(tmp_path), "orgs": ["acme"]}))

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["readme_forge.py", "--config", str(conf), *args])
        return rf.main()

    return run


@pytest.fixture
def stub_pipeline(monkeypatch):
    """Neutralize inventory/scan/dashboard so only the command under test runs."""
    monkeypatch.setattr(rf, "cmd_inventory", lambda cfg, orgs, only=None: [])
    monkeypatch.setattr(rf, "cmd_scan", lambda cfg: [])
    monkeypatch.setattr(dashboard, "generate", lambda cfg: None)


# --------------------------------------------------------------- exit codes ---

def test_a_failed_report_exits_non_zero(monkeypatch, argv):
    """cmd_report's return value was discarded, so forge-watch went green while
    the roll-up issue silently went stale — a rate-limited search prints
    "failed: ..." and the step still succeeds."""
    monkeypatch.setattr(rf, "cmd_report", lambda cfg, repo: "failed")
    with pytest.raises(SystemExit) as exc:
        argv("report", "--repo", "acme/portfolio")
    assert exc.value.code not in (0, None)


def test_a_successful_report_exits_cleanly(monkeypatch, argv):
    monkeypatch.setattr(rf, "cmd_report", lambda cfg, repo: "updated")
    argv("report", "--repo", "acme/portfolio")  # must not raise SystemExit


def test_harmonize_pr_exits_non_zero_when_any_repo_failed(monkeypatch, argv, stub_pipeline):
    monkeypatch.setattr(rf, "cmd_harmonize_pr", lambda cfg, commit: {
        "created": 2, "updated": 0, "unchanged": 0, "failed": 1, "capped": 0, "would_pr": 0})
    with pytest.raises(SystemExit) as exc:
        argv("harmonize", "--pr", "--commit", "--org", "acme")
    assert exc.value.code not in (0, None)


def test_harmonize_pr_exits_cleanly_when_nothing_failed(monkeypatch, argv, stub_pipeline):
    monkeypatch.setattr(rf, "cmd_harmonize_pr", lambda cfg, commit: {
        "created": 2, "updated": 1, "unchanged": 3, "failed": 0, "capped": 0, "would_pr": 0})
    argv("harmonize", "--pr", "--commit", "--org", "acme")


# ------------------------------------------------------------ the --commit gate ---

def test_harmonize_pr_without_commit_runs_the_driver_in_dry_run_mode(
        monkeypatch, argv, stub_pipeline):
    """`harmonize --pr` with no --commit must write nothing: the docstring and
    README both promise dry-run by default, and a maintainer expecting a preview
    would otherwise get up to max_prs real PRs on other people's repositories."""
    seen = {}

    def fake(cfg, commit):
        seen["commit"] = commit
        return {"created": 0, "updated": 0, "unchanged": 0, "failed": 0,
                "capped": 0, "would_pr": 0}

    monkeypatch.setattr(rf, "cmd_harmonize_pr", fake)
    argv("harmonize", "--pr", "--org", "acme")
    assert seen["commit"] is False


def test_harmonize_pr_with_commit_runs_the_driver_in_writing_mode(
        monkeypatch, argv, stub_pipeline):
    seen = {}

    def fake(cfg, commit):
        seen["commit"] = commit
        return {"created": 0, "updated": 0, "unchanged": 0, "failed": 0,
                "capped": 0, "would_pr": 0}

    monkeypatch.setattr(rf, "cmd_harmonize_pr", fake)
    argv("harmonize", "--pr", "--commit", "--org", "acme")
    assert seen["commit"] is True


def test_max_prs_override_still_reaches_the_driver(monkeypatch, argv, stub_pipeline):
    seen = {}

    def fake(cfg, commit):
        seen["max_prs"] = cfg["max_prs"]
        return {"created": 0, "updated": 0, "unchanged": 0, "failed": 0,
                "capped": 0, "would_pr": 0}

    monkeypatch.setattr(rf, "cmd_harmonize_pr", fake)
    argv("harmonize", "--pr", "--max-prs", "3", "--org", "acme")
    assert seen["max_prs"] == 3


# ------------------------------------------------------------- legacy paths ---

def test_legacy_harmonize_still_runs_every_sweep_and_refreshes_the_dashboard(
        monkeypatch, argv, stub_pipeline):
    """The pre-existing `harmonize` path is untouched by the --pr changes: all
    six sweeps in order, --commit forwarded, dashboard regenerated at the end."""
    ran, drawn = [], []
    for name in rf.SWEEPS:
        monkeypatch.setitem(rf.SWEEPS, name,
                            lambda cfg, commit, _n=name: ran.append((_n, commit)))
    monkeypatch.setattr(dashboard, "generate", lambda cfg: drawn.append(True))

    argv("harmonize", "--commit", "--org", "acme")

    assert [n for n, _ in ran] == rf.SWEEP_ORDER
    assert all(commit is True for _, commit in ran)
    assert drawn == [True]


def test_legacy_harmonize_dry_run_forwards_commit_false(monkeypatch, argv, stub_pipeline):
    ran = []
    for name in rf.SWEEPS:
        monkeypatch.setitem(rf.SWEEPS, name,
                            lambda cfg, commit, _n=name: ran.append((_n, commit)))
    argv("harmonize", "--org", "acme")
    assert all(commit is False for _, commit in ran)


def test_sweep_forwards_the_commit_flag_and_exits_cleanly(monkeypatch, argv):
    seen = {}
    monkeypatch.setitem(rf.SWEEPS, "roadmap",
                        lambda cfg, commit: seen.setdefault("commit", commit))
    argv("sweep", "roadmap", "--commit")
    assert seen["commit"] is True


def test_run_still_does_inventory_scan_dashboard(monkeypatch, argv):
    steps = []
    monkeypatch.setattr(rf, "cmd_inventory", lambda cfg, orgs, only=None: steps.append("inv"))
    monkeypatch.setattr(rf, "cmd_scan", lambda cfg: steps.append("scan"))
    monkeypatch.setattr(dashboard, "generate", lambda cfg: steps.append("dash"))
    argv("run", "--org", "acme")
    assert steps == ["inv", "scan", "dash"]
