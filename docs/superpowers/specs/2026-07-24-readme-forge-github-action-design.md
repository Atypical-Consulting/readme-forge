# readme-forge as a GitHub Action — Design

**Date:** 2026-07-24
**Status:** Approved
**Repo:** `Atypical-Consulting/readme-forge`

## Goal

Turn readme-forge from a manually-driven CLI into a **self-running portfolio watchdog**: on a
schedule it scans every repo across the configured accounts, publishes a live dashboard, reports
drift in a single issue, and opens pull requests that fix the deterministic gaps — including on
repos created after this was built.

## Context

readme-forge today is a Python CLI (`readme_forge.py`) that shells out to the `gh` CLI for all
GitHub access. Subcommands: `inventory · scan · dashboard · sweep <which> · run · harmonize`.

Relevant facts about the current state, established by reading the code:

- **Sweeps already separate pure from impure.** Each `sweep_X(cfg, commit)` builds a target list,
  defines an inner `build(r, repo, content) -> new_content`, and hands both to `_run_sweep`, which
  owns the fetch/PUT loop. The content transform is already a content→content function.
- **Sweeps write directly to the default branch**, one `GET` + one `PUT` per sweep per repo. Running
  several sweeps in succession against one repo caused **HTTP 409 stale-sha twice** (`trading-lab`,
  then `music-dotnet`) because of GitHub's read-after-write lag.
- **`.forge/` is gitignored in full.** Nothing generated is tracked; only `readme_forge.py`,
  `dashboard.py`, `forge.config.json`, `README.md`, `PROMPT-content.md`, `LICENSE`, and the banner
  are in git.
- **The inventory does not collect `createdAt` or repository topics** — both are needed for the
  guardrails below.
- **`forge.config.json` is deliberately generic** (`orgs: ["your-org", "your-username"]`). The real
  portfolio's curated exclusion list (21 non-software repos: LaTeX papers, games, the profile repo,
  release/support buckets) currently lives in `repo-audit/gen_dashboard.py`, not in the forge.
- Portfolio state at design time: **191/192 scored repos complete**. Steady-state drift is therefore
  small — new repos and occasional regressions, not bulk remediation.

## Locked Decisions

1. **Posture — report + auto-PR.** The bot never commits to a target repo's default branch. It
   opens pull requests; a human merges. This preserves the portfolio playbook's "validation before
   any destructive action" rule.
2. **Guardrails — grace period + topic opt-out.** A repo is ignored while it is younger than
   `grace_days`, or if it carries the `forge-ignore` topic.
3. **Architecture — pure-sweep registry + `harmonize --pr` driver.** Chosen over a
   checkout/matrix workflow (leaks logic into YAML, not portable) and over adding `--branch` to the
   existing per-sweep PUT loop (would industrialise the known 409 weakness and produce 5 commits
   per PR).

## Architecture

### Two workflows, separated by privilege

| Workflow | Trigger | Scope on **target** repos | Scope on **readme-forge itself** | Responsibility |
|---|---|---|---|---|
| `forge-watch.yml` | weekly cron + `workflow_dispatch` | **read-only** (`FORGE_TOKEN`) | write (built-in `GITHUB_TOKEN`) | inventory → scan → dashboard → Pages → roll-up issue → commit state snapshot |
| `forge-harmonize.yml` | weekly cron (offset) + `workflow_dispatch` | **write** (`FORGE_TOKEN`) | none | `harmonize --pr` over drifting repos |

The privilege separation is specifically about the **target** repos: the workflow that runs
unattended most often can only *read* the portfolio. Only the PR-opening job can write to it.
The watch workflow does write — but exclusively to readme-forge itself (committing `state/`, editing
the roll-up issue), which the built-in `GITHUB_TOKEN` covers with `contents: write` and
`issues: write`.

### Sweep registry

Hoist each sweep's inner `build` closure into a `make_build(cfg) -> build` factory and describe the
sweeps declaratively:

```python
SWEEP_SPECS = [
    {"name": "header",         "applies": lambda r: not essential_ok(r, "badges"),
     "make_build": make_header_build,         "msg": "add standardized badge header"},
    {"name": "sections",       "applies": lambda r: not essential_ok(r, "contributing")
                                              or not essential_ok(r, "license_sec"),
     "make_build": make_sections_build,       "msg": "add contributing and license sections"},
    {"name": "techstack",      "applies": lambda r: not essential_ok(r, "tech_stack"),  ...},
    {"name": "gettingstarted", "applies": lambda r: not essential_ok(r, "install"),     ...},
    {"name": "roadmap",        "applies": lambda r: not essential_ok(r, "roadmap"),     ...},
    {"name": "toc",            "applies": lambda r: not essential_ok(r, "toc"),         ...},
]
```

`applies` is a **predicate**, not a single essential key, because `sections` triggers on
`contributing` *or* `license_sec`.

Order is the existing `SWEEP_ORDER`: `header → sections → techstack → gettingstarted → roadmap → toc`.

**Backward compatibility is a requirement.** `sweep_X(cfg, commit)` keeps its current signature and
behaviour, re-expressed as
`_run_sweep(cfg, targets_for(spec), spec["make_build"](cfg), spec["msg"], commit)`.
Existing invocations (`readme_forge.py sweep toc --commit`) must behave exactly as before.

### `harmonize --pr` driver

For each eligible repo:

1. **One** `GET` of the README.
2. Apply, in `SWEEP_ORDER`, the `build` of every spec whose `applies(r)` is true — chained **in
   memory**, each transform consuming the previous one's output.
3. If the result differs from the original, perform **one** write and open a PR.

This collapses N round-trips into one and removes the 409 class of failure by construction.

**Honest caveat:** `techstack` and `gettingstarted` perform their own network reads inside `build`
(fetching `.csproj` / `package.json` to derive a real stack and real commands). They are not pure in
the strict sense, but they honour the content→content contract from the driver's perspective. The
batching benefit is unaffected.

### PR mechanics — API only, no checkout

Four calls per drifting repo:

```
GET  repos/{repo}/git/ref/heads/{default_branch}   -> base sha
POST repos/{repo}/git/refs                         -> create refs/heads/{branch} at base sha
PUT  repos/{repo}/contents/{path}?branch={branch}  -> single commit
POST repos/{repo}/pulls                            -> open PR
```

**Idempotency is mandatory** — this runs on a schedule:

- If the branch already exists, update it instead of failing on create.
- If an open PR from that branch already exists, **update the branch and do not open a second PR**.
- If the recomputed content equals what is already on the branch, do nothing.

Branch name comes from config (`pr_branch`, default `forge/harmonize`).

## Data Model Changes

### Inventory (`cmd_inventory`)

Add to the `gh repo list --json` field list and to the emitted record:

- `createdAt` → `created_at` (grace period)
- `repositoryTopics` → `topics` (opt-out)

### `forge.config.json`

New keys, with defaults that keep the tool generic:

| Key | Default | Purpose |
|---|---|---|
| `grace_days` | `30` | Skip repos younger than this |
| `ignore_topic` | `"forge-ignore"` | Topic that opts a repo out entirely |
| `max_prs` | `10` | Cap PRs per run; overflow is reported, not opened |
| `pr_branch` | `"forge/harmonize"` | Branch used for harmonization PRs |
| `report_label` | `"forge-report"` | Label identifying the roll-up issue |

`exclude_names` gains the 21 curated non-software repos ported from `repo-audit/gen_dashboard.py`.
Because readme-forge is a public, reusable tool, the **accounts to scan are not committed**: the
workflows pass `--org` from the repository variable `FORGE_ORGS`.

### Persisted state

`.forge/` stays the ephemeral working directory. Two files are persisted in a **tracked** `state/`
directory so drift has real history:

- `state/data.json` — rolling snapshot, rewritten and committed by each watch run. `git log state/data.json`
  becomes the portfolio's drift history.
- `state/baseline.json` — frozen reference the dashboard diffs against for the progress bar. **Seeded
  from the first run's `data.json` if absent**, then updated only deliberately — never per run, or
  the progress bar would always read zero.

## Reporting

- **Dashboard → GitHub Pages.** `dashboard.html` is deployed from the watch workflow, giving a
  stable URL that refreshes itself. This replaces manual artifact republishing.
- **Roll-up issue.** A single issue, located by the `report_label` label and **edited in place**,
  never duplicated. Body lists each incomplete repo and its missing essentials, plus any repos whose
  PRs were suppressed by `max_prs`.

## Eligibility Rules

A repo is harmonization-eligible when **all** hold:

- not a fork, not archived, not empty
- not matched by `exclude_names` / `exclude_suffixes` / `exclude_regex`
- has a README (repos with none are reported, not PR'd — a README from nothing is content work)
- `created_at` older than `grace_days`
- does not carry the `ignore_topic` topic
- at least one essential is missing

## Setup Required From the Maintainer

1. **`FORGE_TOKEN`** — fine-grained PAT as a repo secret on readme-forge, scoped to **both**
   accounts: `contents:write`, `pull_requests:write`, `metadata:read`. The default `GITHUB_TOKEN`
   cannot reach repos outside readme-forge, hence the PAT. (`issues:write` is *not* needed on the
   PAT — the roll-up issue lives in readme-forge and is written with the built-in `GITHUB_TOKEN`.)
2. **Pages enabled** on readme-forge, source *GitHub Actions*.
3. **Repository variable `FORGE_ORGS`** = `phmatray,Atypical-Consulting`.

## Testing Strategy

The registry refactor makes every `build` a **content→content function testable without network**.
Tests cover:

- Each sweep's `build`: given README fixture → expected transform.
- **Idempotency**: applying a build twice equals applying it once (the marker contract).
- **Chaining**: all six builds applied in order to a bare README yield a README that the scanner
  scores as complete.
- Eligibility: grace period, `forge-ignore` topic, exclusions, forks/archived.
- Backward compatibility: `sweep_X` still selects the same targets as before the refactor.

Network-touching paths (`techstack`, `gettingstarted` manifest reads; PR mechanics) are tested with
injected fakes rather than live calls.

## Out of Scope (YAGNI)

- A reusable per-repo action for third parties to drop into their own repos.
- Auto-merging the PRs the bot opens.
- Generating *content* essentials that need judgement (`Features`, a real `Usage` narrative) — these
  stay human/subagent work and are reported, not PR'd.
- Harmonizing anything other than READMEs (LICENSE, CI, default branch).

## Success Criteria

1. A scheduled run needs **zero human action** to refresh the dashboard and the roll-up issue.
2. A repo created today and left alone for `grace_days` receives a harmonization PR automatically.
3. Putting `forge-ignore` on a repo stops all bot activity on it.
4. Two consecutive runs with no portfolio change produce **no second PR** and no duplicate issue.
5. A harmonization PR contains **exactly one commit** touching only the README.
6. The existing CLI (`sweep`, `run`, `harmonize` without `--pr`) behaves exactly as it does today.
