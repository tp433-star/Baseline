import pytest

from baseline import report
from baseline.engine import Engine, Workspace, detect_number_format, parse_number

ME = "Tester"
LXG_STATUS = {"OPEN": "OPEN", "SHPD": "IN_PROGRESS", "INV": "COMPLETE", "CANC": "CANCELLED"}


@pytest.fixture
def e(tmp_path):
    return Engine(Workspace(tmp_path))


def flags(e, site):
    return {f.id: f for f in e.analyze(site).flags}


def test_number_formats():
    assert detect_number_format(["1,23,456.00", "999.10"]) == "grouped"
    assert parse_number("1,23,456.50", "grouped") == 123456.5
    assert detect_number_format(["1.234,50", "12,00"]) == "decimal_comma"
    assert parse_number("1.234,50", "decimal_comma") == 1234.5


def test_clean_sites_fully_reused(e):
    for site in ("MUM", "AHM"):
        s = e.analyze(site).summary()
        assert s["reuse_pct"] == 100 and s["flags_open"] == 0


def test_grangemouth_tax_and_custom_fields(e):
    f = flags(e, "GRG")
    assert f["GRG.price.netwr"].suggested_value == pytest.approx(1.2, abs=0.03)
    assert f["GRG.custom.zzgxprel"].suggested == "carry"      # quality field
    assert f["GRG.custom.zzlegacykunnr"].suggested == "drop"


def test_custom_fields_never_auto_mapped(e):
    a = e.analyze("GRG")
    assert all(not m["column"].startswith("ZZ") for m in a.mapping.values())


def test_lexington_box_size_detected(e):
    assert flags(e, "LXG")["LXG.uom.box"].suggested_value == 12


def test_aurora_date_is_ambiguous_and_has_no_guess(e):
    f = flags(e, "AUR")["AUR.date.erdat"]
    assert set(f.options) == {"%d/%m/%Y", "%m/%d/%Y"} and f.suggested is None


def test_open_flag_blocks_its_field(e):
    res = e.apply(e.analyze("GRG"), limit=3)
    assert res["blocked_fields"] == ["net_amount"]
    assert all(r["net_amount"] is None for r in res["records"])
    assert not res["ready"]


def test_only_offered_options_are_accepted(e):
    with pytest.raises(ValueError):
        e.resolve("GRG", "GRG.price.netwr", "multiply_by_two", ME)
    with pytest.raises(ValueError):
        e.resolve("GRG", "GRG.price.netwr", "net_from_gross", ME, 3.0)
    with pytest.raises(ValueError):
        e.resolve("GRG", "GRG.price.netwr", "net_from_gross", "  ")
    with pytest.raises(ValueError):
        e.resolve("LXG", "LXG.status.status", "map", ME, {"OPEN": "SHIPPED"})


def test_cannot_approve_with_open_flags(e):
    with pytest.raises(ValueError):
        e.approve("GRG", ME)


def test_tax_decision_converts_to_net(e):
    e.resolve("GRG", "GRG.price.netwr", "net_from_gross", ME)
    raw = e.analyze("GRG").columns["NETWR"].clean[0]
    rec = e.apply(e.analyze("GRG"), limit=1)["records"][0]
    assert rec["net_amount"] == pytest.approx(float(raw) / 1.2, abs=0.02)


def _approve_lxg(e):
    e.resolve("LXG", "LXG.match.columns", "confirm_all", ME)
    e.resolve("LXG", "LXG.status.status", "map", ME, LXG_STATUS)
    e.resolve("LXG", "LXG.uom.box", "set_factor", ME)
    return e.approve("LXG", ME)


def test_library_learns_from_approval(e):
    before = e.analyze("TUR").summary()
    out = _approve_lxg(e)
    after = e.analyze("TUR").summary()
    assert before["reuse_pct"] == 50 and before["flags_open"] == 3
    assert after["reuse_pct"] == 100 and after["flags_open"] == 1
    assert "Bill-To Party -> customer_id" in out["learned"]["aliases"]


def test_site_rules_do_not_leak(e):
    _approve_lxg(e)
    # BOX = 12 is Lexington's; Turbhe still converts BOX at the library default of 10
    rec = next(r for r in e.apply(e.analyze("TUR"))["records"] if r["unit"] == "BOX")
    assert rec["quantity_each"] == rec["quantity"] * 10
    lx = next(r for r in e.apply(e.analyze("LXG"))["records"] if r["unit"] == "BOX")
    assert lx["quantity_each"] == lx["quantity"] * 12


def test_numeric_status_codes_stay_site_specific(e):
    e.resolve("AUR", "AUR.date.erdat", "%d/%m/%Y", ME)
    e.resolve("AUR", "AUR.status.gbstk", "map", ME,
              {"10": "OPEN", "20": "IN_PROGRESS", "30": "COMPLETE", "90": "CANCELLED"})
    out = e.approve("AUR", ME)
    assert out["learned"]["status_codes"] == []
    assert "10" not in e.ws.library["status_vocab"]
    assert e.apply(e.analyze("AUR"))["ready"]


def test_confirm_except_drops_column(e):
    e.resolve("LXG", "LXG.match.columns", "confirm_except", ME, ["Lot"])
    assert "batch" not in e.analyze("LXG").mapping


def test_add_site_and_export(e, tmp_path):
    src = tmp_path / "new.csv"
    src.write_text("VBELN,KUNNR,NAME1,ERDAT,MATNR,KWMENG,VRKME,NETWR,WAERK,WERKS,GBSTK,CHARG\n"
                   "4500000001,0000100231,Apollo Distributors,20260801,000000000000001001,10,EA,3486.00,INR,IN09,A,B2608111\n"
                   "4500000002,0000100231,Apollo Distributors,20260802,000000000000001002,20,EA,4565.00,INR,IN09,C,B2608112\n")
    code = e.ws.add_site("PUN", str(src), "pune")
    assert e.analyze(code).summary()["reuse_pct"] == 100
    e.approve(code, ME)
    out = report.export(e, tmp_path / "out")
    assert "crm_orders_PUN.csv" in out["crm_csvs"]
    assert (tmp_path / "out" / "baseline_report.html").read_text().count("<table") >= 2
