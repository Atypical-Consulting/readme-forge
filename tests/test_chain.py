import json

import readme_forge as rf


def test_fixable_true_when_a_sweep_applies(rec):
    assert rf.fixable(rec) is True


def test_fixable_false_when_only_content_gaps_remain(rec):
    """banner_logo / features_sec / usage cannot be fixed by any sweep."""
    rec.update(badges=3, toc=True, tech_stack=True, install=True,
               roadmap=True, contributing=True, license_sec=True)
    rec.update(banner_logo=False, features_sec=False, usage=False, getting_started=False)
    assert rf.fixable(rec) is False


def test_harmonize_content_skips_non_applicable_builds(cfg, rec, bare_readme):
    calls = []

    def make_pair(name, applies):
        spec = {"name": name, "applies": applies}

        def build(r, repo, content):
            calls.append(name)
            return content + f"\n<{name}>"

        return (spec, build)

    pairs = [make_pair("yes", lambda r: True), make_pair("no", lambda r: False)]
    out = rf.harmonize_content(pairs, rec, "acme/widget", bare_readme)
    assert calls == ["yes"]
    assert "<yes>" in out and "<no>" not in out


def test_harmonize_content_chains_output_of_each_build(cfg, rec, bare_readme):
    spec = {"name": "s", "applies": lambda r: True}
    pairs = [(spec, lambda r, repo, c: c + "\nA"), (spec, lambda r, repo, c: c + "\nB")]
    out = rf.harmonize_content(pairs, rec, "acme/widget", bare_readme)
    assert out.endswith("\nA\nB")


def test_harmonize_content_tolerates_none_return(cfg, rec, bare_readme):
    spec = {"name": "s", "applies": lambda r: True}
    pairs = [(spec, lambda r, repo, c: None), (spec, lambda r, repo, c: c + "\nB")]
    out = rf.harmonize_content(pairs, rec, "acme/widget", bare_readme)
    assert out.endswith("\nB")


def test_full_chain_makes_a_bare_readme_score_the_sweepable_essentials(
        cfg, rec, bare_readme, monkeypatch):
    """The six real builds, chained once, must satisfy every essential a sweep owns."""
    with open(f"{cfg['workdir']}/repos.json", "w") as fh:
        json.dump([{"owner": "acme", "name": "widget", "license_key": "mit"}], fh)
    monkeypatch.setattr(rf, "tree", lambda repo: ["src/App.csproj"])
    monkeypatch.setattr(rf, "get_file", lambda repo, path:
                        '<Project Sdk="Microsoft.NET.Sdk.Web">'
                        "<TargetFramework>net8.0</TargetFramework>"
                        '<PackageReference Include="Serilog" /></Project>')

    out = rf.harmonize_content(rf.build_all(cfg), rec, "acme/widget", bare_readme)
    feat = rf.detect(out)

    for key in ("badges", "toc", "tech_stack", "install", "roadmap", "contributing", "license_sec"):
        assert rf.essential_ok(feat, key), f"{key} not satisfied by the chain"


def test_full_chain_is_idempotent(cfg, rec, bare_readme, monkeypatch):
    with open(f"{cfg['workdir']}/repos.json", "w") as fh:
        json.dump([{"owner": "acme", "name": "widget", "license_key": "mit"}], fh)
    monkeypatch.setattr(rf, "tree", lambda repo: ["src/App.csproj"])
    monkeypatch.setattr(rf, "get_file", lambda repo, path:
                        '<Project Sdk="Microsoft.NET.Sdk.Web">'
                        "<TargetFramework>net8.0</TargetFramework></Project>")
    once = rf.harmonize_content(rf.build_all(cfg), rec, "acme/widget", bare_readme)
    twice = rf.harmonize_content(rf.build_all(cfg), rec, "acme/widget", once)
    assert once == twice
