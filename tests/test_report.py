from datetime import datetime, timedelta, timezone

import readme_forge as rf

NOW = datetime.now(timezone.utc)


def _section(body, heading):
    """The lines of one `## heading` section, up to the next `##`."""
    lines = body.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(f"## {heading}")), None)
    if start is None:
        return []
    rest = lines[start + 1:]
    end = next((i for i, ln in enumerate(rest) if ln.startswith("## ")), len(rest))
    return rest[:end]


def test_report_body_lists_incomplete_repos_with_missing_essentials(cfg, rec):
    incomplete = dict(rec, name="widget")
    complete = dict(rec, name="polished", badges=3, toc=True, tech_stack=True,
                    install=True, usage=True, roadmap=True, contributing=True,
                    license_sec=True, banner_logo=True, features_sec=True, code_blocks=2)
    body = rf.report_body([incomplete, complete], cfg)
    assert "widget" in body
    assert "polished" not in body
    assert "roadmap" in body


def test_report_body_reports_totals(cfg, rec):
    body = rf.report_body([dict(rec, name="a"), dict(rec, name="b")], cfg)
    assert "0/2" in body


def test_report_body_marks_content_only_gaps_as_human_work(cfg, rec):
    content_only = dict(rec, name="needs-prose", badges=3, toc=True, tech_stack=True,
                        install=True, roadmap=True, contributing=True, license_sec=True)
    body = rf.report_body([content_only], cfg)
    assert "needs-prose" in body
    assert "human" in body.lower()


def test_report_body_is_stable_for_identical_input(cfg, rec):
    data = [dict(rec, name="a"), dict(rec, name="b")]
    assert rf.report_body(data, cfg) == rf.report_body(data, cfg)


def test_forge_ignored_repo_is_not_advertised_as_a_pending_pr_target(cfg, rec):
    """Bucketing on `fixable` listed an opted-out repo under "the forge opens
    PRs for these on its next harmonize run" — a promise it will never keep,
    and the exact way `forge-ignore` failed to feel like an opt-out."""
    ignored = dict(rec, name="wip", topics=["forge-ignore"])
    body = rf.report_body([ignored], cfg)

    assert "wip" not in "\n".join(_section(body, "Fixable automatically"))
    held = "\n".join(_section(body, "Fixable, but held back by a guardrail"))
    assert "wip" in held
    assert "forge-ignore" in held


def test_repo_inside_the_grace_period_is_held_back_not_advertised(cfg, rec):
    young = dict(rec, name="fresh",
                 created_at=(NOW - timedelta(days=3)).isoformat().replace("+00:00", "Z"))
    body = rf.report_body([young], cfg)

    assert "fresh" not in "\n".join(_section(body, "Fixable automatically"))
    assert "grace period" in "\n".join(_section(body, "Fixable, but held back by a guardrail"))


def test_an_eligible_repo_is_still_listed_as_fixable_automatically(cfg, rec):
    body = rf.report_body([dict(rec, name="widget")], cfg)

    assert "widget" in "\n".join(_section(body, "Fixable automatically"))
    assert _section(body, "Fixable, but held back by a guardrail") == []


def test_held_back_repos_keep_their_missing_essentials_visible(cfg, rec):
    """The opt-out stops writes, not reporting: the gap must stay on the board."""
    body = rf.report_body([dict(rec, name="wip", topics=["forge-ignore"])], cfg)
    held = "\n".join(_section(body, "Fixable, but held back by a guardrail"))
    assert "roadmap" in held and "tech_stack" in held


def test_report_body_refuses_to_call_an_empty_scan_a_success(cfg):
    """Zero scored repositories is a broken run, not a clean portfolio. The
    old "**0/0** ... Every scored repository meets the standard. Nothing to do."
    is indistinguishable from real success, so a token whose repository
    selection drifted (or a mistyped FORGE_ORGS) would quietly report green
    week after week."""
    body = rf.report_body([], cfg)
    assert "Nothing to do." not in body
    assert "0/0" not in body
    assert "FORGE_ORGS" in body


def test_report_body_sentinels_when_every_scored_repo_is_complete(cfg, rec):
    complete = dict(rec, name="polished", badges=3, toc=True, tech_stack=True,
                    install=True, usage=True, roadmap=True, contributing=True,
                    license_sec=True, banner_logo=True, features_sec=True, code_blocks=2)
    body = rf.report_body([complete], cfg)
    assert "1/1" in body
    assert "Every scored repository meets the standard. Nothing to do." in body
    assert "Fixable automatically" not in body
    assert "Needs a human" not in body
