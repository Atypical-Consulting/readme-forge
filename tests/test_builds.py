import readme_forge as rf


def test_roadmap_build_inserts_section(cfg, rec, bare_readme):
    build = rf.make_roadmap_build(cfg)
    out = build(rec, "acme/widget", bare_readme)
    assert "## Roadmap" in out
    assert "https://github.com/acme/widget/issues" in out
    assert "<!-- portfolio-roadmap:start -->" in out


def test_roadmap_build_is_idempotent(cfg, rec, bare_readme):
    build = rf.make_roadmap_build(cfg)
    once = build(rec, "acme/widget", bare_readme)
    twice = build(rec, "acme/widget", once)
    assert once == twice


def test_toc_build_needs_four_sections(cfg, rec, bare_readme):
    build = rf.make_toc_build(cfg)
    assert build(rec, "acme/widget", bare_readme) is None


def test_toc_build_lists_h2_sections(cfg, rec):
    md = "# Widget\n\n## Alpha\n\n## Beta\n\n## Gamma\n\n## Delta\n"
    build = rf.make_toc_build(cfg)
    out = build(rec, "acme/widget", md)
    assert "## Table of Contents" in out
    for anchor in ("#alpha", "#beta", "#gamma", "#delta"):
        assert anchor in out


def test_toc_build_ignores_headings_inside_code_fences(cfg, rec):
    md = "# Widget\n\n## Alpha\n\n```\n## NotASection\n```\n\n## Beta\n\n## Gamma\n\n## Delta\n"
    build = rf.make_toc_build(cfg)
    out = build(rec, "acme/widget", md)
    assert "#notasection" not in out


def test_toc_build_is_idempotent(cfg, rec):
    md = "# Widget\n\n## Alpha\n\n## Beta\n\n## Gamma\n\n## Delta\n"
    build = rf.make_toc_build(cfg)
    once = build(rec, "acme/widget", md)
    twice = build(rec, "acme/widget", once)
    assert once == twice
