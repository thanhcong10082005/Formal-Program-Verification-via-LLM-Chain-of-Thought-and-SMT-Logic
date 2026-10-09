"""
visualize_results.py - Generate a self-contained HTML dashboard from experiment results.

Reads results/mbpp_experiment/all_results.json and results/complex_experiment/all_complex_results.json
and writes reports/DASHBOARD.html with charts (pure inline JS/CSS, no dependencies).
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def pct(a, b):
    return round(100 * a / b, 1) if b else 0.0


def load_mbpp():
    d = json.loads((ROOT / "results" / "mbpp_experiment" / "all_results.json").read_text())
    tasks = []
    tot = {
        "v": 0,
        "ce": 0,
        "ur": 0,
        "unsup": 0,
        "te": 0,
        "ast_tok_in": 0,
        "ast_tok_out": 0,
        "direct_tok_in": 0,
        "direct_tok_out": 0,
    }
    for x in d:
        a, u = x["anchored"], x["unanchored"]
        ast_gen = x.get("ast_generation", {})
        direct_gen = x.get("direct_generation", {})
        tasks.append({
            "id": x["task_id"],
            "name": x["name"],
            "anchored": [a["n_verified"], a["n_counterexample"], a["n_unreachable"],
                         a["n_unsupported"] + a["n_translation_error"]],
            "unanchored": [u["n_verified"], u["n_counterexample"], u["n_unreachable"],
                           u["n_unsupported"] + u["n_translation_error"]],
            "ast_tok_in": ast_gen.get("tokens_in", x.get("tokens_in", 0)),
            "ast_tok_out": ast_gen.get("tokens_out", x.get("tokens_out", 0)),
            "direct_tok_in": direct_gen.get("tokens_in", 0),
            "direct_tok_out": direct_gen.get("tokens_out", 0),
        })
        tot["v"] += a["n_verified"]; tot["ce"] += a["n_counterexample"]
        tot["ur"] += a["n_unreachable"]
        tot["unsup"] += a["n_unsupported"] + a["n_translation_error"]
        tot["ast_tok_in"] += tasks[-1]["ast_tok_in"]
        tot["ast_tok_out"] += tasks[-1]["ast_tok_out"]
        tot["direct_tok_in"] += tasks[-1]["direct_tok_in"]
        tot["direct_tok_out"] += tasks[-1]["direct_tok_out"]
    return tasks, tot


def load_complex():
    d = json.loads((ROOT / "results" / "complex_experiment" / "all_complex_results.json").read_text())
    cases = []
    for x in d:
        a, u = x["ast_anchored"], x["unanchored"]
        cases.append({
            "id": x["id"],
            "category": x["category"],
            "desc": x["description"],
            "anchored": [a["n_verified"], a["n_counterexample"], a["n_unreachable"],
                         a["n_unsupported"] + a["n_translation_error"] + a["n_unknown_timeout"]],
            "unanchored": [u["n_verified"], u["n_counterexample"], u["n_unreachable"],
                           u["n_unsupported"] + u["n_translation_error"] + u["n_unknown_timeout"]],
        })
    return cases


def main():
    mbpp, mbpp_tot = load_mbpp()
    complex_cases = load_complex()

    n_claims = sum(sum(t["anchored"]) for t in mbpp)
    n_valid_verified = sum(t["anchored"][0] + t["anchored"][1] for t in mbpp)
    halluc = 100 * mbpp_tot["unsup"] / n_claims if n_claims else 0

    data = {
        "mbpp": mbpp,
        "mbppTot": mbpp_tot,
        "mbppSummary": {
            "tasks": len(mbpp),
            "claims": n_claims,
            "verified": mbpp_tot["v"],
            "ce": mbpp_tot["ce"],
            "verRate": pct(mbpp_tot["v"], n_claims),
            "hallucRate": halluc,
            "astTokIn": mbpp_tot["ast_tok_in"],
            "astTokOut": mbpp_tot["ast_tok_out"],
            "directTokIn": mbpp_tot["direct_tok_in"],
            "directTokOut": mbpp_tot["direct_tok_out"],
        },
        "complex": complex_cases,
    }

    html = """<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8">
<title>AST-Anchored Verification Dashboard</title>
<style>
:root{--bg:#0f1220;--card:#181c2e;--fg:#e7e9f4;--mut:#8b90ad;--acc:#7aa2f7;--ok:#9ece6a;--ce:#f7768e;--ur:#bb9af7;--un:#e0af68}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,'Segoe UI',sans-serif;background:var(--bg);color:var(--fg);padding:28px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:26px 0 12px;color:var(--acc)}
.lead{color:var(--mut);margin-bottom:24px}
.cards{display:flex;gap:14px;flex-wrap:wrap}
.card{background:var(--card);border-radius:12px;padding:16px 20px;min-width:150px}
.card .n{font-size:30px;font-weight:700}
.card .l{color:var(--mut);font-size:12px;margin-top:2px}
.legend{display:flex;gap:16px;font-size:12.5px;color:var(--mut);margin:6px 0 10px;flex-wrap:wrap}
.dot{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:middle}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #262b45}
th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.05em}
.badge{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11.5px;font-weight:600}
.b-a{background:#1d3a2a;color:var(--ok)}.b-c{background:#3a1d26;color:var(--ce)}.b-u{background:#2a2240;color:var(--ur)}.b-x{background:#403317;color:var(--un)}
.rowbar{display:flex;align-items:center;gap:8px;margin:14px 0}
.rowbar .lbl{width:210px;font-size:13px;text-align:right;color:var(--fg);flex-shrink:0}
.rowbar .lbl small{display:block;color:var(--mut);font-size:11px}
.track{flex:1;display:flex;flex-direction:column;gap:4px}
.barline{display:flex;align-items:center;gap:8px}
.barline .bside{width:76px;font-size:10.5px;color:var(--mut);flex-shrink:0}
.bar{height:15px;border-radius:4px;min-width:2px}
.seg-v{background:var(--ok)}.seg-ce{background:var(--ce)}.seg-ur{background:var(--ur)}.seg-x{background:var(--un)}
.val{font-size:11.5px;color:var(--mut);margin-left:6px}
.note{background:#1d2340;border-left:3px solid var(--acc);padding:10px 14px;border-radius:6px;font-size:13px;color:var(--mut);margin-top:14px}
.tokchart{display:flex;align-items:flex-end;gap:10px;height:170px;padding:10px 4px}
.tokbar{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%}
.tokbar .stack{width:34px;display:flex;flex-direction:column;justify-content:flex-end;border-radius:5px 5px 0 0;overflow:hidden}
.tin{background:var(--acc)}.tout{background:#2f3a5e}
.tokbar .cap{font-size:10.5px;color:var(--mut);margin-top:5px;text-align:center}
</style></head><body>
<h1>AST-Anchored vs Direct Formalization Dashboard</h1>
<div class="lead">AST_ANCHORED uses anchored Python claims; UNANCHORED is Baseline A Direct Formalization with independent SMT-LIB2 specifications and no replay.</div>

<h2>MBPP Experiment — 10 tasks</h2>
<div class="cards">
<div class="card"><div class="n">__TASKS__</div><div class="l">Tasks (QF-LIA)</div></div>
<div class="card"><div class="n">__CLAIMS__</div><div class="l">LLM claims generated</div></div>
<div class="card"><div class="n" style="color:var(--ok)">__VER__%</div><div class="l">Verified by Z3</div></div>
<div class="card"><div class="n" style="color:var(--ce)">__CE__</div><div class="l">Counterexamples</div></div>
<div class="card"><div class="n" style="color:var(--acc)">100%</div><div class="l">Anchor accuracy</div></div>
<div class="card"><div class="n">__TOKIN__</div><div class="l">AST tokens in</div></div>
<div class="card"><div class="n">__TOKOUT__</div><div class="l">AST tokens out</div></div>
<div class="card"><div class="n">__DIRECTTOKIN__</div><div class="l">Direct tokens in</div></div>
<div class="card"><div class="n">__DIRECTTOKOUT__</div><div class="l">Direct tokens out</div></div>
</div>
<div class="legend">
<span><span class="dot seg-v"></span>VERIFIED</span>
<span><span class="dot seg-ce"></span>COUNTEREXAMPLE</span>
<span><span class="dot seg-ur"></span>UNREACHABLE</span>
<span><span class="dot seg-x"></span>UNSUPPORTED / TRANS_ERROR</span>
</div>
<table>
<tr><th>Task</th><th>Name</th><th>AST_ANCHORED</th><th>UNANCHORED</th></tr>
__MBPP_ROWS__
</table>

<h2>Complex Experiment — A/B/C categories</h2>
<div class="legend">
<span><span class="dot seg-v"></span>VERIFIED</span>
<span><span class="dot seg-ce"></span>COUNTEREXAMPLE</span>
<span><span class="dot seg-ur"></span>UNREACHABLE</span>
<span><span class="dot seg-x"></span>rejected / unsupported</span>
<span style="margin-left:12px">Top row = AST_ANCHORED · Bottom = UNANCHORED</span>
</div>
<div id="complex"></div>
<div class="note"><b>Direct baseline</b>: UNANCHORED checks only the negation of each validated SMT-LIB2 formula, so its counterexamples have no target location or replay verdict. <b>Grounding</b>: AST_ANCHORED retains exact NodeId binding and target-state replay.</div>
<script>
const C=__DATA__;
// complex grouped bars
const colors=["seg-v","seg-ce","seg-ur","seg-x"], names=["VERIFIED","COUNTEREX","UNREACH","REJECT"];
document.getElementById('complex').innerHTML = C.complex.map(c=>{
  const max=Math.max(1,...c.anchored,...c.unanchored);
  const line=(arr,side)=>{
    let segs='',cur=0;
    arr.forEach((v,i)=>{if(v){segs+=`<div class="bar ${colors[i]}" style="width:${v/max*82}%" title="${names[i]}: ${v}"></div>`;}});
    const tags=arr.map((v,i)=>v?`<span class="badge b-${'vcux'[i]}">${names[i]} ${v}</span>`:'').join(' ')||'<span style="color:#8b90ad">—</span>';
    return `<div class="barline"><span class="bside">${side}</span><div class="track" style="flex-direction:row;flex-wrap:wrap;gap:6px">${tags}</div></div>`;
  };
  return `<div style="background:var(--card);border-radius:12px;padding:14px 18px;margin:10px 0">
    <div style="font-weight:700;font-size:14px">${c.id} <span style="color:var(--mut);font-weight:400;font-size:12px">· ${c.category}</span></div>
    <div style="color:var(--mut);font-size:12px;margin-bottom:6px">${c.desc}</div>${line(c.anchored,'anchored')}${line(c.unanchored,'unanchored')}
  </div>`;}).join('');
</script>
</body></html>"""

    rows = []
    for t in mbpp:
        b = lambda a: " ".join(
            f'<span class="badge b-{k}">{n.split()[0]} {v}</span>' if v else ""
            for k, v, n in zip("vcux", a, ["VERIFIED", "COUNTEREX", "UNREACH", "REJECT"])
        ) or "—"
        rows.append(f'<tr><td><b>{t["id"]}</b></td><td>{t["name"]}</td><td>{b(t["anchored"])}</td><td>{b(t["unanchored"])}</td></tr>')

    out = html
    for k, v in [("__TASKS__", len(mbpp)), ("__CLAIMS__", n_claims),
                 ("__VER__", pct(mbpp_tot["v"], n_claims)), ("__CE__", mbpp_tot["ce"]),
                 ("__TOKIN__", f"{mbpp_tot['ast_tok_in']:,}"), ("__TOKOUT__", f"{mbpp_tot['ast_tok_out']:,}"),
                 ("__DIRECTTOKIN__", f"{mbpp_tot['direct_tok_in']:,}"), ("__DIRECTTOKOUT__", f"{mbpp_tot['direct_tok_out']:,}"),
                 ("__MBPP_ROWS__", "\n".join(rows)), ("__DATA__", json.dumps(data))]:
        out = out.replace(k, str(v))

    dest = ROOT / "reports" / "DASHBOARD.html"
    dest.write_text(out)
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
