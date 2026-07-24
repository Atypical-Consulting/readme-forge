"""Direct regression coverage for cmd_report's issue upsert logic -- the
find-or-create dance against the GitHub Issues API, using a FakeApi double
rather than the real `gh` CLI. report_body's markdown itself is covered by
test_report.py.
"""
import json

import pytest

import readme_forge as rf


class FakeApi:
    """Records calls and replays scripted responses keyed by (method, path-prefix)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, path, method=None, fields=None):
        self.calls.append((method or "GET", path, fields))
        for (m, prefix), resp in self.routes.items():
            if (method or "GET") == m and path.startswith(prefix):
                return resp
        return None, "no route"

    def paths(self, method):
        return [p for m, p, _ in self.calls if m == method]


def _data(cfg, records):
    with open(f"{cfg['workdir']}/data.json", "w") as fh:
        json.dump(records, fh)


def test_cmd_report_creates_a_new_labeled_issue_when_none_is_open(monkeypatch, cfg, rec):
    _data(cfg, [dict(rec, name="widget")])
    fake = FakeApi({
        ("GET", "repos/acme/portfolio/issues"): ([], None),
        ("POST", "repos/acme/portfolio/issues"): ({"number": 9}, None),
    })
    monkeypatch.setattr(rf, "api", fake)

    action = rf.cmd_report(cfg, "acme/portfolio")

    assert action == "created"
    posts = [(p, f) for m, p, f in fake.calls if m == "POST"]
    assert len(posts) == 1
    _, post_fields = posts[0]
    # labels must be a list, not the bare string -- api() renders a list as
    # repeated `-f labels[]=item` fields; a plain string renders as
    # `-f labels=item`, which GitHub's issue-creation endpoint rejects
    # outright (HTTP 422: "... is not an array" -- confirmed empirically
    # against a real repository).
    assert post_fields["labels"] == [cfg["report_label"]]
    assert post_fields["title"] == rf.REPORT_TITLE
    assert "widget" in post_fields["body"]


def test_cmd_report_edits_the_existing_labeled_issue_in_place(monkeypatch, cfg, rec):
    _data(cfg, [dict(rec, name="widget")])
    fake = FakeApi({
        ("GET", "repos/acme/portfolio/issues"): ([{"number": 42}], None),
        ("PATCH", "repos/acme/portfolio/issues/42"): ({"number": 42}, None),
    })
    monkeypatch.setattr(rf, "api", fake)

    action = rf.cmd_report(cfg, "acme/portfolio")

    assert action == "updated"
    assert fake.paths("POST") == []  # never opens a second issue
    patches = [(p, f) for m, p, f in fake.calls if m == "PATCH"]
    assert len(patches) == 1
    patch_path, patch_fields = patches[0]
    assert patch_path == "repos/acme/portfolio/issues/42"
    # the update call only touches body -- labels are set at creation, not here
    assert set(patch_fields) == {"body"}


def test_cmd_report_fails_closed_when_the_labeled_issue_search_errors(monkeypatch, cfg, rec):
    """A failed label search must never be treated as "no issue is open" --
    that would create a duplicate roll-up issue every time the search flakes,
    the same reasoning `open_or_update_pr` applies to its own PR search."""
    _data(cfg, [dict(rec, name="widget")])
    fake = FakeApi({
        ("GET", "repos/acme/portfolio/issues"): (None, "API rate limit exceeded"),
    })
    monkeypatch.setattr(rf, "api", fake)

    action = rf.cmd_report(cfg, "acme/portfolio")

    assert action == "failed"
    assert fake.paths("POST") == []
    assert fake.paths("PATCH") == []


def test_cmd_report_returns_failed_when_the_patch_call_itself_errors(monkeypatch, cfg, rec):
    """A write failure must surface as "failed", not the misleadingly upbeat
    "updated" -- a caller trusting the return value needs to be able to tell
    "the issue body was actually rewritten" from "gh rejected the PATCH"."""
    _data(cfg, [dict(rec, name="widget")])
    fake = FakeApi({
        ("GET", "repos/acme/portfolio/issues"): ([{"number": 42}], None),
        ("PATCH", "repos/acme/portfolio/issues/42"): (None, "Validation Failed"),
    })
    monkeypatch.setattr(rf, "api", fake)

    action = rf.cmd_report(cfg, "acme/portfolio")

    assert action == "failed"


def test_cmd_report_returns_failed_when_the_post_call_itself_errors(monkeypatch, cfg, rec):
    """Same guard on the create path: a rejected POST (e.g. the labels-shape
    422 this task fixed) must not be reported as "created"."""
    _data(cfg, [dict(rec, name="widget")])
    fake = FakeApi({
        ("GET", "repos/acme/portfolio/issues"): ([], None),
        ("POST", "repos/acme/portfolio/issues"): (None, "Validation Failed"),
    })
    monkeypatch.setattr(rf, "api", fake)

    action = rf.cmd_report(cfg, "acme/portfolio")

    assert action == "failed"
