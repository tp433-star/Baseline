# Baseline

**One ERP. Every site set up differently. One order schema.**

Baseline is an MCP server you use from Claude Desktop. It takes sales-order exports from several
sites running the same SAP, maps each one to a single CRM order, and flags only the things a person
has to decide. Every approved site teaches the library, so the next site needs fewer decisions.

Built as a product idea for [Refold AI](https://www.refold.ai/). Refold's founders put the problem
plainly: two companies on the same ERP still configure it differently, so every integration gets
built from scratch. I lived that across 16 pharma sites on SAP S/4HANA. Baseline is a small version
of the fix: make the second integration cheaper than the first.

## What it does

| Step | Who does it |
|---|---|
| Profile every column (types, formats, cardinality, padding) | Plain code |
| Match columns to the 12 CRM order fields, by known name or by what the data looks like | Plain code |
| Pick transforms: date formats, number formats (incl. `1,23,456.00`), leading zeros, units to EACH | Plain code |
| Cross-site checks: same products priced 20% higher? A BOX that holds 12 here, not 10? | Plain code |
| Explain each flag in plain English and recommend an option | Claude |
| Approve the decision | A named person |
| Learn the site's column names and descriptive status codes for next time | Plain code |

Guardrails:
- Claude can only choose options the engine offered (`resolve_flag` rejects anything else).
- Every decision needs `approved_by`. A site can't be approved with open flags.
- Open flags block only their own field in the preview; everything else still syncs.
- Custom `ZZ*` fields are never guessed at. A person decides to carry or drop them.
- Site-only rules (tax, box size, short codes like `10` or `C`) never leak into the shared library.
- When the data can't settle something (all dates fall on day 1 to 12), there's no suggestion. It asks.

## The demo data

Six synthetic sites of a fictional pharma company. Nothing is real customer data.

| Site | What's different | Day one |
|---|---|---|
| MUM Mumbai | Raw SAP technical names | 100% reused |
| AHM Ahmedabad | SAP English labels, `DD.MM.YYYY`, Indian digit grouping | 100% |
| GRG Grangemouth | Amounts include 20% VAT, two custom Z-fields | 92%, 3 flags |
| LXG Lexington | Different report layout, own status codes, BOX = 12 | 0%, 3 flags |
| AUR Aurora | Migrated: zeros stripped, numeric statuses, ambiguous dates | 83%, 2 flags |
| TUR Turbhe | New acquisition on Lexington's template | 50% → **100% after Lexington is approved** |

## Set up in Claude Desktop (about 5 minutes)

Needs Python 3.10+.

```bash
cd baseline
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m pytest -q          # 15 tests
```

Open Claude Desktop → Settings → Developer → Edit Config, and add (use your real path):

```json
{
  "mcpServers": {
    "baseline": {
      "command": "/Users/YOU/baseline/.venv/bin/python",
      "args": ["-m", "baseline.server"]
    }
  }
}
```

Restart Claude Desktop. Decisions and the learned library live in `~/.baseline/state.json`
(change with the `BASELINE_HOME` env var).

## Try it

> Onboard Grangemouth for the order sync.

> Approve your suggestions for Lexington. I'm Tanisha John.

> Now check Turbhe again. What changed?

> Export the report.

There's also an `onboard_site` prompt in the attachments menu, and `reset_demo` to start over.

No Claude Desktop handy? `python scripts/demo_walkthrough.py out/` runs the same story with plain
calls and writes the report.

## Tools

| Tool | What it does |
|---|---|
| `list_sites` | Reuse %, open flags, approval state for every site |
| `analyze_site` | Field mapping, provenance, transforms and flags with evidence |
| `compare_sites` | Field × site matrix |
| `resolve_flag` | Record one approved decision |
| `approve_site` | Lock a site and teach the library |
| `preview_sync` | First CRM records, with blocked fields |
| `add_site` | Bring your own CSV export |
| `export_report` | HTML drift report, mapping spec JSON, CRM-ready CSVs |
| `reset_demo` | Start over |

## Assumptions to argue with

All in `baseline/schema.py`:
- Inferred matches need a score of 0.80 and a 0.15 lead over the runner-up, or a person confirms them.
- Prices more than 8% off the other sites get flagged. FX rates are planning numbers, used only for this check.
- A BOX is 10 EACH unless a site's prices say otherwise.
- Numeric or 1–2 character status codes stay site-specific; descriptive codes (`SHPD`, `Rejected`) are shared.

## What I'd build next as the PM

1. **Write-back to the CRM** with a dry-run diff, so approval is the last step before a live sync.
2. **Drift alerts:** rerun weekly; if a site's export changes shape, reopen only the affected fields.
3. **Library confidence:** track how often a learned pattern needed a correction and retire weak ones.
4. **Measure it:** decisions per new site and days from first export to first sync, by customer.
