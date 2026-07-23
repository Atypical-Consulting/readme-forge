# readme-forge GitHub Action Watchdog — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn readme-forge into a scheduled GitHub Actions watchdog that scans the configured accounts, publishes a live dashboard to Pages, reports drift in one issue, and opens harmonization pull requests.

**Architecture:** Refactor the six sweeps into a declarative `SWEEP_SPECS` registry whose content transforms are exposed as `make_*_build(cfg) -> build(r, repo, content)` factories. A new `harmonize --pr` driver chains the applicable builds **in memory** (one GET, one write) and opens an idempotent PR per repo. Two workflows split privilege: a read-only watcher and a write-scoped harmonizer.

**Tech Stack:** Python 3.11+ (stdlib only, no runtime deps), `gh` CLI for all GitHub access, pytest for tests, GitHub Actions + GitHub Pages.

## Global Constraints

- **Backward compatibility is mandatory.** `python readme_forge.py sweep <which> [--commit]`, `run`, and `harmonize` (without `--pr`) must behave exactly as they do today. The refactor is internal.
- **No runtime dependencies.** `readme_forge.py` and `dashboard.py` stay stdlib-only. pytest is a dev-only dependency.
- **Every write is idempotent.** Sweeps use HTML marker pairs; the PR flow reuses an existing branch and never opens a second PR for the same branch.
- **The bot never commits to a target repo's default branch.** It only pushes to `pr_branch` and opens PRs.
- **`forge.config.json` stays generic.** Accounts are never committed there; workflows pass `--org` from the `FORGE_ORGS` repository variable.
- **Config defaults, exact values:** `grace_days` = `30`, `ignore_topic` = `"forge-ignore"`, `max_prs` = `10`, `pr_branch` = `"forge/harmonize"`, `report_label` = `"forge-report"`.
- **`SWEEP_ORDER` is derived from `SWEEP_SPECS` order** and must remain: `header, sections, techstack, gettingstarted, roadmap, toc` (toc last).
- **PR eligibility = at least one sweep applies**, never "an essential is missing".
- Tests must not perform real network calls. Monkeypatch `readme_forge.tree` / `readme_forge.get_file` / `readme_forge.api`.

---

## File Structure

| File | Responsibility |
|---|---|
| `readme_forge.py` (modify) | Registry, pure build factories, chaining, eligibility, PR mechanics, CLI |
| `forge.config.json` (modify) | New guardrail/PR keys; curated exclusions |
| `tests/conftest.py` (create) | Shared fixtures: config, repo records, README fixtures |
| `tests/test_builds.py` (create) | Per-sweep content transforms + idempotency |
| `tests/test_chain.py` (create) | `harmonize_content` ordering and completeness |
| `tests/test_eligibility.py` (create) | Grace period, ignore topic, exclusions, fixability |
| `tests/test_pr.py` (create) | PR mechanics idempotency with a fake API |
| `tests/test_backcompat.py` (create) | Sweep target selection unchanged |
| `.github/workflows/forge-watch.yml` (create) | Read-only: scan → dashboard → Pages → issue → state commit |
| `.github/workflows/forge-harmonize.yml` (create) | Write: `harmonize --pr` |
| `state/` (create) | Tracked `data.json` + `baseline.json` snapshots |
| `README.md` (modify) | Document the Action, setup, and guardrails |

---

## Task 1: Test scaffolding + registry with the two pure sweeps

**Files:**
- Modify: `readme_forge.py:458-468` (`sweep_roadmap`), `readme_forge.py:511-534` (`sweep_toc`), `readme_forge.py:537-540` (`SWEEPS`/`SWEEP_ORDER`)
- Create: `tests/conftest.py`, `tests/test_builds.py`
- Create: `pytest.ini`

**Interfaces:**
- Produces: `make_roadmap_build(cfg) -> build`, `make_toc_build(cfg) -> build`, `SWEEP_SPECS` (list of dicts with keys `name`, `applies`, `make_build`, `msg`), `_sweep_targets(cfg, spec)`, `run_sweep_by_name(cfg, commit, name)`, `SWEEPS` (name → callable taking `(cfg, commit)`), `SWEEP_ORDER` (list of names).
- Every `build` has signature `build(r, repo, content) -> str | None`, where `r` is a scan record dict, `repo` is `"owner/name"`, and `None` means "no change applicable".

- [ ] **Step 1: Create pytest config**

Create `pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -q
```

- [ ] **Step 2: Create shared fixtures**

Create `tests/conftest.py`:

```python
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import readme_forge as rf


@pytest.fixture
def cfg(tmp_path):
    c = dict(rf.DEFAULTS)
    c["workdir"] = str(tmp_path)
    return c


@pytest.fixture
def rec():
    """A scan record for a repo missing everything the sweeps can fix."""
    return {
        "owner": "acme", "name": "widget", "fork": False, "archived": False,
        "empty": False, "lang": "C#", "default_branch": "main",
        "license_key": "mit", "created_at": "2020-01-01T00:00:00Z", "topics": [],
        "badges": 0, "toc": False, "tech_stack": False, "install": False,
        "usage": False, "getting_started": False, "roadmap": False,
        "contributing": False, "license_sec": False, "banner_logo": False,
        "features_sec": False, "code_blocks": 0,
    }


@pytest.fixture
def bare_readme():
    return "# Widget\n\nA small thing that does a job.\n"
```

- [ ] **Step 3: Write the failing tests for the two pure builds**

Create `tests/test_builds.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python -m pytest tests/test_builds.py -q`
Expected: FAIL with `AttributeError: module 'readme_forge' has no attribute 'make_roadmap_build'`

- [ ] **Step 5: Extract the two pure builds into factories**

In `readme_forge.py`, replace `sweep_roadmap` (currently lines 458-468) with:

```python
def make_roadmap_build(cfg):
    S, E = "<!-- portfolio-roadmap:start -->", "<!-- portfolio-roadmap:end -->"

    def build(r, repo, content):
        block = (f"{S}\n\n## Roadmap\n\nPlanned work and known limitations are tracked in the "
                 f"[open issues](https://github.com/{repo}/issues). Contributions toward them are welcome.\n\n{E}")
        if S in content and E in content:
            return marker_replace(content, S, E, block)
        return insert_before_community(content, block)

    return build
```

Replace `sweep_toc` (currently lines 511-534) with:

```python
def make_toc_build(cfg):
    S, E = "<!-- portfolio-toc:start -->", "<!-- portfolio-toc:end -->"
    SKIP = ("table of contents", "contents", "sommaire", "toc")

    def build(r, repo, content):
        titles, fence = [], False
        for ln in content.splitlines():
            if ln.lstrip().startswith("```"):
                fence = not fence
                continue
            if fence:
                continue
            m = re.match(r"^##\s+(.+?)\s*#*$", ln)
            if m and m.group(1).strip().lower() not in SKIP:
                titles.append(m.group(1).strip())
        if len(titles) < 4:
            return None
        block = "\n".join([S, "", "## Table of Contents", "", *[f"- [{t}](#{_slug(t)})" for t in titles], "", E])
        if S in content and E in content:
            return marker_replace(content, S, E, block)
        r2 = insert_after(content, ["<!-- portfolio-badges:end -->"], block)
        return r2 if r2 is not None else insert_after_h1(content, block)

    return build
```

Note: `_slug` (line 505) must remain defined **above** `make_toc_build`.

- [ ] **Step 6: Add the registry and the generic sweep runner**

Replace the `SWEEPS` / `SWEEP_ORDER` block (currently lines 537-540) with:

```python
SWEEP_SPECS = [
    {"name": "roadmap", "applies": lambda r: not essential_ok(r, "roadmap"),
     "make_build": make_roadmap_build, "msg": "add Roadmap section"},
    {"name": "toc", "applies": lambda r: not essential_ok(r, "toc"),
     "make_build": make_toc_build, "msg": "add table of contents"},
]


def _spec(name):
    return next(s for s in SWEEP_SPECS if s["name"] == name)


def _sweep_targets(cfg, spec):
    """Active, non-excluded repos with a README that the given sweep applies to."""
    data = json.load(open(f"{cfg['workdir']}/data.json"))
    return [r for r in data if _scored(r, cfg) and not r.get("no_readme") and spec["applies"](r)]


def run_sweep_by_name(cfg, commit, name):
    spec = _spec(name)
    _run_sweep(cfg, _sweep_targets(cfg, spec), spec["make_build"](cfg),
               f"{cfg['commit_prefix']} {spec['msg']}", commit)


SWEEPS = {s["name"]: (lambda cfg, commit, _n=s["name"]: run_sweep_by_name(cfg, commit, _n))
          for s in SWEEP_SPECS}
# order in which harmonize runs them (toc last, once other sections exist)
SWEEP_ORDER = [s["name"] for s in SWEEP_SPECS]
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_builds.py -q`
Expected: PASS (6 passed)

- [ ] **Step 8: Verify the CLI still parses both sweeps**

Run: `python readme_forge.py sweep --help`
Expected: help text listing `{roadmap,toc}` as choices for `which`

- [ ] **Step 9: Commit**

```bash
git add pytest.ini tests/conftest.py tests/test_builds.py readme_forge.py
git commit -m "refactor: extract pure roadmap/toc builds into a sweep registry"
```

---

## Task 2: Move the remaining four sweeps into the registry

**Files:**
- Modify: `readme_forge.py` — `sweep_header` (lines 352-382), `sweep_sections` (385-408), `sweep_techstack` (418-455), `sweep_gettingstarted` (471-502), and `SWEEP_SPECS`
- Modify: `tests/test_builds.py`

**Interfaces:**
- Consumes: `SWEEP_SPECS`, `_sweep_targets`, `run_sweep_by_name` from Task 1.
- Produces: `make_header_build(cfg)`, `make_sections_build(cfg)`, `make_techstack_build(cfg)`, `make_gettingstarted_build(cfg)`; `SWEEP_SPECS` complete and ordered `header, sections, techstack, gettingstarted, roadmap, toc`.
- `make_sections_build` uses the compound predicate `not essential_ok(r, "contributing") or not essential_ok(r, "license_sec")`.

- [ ] **Step 1: Write the failing tests for the four builds**

Append to `tests/test_builds.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_builds.py -q`
Expected: FAIL with `AttributeError: module 'readme_forge' has no attribute 'make_header_build'`

- [ ] **Step 3: Convert the four sweeps to factories**

For each of the four functions, apply the same mechanical transformation used in Task 1: rename `sweep_X(cfg, commit)` to `make_X_build(cfg)`, keep the setup lines and the inner `def build(r, repo, content)` **body exactly as-is**, delete the trailing `_run_sweep(...)` call, and `return build`.

Concretely:

1. `sweep_header` → `make_header_build`: keep lines 353-354 (the `S, E` and `licensed` setup) and the `build` body at lines 356-377 unchanged; drop the `_run_sweep(...)` line and `return build`.
2. `sweep_sections` → `make_sections_build`: keep lines 386-387 (`S, E` and `lic`) and the `build` body at lines 392-406 unchanged. **Delete the local `targets` computation at lines 388-390** — target selection now lives in the spec's `applies` predicate. Drop `_run_sweep(...)`, `return build`.
3. `sweep_techstack` → `make_techstack_build`: keep line 419 and the `build` body at lines 421-453 unchanged; drop `_run_sweep(...)`, `return build`.
4. `sweep_gettingstarted` → `make_gettingstarted_build`: keep line 472 and the `build` body at lines 474-500 unchanged; drop `_run_sweep(...)`, `return build`.

The module-level constants `CONTRIB`, `LIC_NAMES`, `PKG_NOISE`, `TFM` stay where they are and must remain defined above the factories that close over them.

- [ ] **Step 4: Complete the registry**

Replace `SWEEP_SPECS` from Task 1 with the full, correctly ordered list:

```python
SWEEP_SPECS = [
    {"name": "header", "applies": lambda r: not essential_ok(r, "badges"),
     "make_build": make_header_build, "msg": "add standardized badge header"},
    {"name": "sections",
     "applies": lambda r: not essential_ok(r, "contributing") or not essential_ok(r, "license_sec"),
     "make_build": make_sections_build, "msg": "add contributing and license sections"},
    {"name": "techstack", "applies": lambda r: not essential_ok(r, "tech_stack"),
     "make_build": make_techstack_build, "msg": "add Tech Stack section derived from manifests"},
    {"name": "gettingstarted", "applies": lambda r: not essential_ok(r, "install"),
     "make_build": make_gettingstarted_build, "msg": "add Getting Started section"},
    {"name": "roadmap", "applies": lambda r: not essential_ok(r, "roadmap"),
     "make_build": make_roadmap_build, "msg": "add Roadmap section"},
    {"name": "toc", "applies": lambda r: not essential_ok(r, "toc"),
     "make_build": make_toc_build, "msg": "add table of contents"},
]
```

Move this block **below** all six `make_*_build` definitions.

- [ ] **Step 5: Delete the now-unused `_targets` helper**

Remove `_targets` (currently lines 321-324). `_sweep_targets(cfg, spec)` replaces it. Verify nothing references it:

Run: `grep -n "_targets(cfg," readme_forge.py`
Expected: only matches for `_sweep_targets(cfg, spec)`

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest -q`
Expected: PASS (15 passed)

- [ ] **Step 7: Verify all six sweeps are still exposed on the CLI**

Run: `python readme_forge.py sweep --help`
Expected: choices `{header,sections,techstack,gettingstarted,roadmap,toc}`

- [ ] **Step 8: Commit**

```bash
git add readme_forge.py tests/test_builds.py
git commit -m "refactor: move all six sweeps into the declarative registry"
```

---

## Task 3: In-memory chaining

**Files:**
- Modify: `readme_forge.py` (add after `SWEEP_SPECS`)
- Create: `tests/test_chain.py`

**Interfaces:**
- Consumes: `SWEEP_SPECS`, `essential_ok`, `detect`, `richness`.
- Produces:
  - `build_all(cfg) -> list[tuple[spec, build]]` — instantiates each build **once**.
  - `harmonize_content(pairs, r, repo, content) -> str` — applies every applicable build in order; returns possibly-unchanged content.
  - `fixable(r) -> bool` — true when at least one spec applies.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_chain.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_chain.py -q`
Expected: FAIL with `AttributeError: module 'readme_forge' has no attribute 'fixable'`

- [ ] **Step 3: Implement the chaining helpers**

Add to `readme_forge.py`, immediately after the `SWEEP_SPECS` block:

```python
def fixable(r):
    """True when at least one deterministic sweep can improve this repo."""
    return any(s["applies"](r) for s in SWEEP_SPECS)


def build_all(cfg):
    """Instantiate every sweep's build once (they read shared state at construction)."""
    return [(s, s["make_build"](cfg)) for s in SWEEP_SPECS]


def harmonize_content(pairs, r, repo, content):
    """Apply every applicable build in registry order, chaining in memory.

    Returns the transformed content (possibly identical to the input). Each build
    sees the previous build's output, which is why `toc` — running last — picks up
    the sections the earlier builds just inserted.
    """
    out = content
    for spec, build in pairs:
        if not spec["applies"](r):
            continue
        new = build(r, repo, out)
        if new:
            out = new
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -q`
Expected: PASS (22 passed)

- [ ] **Step 5: Commit**

```bash
git add readme_forge.py tests/test_chain.py
git commit -m "feat: chain sweep builds in memory via harmonize_content"
```

---

## Task 4: Guardrails — inventory fields, config keys, eligibility

**Files:**
- Modify: `readme_forge.py` — `DEFAULTS` (lines 42-57), `cmd_inventory` (lines 210-237)
- Modify: `forge.config.json`
- Create: `tests/test_eligibility.py`

**Interfaces:**
- Consumes: `_scored`, `fixable`, `excluded`.
- Produces: `eligible(r, cfg, now=None) -> bool`; inventory records gain `created_at` (ISO-8601 string) and `topics` (list of strings); `DEFAULTS` gains `grace_days`, `ignore_topic`, `max_prs`, `pr_branch`, `report_label`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_eligibility.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_eligibility.py -q`
Expected: FAIL with `AttributeError: module 'readme_forge' has no attribute 'eligible'`

- [ ] **Step 3: Add the new config defaults**

In `readme_forge.py`, add these keys to `DEFAULTS` (after `"max_workers": 10,`):

```python
    # --- watchdog / PR mode ---
    "grace_days": 30,               # skip repos younger than this
    "ignore_topic": "forge-ignore",  # topic that opts a repo out entirely
    "max_prs": 10,                  # cap PRs opened per run
    "pr_branch": "forge/harmonize",
    "report_label": "forge-report",
```

- [ ] **Step 4: Add the datetime import**

At the top of `readme_forge.py`, add to the import block:

```python
from datetime import datetime, timedelta, timezone
```

- [ ] **Step 5: Implement `eligible`**

Add to `readme_forge.py`, immediately after `fixable`:

```python
def eligible(r, cfg, now=None):
    """Harmonization-eligible? Adds README/grace/topic checks on top of `_scored`."""
    if not _scored(r, cfg) or r.get("no_readme") or r.get("empty"):
        return False
    if cfg.get("ignore_topic") and cfg["ignore_topic"] in (r.get("topics") or []):
        return False
    created, days = r.get("created_at"), cfg.get("grace_days") or 0
    if created and days:
        born = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if (now or datetime.now(timezone.utc)) - born < timedelta(days=days):
            return False
    return fixable(r)
```

- [ ] **Step 6: Collect the new inventory fields**

In `cmd_inventory`, extend the `--json` field list (line 218) to include `createdAt` and `repositoryTopics`:

```python
        r = sh(["gh", "repo", "list", owner, "--limit", "1000", "--json",
                "name,isFork,isArchived,stargazerCount,primaryLanguage,defaultBranchRef,"
                "licenseInfo,description,pushedAt,isEmpty,createdAt,repositoryTopics"])
```

And add the two fields to the emitted record (after `"empty": x.get("isEmpty", False),`):

```python
                "created_at": x.get("createdAt"),
                "topics": [t["name"] for t in (x.get("repositoryTopics") or [])],
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest -q`
Expected: PASS (32 passed)

- [ ] **Step 8: Verify the new inventory fields against the live API**

Run: `gh repo list phmatray --limit 1 --json name,createdAt,repositoryTopics`
Expected: JSON containing `createdAt` and `repositoryTopics` keys (confirms the `gh` field names are valid)

- [ ] **Step 9: Add the guardrail keys to the shipped config**

In `forge.config.json`, add after `"max_workers": 10`:

```json
  "grace_days": 30,
  "ignore_topic": "forge-ignore",
  "max_prs": 10,
  "pr_branch": "forge/harmonize",
  "report_label": "forge-report"
```

Ensure the preceding line ends with a comma and the JSON stays valid.

- [ ] **Step 10: Verify the config still parses**

Run: `python -c "import json; print(sorted(json.load(open('forge.config.json'))))"`
Expected: a sorted key list including `grace_days`, `ignore_topic`, `max_prs`, `pr_branch`, `report_label`

- [ ] **Step 11: Commit**

```bash
git add readme_forge.py forge.config.json tests/test_eligibility.py
git commit -m "feat: grace period and forge-ignore topic guardrails"
```

---

## Task 5: Idempotent PR mechanics

**Files:**
- Modify: `readme_forge.py` (add after `put_readme`, line 200)
- Create: `tests/test_pr.py`

**Interfaces:**
- Consumes: `api`.
- Produces:
  - `put_readme(repo, path, new, sha, msg, branch=None)` — **extended with an optional `branch`**; existing 5-arg calls keep working.
  - `get_content_meta(repo, path, ref) -> (content, sha)`
  - `ensure_branch(repo, branch, base_sha) -> (ok, err)` — treats "already exists" as success.
  - `find_open_pr(repo, owner, branch) -> dict | None`
  - `open_or_update_pr(repo, r, new_content, path, cfg) -> (action, detail)` where `action` is one of `"created"`, `"updated"`, `"unchanged"`, `"failed"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pr.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_pr.py -q`
Expected: FAIL with `AttributeError: module 'readme_forge' has no attribute 'ensure_branch'`

- [ ] **Step 3: Extend `put_readme` with an optional branch**

Replace `put_readme` (lines 196-200) with:

```python
def put_readme(repo, path, new, sha, msg, branch=None):
    b64 = base64.b64encode(new.encode("utf-8")).decode("ascii")
    fields = {"message": msg, "content": b64, "sha": sha}
    if branch:
        fields["branch"] = branch
    j, err = api(f"repos/{repo}/contents/{path}", "PUT", fields)
    return err is None, err
```

- [ ] **Step 4: Implement the PR helpers**

Add to `readme_forge.py` immediately after `put_readme`:

```python
PR_BODY = (
    "Automated README harmonization by [readme-forge]"
    "(https://github.com/Atypical-Consulting/readme-forge).\n\n"
    "This PR adds the deterministic sections this repository was missing "
    "({missing}). Every insertion is delimited by HTML markers, so re-running "
    "the forge updates this branch in place rather than duplicating content.\n\n"
    "Content that needs real judgement (Features, a written Usage narrative) is "
    "deliberately left to a human.\n"
)


def get_content_meta(repo, path, ref):
    """Return (content, sha) for a file on a specific ref, or (None, None)."""
    j, _ = api(f"repos/{repo}/contents/{path}?ref={ref}")
    if not j or "content" not in j:
        return None, None
    return base64.b64decode(j["content"]).decode("utf-8", "replace"), j["sha"]


def ensure_branch(repo, branch, base_sha):
    """Create `branch` at `base_sha`; an already-existing branch is success."""
    _, err = api(f"repos/{repo}/git/refs", "POST",
                 {"ref": f"refs/heads/{branch}", "sha": base_sha})
    if err and "already exists" in err.lower():
        return True, None
    return err is None, err


def find_open_pr(repo, owner, branch):
    j, _ = api(f"repos/{repo}/pulls?head={owner}:{branch}&state=open")
    return j[0] if isinstance(j, list) and j else None


def open_or_update_pr(repo, r, new_content, path, cfg):
    """Push `new_content` to the forge branch and ensure exactly one open PR.

    Returns (action, detail) with action in {created, updated, unchanged, failed}.
    """
    branch, base = cfg["pr_branch"], r.get("default_branch") or "main"
    ref, err = api(f"repos/{repo}/git/ref/heads/{base}")
    if not ref or "object" not in ref:
        return "failed", f"base ref: {err or 'not found'}"
    ok, err = ensure_branch(repo, branch, ref["object"]["sha"])
    if not ok:
        return "failed", f"branch: {err}"

    current, sha = get_content_meta(repo, path, branch)
    existing = find_open_pr(repo, r["owner"], branch)
    if current == new_content:
        return "unchanged", (existing or {}).get("html_url", "")

    missing = ", ".join(s["name"] for s in SWEEP_SPECS if s["applies"](r)) or "sections"
    ok, err = put_readme(repo, path, new_content, sha,
                         f"{cfg['commit_prefix']} harmonize README", branch=branch)
    if not ok:
        return "failed", f"commit: {err}"
    if existing:
        return "updated", existing.get("html_url", "")

    pr, err = api(f"repos/{repo}/pulls", "POST", {
        "title": f"{cfg['commit_prefix']} harmonize README",
        "head": branch, "base": base, "body": PR_BODY.format(missing=missing)})
    if not pr:
        return "failed", f"pr: {err}"
    return "created", pr.get("html_url", "")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest -q`
Expected: PASS (39 passed)

- [ ] **Step 6: Commit**

```bash
git add readme_forge.py tests/test_pr.py
git commit -m "feat: idempotent branch + PR mechanics over the contents API"
```

---

## Task 6: Wire `harmonize --pr` into the CLI

**Files:**
- Modify: `readme_forge.py` — add `cmd_harmonize_pr`, extend `main` (lines 545-585)
- Create: `tests/test_backcompat.py`

**Interfaces:**
- Consumes: `eligible`, `build_all`, `harmonize_content`, `open_or_update_pr`, `cmd_inventory`, `cmd_scan`.
- Produces: `cmd_harmonize_pr(cfg) -> dict` returning counts `{created, updated, unchanged, failed, capped}`; CLI flag `--pr` on the `harmonize` subcommand.

- [ ] **Step 1: Write the failing backward-compatibility tests**

Create `tests/test_backcompat.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_backcompat.py -q`
Expected: FAIL on `test_cli_exposes_all_six_sweeps_and_the_pr_flag` — `--pr` not in help output

- [ ] **Step 3: Implement the PR driver**

Add to `readme_forge.py`, after `harmonize_content`:

```python
def cmd_harmonize_pr(cfg):
    """Open one harmonization PR per eligible repo. Returns action counts."""
    data = json.load(open(f"{cfg['workdir']}/data.json"))
    targets = [r for r in data if eligible(r, cfg)]
    cap = cfg.get("max_prs") or len(targets)
    capped = targets[cap:]
    targets = targets[:cap]
    print(f"PR MODE · {len(targets)} eligible repo(s)"
          + (f" · {len(capped)} deferred by max_prs" if capped else ""))

    pairs = build_all(cfg)
    counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0,
              "capped": len(capped)}

    def work(r):
        repo = f"{r['owner']}/{r['name']}"
        content, _, path = get_readme(repo)
        if not content or not content.strip():
            return "unchanged", repo, ""
        new = harmonize_content(pairs, r, repo, content)
        if new == content:
            return "unchanged", repo, ""
        action, detail = open_or_update_pr(repo, r, new, path, cfg)
        return action, repo, detail

    with ThreadPoolExecutor(max_workers=cfg["max_workers"]) as ex:
        for action, repo, detail in ex.map(work, targets):
            counts[action] += 1
            if action != "unchanged":
                print(f"  {action}: {repo} {detail}")
    print("Summary:", json.dumps(counts))
    return counts
```

- [ ] **Step 4: Add the `--pr` flag and branch in `main`**

In `main`, add `--pr` to the `harmonize` subparser. Replace the loop that builds subparsers (lines 549-555) with:

```python
    for name in ("inventory", "scan", "dashboard", "run", "harmonize"):
        p = sub.add_parser(name)
        p.add_argument("--org", action="append", default=[])
        if name in ("inventory", "run", "harmonize"):
            p.add_argument("--only", help="comma-separated repo names to limit to")
        if name in ("harmonize",):
            p.add_argument("--commit", action="store_true")
            p.add_argument("--pr", action="store_true",
                           help="open a PR per repo instead of committing to the default branch")
            p.add_argument("--max-prs", type=int, help="override config max_prs")
```

Replace the `harmonize` branch (lines 577-585) with:

```python
    elif args.cmd == "harmonize":
        cmd_inventory(cfg, args.org, args.only)
        cmd_scan(cfg)
        if args.pr:
            if args.max_prs is not None:
                cfg["max_prs"] = args.max_prs
            cmd_harmonize_pr(cfg)
        else:
            for name in SWEEP_ORDER:
                print(f"\n=== sweep: {name} ===")
                SWEEPS[name](cfg, args.commit)
                cmd_scan(cfg)  # refresh so the next sweep sees prior insertions
        import dashboard
        dashboard.generate(cfg)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest -q`
Expected: PASS (43 passed)

- [ ] **Step 6: Verify the legacy path is untouched**

Run: `python readme_forge.py harmonize --help`
Expected: help listing `--commit`, `--pr`, `--max-prs`, `--only`, `--org`

- [ ] **Step 7: Commit**

```bash
git add readme_forge.py tests/test_backcompat.py
git commit -m "feat: harmonize --pr opens one idempotent PR per eligible repo"
```

---

## Task 7: Roll-up report issue

**Files:**
- Modify: `readme_forge.py` — add `cmd_report`, register a `report` subcommand
- Create: `tests/test_report.py`

**Interfaces:**
- Consumes: `_scored`, `essential_ok`, `fixable`, `api`.
- Produces:
  - `report_body(data, cfg) -> str` — pure markdown builder, no network.
  - `cmd_report(cfg, repo) -> str` — upserts the issue in `repo`, returns the action (`"created"` / `"updated"`).
  - CLI: `python readme_forge.py report --repo owner/name`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_report.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_report.py -q`
Expected: FAIL with `AttributeError: module 'readme_forge' has no attribute 'report_body'`

- [ ] **Step 3: Implement the report builder and issue upsert**

Add to `readme_forge.py`, after `cmd_harmonize_pr`:

```python
REPORT_TITLE = "readme-forge: portfolio README drift"


def report_body(data, cfg):
    """Build the roll-up issue body. Pure — no network, stable for identical input."""
    scored = [r for r in data if _scored(r, cfg) and not r.get("no_readme")]
    incomplete = [r for r in scored
                  if not all(essential_ok(r, k) for k in cfg["essentials"])]
    lines = [f"**{len(scored) - len(incomplete)}/{len(scored)}** scored repositories are complete.",
             ""]
    if not incomplete:
        lines.append("Every scored repository meets the standard. Nothing to do.")
        return "\n".join(lines)

    auto = [r for r in incomplete if fixable(r)]
    human = [r for r in incomplete if not fixable(r)]
    if auto:
        lines += ["## Fixable automatically", "",
                  "The forge opens PRs for these on its next harmonize run.", "",
                  "| Repository | Missing |", "|---|---|"]
        for r in sorted(auto, key=lambda x: (x["owner"], x["name"])):
            miss = ", ".join(k for k in cfg["essentials"] if not essential_ok(r, k))
            lines.append(f"| `{r['owner']}/{r['name']}` | {miss} |")
        lines.append("")
    if human:
        lines += ["## Needs a human", "",
                  "No deterministic sweep can write these — they need real judgement.", "",
                  "| Repository | Missing |", "|---|---|"]
        for r in sorted(human, key=lambda x: (x["owner"], x["name"])):
            miss = ", ".join(k for k in cfg["essentials"] if not essential_ok(r, k))
            lines.append(f"| `{r['owner']}/{r['name']}` | {miss} |")
        lines.append("")
    lines.append("<sub>Maintained by readme-forge — this issue is edited in place, never duplicated.</sub>")
    return "\n".join(lines)


def cmd_report(cfg, repo):
    """Create or edit the single roll-up issue in `repo`."""
    data = json.load(open(f"{cfg['workdir']}/data.json"))
    body = report_body(data, cfg)
    label = cfg["report_label"]
    found, _ = api(f"repos/{repo}/issues?state=open&labels={label}")
    if isinstance(found, list) and found:
        num = found[0]["number"]
        _, err = api(f"repos/{repo}/issues/{num}", "PATCH", {"body": body})
        print(f"updated issue #{num}" if not err else f"failed: {err}")
        return "updated"
    _, err = api(f"repos/{repo}/issues", "POST",
                 {"title": REPORT_TITLE, "body": body, "labels": label})
    print("created report issue" if not err else f"failed: {err}")
    return "created"
```

- [ ] **Step 4: Register the `report` subcommand**

In `main`, after the `sweep` subparser block (line 559), add:

```python
    rp = sub.add_parser("report")
    rp.add_argument("--repo", required=True, help="owner/name of the repo holding the roll-up issue")
```

And add a dispatch branch after the `sweep` branch:

```python
    elif args.cmd == "report":
        cmd_report(cfg, args.repo)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest -q`
Expected: PASS (47 passed)

- [ ] **Step 6: Commit**

```bash
git add readme_forge.py tests/test_report.py
git commit -m "feat: roll-up drift report issue, edited in place"
```

---

## Task 8: The two workflows

**Files:**
- Create: `.github/workflows/forge-watch.yml`
- Create: `.github/workflows/forge-harmonize.yml`
- Create: `state/.gitkeep`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: the CLI surface from Tasks 1-7 (`run`, `report --repo`, `harmonize --pr`).
- Produces: two workflows; a tracked `state/` directory.

- [ ] **Step 1: Keep `state/` tracked**

`.forge/` is gitignored in full, so the persisted snapshot lives in `state/`. Create the directory marker:

```bash
mkdir -p state && touch state/.gitkeep
```

Confirm `.gitignore` does not exclude it:

Run: `git check-ignore -v state/.gitkeep; echo "exit=$?"`
Expected: `exit=1` (not ignored)

- [ ] **Step 2: Write the watch workflow**

Create `.github/workflows/forge-watch.yml`:

```yaml
name: forge-watch

on:
  schedule:
    - cron: "0 6 * * 1"   # Mondays 06:00 UTC
  workflow_dispatch:

permissions:
  contents: write     # commit the state snapshot
  issues: write       # upsert the roll-up issue
  pages: write        # deploy the dashboard
  id-token: write     # required by deploy-pages

concurrency:
  group: forge-watch
  cancel-in-progress: false

jobs:
  watch:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deploy.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Restore previous snapshot as the working baseline
        run: |
          mkdir -p .forge
          [ -f state/data.json ] && cp state/data.json .forge/data.json || true
          [ -f state/baseline.json ] && cp state/baseline.json .forge/baseline.json || true

      - name: Inventory, scan and build the dashboard
        env:
          GH_TOKEN: ${{ secrets.FORGE_TOKEN }}
        run: |
          ORGS=""
          for o in $(echo "${{ vars.FORGE_ORGS }}" | tr ',' ' '); do ORGS="$ORGS --org $o"; done
          python readme_forge.py run $ORGS

      - name: Upsert the roll-up issue
        env:
          GH_TOKEN: ${{ github.token }}
        run: python readme_forge.py report --repo ${{ github.repository }}

      - name: Persist the snapshot
        run: |
          cp .forge/data.json state/data.json
          [ -f state/baseline.json ] || cp .forge/baseline.json state/baseline.json
          git config user.name  "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add state/
          git diff --staged --quiet || git commit -m "chore: refresh portfolio drift snapshot"
          git push

      - uses: actions/configure-pages@v5

      - name: Stage the dashboard for Pages
        run: |
          mkdir -p _site
          cp .forge/dashboard.html _site/index.html
          cp .forge/data.json _site/data.json

      - uses: actions/upload-pages-artifact@v3
        with:
          path: _site

      - id: deploy
        uses: actions/deploy-pages@v4
```

- [ ] **Step 3: Write the harmonize workflow**

Create `.github/workflows/forge-harmonize.yml`:

```yaml
name: forge-harmonize

on:
  schedule:
    - cron: "0 7 * * 1"   # Mondays 07:00 UTC, one hour after forge-watch
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Scan and report what would change, without opening PRs"
        type: boolean
        default: false
      max_prs:
        description: "Maximum PRs to open in this run"
        type: string
        default: "10"

permissions:
  contents: read

concurrency:
  group: forge-harmonize
  cancel-in-progress: false

jobs:
  harmonize:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Harmonize eligible repositories
        env:
          GH_TOKEN: ${{ secrets.FORGE_TOKEN }}
        run: |
          ORGS=""
          for o in $(echo "${{ vars.FORGE_ORGS }}" | tr ',' ' '); do ORGS="$ORGS --org $o"; done
          if [ "${{ inputs.dry_run }}" = "true" ]; then
            python readme_forge.py run $ORGS
          else
            python readme_forge.py harmonize --pr --max-prs "${{ inputs.max_prs || 10 }}" $ORGS
          fi
```

- [ ] **Step 4: Validate both workflows parse as YAML**

Run: `python -c "import yaml,sys; [yaml.safe_load(open(f)) for f in ['.github/workflows/forge-watch.yml','.github/workflows/forge-harmonize.yml']]; print('both parse')"`
Expected: `both parse`

If PyYAML is unavailable, run `pip install pyyaml` first — it is a dev-only convenience, not a runtime dependency.

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS (47 passed)

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/forge-watch.yml .github/workflows/forge-harmonize.yml state/.gitkeep
git commit -m "feat: scheduled watch and harmonize workflows"
```

---

## Task 9: Port curated exclusions and document the watchdog

**Files:**
- Modify: `forge.config.json`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above. No new code interfaces.

- [ ] **Step 1: Port the curated non-software exclusions**

In `forge.config.json`, replace `"exclude_names": [".github"]` with the curated list carried over from `repo-audit/gen_dashboard.py`:

```json
  "exclude_names": [
    ".github", "AAA",
    "LegacyModernization", "FormidableAnalyse", "fire-book", "master60",
    "ids-microservices", "StateOfTheArtAI", "ids-llm",
    "Platformer2D_Brackeys", "G3.TreasuresMonsters.Java", "gbstudio-solstice",
    "phmatray", "Nexum", "EmptyRepo", "NetLibTemplate",
    "cap-support", "YendorSupport", "nugetkeep-releases", "ninjadog-product",
    "openjam-monorepo", "aspie-consult", "garry-brain"
  ],
```

- [ ] **Step 2: Verify the config still parses and the count is right**

Run: `python -c "import json; n=json.load(open('forge.config.json'))['exclude_names']; print(len(n), 'exclusions')"`
Expected: `23 exclusions`

- [ ] **Step 3: Document the watchdog in the README**

Add this section to `README.md`, immediately before the `## Tech Stack` marker block:

```markdown
## Running as a GitHub Action

readme-forge ships two scheduled workflows so a portfolio stays at standard on its own.

| Workflow | Schedule | What it does |
|---|---|---|
| `forge-watch` | Mondays 06:00 UTC | Inventory, scan, publish the dashboard to Pages, upsert one roll-up issue |
| `forge-harmonize` | Mondays 07:00 UTC | Open one harmonization PR per eligible repository |

### Setup

1. Create a fine-grained PAT with `contents:write`, `pull_requests:write` and
   `metadata:read` on the accounts you want watched, and store it as the
   `FORGE_TOKEN` repository secret. The built-in `GITHUB_TOKEN` cannot reach
   repositories outside this one.
2. Enable **Settings → Pages → Source: GitHub Actions**.
3. Set the repository variable `FORGE_ORGS` to a comma-separated list of the
   accounts to scan, e.g. `my-org,my-username`.

### Guardrails

- **Grace period** — repositories younger than `grace_days` (default 30) are left alone.
- **Opt out** — add the `forge-ignore` topic to any repository and the forge skips it entirely.
- **PR cap** — at most `max_prs` (default 10) pull requests per run; the rest are listed in the
  roll-up issue instead of flooding your inbox.
- **Never the default branch** — the bot only pushes to `forge/harmonize` and opens a PR.
- Repositories whose only gaps need real writing (Features, a Usage narrative) are reported
  under "Needs a human", never PR'd.
```

- [ ] **Step 4: Run the full suite one last time**

Run: `python -m pytest -q`
Expected: PASS (47 passed)

- [ ] **Step 5: Dogfood the scan against the real portfolio**

Run: `python readme_forge.py run --org phmatray --org Atypical-Consulting`
Expected: a summary line reporting scored repos and completeness; `.forge/dashboard.html` written. This confirms the refactor did not change scoring behaviour.

- [ ] **Step 6: Commit**

```bash
git add forge.config.json README.md
git commit -m "docs: document the Action, port curated exclusions"
```

---

## Manual Verification (after merge)

These require the live secrets and cannot be scripted in tests:

- [ ] Trigger `forge-harmonize` via `workflow_dispatch` with **dry_run = true**; confirm it reports eligible repos and opens nothing.
- [ ] Add the `forge-ignore` topic to `Overbought`; re-run dry-run and confirm it disappears from the eligible list.
- [ ] Trigger `forge-watch`; confirm Pages publishes and exactly one issue carrying `forge-report` exists.
- [ ] Trigger `forge-harmonize` for real with `max_prs = 1`; confirm the PR has **one commit** touching only the README.
- [ ] Re-run `forge-harmonize`; confirm **no second PR** appears on the same repository.
