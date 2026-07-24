from datetime import datetime, timedelta, timezone

import readme_forge as rf

NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


def _aged(rec, days):
    rec["created_at"] = (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    return rec


def test_eligible_for_a_mature_fixable_repo(cfg, rec):
    assert rf.eligible(_aged(rec, 365), cfg, now=NOW) is True


def test_repo_inside_grace_period_is_skipped(cfg, rec):
    assert rf.eligible(_aged(rec, 5), cfg, now=NOW) is False


def test_repo_just_past_grace_period_is_eligible(cfg, rec):
    assert rf.eligible(_aged(rec, 31), cfg, now=NOW) is True


def test_repo_one_day_short_of_grace_period_is_not_eligible(cfg, rec):
    assert rf.eligible(_aged(rec, 29), cfg, now=NOW) is False


def test_repo_exactly_at_grace_period_boundary_is_eligible(cfg, rec):
    # Exactly `grace_days` old is not "younger than grace_days" — the age
    # check only blocks a strict `age < grace_days`, so day 30 (with
    # grace_days=30) is the first day a repo is allowed through.
    assert rf.eligible(_aged(rec, 30), cfg, now=NOW) is True


def test_malformed_created_at_fails_closed(cfg, rec):
    # A present-but-unparseable created_at must not crash the run, and must
    # not be treated as "unknown -> proceed" the way a *missing* field is:
    # it's anomalous data, so the safer call for an unattended PR bot is to
    # skip the repo this run rather than risk acting on data it can't trust.
    rec["created_at"] = "not-a-timestamp"
    assert rf.eligible(rec, cfg, now=NOW) is False


def test_non_string_created_at_fails_closed(cfg, rec):
    # A hand-edited or truncated .forge/data.json cache could carry a
    # non-string created_at (e.g. an int). `.replace()` on it raises
    # AttributeError *before* fromisoformat ever runs, so this must be
    # rejected as a distinct shape from the malformed-string case above.
    rec["created_at"] = 20200101
    assert rf.eligible(rec, cfg, now=NOW) is False


def test_ignore_topic_opts_a_repo_out(cfg, rec):
    rec = _aged(rec, 365)
    rec["topics"] = ["dotnet", "forge-ignore"]
    assert rf.eligible(rec, cfg, now=NOW) is False


def test_missing_created_at_does_not_block(cfg, rec):
    rec.pop("created_at", None)
    assert rf.eligible(rec, cfg, now=NOW) is True


def test_repo_without_readme_is_not_eligible(cfg, rec):
    rec = _aged(rec, 365)
    rec["no_readme"] = True
    assert rf.eligible(rec, cfg, now=NOW) is False


def test_archived_and_fork_repos_are_not_eligible(cfg, rec):
    assert rf.eligible({**_aged(dict(rec), 365), "archived": True}, cfg, now=NOW) is False
    assert rf.eligible({**_aged(dict(rec), 365), "fork": True}, cfg, now=NOW) is False


def test_excluded_name_is_not_eligible(cfg, rec):
    rec = _aged(rec, 365)
    cfg["exclude_names"] = ["widget"]
    assert rf.eligible(rec, cfg, now=NOW) is False


def test_repo_with_only_content_gaps_is_not_eligible(cfg, rec):
    rec = _aged(rec, 365)
    rec.update(badges=3, toc=True, tech_stack=True, install=True,
               roadmap=True, contributing=True, license_sec=True)
    assert rf.eligible(rec, cfg, now=NOW) is False


def test_defaults_carry_the_agreed_guardrail_values():
    assert rf.DEFAULTS["grace_days"] == 30
    assert rf.DEFAULTS["ignore_topic"] == "forge-ignore"
    assert rf.DEFAULTS["max_prs"] == 10
    assert rf.DEFAULTS["pr_branch"] == "forge/harmonize"
    assert rf.DEFAULTS["report_label"] == "forge-report"
