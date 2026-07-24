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

    counts = rf.cmd_harmonize_pr(cfg)

    assert counts == {"created": 0, "updated": 0, "unchanged": 0, "failed": 0, "capped": 3}
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

    counts = rf.cmd_harmonize_pr(cfg)

    assert counts == {"created": 0, "updated": 0, "unchanged": 0, "failed": 0, "capped": 3}
    assert calls == []


def test_counts_tally_all_five_keys_with_a_mix_of_outcomes_including_failed(monkeypatch, cfg, rec):
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
    counts = rf.cmd_harmonize_pr(cfg)

    assert counts == {"created": 1, "updated": 1, "unchanged": 1, "failed": 1, "capped": 0}
