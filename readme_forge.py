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

Every write is idempotent (HTML markers), edits via the GitHub contents API
(GET README -> PUT, no clone), commits directly to the default branch, and is
DRY-RUN unless you pass --commit. The content essentials that need real
understanding (Features / Usage) are left to you or an AI agent — see README.md.
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
    "ignore_topic": "forge-ignore",  # topic that opts a repo out entirely
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


def put_readme(repo, path, new, sha, msg):
    b64 = base64.b64encode(new.encode("utf-8")).decode("ascii")
    j, err = api(f"repos/{repo}/contents/{path}", "PUT",
                 {"message": msg, "content": b64, "sha": sha})
    return err is None, err


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
    if only:
        want = set(only.split(","))
        repos = [r for r in repos if r["name"] in want]
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
    the sections the earlier builds just inserted.
    """
    out = content
    for spec, build in pairs:
        if not spec["applies"](r):
            continue
        new = build(r, repo, out)
        if new:
            out = new
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
    sp = sub.add_parser("sweep")
    sp.add_argument("which", choices=list(SWEEPS))
    sp.add_argument("--commit", action="store_true")
    sp.add_argument("--org", action="append", default=[])
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
    elif args.cmd == "run":
        cmd_inventory(cfg, args.org, args.only)
        cmd_scan(cfg)
        import dashboard
        dashboard.generate(cfg)
    elif args.cmd == "harmonize":
        cmd_inventory(cfg, args.org, args.only)
        cmd_scan(cfg)
        for name in SWEEP_ORDER:
            print(f"\n=== sweep: {name} ===")
            SWEEPS[name](cfg, args.commit)
            cmd_scan(cfg)  # refresh so the next sweep sees prior insertions
        import dashboard
        dashboard.generate(cfg)


if __name__ == "__main__":
    main()
