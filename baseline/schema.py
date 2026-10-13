"""The canonical CRM order every site has to land in, plus the starting pattern library.

The library is what an integrations team "knows" on day one: SAP technical field names,
SAP's standard English column labels, SAP's status codes and default unit sizes.
Each approved site adds to it.
"""

CANONICAL_FIELDS = [
    {"name": "order_id", "type": "id", "desc": "ERP sales order number (CRM external id)"},
    {"name": "customer_id", "type": "id", "desc": "Sold-to customer number"},
    {"name": "customer_name", "type": "text", "desc": "Customer name"},
    {"name": "order_date", "type": "date", "desc": "Order creation date, ISO 8601"},
    {"name": "material_id", "type": "id", "desc": "Product number, no leading zeros"},
    {"name": "quantity", "type": "int", "desc": "Ordered quantity in the unit below"},
    {"name": "unit", "type": "uom", "desc": "Sales unit; converted to EACH for the CRM"},
    {"name": "net_amount", "type": "money", "desc": "Net order value, excluding tax"},
    {"name": "currency", "type": "currency", "desc": "ISO 4217 currency code"},
    {"name": "plant", "type": "plant", "desc": "Shipping plant / site code"},
    {"name": "status", "type": "status", "desc": "OPEN, IN_PROGRESS, COMPLETE or CANCELLED"},
    {"name": "batch", "type": "batch", "desc": "Batch / lot number (GxP traceability)"},
]
FIELD_NAMES = [f["name"] for f in CANONICAL_FIELDS]
CANONICAL_STATUS = ["OPEN", "IN_PROGRESS", "COMPLETE", "CANCELLED"]

SEED_LIBRARY = {
    "aliases": {
        # SAP technical names (VBAK / VBAP / VBUK)
        "vbeln": "order_id", "kunnr": "customer_id", "kunag": "customer_id", "name1": "customer_name",
        "erdat": "order_date", "audat": "order_date", "matnr": "material_id", "kwmeng": "quantity",
        "vrkme": "unit", "netwr": "net_amount", "waerk": "currency", "werks": "plant",
        "gbstk": "status", "charg": "batch",
        # SAP standard English column labels
        "salesdocument": "order_id", "soldtoparty": "customer_id", "customer": "customer_name",
        "createdon": "order_date", "documentdate": "order_date", "material": "material_id",
        "orderquantity": "quantity", "salesunit": "unit", "netvalue": "net_amount",
        "documentcurrency": "currency", "plant": "plant", "overallstatus": "status",
        "batch": "batch",
    },
    "status_vocab": {
        "A": "OPEN", "B": "IN_PROGRESS", "C": "COMPLETE",
        "Not yet processed": "OPEN", "Partially processed": "IN_PROGRESS",
        "Completely processed": "COMPLETE", "Rejected": "CANCELLED",
    },
    "uom_to_each": {"EA": 1, "PC": 1, "BOX": 10, "CS": 120},
    "approved_sites": [],
}

# Planning assumption for cross-site price checks only. Edit freely.
FX_TO_USD = {"USD": 1.0, "INR": 1 / 83.0, "GBP": 1 / 0.79, "EUR": 1 / 0.92, "CAD": 1 / 1.37}

DATE_FORMATS = ["%Y%m%d", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"]

# Thresholds, kept in one place so they can be argued with
AUTO_MATCH_MIN = 0.80        # inferred match below this needs a human
AUTO_MATCH_MARGIN = 0.15     # best match must beat runner-up by this much
PRICE_DRIFT_TOL = 0.08       # site prices >8% off the other sites get flagged
UOM_DRIFT_TOL = 0.08
QUALITY_KEYWORDS = ("GXP", "REL", "COLD", "TEMP", "QA", "BATCH", "LOT", "EXP")
