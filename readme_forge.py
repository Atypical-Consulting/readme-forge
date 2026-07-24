#!/usr/bin/env python3
"""
readme-forge — score and harmonize the READMEs of every repo in one or more
GitHub orgs/users, and track progress toward "100% complete" on a live dashboard.

Portable: pass your org(s) on the command line or in forge.config.json. Nothing
is hardcoded to a particular account. Requires the `gh` CLI, authenticated
(`gh auth login`) with repo access to the target org(s).

Pipeline
  python readme_forge.py inventory --org my-org           # list repos -> .forge/repos.json
  python readme_forge.py scan                             # score current READMEs -> .forge/data.json
  python readme_forge.py dashboard                        # -> .forge/dashboard.html
  python readme_forge.py sweep header --commit            # inject standardized badge block
  python readme_forge.py sweep sections --commit          # Contributing + License
  python readme_forge.py sweep techstack --commit         # Tech Stack from manifests
  python readme_forge.py sweep gettingstarted --commit    # Getting Started from stack
  python readme_forge.py sweep roadmap --commit           # Roadmap pointer
  python readme_forge.py sweep toc --commit               # Table of Contents (run LAST)
  python readme_forge.py scan && python readme_forge.py dashboard   # refresh the board

Or in one go (read-only vs write):
  python readme_forge.py run --org my-org                 # inventory + scan + dashboard
  python readme_forge.py harmonize --org my-org --commit  # all deterministic sweeps + refresh

Unattended mode (what the scheduled workflows run):
  python readme_forge.py report --repo owner/name         # upsert the roll-up drift issue
  python readme_forge.py harmonize --pr --org my-org      # preview one PR per eligible repo
  python readme_forge.py harmonize --pr --commit --org my-org   # actually open them

Every write is idempotent (HTML markers) and edits via the GitHub contents API
(GET README -> PUT, no clone). Nothing is written without --commit. The legacy
sweeps (`sweep`, `harmonize` without --pr) commit to the default branch; the
`--pr` path never does — it pushes to `pr_branch` and opens a pull request. The
content essentials that need real understanding (Features / Usage) are left to
you or an AI agent — see README.md.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------- config -----

DEFAULTS = {
    "orgs": [],
    # A repo is "complete" when it has all of these. Rare/polish features beyond
    # the list are bonus, not required.
    "essentials": ["banner_logo", "badges", "toc", "features_sec", "tech_stack",
                   "install", "usage", "code_blocks", "roadmap", "contributing", "license_sec"],
    "include_forks": False,
    "include_archived": False,
    # excluded from the completeness denominator (like forks/archived)
    "exclude_names": [".github"],
    "exclude_suffixes": ["-backup"],
    "exclude_regex": [],            # e.g. ["^demo-", "-sandbox$"]
    "workdir": ".forge",
    "commit_prefix": "docs:",       # commit message prefix for sweeps
    "max_workers": 10,
    # --- watchdog / PR mode ---
    "grace_days": 30,               # skip repos younger than this
    # Topic that opts a repo out of *writes*: it is never branched, committed to
    # or PR'd. It is still inventoried, scored, shown on the dashboard and listed
    # in the roll-up issue (under "held back by a guardrail"), because the point
    # of the board is an honest picture of the portfolio.
    "ignore_topic": "forge-ignore",
    "max_prs": 10,                  # cap PRs opened per run
    "pr_branch": "forge/harmonize",
    "report_label": "forge-report",
}


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        cfg.update(json.load(open(path)))
    elif os.path.exists("forge.config.json"):
        cfg.update(json.load(open("forge.config.json")))
    return cfg


def excluded(name, cfg):
    if name in cfg["exclude_names"] or any(name.endswith(s) for s in cfg["exclude_suffixes"]):
        return True
    return any(re.search(p, name) for p in cfg["exclude_regex"])


# ------------------------------------------------------------- scoring -------

FEATURE_KEYS = [
    "banner_logo", "screenshot_gif", "live_demo", "badges", "md_table", "toc",
    "features_sec", "tech_stack", "install", "usage", "config", "api_doc",
    "code_blocks", "roadmap", "faq", "contributing", "license_sec", "credits",
    "star_cta", "details_fold", "emoji",
]
EMOJI_RE = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U00002600-\U000027BF" "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF" "\U00002B00-\U00002BFF" "\U0000FE0F" "]")


def _headings(md):
    return [h.lower() for h in re.findall(r"^#{1,6}\s+(.+?)\s*#*$", md, re.M)]


def _hh(headings, *needles):
    return any(any(n in h for n in needles) for h in headings)


def detect(md):
    """Return {feature_key: bool|int} for one README's markdown."""
    low = md.lower()
    headings = _headings(md)
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", md) + re.findall(r"<img[^>]+src=[\"']([^\"']+)", md, re.I)
    badges = [u for u in images if "shields.io" in u or "badge" in u.lower()]
    non_badge = [u for u in images if u not in badges]
    demo_hosts = ("github.io", "vercel.app", "netlify.app", "azurewebsites.net",
                  "herokuapp.com", "fly.dev", "pages.dev", "surge.sh", "render.com")
    f = {}
    first = next((ln.strip() for ln in md.splitlines() if ln.strip()), "")
    lead_img = first.startswith("![") or first.lower().startswith("<img") or bool(
        re.match(r"<p[^>]*>\s*<(img|picture|a)", first, re.I))
    # Only NON-badge images count — a shields URL with `?logo=github` is a badge, not a banner.
    f["banner_logo"] = bool(lead_img or any(re.search(r"banner|logo|hero|header", u, re.I) for u in non_badge))
    f["screenshot_gif"] = bool(any(re.search(r"\.gif|screenshot|demo|preview", u, re.I) for u in non_badge) or len(non_badge) >= 2)
    f["live_demo"] = bool(_hh(headings, "live demo", "demo", "try it") or any(h in low for h in demo_hosts))
    f["badges"] = len(badges)
    f["md_table"] = bool(re.search(r"^\s*\|.*\|\s*$", md, re.M) and re.search(r"^\s*\|?[\s:-]*-{3,}[\s:|-]*\|", md, re.M))
    f["toc"] = bool(_hh(headings, "table of contents", "contents", "sommaire", "toc")
                    or re.search(r"\[[^\]]+\]\(#[^)]+\)[\s\S]{0,80}\[[^\]]+\]\(#", md))
    # A real Features section, not any heading that merely mentions the word
    # (e.g. "AI content pass (Features / Usage)" must not count).
    f["features_sec"] = any(re.match(r"(?:[\d.\)\s]*)?(?:[^\w\s]\s*)?(?:key |core |main )?features?\b", h)
                            for h in headings)
    f["tech_stack"] = _hh(headings, "tech stack", "built with", "stack", "technolog", "tech")
    f["install"] = bool(_hh(headings, "install", "getting started", "quick start", "quickstart", "setup")
                        or re.search(r"\b(npm i|npm install|dotnet add|pip install|yarn add|dotnet restore|git clone)\b", low))
    f["usage"] = _hh(headings, "usage", "how to use", "how it works", "example", "examples")
    # getting_started is tracked for the "usage" essential (Usage OR Getting Started),
    # but is NOT one of the 21 richness features.
    f["getting_started"] = _hh(headings, "getting started", "get started", "quick start", "quickstart",
                               "how to run", "running", "run locally", "démarrage", "démarrer",
                               "utilisation", "prise en main", "lancer", "exécuter")
    f["config"] = _hh(headings, "config", "configuration", "settings", "environment", "options")
    f["api_doc"] = _hh(headings, "api", "reference", "endpoints", "methods", "documentation")
    f["code_blocks"] = md.count("```") // 2
    f["roadmap"] = _hh(headings, "roadmap", "changelog", "what's new", "whats new", "todo", "to do", "release")
    f["faq"] = _hh(headings, "faq", "troubleshoot", "q&a", "questions", "known issue")
    f["contributing"] = bool(_hh(headings, "contribut") or "contributing.md" in low)
    f["license_sec"] = bool(_hh(headings, "license", "licence") or "/license" in low or "license-" in low)
    f["credits"] = _hh(headings, "credit", "acknowledg", "thanks", "kudos", "author")
    f["star_cta"] = bool(re.search(r"star (this|the) (repo|project)|give it a star|leave a star|⭐", low))
    f["details_fold"] = "<details" in low
    f["emoji"] = bool(EMOJI_RE.search(md))
    return f


def richness(feat):
    return sum(1 for k in FEATURE_KEYS
               if feat[k] is True or (isinstance(feat[k], int) and not isinstance(feat[k], bool) and feat[k] > 0))


def has(r, k):
    v = r.get(k)
    return v is True or (isinstance(v, int) and not isinstance(v, bool) and v > 0)


def essential_ok(r, k):
    if k == "usage":
        return has(r, "usage") or has(r, "getting_started")
    return has(r, k)


# ---------------------------------------------------------------- gh api -----

def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def api(path, method=None, fields=None):
    cmd = ["gh", "api"]
    if method:
        cmd += ["--method", method]
    cmd.append(path)
    for k, v in (fields or {}).items():
        # `gh api -f key=value` always sends a plain string. The GitHub REST
        # API rejects that for array-typed properties (e.g. `labels` on issue
        # creation: HTTP 422 "... is not an array", confirmed empirically
        # against a real repository). `gh api --help` documents the fix: repeat
        # `-f key[]=item` once per element (an empty list becomes a single
        # `-f key[]` with no value, gh's syntax for an empty array). Every
        # existing caller passes plain scalars, so this is purely additive.
        if isinstance(v, (list, tuple)):
            if not v:
                cmd += ["-f", f"{k}[]"]
            else:
                for item in v:
                    cmd += ["-f", f"{k}[]={item}"]
        else:
            cmd += ["-f", f"{k}={v}"]
    r = sh(cmd)
    if r.returncode != 0:
        return None, (r.stderr or r.stdout).strip()
    try:
        return json.loads(r.stdout), None
    except json.JSONDecodeError:
        return r.stdout, None


def get_readme(repo):
    j, _ = api(f"repos/{repo}/readme")
    if not j:
        return None, None, None
    return base64.b64decode(j["content"]).decode("utf-8", "replace"), j["sha"], j["path"]


def get_file(repo, path):
    j, _ = api(f"repos/{repo}/contents/{path}")
    if not j or "content" not in j:
        return None
    return base64.b64decode(j["content"]).decode("utf-8", "replace")


def put_readme(repo, path, new, sha, msg, branch=None):
    b64 = base64.b64encode(new.encode("utf-8")).decode("ascii")
    fields = {"message": msg, "content": b64, "sha": sha}
    if branch:
        fields["branch"] = branch
    j, err = api(f"repos/{repo}/contents/{path}", "PUT", fields)
    return err is None, err


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
    """Return (pr, err): the branch's open PR (or None if there isn't one),
    and the api() error (or None on success). A failed search must NOT be
    conflated with "no PR is open" — the caller has to be able to tell the
    two apart to avoid opening a duplicate PR."""
    j, err = api(f"repos/{repo}/pulls?head={owner}:{branch}&state=open")
    if err:
        return None, err
    return (j[0] if isinstance(j, list) and j else None), None


def open_or_update_pr(repo, r, new_content, path, cfg):
    """Push `new_content` to the forge branch and ensure exactly one open PR.

    Returns (action, detail) with action in {created, updated, unchanged, failed}.
    """
    branch, base = cfg["pr_branch"], r.get("default_branch") or "main"
    # "Never the default branch" is this tool's hardest safety constraint, and a
    # single mistyped `pr_branch` would defeat it silently: ensure_branch()
    # reports an existing branch as success, so a pr_branch of "main" sails
    # through and the contents PUT below commits straight to the default branch
    # -- no PR, no review, on someone else's repository. Refuse up front.
    if branch == base:
        return "failed", f"pr_branch {branch!r} is the default branch — refusing to write"
    ref, err = api(f"repos/{repo}/git/ref/heads/{base}")
    if not ref or "object" not in ref:
        return "failed", f"base ref: {err or 'not found'}"
    # ensure_branch runs before the fail-closed PR search below. That ordering is
    # deliberate and safe: creating (or re-finding) the forge branch is not a
    # write to any repository content -- nothing is committed until the PUT, which
    # is gated on the search having succeeded.
    ok, err = ensure_branch(repo, branch, ref["object"]["sha"])
    if not ok:
        return "failed", f"branch: {err}"

    current, sha = get_content_meta(repo, path, branch)
    existing, err = find_open_pr(repo, r["owner"], branch)
    if err:
        # Fail closed: we cannot tell "no PR is open" from "the search
        # failed", and guessing wrong risks a duplicate PR — a worse outcome
        # than skipping this repo for one (self-correcting) cycle.
        return "failed", f"pr search: {err}"
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


def tree(repo):
    j, _ = api(f"repos/{repo}/git/trees/HEAD?recursive=1")
    return [t["path"] for t in j["tree"] if t.get("type") == "blob"] if j and "tree" in j else []


# ------------------------------------------------------------ inventory ------

def cmd_inventory(cfg, orgs, only=None):
    orgs = orgs or cfg["orgs"]
    if not orgs:
        sys.exit("No org given. Use --org NAME (repeatable) or set 'orgs' in forge.config.json.")
    repos = []
    for owner in orgs:
        r = sh(["gh", "repo", "list", owner, "--limit", "1000", "--json",
                "name,isFork,isArchived,stargazerCount,primaryLanguage,defaultBranchRef,"
                "licenseInfo,description,pushedAt,isEmpty,createdAt,repositoryTopics"])
        if r.returncode != 0:
            sys.exit(f"gh repo list {owner} failed: {r.stderr.strip()}")
        for x in json.loads(r.stdout):
            repos.append({
                "owner": owner, "name": x["name"], "fork": x.get("isFork", False),
                "archived": x.get("isArchived", False), "stars": x.get("stargazerCount", 0),
                "lang": (x.get("primaryLanguage") or {}).get("name") if x.get("primaryLanguage") else None,
                "default_branch": (x.get("defaultBranchRef") or {}).get("name"),
                "license_key": (x.get("licenseInfo") or {}).get("key"),
                "empty": x.get("isEmpty", False),
                "created_at": x.get("createdAt"),
                "topics": [t["name"] for t in (x.get("repositoryTopics") or [])],
            })
    # An *empty but successful* `gh repo list` is the dangerous case: a PAT whose
    # repository selection drifted, or an org the token lost access to, exits 0
    # with `[]`. Carried through, that overwrites data.json with an empty scan,
    # renders an empty dashboard and reports "every scored repository meets the
    # standard" -- a green run that watched nothing. Stop here instead.
    if not repos:
        sys.exit(f"No repositories returned for {', '.join(orgs)}. Refusing to continue: "
                 "an empty inventory would overwrite the snapshot and report success. "
                 "Check the org name(s) (--org / FORGE_ORGS) and that the token still "
                 "has access to them (`gh repo list OWNER`).")
    if only:
        want = set(only.split(","))
        repos = [r for r in repos if r["name"] in want]
        if not repos:
            sys.exit(f"--only {only} matched none of the {len(orgs)} owner(s)' repositories.")
    os.makedirs(cfg["workdir"], exist_ok=True)
    json.dump(repos, open(f"{cfg['workdir']}/repos.json", "w"))
    print(f"{len(repos)} repos across {len(orgs)} owner(s) -> {cfg['workdir']}/repos.json")
    return repos


# ---------------------------------------------------------------- scan -------

def cmd_scan(cfg):
    wd = cfg["workdir"]
    inv = json.load(open(f"{wd}/repos.json"))
    print(f"Scanning {len(inv)} READMEs…", file=sys.stderr)
    out = [None] * len(inv)

    def work(i):
        m = inv[i]
        rec = dict(m)
        rec["repo"] = f"{m['owner']}__{m['name']}"
        md = None if m["empty"] else get_readme(f"{m['owner']}/{m['name']}")[0]
        if not md or not md.strip():
            rec.update(no_readme=True, richness=0, lines=0)
        else:
            feat = detect(md)
            rec.update(feat)
            rec["richness"] = richness(feat)
            rec["lines"] = md.count("\n") + 1
        out[i] = rec

    with ThreadPoolExecutor(max_workers=cfg["max_workers"]) as ex:
        list(ex.map(work, range(len(inv))))
    json.dump(out, open(f"{wd}/data.json", "w"))
    if not os.path.exists(f"{wd}/baseline.json"):
        json.dump(out, open(f"{wd}/baseline.json", "w"))
        print("(saved this scan as baseline.json — progress is measured against it)")

    scored = [r for r in out if _scored(r, cfg) and not r.get("no_readme")]
    comp = sum(1 for r in scored if all(essential_ok(r, k) for k in cfg["essentials"]))
    import statistics
    med = statistics.median([r["richness"] for r in scored]) if scored else 0
    print(f"Wrote {wd}/data.json · scored repos: {len(scored)} · complete: {comp} · median richness: {med}/21")
    return out


def _scored(r, cfg):
    incl = (cfg["include_forks"] or not r["fork"]) and (cfg["include_archived"] or not r["archived"])
    return incl and not excluded(r["name"], cfg)


# --------------------------------------------------------- shared inject -----

def marker_replace(content, start, end, block):
    pre = content.split(start, 1)[0].rstrip()
    post = content.split(end, 1)[1]
    return pre + "\n\n" + block + post


def insert_after(content, anchors, block):
    for a in anchors:
        if a in content:
            i = content.index(a) + len(a)
            return content[:i] + "\n\n" + block + "\n" + content[i:]
    return None


def insert_after_h1(content, block):
    lines = content.splitlines()
    h1 = next((i for i, ln in enumerate(lines) if ln.lstrip("﻿ \t").startswith("# ")), None)
    if h1 is not None:
        return "\n".join(lines[:h1 + 1] + ["", block, ""] + lines[h1 + 1:]) + ("\n" if content.endswith("\n") else "")
    at = 1 if lines and lines[0].lstrip().startswith("![") else 0
    return "\n".join(lines[:at] + ([""] if at else []) + [block, ""] + lines[at:])


def insert_before_community(content, block):
    """Insert before the community-sections block or the first License/Contributing heading."""
    anchors = []
    if "<!-- portfolio-sections:start -->" in content:
        anchors.append(content.index("<!-- portfolio-sections:start -->"))
    m = re.search(r"^#{2,3}\s+.*?(License|Licence|Contributing|Contributions)\b", content, re.M)
    if m:
        anchors.append(m.start())
    if anchors:
        pos = min(anchors)
        return content[:pos].rstrip() + "\n\n" + block + "\n\n" + content[pos:]
    return content.rstrip() + "\n\n" + block + "\n"


# ------------------------------------------------------------- sweeps --------

def _run_sweep(cfg, targets, build_and_inject, msg, commit):
    print(f"{'COMMIT' if commit else 'DRY-RUN'} · {len(targets)} target(s)")
    counts = {"done": 0, "skip": 0, "fail": 0}

    def work(r):
        repo = f"{r['owner']}/{r['name']}"
        content, sha, path = get_readme(repo)
        if content is None or not content.strip():
            return ("skip", repo)
        new = build_and_inject(r, repo, content)
        if new is None or new == content:
            return ("skip", repo)
        if not commit:
            return ("done", repo)
        okc, err = put_readme(repo, path, new, sha, msg)
        return ("done", repo) if okc else ("fail", f"{repo}: {err[:80]}")

    with ThreadPoolExecutor(max_workers=cfg["max_workers"]) as ex:
        for status, repo in ex.map(work, targets):
            counts[status] += 1
            if status != "skip":
                print(f"  {status}: {repo}")
    print("Summary:", json.dumps(counts))


def make_header_build(cfg):
    S, E = "<!-- portfolio-badges:start -->", "<!-- portfolio-badges:end -->"
    licensed = {r["name"] for r in json.load(open(f"{cfg['workdir']}/repos.json")) if r.get("license_key") and r["license_key"] != "other"}

    def build(r, repo, content):
        sh_ = "https://img.shields.io/github"
        idn = [
            f"[![{r['owner']} - {r['name']}](https://img.shields.io/static/v1?label={r['owner']}&message={r['name']}&color=blue&logo=github)](https://github.com/{repo})",
            f"![Top language]({sh_}/languages/top/{repo})",
            f"[![Stars]({sh_}/stars/{repo}?style=social)](https://github.com/{repo}/stargazers)",
            f"[![Forks]({sh_}/forks/{repo}?style=social)](https://github.com/{repo}/network/members)",
        ]
        if r["name"] in licensed:
            idn.append(f"[![License]({sh_}/license/{repo})](https://github.com/{repo}/blob/HEAD/LICENSE)")
        act = [f"[![Issues]({sh_}/issues/{repo})](https://github.com/{repo}/issues)",
               f"[![Pull requests]({sh_}/issues-pr/{repo})](https://github.com/{repo}/pulls)",
               f"[![Last commit]({sh_}/last-commit/{repo})](https://github.com/{repo}/commits)"]
        block = "\n".join([S, "<!-- Identity -->", *idn, "", "<!-- Activity -->", *act, E])
        if S in content and E in content:
            return marker_replace(content, S, E, block)
        return insert_after_h1(content, block)

    return build


CONTRIB = ("## Contributing\n\nContributions are welcome. Open an issue first to discuss any significant change.\n\n"
           "1. Fork the repository and create your branch (`git checkout -b feat/my-feature`)\n"
           "2. Commit your changes (`git commit -m 'feat: ...'`)\n3. Push the branch and open a Pull Request")
LIC_NAMES = {"mit": "MIT", "apache-2.0": "Apache-2.0", "gpl-3.0": "GPL-3.0", "gpl-2.0": "GPL-2.0",
             "bsd-3-clause": "BSD-3-Clause", "bsd-2-clause": "BSD-2-Clause", "mpl-2.0": "MPL-2.0",
             "agpl-3.0": "AGPL-3.0", "unlicense": "The Unlicense", "isc": "ISC"}


def make_sections_build(cfg):
    S, E = "<!-- portfolio-sections:start -->", "<!-- portfolio-sections:end -->"
    lic = {r["name"]: r["license_key"] for r in json.load(open(f"{cfg['workdir']}/repos.json")) if r.get("license_key") and r["license_key"] != "other"}

    def build(r, repo, content):
        parts = []
        if not has(r, "contributing"):
            parts.append(CONTRIB)
        if not has(r, "license_sec"):
            if r["name"] in lic:
                parts.append(f"## License\n\nDistributed under the {LIC_NAMES.get(lic[r['name']], lic[r['name']].upper())} License. See [`LICENSE`](LICENSE).")
            else:
                parts.append("## License\n\nNo license has been declared for this repository yet. Until one is added, default copyright applies — see [choosealicense.com](https://choosealicense.com/).")
        if not parts:
            return None
        block = S + "\n\n" + "\n\n".join(parts) + "\n\n" + E
        if S in content and E in content:
            return marker_replace(content, S, E, block)
        return content.rstrip() + "\n\n---\n\n" + block + "\n"

    return build


PKG_NOISE = re.compile(r"^(StyleCop|Roslynator|SonarAnalyzer|Meziantou|coverlet|Microsoft\.NET\.Test\.Sdk|"
                       r"GitHubActionsTestLogger|MinVer|Nerdbank|Microsoft\.SourceLink|Microsoft\.CodeAnalysis\.(Analyzers|NetAnalyzers))", re.I)
TFM = {"net10.0": ".NET 10", "net9.0": ".NET 9", "net8.0": ".NET 8", "net7.0": ".NET 7", "net6.0": ".NET 6",
       "netstandard2.1": ".NET Standard 2.1", "netstandard2.0": ".NET Standard 2.0",
       "net48": ".NET Framework 4.8", "net472": ".NET Framework 4.7.2"}


def make_techstack_build(cfg):
    S, E = "<!-- portfolio-techstack:start -->", "<!-- portfolio-techstack:end -->"

    def build(r, repo, content):
        paths = tree(repo)
        csprojs = [p for p in paths if p.endswith((".csproj", ".fsproj"))][:6]
        pkg = next((p for p in paths if p.endswith("package.json") and "node_modules" not in p), None)
        fw, pkgs = set(), []
        for cp in csprojs:
            txt = get_file(repo, cp) or ""
            for m in re.findall(r"<TargetFrameworks?>([^<]+)</TargetFrameworks?>", txt):
                for t in re.split(r"[;,]", m):
                    t = t.strip()
                    if t:
                        fw.add(TFM.get(t, t))
            for m in re.findall(r'<PackageReference\s+Include="([^"]+)"', txt):
                if not PKG_NOISE.match(m) and m not in pkgs:
                    pkgs.append(m)
        bullets = []
        if fw:
            bullets.append("**" + " · ".join(sorted(fw)) + "**")
        elif r.get("lang"):
            bullets.append(f"**{r['lang']}**")
        if pkg:
            try:
                deps = list((json.loads(get_file(repo, pkg) or "{}").get("dependencies") or {}).keys())
            except json.JSONDecodeError:
                deps = []
            pkgs += [d for d in deps[:8] if d not in pkgs]
        bullets += pkgs[:8]
        if not bullets:
            return None
        block = f"{S}\n\n## Tech Stack\n\n" + "\n".join(f"- {b}" for b in bullets) + f"\n\n{E}"
        if S in content and E in content:
            return marker_replace(content, S, E, block)
        return insert_before_community(content, block)

    return build


def make_roadmap_build(cfg):
    S, E = "<!-- portfolio-roadmap:start -->", "<!-- portfolio-roadmap:end -->"

    def build(r, repo, content):
        block = (f"{S}\n\n## Roadmap\n\nPlanned work and known limitations are tracked in the "
                 f"[open issues](https://github.com/{repo}/issues). Contributions toward them are welcome.\n\n{E}")
        if S in content and E in content:
            return marker_replace(content, S, E, block)
        return insert_before_community(content, block)

    return build


def make_gettingstarted_build(cfg):
    S, E = "<!-- portfolio-getstarted:start -->", "<!-- portfolio-getstarted:end -->"

    def build(r, repo, content):
        paths = tree(repo)
        clone = f"git clone https://github.com/{repo}.git\ncd {r['name']}"
        csprojs = [p for p in paths if p.endswith(".csproj")]
        pkg = next((p for p in paths if p.endswith("package.json") and "node_modules" not in p), None)
        if csprojs:
            startup = next((cp for cp in sorted(csprojs, key=lambda p: p.count("/"))
                            if any(k in (get_file(repo, cp) or "") for k in ("Sdk.Web", "Microsoft.NET.Sdk.Web", "<OutputType>Exe"))), None)
            prereq, cmds = "- [.NET SDK](https://dotnet.microsoft.com/download)", f"{clone}\ndotnet restore\ndotnet build\n" + (f"dotnet run --project {startup}" if startup else "dotnet run")
        elif any(p.endswith("Package.swift") for p in paths):
            prereq, cmds = "- [Swift toolchain](https://www.swift.org/install/)", f"{clone}\nswift build\nswift run"
        elif any(p == "Cargo.toml" or p.endswith("/Cargo.toml") for p in paths):
            prereq, cmds = "- [Rust toolchain](https://www.rust-lang.org/tools/install)", f"{clone}\ncargo build\ncargo run"
        elif pkg:
            try:
                scripts = (json.loads(get_file(repo, pkg) or "{}").get("scripts") or {})
            except json.JSONDecodeError:
                scripts = {}
            run = "npm run dev" if "dev" in scripts else ("npm run start" if "start" in scripts else "npm start")
            prereq, cmds = "- [Node.js](https://nodejs.org/)", f"{clone}\nnpm install\n{run}"
        else:
            return None
        block = f"{S}\n\n## Getting Started\n\n### Prerequisites\n\n{prereq}\n\n### Run\n\n```bash\n{cmds}\n```\n\n{E}"
        if S in content and E in content:
            return marker_replace(content, S, E, block)
        r2 = insert_after(content, ["<!-- portfolio-toc:end -->", "<!-- portfolio-badges:end -->"], block)
        return r2 if r2 is not None else insert_after_h1(content, block)

    return build


def _slug(h):
    s = h.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    return s.replace(" ", "-")


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


def fixable(r):
    """True when at least one deterministic sweep can improve this repo."""
    return any(s["applies"](r) for s in SWEEP_SPECS)


def _born(created):
    """Parse an inventory `created_at` into a datetime, or None if unusable.

    None covers two distinct kinds of bad input, and callers treat both the same
    (fail closed): a non-string shape (int, list, ... — a hand-edited or truncated
    .forge/data.json), rejected *before* `.replace()`/`fromisoformat()` can raise
    on it; and a string that simply is not a timestamp.
    """
    if not isinstance(created, str):
        return None
    try:
        return datetime.fromisoformat(created.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def eligible(r, cfg, now=None):
    """Harmonization-eligible? Adds README/grace/topic checks on top of `_scored`."""
    if not _scored(r, cfg) or r.get("no_readme") or r.get("empty"):
        return False
    if cfg.get("ignore_topic") and cfg["ignore_topic"] in (r.get("topics") or []):
        return False
    created, days = r.get("created_at"), cfg.get("grace_days") or 0
    if created and days:
        born = _born(created)
        if born is None:
            # We cannot confirm this repo is past its grace period, so skip it
            # this run rather than risk an unattended PR against a repo we
            # can't age-check. (A *missing* created_at is a different, expected
            # case — legacy inventory data — handled above by `created and
            # days` being falsy; that path deliberately does not block.)
            return False
        if (now or datetime.now(timezone.utc)) - born < timedelta(days=days):
            return False
    return fixable(r)


def hold_reason(r, cfg, now=None):
    """Why a fixable repo is not harmonization-eligible, in one short phrase.

    Returns None when the repo *is* eligible. Mirrors `eligible()`'s checks in the
    same order so the roll-up issue can name the guardrail that held a repo back
    instead of advertising it as a pending PR target the forge will never open.
    """
    if eligible(r, cfg, now=now):
        return None
    if cfg.get("ignore_topic") and cfg["ignore_topic"] in (r.get("topics") or []):
        return f"opted out via the `{cfg['ignore_topic']}` topic"
    if r.get("empty"):
        return "repository is empty"
    created, days = r.get("created_at"), cfg.get("grace_days") or 0
    if created and days:
        if _born(created) is None:
            return "unreadable `created_at`"
        return f"inside the {days}-day grace period"
    return "not eligible for harmonization"


def build_all(cfg):
    """Instantiate every sweep's build once (they read shared state at construction)."""
    return [(s, s["make_build"](cfg)) for s in SWEEP_SPECS]


# Builds insert content via two code paths that don't always agree on blank-line
# count: `insert_after`'s raw newline splice (first insertion) vs
# `marker_replace`'s rstrip-based join (every insertion after that). Chaining
# several builds back to back can leave runs of 3+ blank lines butted up against
# a portfolio marker. We normalize ONLY those marker-adjacent runs — never a
# blanket `\n{3,}` -> `\n\n` over the whole document, which would also flatten
# blank lines a fenced code block may legitimately contain. Anchoring on the
# marker text (rather than position/line-scanning) keeps code fences untouched
# without needing to track fence state here.
_BLANKS_BEFORE_MARKER = re.compile(r"\n{3,}(?=<!-- portfolio-[\w-]+:start -->)")
_BLANKS_AFTER_MARKER = re.compile(r"(<!-- portfolio-[\w-]+:end -->)\n{3,}")


def harmonize_content(pairs, r, repo, content):
    """Apply every applicable build in registry order, chaining in memory.

    Returns the transformed content (possibly identical to the input). Each build
    sees the previous build's output, which is why `toc` — running last — picks up
    the sections the earlier builds just inserted. Once at least one build has
    written something, the marker-adjacent blank runs left behind by chaining are
    normalized (see `_BLANKS_BEFORE_MARKER`) — so the returned content can differ
    from the input by more than the sum of the builds' own insertions.

    When no build changes anything the input is returned *verbatim*, normalization
    included. `spec["applies"](r)` only means a sweep is relevant to this repo, not
    that its build can write: `gettingstarted` returns None for an unrecognized
    stack, `techstack` with no manifest and no primary language, `toc` under four
    H2s. Normalizing regardless would turn a pre-existing blank run next to a
    marker into a whitespace-only diff — enough to open a PR whose body claims to
    add sections that were never added.
    """
    out = content
    changed = False
    for spec, build in pairs:
        if not spec["applies"](r):
            continue
        new = build(r, repo, out)
        if new and new != out:
            out = new
            changed = True
    if not changed:
        return content
    out = _BLANKS_BEFORE_MARKER.sub("\n\n", out)
    out = _BLANKS_AFTER_MARKER.sub(r"\1\n\n", out)
    return out


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


def cmd_harmonize_pr(cfg, commit=False):
    """Open one harmonization PR per eligible repo. Returns action counts.

    Writes nothing unless `commit` is true — the same dry-run-by-default contract
    the sweeps have always had. A dry run still does the full read-only pass (scan
    each README, run the builds in memory) so it can report which repos would
    actually get a PR, tallied under `would_pr`, rather than guessing from
    eligibility alone.
    """
    data = json.load(open(f"{cfg['workdir']}/data.json"))
    eligible_repos = [r for r in data if eligible(r, cfg)]
    raw_cap = cfg.get("max_prs")
    # An explicit cap of 0 must be honored as "open no PRs this run", not
    # treated as "unset" -- `0 or len(targets)` would silently discard the
    # cap since 0 is falsy, so check identity against None instead. A
    # negative cap is nonsensical as "how many PRs to open"; clamp to 0
    # rather than let Python's negative-index slicing reinterpret it as
    # "all but the last N", which would still process (and PR) repos.
    cap = raw_cap if raw_cap is not None else len(eligible_repos)
    cap = max(cap, 0)
    # Slicing a stable, inventory-ordered list means the same head repos win
    # every week: this starves the tail rather than rotating through it. Not a
    # problem while the eligible set stays under the cap (5 incomplete vs a cap
    # of 10 today); revisit if the backlog ever exceeds max_prs persistently.
    targets, capped = eligible_repos[:cap], eligible_repos[cap:]

    def names(bucket):
        return ", ".join(f"{r['owner']}/{r['name']}" for r in bucket)

    # The counts are pre-cap on purpose: `len(targets)` after slicing reports
    # "0 eligible repo(s)" for a cap of 0, which reads as "there was nothing to
    # do" when the truth is "there was work and the cap suppressed all of it".
    print(f"PR MODE{'' if commit else ' · DRY-RUN (nothing will be written)'}"
          f" · {len(eligible_repos)} eligible repo(s)"
          + (f" · {len(capped)} deferred by max_prs" if capped else ""))
    if targets:
        print(f"  eligible: {names(targets)}")
    if capped:
        print(f"  deferred: {names(capped)}")

    pairs = build_all(cfg)
    counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0,
              "capped": len(capped), "would_pr": 0}

    def work(r):
        repo = f"{r['owner']}/{r['name']}"
        try:
            content, _, path = get_readme(repo)
            if not content or not content.strip():
                return "unchanged", repo, ""
            new = harmonize_content(pairs, r, repo, content)
            if new == content:
                return "unchanged", repo, ""
            if not commit:
                return "would_pr", repo, "(dry-run: no branch, no PR)"
            action, detail = open_or_update_pr(repo, r, new, path, cfg)
            return action, repo, detail
        except Exception as exc:  # noqa: BLE001 - one bad repo must not end the run
            # ThreadPoolExecutor.map re-raises on iteration, which would abandon
            # every repo after this one. Malformed API responses are a live
            # concern here: api() hands back raw stdout (a str) when a response
            # is not JSON, and several guards use substring `in` checks that a
            # str passes before the subscript raises.
            return "failed", repo, f"unexpected {type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=cfg["max_workers"]) as ex:
        for action, repo, detail in ex.map(work, targets):
            counts[action] += 1
            if action != "unchanged":
                print(f"  {action}: {repo} {detail}")
    print("Summary:", json.dumps(counts))
    return counts


REPORT_TITLE = "readme-forge: portfolio README drift"


REPORT_FOOTER = "<sub>Maintained by readme-forge — this issue is edited in place, never duplicated.</sub>"

# An empty scan is indistinguishable from a perfect portfolio in the numbers
# ("0/0 complete"), so it gets its own body naming the plausible causes. The
# inventory step already refuses to continue on an empty repo list; this is the
# backstop for the case where every repo returned was filtered out instead.
EMPTY_SCAN_BODY = (
    "**No repositories were scored.** That is a broken run, not a clean portfolio — "
    "treat this issue as an alarm, not a status.\n\n"
    "Likely causes:\n\n"
    "- `FORGE_ORGS` is unset, mistyped, or names an account that no longer exists\n"
    "- the `FORGE_TOKEN` PAT expired, or its repository selection drifted and no longer "
    "covers those accounts\n"
    "- every repository returned was filtered out by `include_forks` / `include_archived` / "
    "the `exclude_*` rules in the config\n\n"
    + REPORT_FOOTER
)


def report_body(data, cfg):
    """Build the roll-up issue body. Pure — no network, stable for identical input."""
    scored = [r for r in data if _scored(r, cfg) and not r.get("no_readme")]
    if not scored:
        return EMPTY_SCAN_BODY
    incomplete = [r for r in scored
                  if not all(essential_ok(r, k) for k in cfg["essentials"])]
    lines = [f"**{len(scored) - len(incomplete)}/{len(scored)}** scored repositories are complete.",
             ""]
    if not incomplete:
        lines.append("Every scored repository meets the standard. Nothing to do.")
        return "\n".join(lines)

    def rows(bucket, reason=False):
        if reason:
            out = ["| Repository | Missing | Held back by |", "|---|---|---|"]
        else:
            out = ["| Repository | Missing |", "|---|---|"]
        for r in sorted(bucket, key=lambda x: (x["owner"], x["name"])):
            miss = ", ".join(k for k in cfg["essentials"] if not essential_ok(r, k))
            row = f"| `{r['owner']}/{r['name']}` | {miss} |"
            if reason:
                row += f" {hold_reason(r, cfg)} |"
            out.append(row)
        return out + [""]

    # Bucketed on `eligible`, not `fixable`: a sweep *applying* to a repo says
    # nothing about whether the guardrails will let the forge near it. Listing a
    # forge-ignore'd or in-grace repo under "the forge opens PRs for these" is a
    # promise the harmonize run will not keep.
    auto = [r for r in incomplete if eligible(r, cfg)]
    held = [r for r in incomplete if fixable(r) and not eligible(r, cfg)]
    human = [r for r in incomplete if not fixable(r)]
    if auto:
        lines += ["## Fixable automatically", "",
                  "The forge opens PRs for these on its next harmonize run.", ""]
        lines += rows(auto)
    if held:
        lines += ["## Fixable, but held back by a guardrail", "",
                  "A sweep could write these, but the forge will not open a PR for them. "
                  "They stay listed here so the gap is visible; no bot writes reach them.", ""]
        lines += rows(held, reason=True)
    if human:
        lines += ["## Needs a human", "",
                  "No deterministic sweep can write these — they need real judgement.", ""]
        lines += rows(human)
    lines.append(REPORT_FOOTER)
    return "\n".join(lines)


def cmd_report(cfg, repo):
    """Create or edit the single roll-up issue in `repo`.

    Returns the action taken: "created", "updated", or "failed". The open-issue
    search is fail-closed by design: a search *error* is not the same thing as
    "no issue is open" (isinstance(found, list) and found is False either way),
    and treating it as "none open" would POST a duplicate roll-up issue every
    time the search merely flakes -- the same reasoning open_or_update_pr
    already applies to its own PR search (see find_open_pr).
    """
    data = json.load(open(f"{cfg['workdir']}/data.json"))
    body = report_body(data, cfg)
    label = cfg["report_label"]
    found, err = api(f"repos/{repo}/issues?state=open&labels={label}")
    if err:
        print(f"failed: {err}")
        return "failed"
    if isinstance(found, list) and found:
        num = found[0]["number"]
        _, err = api(f"repos/{repo}/issues/{num}", "PATCH", {"body": body})
        if err:
            print(f"failed: {err}")
            return "failed"
        print(f"updated issue #{num}")
        return "updated"
    _, err = api(f"repos/{repo}/issues", "POST",
                 {"title": REPORT_TITLE, "body": body, "labels": [label]})
    if err:
        print(f"failed: {err}")
        return "failed"
    print("created report issue")
    return "created"


# --------------------------------------------------------------- main --------

def main():
    ap = argparse.ArgumentParser(description="Score & harmonize README quality across GitHub org(s).")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
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
    sp = sub.add_parser("sweep")
    sp.add_argument("which", choices=list(SWEEPS))
    sp.add_argument("--commit", action="store_true")
    sp.add_argument("--org", action="append", default=[])
    rp = sub.add_parser("report")
    rp.add_argument("--repo", required=True, help="owner/name of the repo holding the roll-up issue")
    args = ap.parse_args()
    cfg = load_config(args.config)

    if args.cmd == "inventory":
        cmd_inventory(cfg, args.org, args.only)
    elif args.cmd == "scan":
        cmd_scan(cfg)
    elif args.cmd == "dashboard":
        import dashboard
        dashboard.generate(cfg)
    elif args.cmd == "sweep":
        SWEEPS[args.which](cfg, args.commit)
    elif args.cmd == "report":
        # Exit non-zero on failure so a scheduled run cannot go green while the
        # roll-up issue silently goes stale (a rate-limited search, a rejected
        # PATCH). The same reasoning applies to the PR driver below.
        if cmd_report(cfg, args.repo) == "failed":
            sys.exit("report failed — the roll-up issue was not updated")
    elif args.cmd == "run":
        cmd_inventory(cfg, args.org, args.only)
        cmd_scan(cfg)
        import dashboard
        dashboard.generate(cfg)
    elif args.cmd == "harmonize":
        cmd_inventory(cfg, args.org, args.only)
        cmd_scan(cfg)
        if args.pr:
            if args.max_prs is not None:
                cfg["max_prs"] = args.max_prs
            # --pr honors --commit like every other write path: without it the
            # driver reports what it would do and touches nothing. The dashboard
            # is not regenerated here -- the PR run neither publishes nor uploads
            # it, and `run`/`dashboard` own that output.
            counts = cmd_harmonize_pr(cfg, commit=args.commit)
            if counts["failed"]:
                sys.exit(f"{counts['failed']} repo(s) failed to harmonize")
        else:
            for name in SWEEP_ORDER:
                print(f"\n=== sweep: {name} ===")
                SWEEPS[name](cfg, args.commit)
                cmd_scan(cfg)  # refresh so the next sweep sees prior insertions
            import dashboard
            dashboard.generate(cfg)


if __name__ == "__main__":
    main()
