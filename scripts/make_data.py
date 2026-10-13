"""Generate synthetic SAP sales-order exports for six sites of a made-up pharma company.

Every site runs "the same ERP". Every export still looks different, the way real ones do.
Nothing here is real customer data. Run: python scripts/make_data.py
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "baseline" / "data" / "sites"

# Base list price per EACH, in USD. Same products ship from every site.
PRODUCTS = {
    "1001": 4.20,   # amoxicillin 500mg strip
    "1002": 2.75,   # paracetamol 650mg strip
    "1003": 18.50,  # insulin pen
    "1004": 9.90,   # saline 500ml
    "1005": 31.00,  # biologic vial
    "1006": 6.40,   # oral rehydration pack
    "1007": 12.25,  # inhaler
    "1008": 3.10,   # antiseptic 100ml
}
FX = {"USD": 1.0, "INR": 83.0, "GBP": 0.79, "CAD": 1.37}


def rng_dates(r, n, start, days, max_day=None):
    out = []
    while len(out) < n:
        d = start + timedelta(days=r.randint(0, days))
        if max_day and d.day > max_day:
            continue
        out.append(d)
    return sorted(out)


def base_rows(r, n, site, currency, plant, customers, box=10, gross=1.0, max_day=None):
    rows = []
    dates = rng_dates(r, n, date(2026, 7, 1), 90, max_day)
    for i in range(n):
        mat = r.choice(list(PRODUCTS))
        unit = "BOX" if r.random() < 0.4 else "EA"
        qty = r.randint(5, 60) if unit == "BOX" else r.randint(20, 600)
        each = qty * (box if unit == "BOX" else 1)
        price = PRODUCTS[mat] * FX[currency] * r.uniform(0.97, 1.03)
        amount = round(each * price * gross, 2)
        cust_id, cust_name = r.choice(customers)
        status = r.choices(["OPEN", "IN_PROGRESS", "COMPLETE", "CANCELLED"], [3, 2, 6, 1])[0]
        rows.append({
            "order": str(r.randint(4500000000, 4599999999)),
            "cust_id": cust_id, "cust_name": cust_name, "date": dates[i],
            "mat": mat, "qty": qty, "unit": unit, "amount": amount,
            "currency": currency, "plant": plant, "status": status,
            "batch": f"B{dates[i]:%y%m}{r.randint(100, 999)}",
        })
    return rows


def write(name, header, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def inr_group(x):
    """Indian digit grouping, e.g. 1,23,456.00"""
    whole, frac = f"{x:.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return f"{whole}.{frac}"


def main():
    r = random.Random(42)
    sap_status = {"OPEN": "A", "IN_PROGRESS": "B", "COMPLETE": "C", "CANCELLED": "C"}
    txt_status = {"OPEN": "Not yet processed", "IN_PROGRESS": "Partially processed",
                  "COMPLETE": "Completely processed", "CANCELLED": "Rejected"}

    # 1. Mumbai: raw SAP technical names, YYYYMMDD, 18-char material numbers with leading zeros
    cust = [("0000100231", "Apollo Distributors"), ("0000100232", "MedPlus Wholesale"),
            ("0000100233", "Sun Pharma Traders")]
    rows = base_rows(r, 40, "MUM", "INR", "IN01", cust)
    write("MUM_mumbai", ["VBELN", "KUNNR", "NAME1", "ERDAT", "MATNR", "KWMENG", "VRKME",
                         "NETWR", "WAERK", "WERKS", "GBSTK", "CHARG"],
          [[x["order"], x["cust_id"], x["cust_name"], f"{x['date']:%Y%m%d}", x["mat"].zfill(18),
            x["qty"], x["unit"], f"{x['amount']:.2f}", x["currency"], x["plant"],
            sap_status[x["status"]], x["batch"]] for x in rows])

    # 2. Ahmedabad: same ERP, exported with English labels, DD.MM.YYYY, Indian digit grouping
    cust = [("0000200110", "Gujarat Health Stores"), ("0000200111", "Narmada Medical"),
            ("0000200112", "Sabarmati Pharma")]
    rows = base_rows(r, 40, "AHM", "INR", "IN02", cust)
    write("AHM_ahmedabad", ["Sales Document", "Sold-To Party", "Customer", "Created On",
                            "Material", "Order Quantity", "Sales Unit", "Net Value",
                            "Document Currency", "Plant", "Overall Status", "Batch"],
          [[x["order"], x["cust_id"], x["cust_name"], f"{x['date']:%d.%m.%Y}", x["mat"].zfill(18),
            x["qty"], x["unit"], inr_group(x["amount"]), x["currency"], x["plant"],
            txt_status[x["status"]], x["batch"]] for x in rows])

    # 3. Grangemouth (UK): technical names, DD/MM/YYYY, amounts include 20% VAT, two custom Z-fields
    cust = [("0000300501", "Boots Supply Chain"), ("0000300502", "NHS Scotland Procurement"),
            ("0000300503", "Lloyds Pharmacy DC")]
    rows = base_rows(r, 40, "GRG", "GBP", "GB01", cust, gross=1.20)
    write("GRG_grangemouth", ["VBELN", "KUNNR", "NAME1", "ERDAT", "MATNR", "KWMENG", "VRKME",
                              "NETWR", "WAERK", "WERKS", "GBSTK", "CHARG", "ZZGXP_REL",
                              "ZZLEGACY_KUNNR"],
          [[x["order"], x["cust_id"], x["cust_name"], f"{x['date']:%d/%m/%Y}", x["mat"].zfill(18),
            x["qty"], x["unit"], f"{x['amount']:.2f}", x["currency"], x["plant"],
            sap_status[x["status"]], x["batch"], r.choice(["Y", "Y", "Y", "N"]),
            f"L{r.randint(10000, 99999)}"] for x in rows])

    # 4. Lexington (US): a different team's report layout, MM/DD/YYYY, own status codes, BOX = 12
    cust = [("0000400900", "McKesson East"), ("0000400901", "Cardinal Health KY"),
            ("0000400902", "AmerisourceBergen OH")]
    rows = base_rows(r, 40, "LXG", "USD", "US01", cust, box=12)
    lxg_status = {"OPEN": "OPEN", "IN_PROGRESS": "SHPD", "COMPLETE": "INV", "CANCELLED": "CANC"}
    write("LXG_lexington", ["Order Number", "Bill-To Party", "Customer Name", "Order Date",
                            "Item", "Qty", "Unit", "Amount", "Currency", "Site", "Status", "Lot"],
          [[x["order"], x["cust_id"], x["cust_name"], f"{x['date']:%m/%d/%Y}", x["mat"].zfill(18),
            x["qty"], x["unit"], f"{x['amount']:.2f}", x["currency"], x["plant"],
            lxg_status[x["status"]], x["batch"]] for x in rows])

    # 5. Aurora (Canada): migrated from a legacy system, leading zeros stripped,
    #    numeric statuses, and every order happens to fall on day 1-12 (date order is ambiguous)
    cust = [("100771", "McKesson Canada"), ("100772", "Shoppers Drug Mart DC"),
            ("100773", "Rexall West")]
    rows = base_rows(r, 40, "AUR", "CAD", "CA01", cust, max_day=12)
    aur_status = {"OPEN": "10", "IN_PROGRESS": "20", "COMPLETE": "30", "CANCELLED": "90"}
    write("AUR_aurora", ["VBELN", "KUNNR", "NAME1", "ERDAT", "MATNR", "KWMENG", "VRKME",
                         "NETWR", "WAERK", "WERKS", "GBSTK", "CHARG"],
          [[x["order"], x["cust_id"], x["cust_name"], f"{x['date']:%d/%m/%Y}", x["mat"],
            x["qty"], x["unit"], f"{x['amount']:.2f}", x["currency"], x["plant"],
            aur_status[x["status"]], x["batch"]] for x in rows])

    # 6. Turbhe: newly acquired site. Its IT team built reports from the same template as
    #    Lexington (same order-report layout and status codes), plus one new Z-field.
    cust = [("0000600301", "Navi Mumbai Medicals"), ("0000600302", "Thane Health Mart"),
            ("0000600303", "Konkan Pharma")]
    rows = base_rows(r, 40, "TUR", "INR", "IN03", cust)
    write("TUR_turbhe", ["Order Number", "Bill-To Party", "Customer", "Created On", "Item", "Qty",
                         "Sales Unit", "Net Value", "Document Currency", "Plant", "Status", "Lot",
                         "ZZCOLD_CHAIN"],
          [[x["order"], x["cust_id"], x["cust_name"], f"{x['date']:%d.%m.%Y}", x["mat"].zfill(18),
            x["qty"], x["unit"], inr_group(x["amount"]), x["currency"], x["plant"],
            lxg_status[x["status"]], x["batch"], r.choice(["2-8C", "AMBIENT", "AMBIENT"])]
           for x in rows])
    print(f"wrote 6 site exports to {OUT}")


if __name__ == "__main__":
    main()
