"""Export: one HTML drift report, one mapping spec (JSON), and CRM-ready CSVs for approved sites."""
from __future__ import annotations

import csv
import html
import json
from datetime import datetime
from pathlib import Path

from . import schema as S

PROV_LABEL = {"library": "Reused", "inferred": "Matched on data", "human": "Human decision",
              "open": "Needs a decision", "missing": "Missing"}

SITE_NAMES = {"MUM": "Mumbai", "AHM": "Ahmedabad", "GRG": "Grangemouth", "LXG": "Lexington",
              "AUR": "Aurora", "TUR": "Turbhe"}


def site_name(a):
    return SITE_NAMES.get(a.site) or a.path.stem.split("_", 1)[-1].replace("-", " ").title()


def spec(engine, analyses) -> dict:
    out = {"generated": datetime.now().isoformat(timespec="seconds"),
           "target": "CRM Order", "fields": S.CANONICAL_FIELDS, "sites": {}}
    for code, a in analyses.items():
        out["sites"][code] = {
            "file": a.path.name,
            "approved": code in engine.ws.library["approved_sites"],
            "mapping": {f: {"column": m["column"], "how": m["how"],
                            "transform": a.transforms.get(f, {"op": "copy"})} for f, m in a.mapping.items()},
            "custom_fields": a.custom_fields,
            "flags": [f.to_dict() for f in a.flags],
            "summary": a.summary(),
        }
    return out


def export(engine, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    analyses = engine.analyze_all()
    sp = spec(engine, analyses)
    (out_dir / "mapping_spec.json").write_text(json.dumps(sp, indent=2, default=str))
    (out_dir / "baseline_report.html").write_text(render(engine, analyses))
    written = []
    for code, a in analyses.items():
        if code not in engine.ws.library["approved_sites"]:
            continue
        res = engine.apply(a)
        if not res["records"]:
            continue
        cols = list(dict.fromkeys(k for r in res["records"] for k in r))
        p = out_dir / f"crm_orders_{code}.csv"
        with open(p, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(res["records"])
        written.append(p.name)
    return {"folder": str(out_dir), "report": "baseline_report.html", "spec": "mapping_spec.json",
            "crm_csvs": written}


def _e(x):
    return html.escape(str(x))


def render(engine, analyses) -> str:
    lib = engine.ws.library
    approved = lib["approved_sites"]
    sites = list(analyses.values())
    total_fields = len(S.FIELD_NAMES) * len(sites)
    reused = sum(a.summary()["library"] for a in sites)
    open_flags = sum(len(a.open_flags) for a in sites)
    resolved = sum(len(a.flags) - len(a.open_flags) for a in sites)

    # site rows
    site_rows = []
    for a in sites:
        s = a.summary()
        segs = "".join(
            f'<span class="seg p-{k}" style="flex:{s[k]}" title="{PROV_LABEL[k]}: {s[k]}"></span>'
            for k in ("library", "inferred", "human", "open", "missing") if s[k])
        state = ("Approved" if a.site in approved else
                 "Ready to approve" if not a.open_flags else f"{len(a.open_flags)} open")
        first = engine.ws.state.get("first_seen", {}).get(a.site, {})
        day1 = f'{first.get("reuse_pct", s["reuse_pct"])}%'
        cls = "ok" if a.site in approved else ("ready" if not a.open_flags else "todo")
        site_rows.append(f"""
      <tr>
        <th scope="row"><span class="code">{_e(a.site)}</span> {_e(site_name(a))}</th>
        <td class="barcell"><div class="bar">{segs}</div></td>
        <td class="num day1">{day1}</td>
        <td class="num">{s['reuse_pct']}%</td>
        <td><span class="pill {cls}">{_e(state)}</span></td>
      </tr>""")

    # matrix
    head = "".join(f'<th scope="col"><span class="code">{_e(a.site)}</span></th>' for a in sites)
    body = []
    for f in S.CANONICAL_FIELDS:
        cells = []
        for a in sites:
            p = a.provenance()[f["name"]]
            col = a.mapping.get(f["name"], {}).get("column", "")
            t = a.transforms.get(f["name"], {})
            note = ""
            if t.get("divide_by"):
                note = f"÷ {t['divide_by']}"
            elif t.get("overrides"):
                note = ", ".join(f"{k}={v}" for k, v in t["overrides"].items())
            elif t.get("op") == "parse_date":
                note = t["format"].replace("%", "")
            cells.append(f'<td class="cell p-{p}" title="{PROV_LABEL[p]}"><span class="src">{_e(col) or "—"}</span>'
                         + (f'<span class="note">{_e(note)}</span>' if note else "") + "</td>")
        body.append(f'<tr><th scope="row"><span class="fname">{_e(f["name"])}</span>'
                    f'<span class="fdesc">{_e(f["desc"])}</span></th>{"".join(cells)}</tr>')
    extra = []
    for a in sites:
        for col in a.unmapped:
            tgt = a.custom_fields.get(col)
            fl = next((x for x in a.flags if x.column == col and x.kind == "custom"), None)
            state = "carried as " + tgt if tgt else ("dropped" if fl and fl.decision else "needs a decision")
            extra.append(f'<li><span class="code">{_e(a.site)}</span> <code>{_e(col)}</code> {_e(state)}</li>')

    # decisions: every signed-off record (even if the library has since absorbed the flag), then open flags
    dec = []
    for code, recs in engine.ws.state["decisions"].items():
        for fid, d in recs.items():
            val = d.get("value")
            vtxt = ""
            if isinstance(val, dict):
                vtxt = ", ".join(f"{k} → {v}" for k, v in val.items())
            elif isinstance(val, list):
                vtxt = "except " + ", ".join(val)
            dec.append(f"""
      <article class="flag done">
        <header><span class="code">{_e(code)}</span><h3>{_e(d.get('title', fid))}</h3><span class="kind">{_e(d.get('kind', ''))}</span></header>
        <p class="evidence">{_e(d.get('evidence', ''))}</p>
        <p class="decided"><b>{_e(d.get('chosen', d['option']))}</b>{f' <span class="muted">({_e(vtxt)})</span>' if vtxt else ''}<span class="who">Approved by {_e(d['approved_by'])}</span></p>
      </article>""")
    for a in sites:
        for fl in a.open_flags:
            sug = fl.options.get(fl.suggested) if fl.suggested else None
            hint = f"Suggested: {_e(sug)}" if sug else "No suggestion: the data cannot tell."
            dec.insert(0, f"""
      <article class="flag open">
        <header><span class="code">{_e(a.site)}</span><h3>{_e(fl.title)}</h3><span class="kind">{_e(fl.kind)}</span></header>
        <p class="evidence">{_e(fl.evidence)}</p>
        <p class="decided pending">Waiting for a person. {hint}</p>
      </article>""")

    learned = []
    for h in engine.ws.state["history"]:
        if "learned" in h:
            L = h["learned"]
            bits = []
            if L["aliases"]:
                bits.append(f"{len(L['aliases'])} column names")
            if L["status_codes"]:
                bits.append(f"{len(L['status_codes'])} status codes")
            shared = ", ".join(bits) or "nothing new to share"
            k = len(L["kept_site_specific"])
            local = f"; kept {k} rule{'s' if k != 1 else ''} site-only" if k else ""
            learned.append(f'<li><span class="code">{_e(h["site"])}</span> approved by {_e(h["approved_by"])}: '
                           f'library learned {_e(shared)}{_e(local)}.</li>')

    return TEMPLATE.format(
        generated=datetime.now().strftime("%b %d, %Y %H:%M"),
        n_sites=len(sites), reuse_pct=round(100 * reused / total_fields) if total_fields else 0,
        open_flags=open_flags, resolved=resolved, lib_size=len(lib["aliases"]) + len(lib["status_vocab"]),
        site_rows="".join(site_rows), head=head, body="".join(body),
        extra="".join(extra) or "<li>None</li>", decisions="".join(dec) or "<p>No decisions yet.</p>",
        learned="".join(learned) or "<li>No sites approved yet. Approving a site teaches the library its patterns.</li>",
    )


TEMPLATE = """<meta charset="utf-8">
<title>Baseline Drift Report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;800&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
/* Layout: a mapping ledger. Site rail, then the field x site matrix, then the decision log. */
:root {{
  --bg: #f5f7f8; --panel: #ffffff; --fg: #15232b; --muted: #5b6c75; --line: #d6dfe3;
  --accent: #0b7a75; --accent-soft: #cfe9e6;
  --p-library: #0b7a75; --p-library-bg: #dcefec;
  --p-inferred: #3f6c9c; --p-inferred-bg: #e1eaf4;
  --p-human: #a35f00; --p-human-bg: #f7ead2;
  --p-open: #b3261e; --p-open-bg: #f8dedb;
  --p-missing: #7d8a90; --p-missing-bg: #eceff1;
  --display: "Archivo", "Helvetica Neue", Arial, sans-serif;
  --body: "Source Serif 4", Georgia, serif;
  --mono: "JetBrains Mono", ui-monospace, Menlo, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #0f171b; --panel: #152127; --fg: #e3ecef; --muted: #93a6ae; --line: #26363d;
  --accent: #4fc7bf; --accent-soft: #173c3a;
  --p-library: #4fc7bf; --p-library-bg: #143633;
  --p-inferred: #8cb4e0; --p-inferred-bg: #1a2a3b;
  --p-human: #f0b65a; --p-human-bg: #3a2b12;
  --p-open: #ff8a80; --p-open-bg: #3d1a18;
  --p-missing: #8a979d; --p-missing-bg: #1d272c; color-scheme: dark; }} }}
:root[data-theme="dark"] {{
  --bg: #0f171b; --panel: #152127; --fg: #e3ecef; --muted: #93a6ae; --line: #26363d;
  --accent: #4fc7bf; --accent-soft: #173c3a;
  --p-library: #4fc7bf; --p-library-bg: #143633;
  --p-inferred: #8cb4e0; --p-inferred-bg: #1a2a3b;
  --p-human: #f0b65a; --p-human-bg: #3a2b12;
  --p-open: #ff8a80; --p-open-bg: #3d1a18;
  --p-missing: #8a979d; --p-missing-bg: #1d272c; color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{ background: var(--bg); color: var(--fg); font: 16px/1.55 var(--body); margin: 0; }}
.wrap {{ max-width: 1180px; margin: 0 auto; padding-inline: 20px; padding-block: 32px 64px; display: grid; gap: 40px; }}
h1, h2, h3 {{ font-family: var(--display); text-wrap: balance; margin: 0; }}
h1 {{ font-size: clamp(1.9rem, 4vw, 2.8rem); font-weight: 800; letter-spacing: -0.02em; line-height: 1.05; }}
h2 {{ font-size: 1.25rem; font-weight: 800; }}
.eyebrow {{ font: 600 0.72rem/1 var(--mono); letter-spacing: 0.12em; text-transform: uppercase; color: var(--accent); }}
.lede {{ max-width: 64ch; color: var(--muted); margin: 0; }}
.top {{ display: grid; gap: 14px; }}
.stats {{ display: flex; flex-wrap: wrap; gap: 28px; font-family: var(--mono); font-variant-numeric: tabular-nums; }}
.stats div {{ display: grid; gap: 2px; }}
.stats b {{ font: 800 1.9rem/1 var(--display); color: var(--fg); }}
.stats span {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }}
.section {{ display: grid; gap: 14px; min-width: 0; }}
.section > p {{ margin: 0; color: var(--muted); max-width: 70ch; }}
.scroll {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 6px; background: var(--panel); }}
table {{ border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }}
th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: middle; }}
tr:last-child th, tr:last-child td {{ border-bottom: 0; }}
.code {{ font: 600 0.72rem/1 var(--mono); background: var(--fg); color: var(--bg); padding: 3px 6px; border-radius: 3px; letter-spacing: 0.04em; }}
.sites th {{ font: 600 0.95rem var(--display); white-space: nowrap; }}
.barcell {{ width: 55%; }}
.bar {{ display: flex; height: 12px; border-radius: 2px; overflow: hidden; background: var(--line); gap: 2px; }}
.seg.p-library {{ background: var(--p-library); }} .seg.p-inferred {{ background: var(--p-inferred); }}
.seg.p-human {{ background: var(--p-human); }} .seg.p-open {{ background: var(--p-open); }}
.seg.p-missing {{ background: var(--p-missing); }}
.num {{ font: 600 0.95rem var(--mono); text-align: right; }}
.day1 {{ color: var(--muted); font-weight: 400; }}
.sites thead th {{ font: 600 0.7rem var(--mono); text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }}
.pill {{ font: 600 0.72rem var(--mono); padding: 4px 8px; border-radius: 999px; white-space: nowrap; }}
.pill.ok {{ background: var(--p-library-bg); color: var(--p-library); }}
.pill.ready {{ background: var(--p-inferred-bg); color: var(--p-inferred); }}
.pill.todo {{ background: var(--p-open-bg); color: var(--p-open); }}
.legend {{ display: flex; flex-wrap: wrap; gap: 14px; font: 0.8rem var(--mono); color: var(--muted); padding: 0; margin: 0; list-style: none; }}
.legend li {{ display: flex; align-items: center; gap: 6px; }}
.legend i {{ width: 12px; height: 12px; border-radius: 2px; display: inline-block; }}
.matrix {{ min-width: 860px; }}
.matrix thead th {{ font-weight: 400; text-align: center; }}
.matrix tbody th {{ width: 220px; }}
.fname {{ display: block; font: 600 0.85rem var(--mono); }}
.fdesc {{ display: block; font-size: 0.78rem; color: var(--muted); line-height: 1.3; }}
.cell {{ font: 0.78rem/1.25 var(--mono); text-align: center; border-left: 1px solid var(--line); }}
.cell .src {{ display: block; word-break: break-word; }}
.cell .note {{ display: block; font-size: 0.7rem; opacity: 0.85; margin-top: 2px; }}
.cell.p-library {{ background: var(--p-library-bg); color: var(--p-library); }}
.cell.p-inferred {{ background: var(--p-inferred-bg); color: var(--p-inferred); }}
.cell.p-human {{ background: var(--p-human-bg); color: var(--p-human); font-weight: 600; }}
.cell.p-open {{ background: var(--p-open-bg); color: var(--p-open); font-weight: 600; }}
.cell.p-missing {{ background: var(--p-missing-bg); color: var(--p-missing); }}
.extra {{ margin: 0; padding-left: 0; list-style: none; display: grid; gap: 6px; font-size: 0.92rem; }}
.extra code {{ font: 0.85rem var(--mono); }}
.flags {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }}
.flag {{ background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 16px; display: grid; gap: 10px; align-content: start; min-width: 0; }}
.flag.open {{ border-color: var(--p-open); }}
.flag header {{ display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }}
.flag h3 {{ font-size: 1rem; font-weight: 600; flex: 1; min-width: 12ch; }}
.kind {{ font: 0.7rem var(--mono); color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }}
.evidence {{ margin: 0; font-size: 0.88rem; color: var(--muted); overflow-wrap: anywhere; }}
.decided {{ margin: 0; font-size: 0.9rem; display: grid; gap: 4px; }}
.decided.pending {{ color: var(--p-open); }}
.who {{ font: 0.75rem var(--mono); color: var(--p-human); }}
.muted {{ color: var(--muted); }}
.learned {{ margin: 0; padding-left: 0; list-style: none; display: grid; gap: 8px; }}
footer {{ font: 0.78rem var(--mono); color: var(--muted); border-top: 1px solid var(--line); padding-top: 16px; }}
@media (max-width: 640px) {{ .barcell {{ width: auto; min-width: 90px; }} }}
</style>
<div class="wrap">
  <section class="top">
    <span class="eyebrow">Baseline · ERP to CRM order sync</span>
    <h1>One ERP. {n_sites} setups. One order schema.</h1>
    <p class="lede">Every site runs the same SAP, and every export looks different. Plain rules map each site to the CRM order and flag what only a person should decide. Each approved site teaches the library, so the next one starts further ahead.</p>
    <div class="stats">
      <div><b>{reuse_pct}%</b><span>field mappings reused</span></div>
      <div><b>{resolved}</b><span>decisions signed off</span></div>
      <div><b>{open_flags}</b><span>still need a person</span></div>
      <div><b>{lib_size}</b><span>patterns in library</span></div>
    </div>
  </section>

  <section class="section">
    <h2>Sites</h2>
    <ul class="legend">
      <li><i style="background:var(--p-library)"></i>Reused from library</li>
      <li><i style="background:var(--p-inferred)"></i>Matched on data</li>
      <li><i style="background:var(--p-human)"></i>Human decision</li>
      <li><i style="background:var(--p-open)"></i>Needs a decision</li>
      <li><i style="background:var(--p-missing)"></i>Missing</li>
    </ul>
    <div class="scroll"><table class="sites"><thead><tr><th scope="col">Site</th><th scope="col">Where each field comes from now</th><th scope="col" class="num">Day one</th><th scope="col" class="num">Now</th><th scope="col">State</th></tr></thead><tbody>{site_rows}</tbody></table></div>
  </section>

  <section class="section">
    <h2>Where each field comes from</h2>
    <p>Rows are the CRM order fields. Each cell shows the source column at that site, plus any site-only rule.</p>
    <div class="scroll"><table class="matrix"><thead><tr><th scope="col">CRM field</th>{head}</tr></thead><tbody>{body}</tbody></table></div>
    <h2>Custom columns</h2>
    <ul class="extra">{extra}</ul>
  </section>

  <section class="section">
    <h2>Decision log</h2>
    <p>Open questions first. Only options the engine offered can be chosen, and every decision carries a name.</p>
    <div class="flags">{decisions}</div>
  </section>

  <section class="section">
    <h2>What the library learned</h2>
    <ul class="learned">{learned}</ul>
  </section>

  <footer>Generated {generated}. Synthetic data for a fictional pharma company. FX rates are planning assumptions, used only for cross-site price checks.</footer>
</div>
"""
