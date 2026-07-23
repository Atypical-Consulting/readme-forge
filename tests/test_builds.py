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


import json


def _write_repos(cfg, records):
    with open(f"{cfg['workdir']}/repos.json", "w") as fh:
        json.dump(records, fh)


def test_header_build_injects_badges_after_h1(cfg, rec, bare_readme):
    _write_repos(cfg, [{"owner": "acme", "name": "widget", "license_key": "mit"}])
    build = rf.make_header_build(cfg)
    out = build(rec, "acme/widget", bare_readme)
    assert "<!-- portfolio-badges:start -->" in out
    assert "img.shields.io" in out
    assert out.index("# Widget") < out.index("<!-- portfolio-badges:start -->")


def test_header_build_is_idempotent(cfg, rec, bare_readme):
    _write_repos(cfg, [{"owner": "acme", "name": "widget", "license_key": "mit"}])
    build = rf.make_header_build(cfg)
    once = build(rec, "acme/widget", bare_readme)
    assert once == build(rec, "acme/widget", once)


def test_sections_build_adds_contributing_and_license(cfg, rec, bare_readme):
    _write_repos(cfg, [{"owner": "acme", "name": "widget", "license_key": "mit"}])
    build = rf.make_sections_build(cfg)
    out = build(rec, "acme/widget", bare_readme)
    assert "## Contributing" in out
    assert "MIT License" in out


def test_sections_build_returns_none_when_both_present(cfg, rec, bare_readme):
    _write_repos(cfg, [{"owner": "acme", "name": "widget", "license_key": "mit"}])
    rec["contributing"], rec["license_sec"] = True, True
    build = rf.make_sections_build(cfg)
    assert build(rec, "acme/widget", bare_readme) is None


def test_techstack_build_uses_manifests(cfg, rec, bare_readme, monkeypatch):
    monkeypatch.setattr(rf, "tree", lambda repo: ["src/App.csproj"])
    monkeypatch.setattr(rf, "get_file", lambda repo, path:
                        "<Project><TargetFramework>net8.0</TargetFramework>"
                        '<PackageReference Include="Serilog" /></Project>')
    build = rf.make_techstack_build(cfg)
    out = build(rec, "acme/widget", bare_readme)
    assert "## Tech Stack" in out
    assert ".NET 8" in out
    assert "Serilog" in out


def test_techstack_build_returns_none_without_stack(cfg, rec, bare_readme, monkeypatch):
    monkeypatch.setattr(rf, "tree", lambda repo: [])
    monkeypatch.setattr(rf, "get_file", lambda repo, path: None)
    rec["lang"] = None
    build = rf.make_techstack_build(cfg)
    assert build(rec, "acme/widget", bare_readme) is None


def test_gettingstarted_build_emits_dotnet_commands(cfg, rec, bare_readme, monkeypatch):
    monkeypatch.setattr(rf, "tree", lambda repo: ["src/App.csproj"])
    monkeypatch.setattr(rf, "get_file", lambda repo, path: "<Project Sdk=\"Microsoft.NET.Sdk.Web\"></Project>")
    build = rf.make_gettingstarted_build(cfg)
    out = build(rec, "acme/widget", bare_readme)
    assert "## Getting Started" in out
    assert "git clone https://github.com/acme/widget.git" in out
    assert "dotnet build" in out
    assert "```bash" in out


def test_gettingstarted_build_returns_none_for_unknown_stack(cfg, rec, bare_readme, monkeypatch):
    monkeypatch.setattr(rf, "tree", lambda repo: ["README.md"])
    monkeypatch.setattr(rf, "get_file", lambda repo, path: None)
    build = rf.make_gettingstarted_build(cfg)
    assert build(rec, "acme/widget", bare_readme) is None


def test_sweep_specs_order_is_canonical():
    assert rf.SWEEP_ORDER == ["header", "sections", "techstack", "gettingstarted", "roadmap", "toc"]
