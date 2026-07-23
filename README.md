# README Forge

<!-- portfolio-badges:start -->
<!-- Identity -->
[![Atypical-Consulting - readme-forge](https://img.shields.io/static/v1?label=Atypical-Consulting&message=readme-forge&color=blue&logo=github)](https://github.com/Atypical-Consulting/readme-forge)
![Top language](https://img.shields.io/github/languages/top/Atypical-Consulting/readme-forge)
[![Stars](https://img.shields.io/github/stars/Atypical-Consulting/readme-forge?style=social)](https://github.com/Atypical-Consulting/readme-forge/stargazers)
[![Forks](https://img.shields.io/github/forks/Atypical-Consulting/readme-forge?style=social)](https://github.com/Atypical-Consulting/readme-forge/network/members)
[![License](https://img.shields.io/github/license/Atypical-Consulting/readme-forge)](https://github.com/Atypical-Consulting/readme-forge/blob/HEAD/LICENSE)

<!-- Activity -->
[![Issues](https://img.shields.io/github/issues/Atypical-Consulting/readme-forge)](https://github.com/Atypical-Consulting/readme-forge/issues)
[![Pull requests](https://img.shields.io/github/issues-pr/Atypical-Consulting/readme-forge)](https://github.com/Atypical-Consulting/readme-forge/pulls)
[![Last commit](https://img.shields.io/github/last-commit/Atypical-Consulting/readme-forge)](https://github.com/Atypical-Consulting/readme-forge/commits)
<!-- portfolio-badges:end -->


Score and harmonize the READMEs of every repo in one or more GitHub orgs/users,
and track progress toward **100% complete** on a live, theme-aware dashboard.

Extracted from a run that took a 2‑account, 300‑repo portfolio from **6% → 100%**
of active software repos "complete" (all 11 essentials), median README richness
5 → 12 / 21. It is **portable**: point it at any org you have `gh` access to.

## What "complete" means

Each README is scored on **21 best practices**; "richness" is how many are present.
A repo is **complete** when it has all **11 essentials**:

| | | |
|---|---|---|
| Banner / logo | Badges | Table of contents |
| Features | Tech stack | Installation |
| Usage / Getting Started | Code examples | Roadmap |
| Contributing | License | |

The other 10 (live demo, screenshot, FAQ, `<details>`, star CTA, …) are **bonus**.
Edit `essentials` in `forge.config.json` to change the bar.

## Requirements

- Python 3.9+
- [`gh`](https://cli.github.com/) authenticated with repo access to the target org(s):
  `gh auth login` (needs `repo` scope to read + write READMEs).

## Quick start

```bash
# 1. configure (or pass --org on the command line)
cp forge.config.json my.config.json   # edit "orgs"

# 2. read-only: inventory + score + dashboard
python readme_forge.py run --org your-org
open .forge/dashboard.html            # watch the bar

# 3. harmonize — run all deterministic sweeps, then re-score + refresh the board
python readme_forge.py harmonize --org your-org            # DRY-RUN (prints what it would do)
python readme_forge.py harmonize --org your-org --commit   # actually writes + commits
```

Everything is **DRY-RUN by default**; nothing is written until you add `--commit`.

## How it works

Two layers — **deterministic tools** get you most of the way with zero fabrication;
an **AI content pass** finishes the parts that need understanding the code.

### 1. Deterministic sweeps (scripted, safe, idempotent)

Each sweep edits READMEs via the GitHub contents API (GET → PUT, **no clone**),
wraps its output in an HTML marker so re‑runs replace instead of duplicating, and
commits directly to the default branch. Run individually or via `harmonize`:

| Sweep | Essential it satisfies | How |
|---|---|---|
| `header` | banner + badges | dynamic shields badges (stars/forks/issues/PRs/last‑commit/top‑language) — resolve live, **zero metadata** |
| `sections` | Contributing + License | universal boilerplate + license status from the inventory |
| `techstack` | Tech Stack | parsed from `.csproj` / `package.json`; falls back to primary language |
| `gettingstarted` | Installation | clone/restore/build/run derived from the stack (.NET / Node / Swift / Rust) |
| `roadmap` | Roadmap | a real pointer to the repo's issue tracker |
| `toc` | Table of contents | generated from the README's own `##` headings — **run LAST** |

```bash
python readme_forge.py sweep header --commit
python readme_forge.py sweep toc --commit          # after the others
python readme_forge.py scan && python readme_forge.py dashboard   # refresh
```

### 2. AI content pass (Features / Usage) — not scriptable

**Features** and **Usage** describe what a project *does* and *how to use it* —
that needs reading each repo. Do it yourself, or hand it to an AI coding agent
(Claude Code, etc.) with a prompt like the one in `PROMPT-content.md`. Run the
`toc` sweep again afterward so the table of contents picks up the new sections.

## The loop (live tracking)

```bash
python readme_forge.py scan          # re-score current state
python readme_forge.py dashboard     # regenerate .forge/dashboard.html
```

Progress is measured against `.forge/baseline.json` (the first scan, saved
automatically). The dashboard shows the completion bar, the delta since baseline,
per-essential adoption, and a "to do" table with each repo's missing essentials.

## Scope control (what counts)

Non-software repos (papers, games, profile READMEs, empty stubs, release buckets)
should not be judged as software READMEs. Exclude them from the denominator via
`exclude_names`, `exclude_suffixes`, or `exclude_regex` in the config — the same
way forks and archived repos are excluded. They stay untouched; they just don't
drag the score.

## Gotchas learned the hard way

- **Licenses:** `gh repo view --json licenseInfo` omits `spdxId`. Use the
  inventory's `license_key` (this tool does) or `gh api repos/{o}/{r} --jq .license.spdx_id`.
- **No CI/NuGet badges by default:** workflow filenames and package ids vary too
  much to auto-generate without producing broken badges.
- **Rate limits:** a big run can exhaust the 5000/h REST budget (you'll see false
  "no README"). Re-scan after the reset; `gh api rate_limit` shows remaining.
- **TOC anchors** follow github-slugger (an emoji-prefixed heading keeps a leading
  hyphen, e.g. `✨ Features` → `#-features`).

## Files

```
readme_forge.py     scan + sweeps + inventory (one file, portable)
dashboard.py        the live progress board
forge.config.json   orgs, essentials, exclusions
PROMPT-content.md    reusable prompt for the AI content pass
.forge/             generated: repos.json, data.json, baseline.json, dashboard.html
```
