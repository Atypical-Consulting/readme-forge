# AI content pass — prompt template

The deterministic sweeps in `readme_forge.py` cannot write **Features** or
**Usage**: those require reading each repo. Hand this to an AI coding agent
(Claude Code, etc.), one batch of repos per agent. After the content pass, run
`python readme_forge.py sweep toc --commit` again so the TOC picks up the new
sections.

Find which repos still need content:

```bash
python readme_forge.py scan
# the dashboard "to do" table lists each repo's missing essentials;
# features_sec / usage are the ones this pass handles.
```

---

## Prompt (fill in the repo list)

> You ADD missing sections to EXISTING READMEs in `<ORG>`'s GitHub repos
> (harmonization toward a complete standard). Each repo ALREADY has, from
> automated sweeps: a banner, a `<!-- portfolio-badges -->` block, usually a
> `<!-- portfolio-techstack -->` (## Tech Stack), a `<!-- portfolio-roadmap -->`
> (## Roadmap), and a `<!-- portfolio-sections -->` block (## Contributing +
> ## License). **DO NOT touch, move, or duplicate any of those.** Add ONLY the
> section(s) listed per repo, with REAL content read from the code. Commit
> directly to the default branch.
>
> ### Repos — add exactly these sections
> 1. `<owner>/<repo>` — Features
> 2. `<owner>/<repo>` — Features, Usage
> … (5–7 per agent)
>
> ### Section meanings (REAL content from code — never invent)
> - **## Features**: 4–8 concrete bullets (bold lead + short description) of what
>   the project actually does. Read the `.csproj`/`package.json`, entry points,
>   key types.
> - **## Usage**: how to actually use/run it, with a REAL command or code example
>   taken from the repo. App → run command + what you see. Library → a minimal
>   example using its real public API.
>
> ### Procedure per repo
> 1. `gh repo clone <owner>/<repo> <dir> -- --depth 1`
> 2. Read `README.md` AND the code for real material.
> 3. Insert ONLY the missing section(s): `## Features` near the top of the body
>    (after the badge block); `## Usage` after any Getting Started/Installation,
>    else after Features. **If the README already covers a listed section under an
>    equivalent heading (e.g. an existing "Quick Start" covers Usage), SKIP it and
>    say so — never duplicate.**
> 4. `git add README.md && git commit -m "docs: add <sections>" && git push origin HEAD`
>
> ### Rules
> - Never invent features, demos, or APIs. Describe only what the code does.
> - The GitHub *description* is often stale/wrong — trust the code, and flag any
>   mismatch you find.
> - Match the README's existing language/tone.
>
> Report per repo: repo, sections added, commit sha (or skipped/failed + reason).

---

## Why not script this?

A Features/Usage section that's genuinely useful has to reflect what the code
really does. Auto-generating it from templates produces filler that's worse than
nothing — and in practice the GitHub description is wrong often enough that only
reading the code gets it right. Keep the deterministic sweeps for structure;
keep a human or an agent in the loop for substance.
