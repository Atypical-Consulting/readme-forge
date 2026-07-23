#!/usr/bin/env python3
"""dashboard.py — render .forge/dashboard.html from .forge/data.json (+ baseline).

Imported by readme_forge.py (`dashboard` / `run` / `harmonize` subcommands), or
run standalone: `python dashboard.py` after a scan. Config-driven: essentials and
exclusions come from forge.config.json; nothing is hardcoded to an account.
"""
import json
import os
import statistics

FEATURES = [
    ("banner_logo", "Banner / logo", True), ("screenshot_gif", "Screenshot / GIF", True),
    ("live_demo", "Live demo", True), ("badges", "Badges", False), ("md_table", "Table", False),
    ("toc", "Table of contents", False), ("features_sec", "Features", False),
    ("tech_stack", "Tech stack", False), ("install", "Installation", False),
    ("usage", "Usage", False), ("config", "Configuration", False), ("api_doc", "API / docs", False),
    ("code_blocks", "Code examples", False), ("roadmap", "Roadmap", True), ("faq", "FAQ", True),
    ("contributing", "Contributing", False), ("license_sec", "License", False),
    ("credits", "Credits", False), ("star_cta", "Star CTA", True),
    ("details_fold", "Collapsibles", True), ("emoji", "Emoji", False),
]
LABELS = {
    "banner_logo": "Banner", "badges": "Badges", "toc": "Table of contents", "features_sec": "Features",
    "tech_stack": "Tech stack", "install": "Installation", "usage": "Usage / Getting Started",
    "code_blocks": "Code examples", "roadmap": "Roadmap", "contributing": "Contributing", "license_sec": "License",
}


def has(r, k):
    v = r.get(k)
    return v is True or (isinstance(v, int) and not isinstance(v, bool) and v > 0)


def generate(cfg):
    wd = cfg["workdir"]
    essentials = cfg["essentials"]

    def ok(r, k):
        if k == "usage":
            return has(r, "usage") or has(r, "getting_started")
        return has(r, k)

    def excluded(n):
        import re
        return (n in cfg["exclude_names"] or any(n.endswith(s) for s in cfg["exclude_suffixes"])
                or any(re.search(p, n) for p in cfg["exclude_regex"]))

    def scored(r):
        incl = (cfg["include_forks"] or not r["fork"]) and (cfg["include_archived"] or not r["archived"])
        return incl and not excluded(r["name"])

    data = json.load(open(f"{wd}/data.json"))
    baseline = json.load(open(f"{wd}/baseline.json")) if os.path.exists(f"{wd}/baseline.json") else None

    active = [r for r in data if scored(r)]
    excluded_n = sum(1 for r in data if (cfg["include_forks"] or not r["fork"]) and (cfg["include_archived"] or not r["archived"]) and not scored(r))
    withr = [r for r in active if not r.get("no_readme")]

    def missing(r):
        return essentials[:] if r.get("no_readme") else [k for k in essentials if not ok(r, k)]

    def ncomplete(rows):
        return sum(1 for r in rows if not r.get("no_readme") and not missing(r))

    complete = ncomplete(active)
    median = int(statistics.median([r["richness"] for r in withr])) if withr else 0
    base = None
    if baseline:
        bmap = {r["repo"]: r for r in baseline}
        bact = [b for b in (bmap.get(r["repo"]) for r in active) if b]
        base = {"complete": ncomplete(bact),
                "median": int(statistics.median([b["richness"] for b in bact if not b.get("no_readme")])) if bact else 0}

    payload = {
        "repos": data,
        "features": [{"key": k, "label": l, "rare": rare, "count": sum(1 for r in withr if has(r, k))} for k, l, rare in FEATURES],
        "essentials": [{"key": k, "label": LABELS.get(k, k), "count": sum(1 for r in withr if ok(r, k))} for k in essentials],
        "meta": {"active": len(active), "complete": complete, "excluded": excluded_n, "median": median,
                 "essentials": essentials, "stars": sum(r.get("stars", 0) for r in data if not r["fork"]),
                 "generated": os.popen('date +"%Y-%m-%d %H:%M:%S"').read().strip() or "—", "baseline": base},
    }
    html = HTML.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))
    open(f"{wd}/dashboard.html", "w", encoding="utf-8").write(html)
    print(f"Wrote {wd}/dashboard.html · complete {complete}/{len(active)} · median {median}/21")


HTML = r"""<div class="wrap">
  <header class="hero">
    <div class="hero-main">
      <div class="eyebrow">README quality · live progress board</div>
      <h1>README Forge</h1>
      <p class="lede">Every repo scored on 21 best practices, of which <b>11 essentials</b>.
        Goal: <b>100% of active software repos complete</b>. Re-scan to watch the bar move.</p>
    </div>
    <div class="stamp"><span class="stamp-n" id="s-pct">—</span><span class="stamp-l">repos complete</span>
      <div class="stamp-bar"><span class="stamp-fill" id="s-fill"></span></div><span class="stamp-sub" id="s-sub">—</span></div>
  </header>
  <section class="stats" id="stats"></section>
  <section class="panel"><div class="panel-head"><h2>Progress to a perfect score</h2>
    <p class="hint">A repo is <b>complete</b> when it has all 11 essentials. Bar = share complete; the light ghost = starting point.</p></div>
    <div class="bigbar"><span class="bigbar-base" id="bb-base"></span><span class="bigbar-now" id="bb-now"></span><span class="bigbar-tick" id="bb-txt"></span></div></section>
  <section class="panel"><div class="panel-head"><h2>Essentials adoption</h2>
    <p class="hint">The 11 required sections. Click one to filter repos that lack it.</p></div><div class="prev" id="ess"></div></section>
  <section class="panel"><div class="panel-head row"><h2>To do</h2>
    <div class="controls"><input id="q" type="search" placeholder="Search a repo…"><label class="chk"><input type="checkbox" id="onlyInc" checked>only incomplete</label><button id="clear" class="clearbtn" hidden>✕ filter</button></div></div>
    <div class="tablescroll"><table><thead id="thead"></thead><tbody id="tbody"></tbody></table></div><p class="count" id="count"></p></section>
  <footer class="foot">Generated <span id="gen"></span> · scope: active software repos (<span id="excl"></span> excluded) · <code>readme_forge.py scan &amp;&amp; dashboard</code></footer>
</div>
<script>
const DATA=__PAYLOAD__, R=DATA.repos, M=DATA.meta, ESS=DATA.essentials, ESSK=M.essentials;
const has=(r,k)=>r[k]===true||(typeof r[k]==='number'&&r[k]>0);
const ok=(r,k)=>k==='usage'?(has(r,'usage')||has(r,'getting_started')):has(r,k);
const miss=r=>r.no_readme?ESSK.slice():ESSK.filter(k=>!ok(r,k));
const done=r=>!r.no_readme&&miss(r).length===0;
document.getElementById('gen').textContent=M.generated; document.getElementById('excl').textContent=M.excluded;
const pct=Math.round(M.complete/M.active*100);
document.getElementById('s-pct').textContent=pct+'%'; document.getElementById('s-fill').style.width=pct+'%'; document.getElementById('s-sub').textContent=M.complete+'/'+M.active;
function dl(n,w){if(w==null)return'';const d=n-w;return d===0?'':` <span class="dl ${d>0?'up':'dn'}">${d>0?'+':''}${d}</span>`;}
const b=M.baseline;
document.getElementById('stats').innerHTML=[[M.active,'active software repos','ok',''],[M.complete+'/'+M.active,'complete','gold',b?dl(M.complete,b.complete):''],[M.median+'/21','median richness','mut',b?dl(M.median,b.median):'']].map(([n,l,c,d])=>`<div class="stat ${c}"><div class="stat-n">${n}${d}</div><div class="stat-l">${l}</div></div>`).join('');
document.getElementById('bb-base').style.width=(b?Math.round(b.complete/M.active*100):0)+'%'; document.getElementById('bb-now').style.width=pct+'%'; document.getElementById('bb-txt').textContent=pct+'% · target 100%';
let filter=null;
function prev(){const el=document.getElementById('ess');const s=[...ESS].sort((a,b)=>b.count-a.count);
  el.innerHTML=s.map(f=>{const p=Math.round(f.count/(R.filter(r=>!r.fork&&!r.archived&&!r.no_readme).length||1)*100);return `<button class="prow${filter===f.key?' active':''}" data-k="${f.key}"><span class="prow-l">${f.label}</span><span class="prow-bar"><span class="prow-fill${p>=95?' done':''}" style="width:${Math.min(p,100)}%"></span></span><span class="prow-pct">${p}%</span></button>`;}).join('');
  el.querySelectorAll('.prow').forEach(x=>x.onclick=()=>{filter=filter===x.dataset.k?null:x.dataset.k;document.getElementById('clear').hidden=!filter;document.getElementById('onlyInc').checked=false;render();});}
function render(){const q=document.getElementById('q').value.toLowerCase(),oi=document.getElementById('onlyInc').checked;
  let rows=R.filter(r=>!r.fork&&!r.archived&&!r.no_readme).filter(r=>(!q||r.name.toLowerCase().includes(q))&&(!oi||!done(r))&&(!filter||!ok(r,filter))).map(r=>({r,m:miss(r)})).sort((a,b)=>a.m.length-b.m.length);
  const L=Object.fromEntries(ESS.map(e=>[e.key,e.label]));
  document.getElementById('thead').innerHTML='<tr><th>Repo</th><th class="num">Score</th><th class="num">Miss</th><th>Missing essentials</th></tr>';
  document.getElementById('tbody').innerHTML=rows.map(({r,m})=>`<tr><td><span class="rname">${r.owner}/${r.name}</span>${done(r)?' <span class="tag ok">✓</span>':''}</td><td class="num">${r.richness}</td><td class="num ${m.length?'':'mut'}">${m.length||'—'}</td><td>${m.map(k=>`<span class="chip">${L[k]}</span>`).join('')||'<span class="mut">—</span>'}</td></tr>`).join('');
  document.getElementById('count').textContent=rows.length+' repos'+(filter?' — missing '+ESS.find(e=>e.key===filter).label:'');}
document.getElementById('clear').onclick=()=>{filter=null;document.getElementById('clear').hidden=true;render();};
['q','onlyInc'].forEach(id=>document.getElementById(id).addEventListener(id==='q'?'input':'change',render));
prev();render();
</script>
<style>
:root{--bg:#EAECF0;--surface:#FFF;--surface2:#F5F6F8;--ink:#181D26;--muted:#5C6675;--line:#DBDFE6;--accent:#B4832F;--accent2:#C69749;--teal:#2C9A78;--warn:#C0762A;--shadow:0 1px 2px rgba(20,28,40,.06),0 8px 24px rgba(20,28,40,.05)}
@media(prefers-color-scheme:dark){:root{--bg:#0E1116;--surface:#161B22;--surface2:#1C222C;--ink:#E6EAF1;--muted:#8A93A2;--line:#28303B;--accent:#D9AE5F;--accent2:#E0BC77;--teal:#3DBE95;--warn:#E0975A;--shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.3)}}
:root[data-theme="dark"]{--bg:#0E1116;--surface:#161B22;--surface2:#1C222C;--ink:#E6EAF1;--muted:#8A93A2;--line:#28303B;--accent:#D9AE5F;--accent2:#E0BC77;--teal:#3DBE95;--warn:#E0975A}
:root[data-theme="light"]{--bg:#EAECF0;--surface:#FFF;--surface2:#F5F6F8;--ink:#181D26;--muted:#5C6675;--line:#DBDFE6;--accent:#B4832F;--accent2:#C69749;--teal:#2C9A78;--warn:#C0762A}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg)}
.wrap{--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,monospace;background:var(--bg);color:var(--ink);font-family:var(--sans);padding:clamp(16px,3vw,40px);max-width:1180px;margin:0 auto;line-height:1.5;font-size:15px}
h1,h2{text-wrap:balance;margin:0}
.hero{display:flex;gap:24px;align-items:flex-start;justify-content:space-between;margin-bottom:26px;flex-wrap:wrap}
.eyebrow{font-family:var(--mono);font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:600}
h1{font-size:clamp(28px,4.5vw,42px);letter-spacing:-.02em;margin:.28em 0 .3em;font-weight:680}
.lede{color:var(--muted);max-width:60ch;margin:0}
.stamp{flex:none;text-align:center;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px 22px;box-shadow:var(--shadow);min-width:170px}
.stamp-n{display:block;font-family:var(--mono);font-size:40px;font-weight:700;line-height:1;color:var(--accent)}
.stamp-l{font-size:11px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted)}
.stamp-bar{height:7px;background:var(--surface2);border:1px solid var(--line);border-radius:5px;overflow:hidden;margin:10px 0 6px}
.stamp-fill{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));transition:width .6s}
.stamp-sub{font-family:var(--mono);font-size:12px;color:var(--muted)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:22px}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 18px;box-shadow:var(--shadow);border-left:3px solid var(--line)}
.stat.ok{border-left-color:var(--teal)}.stat.gold{border-left-color:var(--accent)}.stat.mut{border-left-color:var(--muted)}
.stat-n{font-family:var(--mono);font-size:24px;font-weight:700}.stat-l{color:var(--muted);font-size:12.5px;margin-top:2px}
.dl{font-size:12px;font-weight:600;padding:1px 5px;border-radius:5px}.dl.up{color:var(--teal);background:color-mix(in srgb,var(--teal) 15%,transparent)}.dl.dn{color:var(--warn)}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:clamp(16px,2.4vw,26px);margin-bottom:22px;box-shadow:var(--shadow)}
.panel-head{margin-bottom:16px}.panel-head.row{display:flex;gap:16px;align-items:center;justify-content:space-between;flex-wrap:wrap}
h2{font-size:19px;font-weight:640}.hint{color:var(--muted);font-size:13px;margin:.4em 0 0}
.bigbar{position:relative;height:34px;background:var(--surface2);border:1px solid var(--line);border-radius:9px;overflow:hidden}
.bigbar-base{position:absolute;inset:0 auto 0 0;background:color-mix(in srgb,var(--accent) 22%,transparent)}
.bigbar-now{position:absolute;inset:0 auto 0 0;background:linear-gradient(90deg,var(--accent),var(--accent2));transition:width .6s}
.bigbar-tick{position:absolute;right:12px;top:50%;transform:translateY(-50%);font-family:var(--mono);font-size:13px;font-weight:600;mix-blend-mode:difference;filter:invert(1)}
.prev{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:6px 22px}
.prow{display:grid;grid-template-columns:140px 1fr 40px;align-items:center;gap:10px;background:none;border:0;border-radius:7px;padding:5px 8px;cursor:pointer;text-align:left;color:inherit;font:inherit}
.prow:hover{background:var(--surface2)}.prow.active{background:var(--surface2);outline:1.5px solid var(--accent)}
.prow-l{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.prow-bar{height:8px;background:var(--surface2);border-radius:5px;overflow:hidden;border:1px solid var(--line)}
.prow-fill{display:block;height:100%;background:var(--teal)}.prow-fill.done{background:var(--teal)}
.prow-pct{font-family:var(--mono);font-size:12.5px;text-align:right;color:var(--muted)}
.controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#q{font:inherit;font-size:13.5px;padding:7px 11px;border:1px solid var(--line);border-radius:9px;background:var(--surface2);color:var(--ink);min-width:170px}
.chk{display:flex;align-items:center;gap:5px;font-size:12.5px;color:var(--muted);cursor:pointer}
.clearbtn{font:inherit;font-size:12px;padding:6px 10px;border-radius:8px;border:1px solid var(--accent);background:none;color:var(--accent);cursor:pointer}
.tablescroll{overflow-x:auto;border:1px solid var(--line);border-radius:12px}
table{border-collapse:collapse;width:100%;font-size:13px}
thead th{position:sticky;top:0;background:var(--surface2);border-bottom:1px solid var(--line);padding:9px 10px;text-align:left;color:var(--muted);font-size:11.5px}
tbody td{padding:7px 10px;border-bottom:1px solid var(--line)}tbody tr:hover td{background:var(--surface2)}
.num{text-align:right;font-family:var(--mono)}.rname{font-family:var(--mono);font-size:12.5px}
.tag.ok{font-size:9.5px;padding:1px 5px;border-radius:5px;background:color-mix(in srgb,var(--teal) 20%,transparent);color:var(--teal)}
.chip{display:inline-block;font-size:11px;padding:2px 8px;margin:2px 4px 2px 0;border-radius:20px;background:color-mix(in srgb,var(--warn) 13%,transparent);color:var(--warn);border:1px solid color-mix(in srgb,var(--warn) 30%,transparent)}
.mut{color:var(--muted)}.count{color:var(--muted);font-size:12.5px;margin:12px 2px 0;font-family:var(--mono)}
.foot{color:var(--muted);font-size:12px;text-align:center;margin-top:26px;font-family:var(--mono)}.foot code{background:var(--surface2);padding:1px 5px;border-radius:4px}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
</style>"""

if __name__ == "__main__":
    import json as _j
    generate(_j.load(open("forge.config.json")) if os.path.exists("forge.config.json") else {
        "workdir": ".forge", "essentials": ["banner_logo", "badges", "toc", "features_sec", "tech_stack",
        "install", "usage", "code_blocks", "roadmap", "contributing", "license_sec"],
        "include_forks": False, "include_archived": False, "exclude_names": [".github"],
        "exclude_suffixes": ["-backup"], "exclude_regex": []})
