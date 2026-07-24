import readme_forge as rf


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
