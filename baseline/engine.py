"""Baseline engine. Plain, deterministic code: no model calls in here.

Pipeline for each site export:
  profile columns -> match columns to the canonical order -> pick transforms
  -> cross-site sanity checks -> flags a human must decide -> apply decisions.

The library of known patterns grows each time a site is approved, so the next
site needs fewer decisions. That is the whole idea: integrations should get
cheaper with every customer, not cost the same every time.
"""
from __future__ import annotations

import copy
import csv
import json
import os
import re
import shutil
import statistics
from dataclasses import dataclass, field as dc_field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from . import schema as S

BUNDLED_SITES = Path(__file__).parent / "data" / "sites"
NUM_PLAIN = re.compile(r"^-?\d+(\.\d+)?$")
NUM_GROUPED = re.compile(r"^-?\d{1,3}(,\d{2,3})+(\.\d+)?$")      # 1,234.50 and 1,23,456.00
NUM_DEC_COMMA = re.compile(r"^-?\d{1,3}(\.\d{3})+,\d+$|^-?\d+,\d+$")  # 1.234,50


def norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", h.lower())


# ----------------------------------------------------------------------------- workspace

class Workspace:
    """Where sites, decisions and the learned library live. One JSON file, easy to read."""

    def __init__(self, home: str | os.PathLike | None = None):
        self.home = Path(home or os.environ.get("BASELINE_HOME") or Path.home() / ".baseline")
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "sites").mkdir(exist_ok=True)
        self.state_path = self.home / "state.json"
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
        else:
            self.reset()

    def reset(self):
        self.state = {"library": copy.deepcopy(S.SEED_LIBRARY), "decisions": {}, "history": []}
        self.save()

    def save(self):
        self.state_path.write_text(json.dumps(self.state, indent=2, sort_keys=True))

    @property
    def library(self):
        return self.state["library"]

    def site_files(self) -> dict[str, Path]:
        files = {}
        for folder in (BUNDLED_SITES, self.home / "sites"):
            for p in sorted(folder.glob("*.csv")):
                files[p.stem.split("_")[0].upper()] = p
        return files

    def add_site(self, code: str, csv_path: str, name: str = "") -> str:
        code = norm(code).upper()[:6]
        if not code:
            raise ValueError("site code must contain letters or digits")
        src = Path(csv_path).expanduser()
        if not src.exists():
            raise FileNotFoundError(f"no file at {src}")
        slug = norm(name) or "site"
        dest = self.home / "sites" / f"{code}_{slug}.csv"
        shutil.copyfile(src, dest)
        return code


# ----------------------------------------------------------------------------- profiling

@dataclass
class Column:
    header: str
    values: list[str]

    def __post_init__(self):
        v = [x.strip() for x in self.values if x is not None and x.strip() != ""]
        self.clean = v
        self.n = len(v)
        self.distinct = len(set(v))
        self.unique_ratio = self.distinct / self.n if self.n else 0
        self.all_digits = self.n > 0 and all(x.isdigit() for x in v)
        self.lengths = {len(x) for x in v}
        self.padded = self.all_digits and any(x.startswith("0") and len(x) > 1 for x in v)
        self.number_format = detect_number_format(v)
        self.has_decimals = any("." in x or "," in x for x in v) and self.number_format is not None
        self.date_formats = [f for f in S.DATE_FORMATS if v and all(_parses(x, f) for x in v)]
        self.avg_len = sum(len(x) for x in v) / self.n if self.n else 0

    def sample(self, k=3):
        seen = []
        for x in self.clean:
            if x not in seen:
                seen.append(x)
            if len(seen) == k:
                break
        return seen


def _parses(x, fmt):
    try:
        datetime.strptime(x, fmt)
        return True
    except ValueError:
        return False


def detect_number_format(values):
    if not values:
        return None
    if all(NUM_PLAIN.match(x) for x in values):
        return "plain"
    if all(NUM_PLAIN.match(x) or NUM_GROUPED.match(x) for x in values):
        return "grouped"
    if all(NUM_PLAIN.match(x) or NUM_DEC_COMMA.match(x) for x in values):
        return "decimal_comma"
    return None


def parse_number(x: str, fmt: str):
    x = x.strip()
    if not x:
        return None
    if fmt == "decimal_comma":
        x = x.replace(".", "").replace(",", ".")
    else:
        x = x.replace(",", "")
    return float(x)


def value_fit(fieldname: str, c: Column, library) -> float:
    """How much does this column's data look like the canonical field? 0..1, pure rules."""
    if c.n == 0:
        return 0.0
    v = c.clean
    if fieldname == "order_id":
        if c.all_digits and c.lengths <= set(range(6, 13)):
            return 1.0 if c.unique_ratio >= 0.95 else 0.2
        return 0.0
    if fieldname == "customer_id":
        if c.all_digits and c.unique_ratio < 0.5:
            if c.lengths == {10} or (not c.padded and max(c.lengths) <= 8):
                return 0.9
            return 0.3
        return 0.0
    if fieldname == "material_id":
        if c.all_digits and c.unique_ratio < 0.5:
            if c.lengths <= {18, 40} and c.padded:
                return 1.0
            return 0.6 if max(c.lengths) <= 8 else 0.3
        return 0.0
    if fieldname == "customer_name":
        if not c.all_digits and all(re.search(r"[A-Za-z]{2}", x) and " " in x for x in v):
            return 0.9 if c.unique_ratio <= 0.5 and c.avg_len >= 6 else 0.5
        return 0.0
    if fieldname == "order_date":
        return 1.0 if c.date_formats else 0.0
    if fieldname == "quantity":
        if c.all_digits and not c.padded:
            return 0.9 if c.distinct > 6 else 0.3
        return 0.0
    if fieldname == "unit":
        known = set(library["uom_to_each"])
        if set(v) <= known:
            return 1.0
        if all(re.fullmatch(r"[A-Z]{2,3}", x) for x in v) and c.distinct <= 5:
            return 0.6
        return 0.0
    if fieldname == "net_amount":
        return 1.0 if c.number_format and c.has_decimals else 0.0
    if fieldname == "currency":
        if set(v) <= set(S.FX_TO_USD):
            return 1.0
        return 0.6 if all(re.fullmatch(r"[A-Z]{3}", x) for x in v) and c.distinct <= 2 else 0.0
    if fieldname == "plant":
        if all(re.fullmatch(r"[A-Z0-9]{4}", x) and re.search(r"\d", x) for x in v) and c.distinct <= 3:
            return 1.0
        return 0.0
    if fieldname == "status":
        if c.distinct > 8 or c.number_format == "plain" and c.has_decimals:
            return 0.0
        if set(v) & set(library["status_vocab"]):
            return 1.0
        return 0.6
    if fieldname == "batch":
        mixed = all(re.search(r"[A-Za-z]", x) and re.search(r"\d", x) for x in v)
        return 0.9 if mixed and c.unique_ratio > 0.5 else 0.0
    return 0.0


def name_fit(header: str, library) -> dict[str, float]:
    """Best fuzzy similarity of a header to any known alias, per canonical field."""
    h = norm(header)
    out = {f: 0.0 for f in S.FIELD_NAMES}
    for alias, f in library["aliases"].items():
        out[f] = max(out[f], SequenceMatcher(None, h, alias).ratio())
    return out


# ----------------------------------------------------------------------------- analysis

@dataclass
class Flag:
    id: str
    kind: str
    site: str
    column: str | None
    field: str | None
    title: str
    evidence: str
    options: dict
    suggested: str | None
    suggested_value: object = None
    severity: str = "blocker"
    decision: dict | None = None
    fields: list = dc_field(default_factory=list)

    def to_dict(self):
        return {
            "id": self.id, "kind": self.kind, "column": self.column, "field": self.field or self.fields or None,
            "title": self.title, "evidence": self.evidence, "severity": self.severity,
            "options": self.options, "suggested": self.suggested,
            "suggested_value": self.suggested_value,
            "status": "resolved" if self.decision else "open", "decision": self.decision,
        }


@dataclass
class SiteAnalysis:
    site: str
    path: Path
    columns: dict[str, Column]
    mapping: dict[str, dict] = dc_field(default_factory=dict)       # field -> {column, how, score}
    transforms: dict[str, dict] = dc_field(default_factory=dict)    # field -> transform spec
    unmapped: list[str] = dc_field(default_factory=list)
    flags: list[Flag] = dc_field(default_factory=list)
    custom_fields: dict[str, str] = dc_field(default_factory=dict)  # column -> crm field

    @property
    def open_flags(self):
        return [f for f in self.flags if not f.decision]

    def provenance(self) -> dict[str, str]:
        """For each canonical field: library (reused), inferred (auto, new), human, open, missing."""
        out = {}
        for f in S.FIELD_NAMES:
            related = [x for x in self.flags if x.field == f or f in x.fields]
            if any(not x.decision for x in related):
                out[f] = "open"
            elif related:
                out[f] = "human"
            elif f not in self.mapping:
                out[f] = "missing"
            else:
                out[f] = self.mapping[f]["how"]
        return out

    def summary(self):
        prov = self.provenance()
        counts = {k: list(prov.values()).count(k) for k in ("library", "inferred", "human", "open", "missing")}
        return {
            "site": self.site, "file": self.path.name, "rows": max((c.n for c in self.columns.values()), default=0),
            "fields_total": len(S.FIELD_NAMES), **counts,
            "reuse_pct": round(100 * counts["library"] / len(S.FIELD_NAMES)),
            "flags_open": len(self.open_flags), "flags_resolved": len(self.flags) - len(self.open_flags),
            "custom_columns": len(self.unmapped),
        }


def load_columns(path: Path) -> dict[str, Column]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        raise ValueError(f"{path.name} is empty")
    header, body = rows[0], rows[1:]
    return {h: Column(h, [r[i] if i < len(r) else "" for r in body]) for i, h in enumerate(header)}


class Engine:
    def __init__(self, ws: Workspace):
        self.ws = ws

    # ---- public
    def sites(self):
        return list(self.ws.site_files())

    def analyze_all(self) -> dict[str, SiteAnalysis]:
        files = self.ws.site_files()
        res = {code: self._analyze_local(code, p) for code, p in files.items()}
        self._cross_site_price_check(res)
        for a in res.values():
            self._attach_decisions(a)
        first = self.ws.state.setdefault("first_seen", {})
        new = {c: {"reuse_pct": a.summary()["reuse_pct"], "flags": len(a.flags)}
               for c, a in res.items() if c not in first}
        if new:
            first.update(new)
            self.ws.save()
        return res

    def analyze(self, site: str) -> SiteAnalysis:
        site = site.upper()
        all_ = self.analyze_all()
        if site not in all_:
            raise KeyError(f"unknown site {site}. Known: {', '.join(all_)}")
        return all_[site]

    # ---- matching
    def _analyze_local(self, site: str, path: Path) -> SiteAnalysis:
        lib = self.ws.library
        cols = load_columns(path)
        a = SiteAnalysis(site, path, cols)
        candidates = []  # (score, header, field, how, detail)
        for h, c in cols.items():
            if norm(h).startswith(("zz", "yy")):
                continue  # customer-specific extension fields are never guessed at
            known = lib["aliases"].get(norm(h))
            if known:
                candidates.append((1.0, h, known, "library", {"name": 1.0, "value": None}))
                continue
            names = name_fit(h, lib)
            for f in S.FIELD_NAMES:
                vf = value_fit(f, c, lib)
                if vf == 0:
                    continue
                score = round(0.35 * names[f] + 0.65 * vf, 3)
                candidates.append((score, h, f, "inferred", {"name": round(names[f], 2), "value": vf}))
        candidates.sort(key=lambda x: (-x[0], x[1], x[2]))
        used_h, used_f = set(), set()
        weak = []
        for score, h, f, how, detail in candidates:
            if h in used_h or f in used_f or score < 0.5:
                continue
            used_h.add(h)
            used_f.add(f)
            a.mapping[f] = {"column": h, "how": how, "score": score, **detail}
            if how == "inferred":
                runner = max((s2 for s2, h2, f2, *_ in candidates if h2 == h and f2 != f), default=0)
                if score < S.AUTO_MATCH_MIN or score - runner < S.AUTO_MATCH_MARGIN:
                    weak.append((h, f, detail, runner))
        if weak:
            lines = [f"'{h}' -> {f} (name {int(d['name'] * 100)}%, data {int(d['value'] * 100)}%, "
                     f"next best {r:.2f}; sample {', '.join(cols[h].sample(2))})" for h, f, d, r in weak]
            a.flags.append(Flag(
                id=f"{site}.match.columns", kind="match", site=site, column=None, field=None,
                fields=[f for _, f, _, _ in weak],
                title=f"{len(weak)} columns matched on their data, not their names",
                evidence="; ".join(lines),
                options={"confirm_all": "accept every proposed match",
                         "confirm_except": "accept all except the columns listed in value"},
                suggested="confirm_all", suggested_value={h: f for h, f, _, _ in weak}))
        a.unmapped = [h for h in cols if h not in used_h]
        for h in a.unmapped:
            quality = any(k in h.upper() for k in S.QUALITY_KEYWORDS)
            c = cols[h]
            a.flags.append(Flag(
                id=f"{site}.custom.{norm(h)}", kind="custom", site=site, column=h, field=None,
                title=f"Column '{h}' has no home in the CRM order",
                evidence=(f"{c.distinct} distinct values, sample: {', '.join(c.sample())}."
                          + (" Name suggests a quality or GxP field." if quality else "")),
                options={"carry": f"sync as custom field {norm(h)}__c", "drop": "do not sync"},
                suggested="carry" if quality else "drop", severity="review"))
        for f in S.FIELD_NAMES:
            if f not in a.mapping:
                a.flags.append(Flag(
                    id=f"{site}.missing.{f}", kind="missing", site=site, column=None, field=f,
                    title=f"No column found for {f}", evidence="Nothing in the export matched by name or data.",
                    options={"leave_blank": f"sync without {f}"}, suggested=None))
        self._transforms(a)
        return a

    # ---- transforms and in-site checks
    def _transforms(self, a: SiteAnalysis):
        lib = self.ws.library
        m, cols = a.mapping, a.columns
        if "order_date" in m:
            c = cols[m["order_date"]["column"]]
            if len(c.date_formats) == 1:
                a.transforms["order_date"] = {"op": "parse_date", "format": c.date_formats[0]}
            elif c.date_formats:
                a.flags.append(Flag(
                    id=f"{a.site}.date.{norm(c.header)}", kind="date", site=a.site, column=c.header,
                    field="order_date", title="Day and month order is ambiguous",
                    evidence=(f"Every value parses as {' and as '.join(c.date_formats)} "
                              f"(no day above 12 in {c.n} rows). Sample: {', '.join(c.sample())}"),
                    options={fmt: f"read dates as {fmt}" for fmt in c.date_formats}, suggested=None))
        for f in ("order_id", "customer_id", "material_id"):
            if f in m:
                a.transforms[f] = {"op": "strip_leading_zeros"} if cols[m[f]["column"]].padded or f == "material_id" else {"op": "copy"}
        for f in ("quantity",):
            if f in m:
                a.transforms[f] = {"op": "parse_number", "format": cols[m[f]["column"]].number_format or "plain"}
        if "net_amount" in m:
            a.transforms["net_amount"] = {"op": "parse_number", "format": cols[m["net_amount"]["column"]].number_format or "plain"}
        if "status" in m:
            c = cols[m["status"]["column"]]
            vocab = lib["status_vocab"]
            site_vocab = lib.get("site_status_vocab", {}).get(a.site, {})
            unknown = sorted({v for v in c.clean if v not in vocab and v not in site_vocab})
            a.transforms["status"] = {"op": "map_status"}
            if unknown:
                counts = {v: c.clean.count(v) for v in unknown}
                a.flags.append(Flag(
                    id=f"{a.site}.status.{norm(c.header)}", kind="status", site=a.site, column=c.header,
                    field="status", title=f"{len(unknown)} status codes nobody has mapped before",
                    evidence="Codes and row counts: " + ", ".join(f"{k} ({v})" for k, v in counts.items()),
                    options={"map": f"map each code to one of {', '.join(S.CANONICAL_STATUS)}"},
                    suggested="map", suggested_value=None))
        if "unit" in m:
            c = cols[m["unit"]["column"]]
            unknown = sorted(set(c.clean) - set(lib["uom_to_each"]))
            if unknown:
                a.flags.append(Flag(
                    id=f"{a.site}.uomcode.{norm(c.header)}", kind="uomcode", site=a.site, column=c.header,
                    field="unit", title=f"Unknown units: {', '.join(unknown)}",
                    evidence="These units have no conversion to EACH in the library.",
                    options={"set_factors": "give each unit an EACH conversion factor"}, suggested=None))
            self._uom_check(a)

    def _rows(self, a: SiteAnalysis):
        """Rows as dicts of canonical field -> raw string, for numeric checks."""
        m = a.mapping
        n = max(c.n for c in a.columns.values())
        get = lambda f, i: a.columns[m[f]["column"]].values[i].strip() if f in m and i < len(a.columns[m[f]["column"]].values) else ""
        return [{f: get(f, i) for f in m} for i in range(n)]

    def _unit_prices(self, a: SiteAnalysis, unit_filter=None):
        """material -> list of (amount / quantity) in site currency."""
        need = {"material_id", "quantity", "net_amount", "unit"}
        if not need <= set(a.mapping):
            return {}
        fmt = a.transforms["net_amount"]["format"]
        out = {}
        for r in self._rows(a):
            try:
                q = float(r["quantity"])
                amt = parse_number(r["net_amount"], fmt)
            except (ValueError, TypeError):
                continue
            if not q or amt is None or (unit_filter and r["unit"] != unit_filter):
                continue
            out.setdefault(r["material_id"].lstrip("0"), []).append(amt / q)
        return out

    def _uom_check(self, a: SiteAnalysis):
        lib = self.ws.library
        ea, box = self._unit_prices(a, "EA"), self._unit_prices(a, "BOX")
        ratios = [statistics.median(box[k]) / statistics.median(ea[k]) for k in box if k in ea]
        if len(ratios) < 3:
            return
        implied = statistics.median(ratios)
        default = lib["uom_to_each"]["BOX"]
        if abs(implied / default - 1) > S.UOM_DRIFT_TOL:
            col = a.mapping["unit"]["column"]
            a.flags.append(Flag(
                id=f"{a.site}.uom.box", kind="uom", site=a.site, column=col, field="unit",
                title=f"A BOX here looks like {round(implied)} units, not {default}",
                evidence=(f"Across {len(ratios)} products, the price of one BOX is {implied:.2f}x the "
                          f"price of one EACH. The library assumes {default}."),
                options={"set_factor": f"use BOX = {round(implied)} EACH for this site",
                         "keep_default": f"keep BOX = {default}"},
                suggested="set_factor", suggested_value=round(implied)))

    def _cross_site_price_check(self, res: dict[str, SiteAnalysis]):
        usd = {}
        for code, a in res.items():
            if "currency" not in a.mapping:
                continue
            cur = (a.columns[a.mapping["currency"]["column"]].clean or [""])[0]
            rate = S.FX_TO_USD.get(cur)
            if rate is None:
                continue
            prices = self._unit_prices(a, "EA")
            usd[code] = {k: statistics.median(v) * rate for k, v in prices.items()}
        for code, mine in usd.items():
            ratios = []
            for mat, p in mine.items():
                others = [usd[o][mat] for o in usd if o != code and mat in usd[o]]
                if len(others) >= 2:
                    ratios.append(p / statistics.median(others))
            if len(ratios) < 3:
                continue
            r = statistics.median(ratios)
            if abs(r - 1) > S.PRICE_DRIFT_TOL:
                a = res[code]
                col = a.mapping["net_amount"]["column"]
                higher = r > 1
                a.flags.append(Flag(
                    id=f"{code}.price.{norm(col)}", kind="price", site=code, column=col, field="net_amount",
                    title=f"Prices run {abs(r - 1) * 100:.0f}% {'above' if higher else 'below'} every other site",
                    evidence=(f"Same products, per-EACH price in USD, median over {len(ratios)} products: "
                              f"{r:.2f}x the other sites. Common causes: amount includes tax, "
                              f"or a different price basis. FX rates are a planning assumption."),
                    options={"net_from_gross": f"divide amounts by {r:.2f} (amount includes tax)",
                             "keep": "amounts are correct as exported"},
                    suggested="net_from_gross" if higher else None, suggested_value=round(r, 2)))

    # ---- decisions
    def _attach_decisions(self, a: SiteAnalysis):
        dec = self.ws.state["decisions"].get(a.site, {})
        for f in a.flags:
            if f.id in dec:
                f.decision = dec[f.id]
        for f in a.flags:
            d = f.decision
            if not d:
                continue
            if f.kind == "match" and d["option"] == "confirm_except":
                for col in d["value"]:
                    for fld in [k for k, v in a.mapping.items() if v["column"] == col]:
                        a.mapping.pop(fld)
            elif f.kind == "custom" and d["option"] == "carry":
                a.custom_fields[f.column] = f"{norm(f.column)}__c"
            elif f.kind == "date":
                a.transforms["order_date"] = {"op": "parse_date", "format": d["option"]}
            elif f.kind == "price" and d["option"] == "net_from_gross":
                a.transforms["net_amount"]["divide_by"] = float(d["value"])
            elif f.kind == "uom" and d["option"] == "set_factor":
                a.transforms["unit"] = {"op": "to_each", "overrides": {"BOX": int(d["value"])}}
            elif f.kind == "uomcode":
                a.transforms.setdefault("unit", {"op": "to_each", "overrides": {}})["overrides"].update(d["value"])
            elif f.kind == "status":
                a.transforms["status"]["site_vocab"] = d["value"]

    def resolve(self, site: str, flag_id: str, option: str, approved_by: str, value=None) -> dict:
        """Record a human decision. Only the options the engine offered are accepted."""
        if not approved_by or not approved_by.strip():
            raise ValueError("approved_by is required: a named person signs off every decision")
        a = self.analyze(site)
        flag = next((f for f in a.flags if f.id == flag_id), None)
        if not flag:
            raise KeyError(f"no flag {flag_id} at {site}. Open: {', '.join(f.id for f in a.open_flags) or 'none'}")
        if option not in flag.options:
            raise ValueError(f"'{option}' is not an allowed option. Allowed: {', '.join(flag.options)}")
        if flag.kind == "match" and option == "confirm_except":
            proposed = flag.suggested_value
            if not isinstance(value, list) or not set(value) <= set(proposed):
                raise ValueError(f"value must be a list of columns from {list(proposed)}")
        if flag.kind == "status":
            if not isinstance(value, dict):
                raise ValueError(f"value must map each code to one of {S.CANONICAL_STATUS}")
            c = a.columns[flag.column]
            unknown = {v for v in c.clean if v not in self.ws.library["status_vocab"]}
            missing = unknown - set(value)
            bad = {k: v for k, v in value.items() if v not in S.CANONICAL_STATUS}
            if missing or bad:
                raise ValueError(f"unmapped codes: {sorted(missing)}; invalid targets: {bad}")
        if flag.kind == "price" and option == "net_from_gross":
            value = float(value if value is not None else flag.suggested_value)
            if not 1.0 < value <= 1.5:
                raise ValueError("tax factor must be between 1.0 and 1.5")
        if flag.kind == "uom" and option == "set_factor":
            value = int(value if value is not None else flag.suggested_value)
            if not 2 <= value <= 1000:
                raise ValueError("BOX factor must be between 2 and 1000")
        if flag.kind == "uomcode":
            if not isinstance(value, dict) or not all(isinstance(v, int) and v > 0 for v in value.values()):
                raise ValueError("value must map each unit to a positive whole number of EACH")
        d = {"option": option, "value": value, "approved_by": approved_by.strip(),
             "at": datetime.now().isoformat(timespec="seconds"),
             "title": flag.title, "evidence": flag.evidence, "kind": flag.kind,
             "chosen": flag.options[option]}
        self.ws.state["decisions"].setdefault(site.upper(), {})[flag_id] = d
        self.ws.state["history"].append({"site": site.upper(), "flag": flag_id, **d})
        self.ws.save()
        return {"flag": flag_id, "recorded": d, "still_open": [f.id for f in self.analyze(site).open_flags]}

    def approve(self, site: str, approved_by: str) -> dict:
        """Lock a site's mapping and teach the library what it learned."""
        site = site.upper()
        a = self.analyze(site)
        if a.open_flags:
            raise ValueError(f"{len(a.open_flags)} open flags first: {', '.join(f.id for f in a.open_flags)}")
        if not approved_by or not approved_by.strip():
            raise ValueError("approved_by is required")
        lib = self.ws.library
        learned = {"aliases": [], "status_codes": [], "kept_site_specific": []}
        for f, mp in a.mapping.items():
            key = norm(mp["column"])
            if key not in lib["aliases"]:
                lib["aliases"][key] = f
                learned["aliases"].append(f"{mp['column']} -> {f}")
        site_vocab = a.transforms.get("status", {}).get("site_vocab") or {}
        for code, target in site_vocab.items():
            # Short or numeric codes ("10", "C") mean different things at different sites,
            # so they stay site-specific. Descriptive codes are shared.
            if code.isdigit() or len(code) <= 2:
                lib.setdefault("site_status_vocab", {}).setdefault(site, {})[code] = target
                learned["kept_site_specific"].append(f"status {code} -> {target}")
            elif code not in lib["status_vocab"]:
                lib["status_vocab"][code] = target
                learned["status_codes"].append(f"{code} -> {target}")
        for f in a.flags:
            if f.kind in ("price", "uom", "date") and f.decision and f.decision["option"] not in ("keep", "keep_default"):
                learned["kept_site_specific"].append(f"{f.title} ({f.decision['option']})")
        if site not in lib["approved_sites"]:
            lib["approved_sites"].append(site)
        self.ws.state["history"].append({"site": site, "approved_by": approved_by.strip(),
                                         "at": datetime.now().isoformat(timespec="seconds"), "learned": learned})
        self.ws.save()
        return {"site": site, "approved_by": approved_by.strip(), "learned": learned,
                "library_size": len(lib["aliases"]) + len(lib["status_vocab"])}

    # ---- apply
    def apply(self, a: SiteAnalysis, limit: int | None = None) -> dict:
        lib = self.ws.library
        blocked = {f.field for f in a.open_flags if f.field}
        out, errors = [], []
        status_vocab = {**lib["status_vocab"], **lib.get("site_status_vocab", {}).get(a.site, {}),
                        **(a.transforms.get("status", {}).get("site_vocab") or {})}
        uom = {**lib["uom_to_each"], **a.transforms.get("unit", {}).get("overrides", {})}
        rows = self._rows(a)
        for i, r in enumerate(rows[:limit] if limit else rows):
            rec, errs = {}, []
            for f in S.FIELD_NAMES:
                raw = r.get(f, "")
                if f in blocked:
                    rec[f] = None
                    continue
                if f not in a.mapping or raw == "":
                    rec[f] = None
                    continue
                t = a.transforms.get(f, {})
                try:
                    if f == "order_date":
                        rec[f] = datetime.strptime(raw, t["format"]).date().isoformat()
                    elif t.get("op") == "strip_leading_zeros":
                        rec[f] = raw.lstrip("0") or "0"
                    elif f == "quantity":
                        rec[f] = int(parse_number(raw, t.get("format", "plain")))
                    elif f == "net_amount":
                        v = parse_number(raw, t["format"]) / t.get("divide_by", 1.0)
                        rec[f] = round(v, 2)
                    elif f == "status":
                        rec[f] = status_vocab.get(raw)
                        if rec[f] is None:
                            errs.append(f"unknown status {raw}")
                    else:
                        rec[f] = raw
                except (ValueError, KeyError) as e:
                    rec[f] = None
                    errs.append(f"{f}: {e}")
            if rec.get("quantity") is not None and rec.get("unit") in uom:
                rec["quantity_each"] = rec["quantity"] * uom[rec["unit"]]
            for col, crm in a.custom_fields.items():
                rec[crm] = a.columns[col].values[i].strip()
            rec["source_site"] = a.site
            out.append(rec)
            if errs:
                errors.append({"row": i + 2, "errors": errs})
        return {"records": out, "row_errors": errors,
                "blocked_fields": sorted(blocked), "ready": not a.open_flags and not errors}
