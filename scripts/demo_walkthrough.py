"""Run the demo story without Claude, in a throwaway workspace, and export the report.

The same calls Claude makes through MCP, in order. Useful as a Loom fallback and as a smoke test.
Usage: python scripts/demo_walkthrough.py [output_dir]
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from baseline import report  # noqa: E402
from baseline.engine import Engine, Workspace  # noqa: E402


def show(e, site):
    a = e.analyze(site)
    s = a.summary()
    print(f"  {site}: {s['reuse_pct']}% reused, {s['flags_open']} open -> {[f.id for f in a.open_flags]}")


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo_export")
    e = Engine(Workspace(tempfile.mkdtemp(prefix="baseline_")))
    me = "Tanisha John"

    print("Day one, before any approvals:")
    for s in e.sites():
        show(e, s)

    print("\nGrangemouth: prices 20% high, two custom Z-fields")
    e.resolve("GRG", "GRG.price.netwr", "net_from_gross", me)
    e.resolve("GRG", "GRG.custom.zzgxprel", "carry", me)
    e.resolve("GRG", "GRG.custom.zzlegacykunnr", "drop", me)
    print("  learned:", e.approve("GRG", me)["learned"])

    print("\nLexington: a different report layout, its own status codes, BOX = 12")
    e.resolve("LXG", "LXG.match.columns", "confirm_all", me)
    e.resolve("LXG", "LXG.status.status", "map", me,
              {"OPEN": "OPEN", "SHPD": "IN_PROGRESS", "INV": "COMPLETE", "CANC": "CANCELLED"})
    e.resolve("LXG", "LXG.uom.box", "set_factor", me)
    print("  learned:", e.approve("LXG", me)["learned"])

    print("\nMumbai and Ahmedabad: nothing to decide")
    e.approve("MUM", me)
    e.approve("AHM", me)

    print("\nTurbhe (new acquisition, Lexington's report template) after the library learned:")
    show(e, "TUR")
    print("\nAurora left open on purpose: the data cannot tell DD/MM from MM/DD.")
    show(e, "AUR")

    res = report.export(e, out)
    print(f"\nExported to {res['folder']}: {res['report']}, {res['spec']}, {', '.join(res['crm_csvs'])}")


if __name__ == "__main__":
    main()
