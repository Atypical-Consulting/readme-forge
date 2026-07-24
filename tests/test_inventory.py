"""Coverage for cmd_inventory's floor check.

`gh repo list` exiting 0 with an empty list is the dangerous case — a
fine-grained PAT whose repository selection drifted, or an org the token lost
access to, looks exactly like a healthy run that found nothing. `sh` is stubbed
here; no `gh` process is ever spawned.
"""
import json

import pytest

import readme_forge as rf


class FakeResult:
    def __init__(self, returncode=0, stdout="[]", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


REPO = {"name": "widget", "isFork": False, "isArchived": False, "stargazerCount": 1,
        "primaryLanguage": {"name": "C#"}, "defaultBranchRef": {"name": "main"},
        "licenseInfo": {"key": "mit"}, "description": "", "pushedAt": "2026-01-01T00:00:00Z",
        "isEmpty": False, "createdAt": "2020-01-01T00:00:00Z", "repositoryTopics": []}


def test_an_empty_but_successful_repo_list_aborts_the_run(monkeypatch, cfg):
    """Without the floor check this returns [], cmd_scan overwrites data.json
    with an empty scan, the dashboard renders empty, the roll-up issue reports
    "every scored repository meets the standard" — and every step is green."""
    monkeypatch.setattr(rf, "sh", lambda cmd: FakeResult(stdout="[]"))
    with pytest.raises(SystemExit) as exc:
        rf.cmd_inventory(cfg, ["acme"])
    assert "acme" in str(exc.value)


def test_the_abort_message_names_the_things_worth_checking(monkeypatch, cfg):
    monkeypatch.setattr(rf, "sh", lambda cmd: FakeResult(stdout="[]"))
    with pytest.raises(SystemExit) as exc:
        rf.cmd_inventory(cfg, ["acme"])
    msg = str(exc.value)
    assert "--org" in msg and "FORGE_ORGS" in msg


def test_a_non_empty_repo_list_is_written_out_as_usual(monkeypatch, cfg):
    monkeypatch.setattr(rf, "sh", lambda cmd: FakeResult(stdout=json.dumps([REPO])))
    repos = rf.cmd_inventory(cfg, ["acme"])
    assert [r["name"] for r in repos] == ["widget"]
    with open(f"{cfg['workdir']}/repos.json") as fh:
        assert [r["name"] for r in json.load(fh)] == ["widget"]


def test_an_only_filter_that_matches_nothing_aborts_rather_than_scanning_zero_repos(
        monkeypatch, cfg):
    """A typo in --only is the same empty-inventory hazard, arrived at from the
    other direction."""
    monkeypatch.setattr(rf, "sh", lambda cmd: FakeResult(stdout=json.dumps([REPO])))
    with pytest.raises(SystemExit) as exc:
        rf.cmd_inventory(cfg, ["acme"], only="widgtet")
    assert "widgtet" in str(exc.value)


def test_a_failing_gh_call_still_aborts(monkeypatch, cfg):
    monkeypatch.setattr(rf, "sh", lambda cmd: FakeResult(returncode=1, stderr="Bad credentials"))
    with pytest.raises(SystemExit) as exc:
        rf.cmd_inventory(cfg, ["acme"])
    assert "Bad credentials" in str(exc.value)
