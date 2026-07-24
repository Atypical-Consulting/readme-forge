import json
import subprocess
import sys

import readme_forge as rf


def _data(cfg, records):
    with open(f"{cfg['workdir']}/data.json", "w") as fh:
        json.dump(records, fh)


def test_sweep_targets_selects_only_repos_missing_the_essential(cfg, rec):
    other = dict(rec, name="done", roadmap=True)
    _data(cfg, [rec, other])
    targets = rf._sweep_targets(cfg, rf._spec("roadmap"))
    assert [t["name"] for t in targets] == ["widget"]


def test_sweep_targets_skips_repos_without_readme(cfg, rec):
    _data(cfg, [dict(rec, no_readme=True)])
    assert rf._sweep_targets(cfg, rf._spec("roadmap")) == []


def test_sections_target_selection_uses_the_compound_predicate(cfg, rec):
    only_license = dict(rec, name="lic", contributing=True, license_sec=False)
    neither = dict(rec, name="none", contributing=True, license_sec=True)
    _data(cfg, [only_license, neither])
    targets = rf._sweep_targets(cfg, rf._spec("sections"))
    assert [t["name"] for t in targets] == ["lic"]


def test_cli_exposes_all_six_sweeps_and_the_pr_flag():
    out = subprocess.run([sys.executable, "readme_forge.py", "harmonize", "--help"],
                         capture_output=True, text=True).stdout
    assert "--pr" in out
    for name in ("header", "sections", "techstack", "gettingstarted", "roadmap", "toc"):
        assert name in rf.SWEEPS
