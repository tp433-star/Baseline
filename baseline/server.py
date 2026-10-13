"""Baseline MCP server. Claude explains and proposes; plain code decides what is allowed; a person approves.

Run with:  python -m baseline.server   (stdio, for Claude Desktop)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import report
from . import schema as S
from .engine import Engine, Workspace

INSTRUCTIONS = """Baseline maps sales-order exports from many sites of the same ERP into one CRM order schema.
The engine is deterministic: it profiles columns, matches them, picks transforms, runs cross-site
checks and raises flags. Your job is to explain flags in plain English, recommend an option with
reasons drawn from the evidence, and record decisions only after the user approves them.

Rules:
- Never call resolve_flag or approve_site unless the user has approved that specific decision in
  this conversation. approved_by must be the user's own name.
- Only offer options the flag lists. Do not invent transforms.
- When the data cannot settle a question (for example an ambiguous date order), say so and ask.
- Lead with the numbers: reuse %, open flags, what changed after approval."""

mcp = FastMCP("baseline", instructions=INSTRUCTIONS)
_ws = Workspace()
_engine = Engine(_ws)


def _site_view(a):
    return {
        "summary": a.summary(),
        "mapping": [
            {"field": f, "column": a.mapping[f]["column"] if f in a.mapping else None,
             "provenance": p, "transform": a.transforms.get(f, {"op": "copy"}) if f in a.mapping else None}
            for f, p in a.provenance().items()
        ],
        "custom_columns": {c: a.custom_fields.get(c, "undecided or dropped") for c in a.unmapped},
        "flags": [f.to_dict() for f in a.flags],
    }


@mcp.tool()
def list_sites() -> dict:
    """List every site export with its reuse %, open flags and approval state."""
    res = _engine.analyze_all()
    lib = _ws.library
    return {
        "sites": [{**a.summary(), "approved": code in lib["approved_sites"]} for code, a in res.items()],
        "library": {"column_names": len(lib["aliases"]), "status_codes": len(lib["status_vocab"]),
                    "approved_sites": lib["approved_sites"]},
        "target_fields": [f["name"] for f in S.CANONICAL_FIELDS],
    }


@mcp.tool()
def analyze_site(site: str) -> dict:
    """Map one site's export to the CRM order. Returns the field mapping, how each field was
    matched (library, inferred, human, open, missing), transforms, and every flag with evidence."""
    return _site_view(_engine.analyze(site))


@mcp.tool()
def compare_sites() -> dict:
    """Field-by-site matrix: which source column feeds each CRM field at each site, and how it got there."""
    res = _engine.analyze_all()
    return {
        "matrix": {f: {code: {"column": a.mapping.get(f, {}).get("column"), "provenance": a.provenance()[f]}
                       for code, a in res.items()} for f in S.FIELD_NAMES},
        "summaries": [a.summary() for a in res.values()],
    }


@mcp.tool()
def resolve_flag(site: str, flag_id: str, option: str, approved_by: str, value: Any = None) -> dict:
    """Record a person's decision on one flag. Only call after the user approves it.

    value depends on the flag kind:
      status -> {"CODE": "OPEN" | "IN_PROGRESS" | "COMPLETE" | "CANCELLED", ...} covering every listed code
      price (net_from_gross) -> tax factor such as 1.2 (defaults to the suggested value)
      uom (set_factor) -> whole number of EACH per BOX (defaults to the suggested value)
      uomcode -> {"UNIT": each_per_unit}
      match (confirm_except) -> list of column names to leave out
    """
    return _engine.resolve(site, flag_id, option, approved_by, value)


@mcp.tool()
def approve_site(site: str, approved_by: str) -> dict:
    """Lock a site's mapping once every flag is resolved. The library learns its column names and
    descriptive status codes; site-only rules (tax, box size, short codes) stay with that site."""
    return _engine.approve(site, approved_by)


@mcp.tool()
def preview_sync(site: str, limit: int = 5) -> dict:
    """Show the first CRM order records this site would produce, with any fields still blocked by open flags."""
    a = _engine.analyze(site)
    res = _engine.apply(a, limit=limit)
    full = _engine.apply(a)
    res["rows_total"] = len(full["records"])
    res["rows_with_errors"] = len(full["row_errors"])
    return res


@mcp.tool()
def add_site(code: str, csv_path: str, name: str = "") -> dict:
    """Add a new site export (CSV with a header row) and analyze it against the current library."""
    code = _ws.add_site(code, csv_path, name)
    return {"added": code, **_site_view(_engine.analyze(code))}


@mcp.tool()
def export_report(output_dir: str = "") -> dict:
    """Write the HTML drift report, the mapping spec (JSON) and CRM-ready CSVs for approved sites."""
    out = Path(output_dir).expanduser() if output_dir else _ws.home / "export"
    return report.export(_engine, out)


@mcp.tool()
def reset_demo(confirm: bool = False) -> dict:
    """Forget every decision and approval and restore the starting library. Needs confirm=true."""
    if not confirm:
        return {"reset": False, "note": "Pass confirm=true to wipe decisions and the learned library."}
    _ws.reset()
    return {"reset": True}


@mcp.prompt()
def onboard_site(site: str) -> str:
    """Walk a site through mapping, decisions and approval."""
    return (f"Onboard site {site} for the ERP to CRM order sync. Call analyze_site first. "
            "Give me the reuse % and a one-line read of each open flag. For each flag, recommend an option "
            "and say what evidence supports it, then wait for my approval before resolving anything. "
            "When nothing is open, preview a few records, then ask before approving the site.")


def main():
    mcp.run()


if __name__ == "__main__":
    main()
