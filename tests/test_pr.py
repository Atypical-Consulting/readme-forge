import base64

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


@pytest.fixture
def rec_pr(rec):
    rec["default_branch"] = "main"
    return rec


def _b64(s):
    return base64.b64encode(s.encode()).decode()


def test_ensure_branch_treats_existing_branch_as_success(monkeypatch):
    fake = FakeApi({("POST", "repos/acme/widget/git/refs"): (None, "Reference already exists")})
    monkeypatch.setattr(rf, "api", fake)
    ok, err = rf.ensure_branch("acme/widget", "forge/harmonize", "abc123")
    assert ok is True and err is None


def test_ensure_branch_reports_real_errors(monkeypatch):
    fake = FakeApi({("POST", "repos/acme/widget/git/refs"): (None, "Bad credentials")})
    monkeypatch.setattr(rf, "api", fake)
    ok, err = rf.ensure_branch("acme/widget", "forge/harmonize", "abc123")
    assert ok is False and "Bad credentials" in err


def test_open_or_update_pr_creates_branch_commit_and_pr(monkeypatch, cfg, rec_pr):
    fake = FakeApi({
        ("GET", "repos/acme/widget/git/ref/heads/main"): ({"object": {"sha": "base1"}}, None),
        ("POST", "repos/acme/widget/git/refs"): ({"ref": "ok"}, None),
        ("GET", "repos/acme/widget/contents/README.md"): (
            {"content": _b64("old"), "sha": "sha1"}, None),
        ("PUT", "repos/acme/widget/contents/README.md"): ({"commit": {}}, None),
        ("GET", "repos/acme/widget/pulls"): ([], None),
        ("POST", "repos/acme/widget/pulls"): ({"number": 7, "html_url": "u"}, None),
    })
    monkeypatch.setattr(rf, "api", fake)
    action, _ = rf.open_or_update_pr("acme/widget", rec_pr, "new content", "README.md", cfg)
    assert action == "created"
    assert any(p.startswith("repos/acme/widget/pulls") for p in fake.paths("POST"))


def test_open_or_update_pr_does_not_open_a_second_pr(monkeypatch, cfg, rec_pr):
    fake = FakeApi({
        ("GET", "repos/acme/widget/git/ref/heads/main"): ({"object": {"sha": "base1"}}, None),
        ("POST", "repos/acme/widget/git/refs"): (None, "Reference already exists"),
        ("GET", "repos/acme/widget/contents/README.md"): (
            {"content": _b64("old"), "sha": "sha1"}, None),
        ("PUT", "repos/acme/widget/contents/README.md"): ({"commit": {}}, None),
        ("GET", "repos/acme/widget/pulls"): ([{"number": 7, "html_url": "u"}], None),
    })
    monkeypatch.setattr(rf, "api", fake)
    action, _ = rf.open_or_update_pr("acme/widget", rec_pr, "new content", "README.md", cfg)
    assert action == "updated"
    assert not any(p.startswith("repos/acme/widget/pulls") for p in fake.paths("POST"))


def test_open_or_update_pr_is_a_noop_when_branch_already_matches(monkeypatch, cfg, rec_pr):
    fake = FakeApi({
        ("GET", "repos/acme/widget/git/ref/heads/main"): ({"object": {"sha": "base1"}}, None),
        ("POST", "repos/acme/widget/git/refs"): (None, "Reference already exists"),
        ("GET", "repos/acme/widget/contents/README.md"): (
            {"content": _b64("same"), "sha": "sha1"}, None),
        ("GET", "repos/acme/widget/pulls"): ([{"number": 7, "html_url": "u"}], None),
    })
    monkeypatch.setattr(rf, "api", fake)
    action, _ = rf.open_or_update_pr("acme/widget", rec_pr, "same", "README.md", cfg)
    assert action == "unchanged"
    assert fake.paths("PUT") == []


def test_put_readme_targets_a_branch_when_given(monkeypatch):
    fake = FakeApi({("PUT", "repos/acme/widget/contents/README.md"): ({}, None)})
    monkeypatch.setattr(rf, "api", fake)
    rf.put_readme("acme/widget", "README.md", "x", "sha1", "msg", branch="forge/harmonize")
    _, _, fields = fake.calls[0]
    assert fields["branch"] == "forge/harmonize"


def test_put_readme_omits_branch_by_default(monkeypatch):
    fake = FakeApi({("PUT", "repos/acme/widget/contents/README.md"): ({}, None)})
    monkeypatch.setattr(rf, "api", fake)
    rf.put_readme("acme/widget", "README.md", "x", "sha1", "msg")
    _, _, fields = fake.calls[0]
    assert "branch" not in fields


def test_open_or_update_pr_fails_closed_when_pr_search_errors(monkeypatch, cfg, rec_pr):
    """A failed PR search must never be treated as "no PR is open" — that
    would let a re-run commit and then POST a second, duplicate PR."""
    fake = FakeApi({
        ("GET", "repos/acme/widget/git/ref/heads/main"): ({"object": {"sha": "base1"}}, None),
        ("POST", "repos/acme/widget/git/refs"): (None, "Reference already exists"),
        ("GET", "repos/acme/widget/contents/README.md"): (
            {"content": _b64("old"), "sha": "sha1"}, None),
        ("GET", "repos/acme/widget/pulls"): (None, "API rate limit exceeded"),
    })
    monkeypatch.setattr(rf, "api", fake)
    action, detail = rf.open_or_update_pr("acme/widget", rec_pr, "new content", "README.md", cfg)
    assert action == "failed"
    assert "API rate limit exceeded" in detail
    assert not any(p.startswith("repos/acme/widget/pulls") for p in fake.paths("POST"))
    assert fake.paths("PUT") == []


def test_open_or_update_pr_fails_when_base_ref_lookup_fails(monkeypatch, cfg, rec_pr):
    fake = FakeApi({
        ("GET", "repos/acme/widget/git/ref/heads/main"): (None, "Not Found"),
    })
    monkeypatch.setattr(rf, "api", fake)
    action, detail = rf.open_or_update_pr("acme/widget", rec_pr, "new content", "README.md", cfg)
    assert action == "failed"
    assert "Not Found" in detail


def test_open_or_update_pr_refuses_when_pr_branch_is_the_default_branch(monkeypatch, cfg, rec_pr):
    """"Never the default branch" is the hardest constraint here, and a single
    mistyped `pr_branch` defeats it silently: ensure_branch reports an existing
    branch as success, so `"pr_branch": "main"` would sail through and the
    contents PUT would commit straight to main on a foreign repository."""
    cfg["pr_branch"] = "main"  # == rec_pr["default_branch"]
    fake = FakeApi({
        ("GET", "repos/acme/widget/git/ref/heads/main"): ({"object": {"sha": "base1"}}, None),
        ("POST", "repos/acme/widget/git/refs"): (None, "Reference already exists"),
        ("GET", "repos/acme/widget/contents/README.md"): (
            {"content": _b64("old"), "sha": "sha1"}, None),
        ("PUT", "repos/acme/widget/contents/README.md"): ({"commit": {}}, None),
        ("GET", "repos/acme/widget/pulls"): ([], None),
        ("POST", "repos/acme/widget/pulls"): ({"number": 7, "html_url": "u"}, None),
    })
    monkeypatch.setattr(rf, "api", fake)
    action, detail = rf.open_or_update_pr("acme/widget", rec_pr, "new content", "README.md", cfg)
    assert action == "failed"
    assert "default branch" in detail
    assert fake.paths("PUT") == []   # nothing was committed
    assert fake.calls == []          # and it never even reached the API


def test_open_or_update_pr_fails_when_pr_creation_fails(monkeypatch, cfg, rec_pr):
    fake = FakeApi({
        ("GET", "repos/acme/widget/git/ref/heads/main"): ({"object": {"sha": "base1"}}, None),
        ("POST", "repos/acme/widget/git/refs"): ({"ref": "ok"}, None),
        ("GET", "repos/acme/widget/contents/README.md"): (
            {"content": _b64("old"), "sha": "sha1"}, None),
        ("PUT", "repos/acme/widget/contents/README.md"): ({"commit": {}}, None),
        ("GET", "repos/acme/widget/pulls"): ([], None),
        ("POST", "repos/acme/widget/pulls"): (None, "Validation Failed"),
    })
    monkeypatch.setattr(rf, "api", fake)
    action, detail = rf.open_or_update_pr("acme/widget", rec_pr, "new content", "README.md", cfg)
    assert action == "failed"
    assert "Validation Failed" in detail
