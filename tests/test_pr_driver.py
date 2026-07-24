"""Direct regression coverage for cmd_harmonize_pr's selection/capping/tallying
logic. These stub build_all/get_readme/harmonize_content/open_or_update_pr
directly (rather than routing through FakeApi + real sweeps) so each test
isolates the driver's own orchestration -- the sweep builders themselves are
already covered by test_builds.py/test_chain.py, and the PR mechanics by
test_pr.py.
"""
import json

import pytest

import readme_forge as rf


def _data(cfg, records):
    with open(f"{cfg['workdir']}/data.json", "w") as fh:
        json.dump(records, fh)


@pytest.fixture(autouse=True)
def stub_build_all(monkeypatch):
    # cmd_harmonize_pr calls build_all(cfg) unconditionally up front; every
    # test here also stubs harmonize_content, so the actual pairs value it
    # returns is never inspected. Stubbing it out avoids needing repos.json
    # on disk just to satisfy make_header_build/make_sections_build.
    monkeypatch.setattr(rf, "build_all", lambda cfg: [])


def test_max_prs_zero_defers_every_eligible_repo_and_attempts_no_prs(monkeypatch, cfg, rec):
    """Regression test for the None-vs-0 fix: with the brief's original
    `cap = cfg.get("max_prs") or len(targets)`, an explicit 0 is falsy and
    silently falls back to "no cap", so this would fail (capped == 0 and
    get_readme would be called for every repo) without the `is not None` fix.
    """
    cfg["max_prs"] = 0
    repos = [dict(rec, name=f"repo{i}") for i in range(3)]
    _data(cfg, repos)

    calls = []
    monkeypatch.setattr(rf, "get_readme", lambda repo: calls.append(repo) or (None, None, None))

    counts = rf.cmd_harmonize_pr(cfg, commit=True)

    assert counts == {"created": 0, "updated": 0, "unchanged": 0, "failed": 0,
                      "capped": 3, "would_pr": 0}
    assert calls == []  # not one repo's README was even fetched -- no work attempted


def test_max_prs_none_applies_no_cap(monkeypatch, cfg, rec):
    cfg["max_prs"] = None  # key present but unset, as if never configured
    repos = [dict(rec, name=f"repo{i}") for i in range(3)]
    _data(cfg, repos)

    calls = []
    monkeypatch.setattr(rf, "get_readme", lambda repo: calls.append(repo) or (None, None, None))

    counts = rf.cmd_harmonize_pr(cfg)

    assert counts["capped"] == 0
    assert counts["unchanged"] == 3
    assert sorted(calls) == ["acme/repo0", "acme/repo1", "acme/repo2"]


def test_positive_cap_smaller_than_eligible_set_processes_exactly_that_many(monkeypatch, cfg, rec):
    cfg["max_prs"] = 2
    repos = [dict(rec, name=f"repo{i}") for i in range(5)]
    _data(cfg, repos)

    calls = []
    monkeypatch.setattr(rf, "get_readme", lambda repo: calls.append(repo) or (None, None, None))

    counts = rf.cmd_harmonize_pr(cfg)

    assert len(calls) == 2
    assert counts["capped"] == 3
    assert counts["unchanged"] == 2


def test_negative_max_prs_is_clamped_to_zero_not_treated_as_a_trailing_slice(monkeypatch, cfg, rec):
    """Without clamping, `targets[:-1]` / `targets[-1:]` would reinterpret a
    negative cap as "all but the last N", still processing (and PR-ing)
    most of the eligible set instead of opening none."""
    cfg["max_prs"] = -1
    repos = [dict(rec, name=f"repo{i}") for i in range(3)]
    _data(cfg, repos)

    calls = []
    monkeypatch.setattr(rf, "get_readme", lambda repo: calls.append(repo) or (None, None, None))

    counts = rf.cmd_harmonize_pr(cfg, commit=True)

    assert counts == {"created": 0, "updated": 0, "unchanged": 0, "failed": 0,
                      "capped": 3, "would_pr": 0}
    assert calls == []


def test_counts_tally_every_key_with_a_mix_of_outcomes_including_failed(monkeypatch, cfg, rec):
    names = ["created_repo", "updated_repo", "unchanged_repo", "failed_repo"]
    repos = [dict(rec, name=n) for n in names]
    _data(cfg, repos)

    # Every repo has non-empty content and harmonize_content always changes
    # it, so every repo reaches open_or_update_pr.
    monkeypatch.setattr(rf, "get_readme", lambda repo: ("old content", "sha1", "README.md"))
    monkeypatch.setattr(rf, "harmonize_content", lambda pairs, r, repo, content: content + " NEW")

    action_by_repo = {
        "acme/created_repo": ("created", "url1"),
        "acme/updated_repo": ("updated", "url2"),
        "acme/unchanged_repo": ("unchanged", ""),
        "acme/failed_repo": ("failed", "API rate limit exceeded"),
    }
    monkeypatch.setattr(rf, "open_or_update_pr",
                         lambda repo, r, new, path, cfg: action_by_repo[repo])

    # A missing key for any of these four actions would KeyError here rather
    # than surface as a wrong count -- exactly the failure mode a real sweep
    # across ~220 repos cannot afford to hit mid-run.
    counts = rf.cmd_harmonize_pr(cfg, commit=True)

    assert counts == {"created": 1, "updated": 1, "unchanged": 1, "failed": 1,
                      "capped": 0, "would_pr": 0}


def test_without_commit_the_pr_path_writes_nothing(monkeypatch, cfg, rec):
    """The module docstring and README both promise nothing is written without
    --commit. Before the fix `args.commit` was never consulted on the --pr
    branch, so `harmonize --pr` opened up to max_prs real branches and PRs
    across foreign repositories for someone expecting a preview."""
    repos = [dict(rec, name=f"repo{i}") for i in range(2)]
    _data(cfg, repos)
    monkeypatch.setattr(rf, "get_readme", lambda repo: ("old content", "sha1", "README.md"))
    monkeypatch.setattr(rf, "harmonize_content", lambda pairs, r, repo, content: content + " NEW")

    def explode(*a, **k):
        raise AssertionError("open_or_update_pr must not be reached in a dry run")

    monkeypatch.setattr(rf, "open_or_update_pr", explode)

    counts = rf.cmd_harmonize_pr(cfg)  # no commit=True

    assert counts["would_pr"] == 2
    assert counts["created"] == counts["updated"] == counts["failed"] == 0


def test_dry_run_reports_would_pr_only_for_repos_whose_content_actually_changes(
        monkeypatch, cfg, rec):
    """"What it would do" has to mean what it would *do*, not who is eligible:
    an eligible repo whose builds produce no change gets no PR either way."""
    _data(cfg, [dict(rec, name="changes"), dict(rec, name="stable")])
    monkeypatch.setattr(rf, "get_readme", lambda repo: ("old content", "sha1", "README.md"))
    monkeypatch.setattr(rf, "harmonize_content", lambda pairs, r, repo, content:
                        content + " NEW" if repo.endswith("changes") else content)
    monkeypatch.setattr(rf, "open_or_update_pr", lambda *a, **k: pytest.fail(
        "a dry run must not reach the write path"))

    counts = rf.cmd_harmonize_pr(cfg)

    assert counts["would_pr"] == 1
    assert counts["unchanged"] == 1


def test_the_run_prints_eligible_and_deferred_repository_names(monkeypatch, cfg, rec, capsys):
    """The forge-harmonize dry run is the documented way to answer "which repos
    would be touched?" and "did adding forge-ignore take this repo out?" —
    counts alone cannot answer either."""
    cfg["max_prs"] = 1
    _data(cfg, [dict(rec, name="first"), dict(rec, name="second"),
                dict(rec, name="ignored", topics=["forge-ignore"])])
    monkeypatch.setattr(rf, "get_readme", lambda repo: (None, None, None))

    rf.cmd_harmonize_pr(cfg)
    out = capsys.readouterr().out

    assert "eligible: acme/first" in out
    assert "deferred: acme/second" in out
    assert "ignored" not in out  # opted out -- neither eligible nor deferred


def test_the_pre_cap_eligible_count_is_reported_not_the_post_slice_one(
        monkeypatch, cfg, rec, capsys):
    """With max_prs=0 the old message read "0 eligible repo(s) · 3 deferred",
    which invites the reading "there was nothing to do"."""
    cfg["max_prs"] = 0
    _data(cfg, [dict(rec, name=f"repo{i}") for i in range(3)])

    rf.cmd_harmonize_pr(cfg)
    out = capsys.readouterr().out

    assert "3 eligible repo(s)" in out
    assert "0 eligible repo(s)" not in out


def test_one_repo_raising_does_not_abandon_the_rest_of_the_run(monkeypatch, cfg, rec):
    """ThreadPoolExecutor.map re-raises on iteration, so an unexpected
    exception on repo #1 used to abandon every repo after it. api() returning
    raw stdout (a str) on a JSON decode failure makes that reachable: several
    guards use substring `in` checks a str passes before the subscript raises."""
    _data(cfg, [dict(rec, name=n) for n in ("good1", "poison", "good2")])
    monkeypatch.setattr(rf, "get_readme", lambda repo: ("old content", "sha1", "README.md"))

    def harmonize(pairs, r, repo, content):
        if repo.endswith("poison"):
            raise TypeError("string indices must be integers")  # what a str response raises
        return content + " NEW"

    monkeypatch.setattr(rf, "harmonize_content", harmonize)
    monkeypatch.setattr(rf, "open_or_update_pr",
                        lambda repo, r, new, path, cfg: ("created", "url"))

    counts = rf.cmd_harmonize_pr(cfg, commit=True)

    assert counts["failed"] == 1
    assert counts["created"] == 2  # both healthy repos still processed


def test_a_raising_repo_is_reported_with_its_name_and_the_exception(monkeypatch, cfg, rec, capsys):
    _data(cfg, [dict(rec, name="poison")])
    monkeypatch.setattr(rf, "get_readme", lambda repo: ("old content", "sha1", "README.md"))

    def boom(pairs, r, repo, content):
        raise KeyError("tree")

    monkeypatch.setattr(rf, "harmonize_content", boom)

    counts = rf.cmd_harmonize_pr(cfg, commit=True)
    out = capsys.readouterr().out

    assert counts["failed"] == 1
    assert "acme/poison" in out
    assert "KeyError" in out
