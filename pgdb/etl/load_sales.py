"""Load the 'Direct Sales Report' sheet into PostgreSQL.

Use it once to migrate history out of Google Sheets, and again whenever
someone hands over a workbook of rows that never went through the web form.
Re-running it is safe: a row whose invoice number and brand are already in
the ledger is skipped, not duplicated.

    set DATABASE_URL=postgresql://user:pw@host:5432/dstt
    python load_sales.py "C:\\Users\\Admin\\Downloads\\DST & TT DATABASE.xlsx"
    python load_sales.py <file> --dry-run       # report only, write nothing

What it fixes on the way in, and reports at the end:
  * state spellings  (Telengana, Odissa, Rajastan, and 'Aurangabad' as a state)
  * city spellings   (TVM, Cochin, Calicut, Bengaluru, Kolkatta, Tiurpur ...)
  * order type       (Re-order / Re-Order)
  * sub channel      (Distributon -> Distribution)
  * Month and Week columns are ignored; both are derived from the invoice date

What it will not do is guess. A city it cannot place, or a value that looks
like a person's name in the city column, is loaded as written and listed in
the report so a human can settle it in the app.
"""

import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime

try:
    import openpyxl
    import psycopg2
    import psycopg2.extras
except ImportError as e:
    sys.exit(f"missing dependency: {e}.  pip install -r requirements.txt")


SHEET = "Direct Sales Report"

# ------------------------------------------------------------------ fixes

# Wrong in the sheet today. Left column is what is typed, right is the truth.
STATE_FIX = {
    "telengana": "Telangana",
    "odissa": "Odisha",
    "rajastan": "Rajasthan",
    # A city sitting in the state column. Its rows carry City = Pune.
    "aurangabad": "Maharashtra",
    "daman & diu": "Dadra & Nagar Haveli and Daman & Diu",
}

# Same place, different spellings.
CITY_FIX = {
    "tvm": "Trivandrum",
    "cochin": "Kochi",
    "calicut": "Kozhikode",
    "bengaluru": "Bangalore",
    "tiruneveli": "Tirunelveli",
    "tiurpur": "Tirupur",
    "secunderbad": "Secunderabad",
    "kolkatta": "Kolkata",
    "malad west": "Malad",
    "ivory mall malad": "Malad",
    "navi": "Navi Mumbai",
    "patia": "Bhubaneswar",
}

# Cities filed under the wrong state in the sheet. Only the unambiguous ones.
CITY_STATE_FIX = {
    "hyderabad": "Telangana",       # appears under Andhra Pradesh on 2 rows
    "moradabad": "Uttar Pradesh",   # appears under Madhya Pradesh on 2 rows
    "pondicherry": "Puducherry",    # a union territory, filed under Tamil Nadu
}

SUB_CHANNEL_FIX = {"distributon": "Distribution"}

# The sheet spaces its dashes ('D&R - S'); the master table does not. Without
# this the loader would create a second channel alongside the seeded one and
# every channel target would miss half its sales.
CHANNEL_FIX = {
    "d&r - s": "D&R-S", "d&r-s": "D&R-S",
    "d&r - n": "D&R-N", "d&r-n": "D&R-N",
    "l - lfo": "L-LFO", "l-lfo": "L-LFO",
    "lfo": "LFO", "saloon": "Saloon", "hospital": "Hospital",
}

ORDER_FIX = {"re-order": "Re-order", "first": "First"}

# City -> district, for the places that carry the business. Anything missing
# just leaves district blank on the row; it is optional.
CITY_DISTRICT = {
    "chennai": "Chennai", "coimbatore": "Coimbatore", "madurai": "Madurai",
    "trichy": "Tiruchirappalli", "salem": "Salem", "tirupur": "Tirupur",
    "erode": "Erode", "vellore": "Vellore", "tirunelveli": "Tirunelveli",
    "dindigul": "Dindigul", "karaikudi": "Sivaganga", "kumbakonam": "Thanjavur",
    "nagercoil": "Kanyakumari", "tenkasi": "Tenkasi",
    "chengalpattu": "Chengalpattu", "dharapuram": "Tirupur",
    "trivandrum": "Thiruvananthapuram", "kochi": "Ernakulam",
    "ernakulam": "Ernakulam", "kozhikode": "Kozhikode",
    "kottayam": "Kottayam", "palakkad": "Palakkad", "kollam": "Kollam",
    "pala": "Kottayam",
    "bangalore": "Bengaluru Urban", "belgaum": "Belagavi",
    "mysore": "Mysuru", "shimoga": "Shivamogga",
    "hyderabad": "Hyderabad", "secunderabad": "Hyderabad",
    "guntur": "Guntur", "visakhapatnam": "Visakhapatnam",
    "srikakulam": "Srikakulam", "rajamahendravaram": "East Godavari",
    "west godavari": "West Godavari",
    "pune": "Pune", "malad": "Mumbai Suburban", "kandivali": "Mumbai Suburban",
    "bhiwandi": "Thane", "navi mumbai": "Thane", "seawoods": "Thane",
    "panvel": "Raigad",
    "lucknow": "Lucknow", "noida": "Gautam Buddha Nagar", "varanasi": "Varanasi",
    "moradabad": "Moradabad",
    "ranchi": "Ranchi", "jamshedpur": "East Singhbhum",
    "ahmedabad": "Ahmedabad", "anand": "Anand", "indore": "Indore",
    "begusarai": "Begusarai", "gaya": "Gaya", "bhubaneswar": "Khordha",
    "kolkata": "Kolkata", "guwahati": "Kamrup Metropolitan",
    "jaipur": "Jaipur", "delhi": "New Delhi", "chandigarh": "Chandigarh",
    "pondicherry": "Puducherry", "silvassa": "Dadra & Nagar Haveli",
}

# Values in the Executive column that name a channel, not a person. They are
# kept so old rows still resolve, but flagged so person-wise reports ignore
# them and somebody can reassign the rows to a real owner.
NOT_PEOPLE = {"lfo", "north india", "saloon"}

# Courier type, however it gets typed. Anything not listed here is created as
# a new courier and reported, so a genuinely new carrier is not lost -- but
# the spellings already in use land on the right row.
COURIER_FIX = {
    "dtdc": "DTDC", "d.t.d.c": "DTDC", "dtdc express": "DTDC",
    "rk": "RK", "r.k": "RK", "r.k.": "RK",
    "rkb": "RKB", "r.k.b": "RKB", "rkb logistics": "RKB",
    "own": "OWN", "self": "OWN", "own delivery": "OWN",
    "by hand": "OWN", "hand delivery": "OWN",
}

# Cities in these states need a state row that the seed may not have created
# because nothing was ever billed there. The loader creates states it meets.
DEFAULT_ZONE = {
    "Tamil Nadu": "South", "Kerala": "South", "Karnataka": "South",
    "Andhra Pradesh": "South", "Telangana": "South", "Puducherry": "South",
    "Maharashtra": "West", "Gujarat": "West", "Goa": "West",
    "Rajasthan": "North", "Delhi": "North", "Uttar Pradesh": "North",
    "Punjab": "North", "Haryana": "North", "Chandigarh": "North",
    "Madhya Pradesh": "Central", "Chhattisgarh": "Central",
    "West Bengal": "East", "Bihar": "East", "Jharkhand": "East",
    "Odisha": "East", "Assam": "North East",
}


# ----------------------------------------------------------------- helpers

def clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def num(v):
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("\u20b9", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0


def as_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if v is None:
        return None
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            pass
    return None


class Cache:
    """get-or-create for the master tables, one round trip per new value."""

    def __init__(self, cur, report):
        self.cur = cur
        self.report = report
        self.state, self.district, self.city = {}, {}, {}
        self.channel, self.sub_channel, self.brand = {}, {}, {}
        self.person, self.customer, self.courier = {}, {}, {}
        self._load()

    def _load(self):
        c = self.cur
        c.execute("select state_id, lower(name) from master.state")
        self.state = dict((r[1], r[0]) for r in c.fetchall())
        c.execute("""select d.district_id, lower(s.name), lower(d.name)
                     from master.district d join master.state s using (state_id)""")
        self.district = dict(((r[1], r[2]), r[0]) for r in c.fetchall())
        c.execute("""select ci.city_id, ci.state_id, lower(ci.name) from master.city ci""")
        self.city = dict(((r[1], r[2]), r[0]) for r in c.fetchall())
        c.execute("select channel_id, lower(code) from master.channel")
        self.channel = dict((r[1], r[0]) for r in c.fetchall())
        c.execute("select sub_channel_id, lower(code) from master.sub_channel")
        self.sub_channel = dict((r[1], r[0]) for r in c.fetchall())
        c.execute("select brand_id, lower(name) from master.brand")
        self.brand = dict((r[1], r[0]) for r in c.fetchall())
        c.execute("select person_id, lower(full_name) from master.sales_person")
        self.person = dict((r[1], r[0]) for r in c.fetchall())
        c.execute("select customer_id, lower(name) from master.customer")
        self.customer = dict((r[1], r[0]) for r in c.fetchall())
        c.execute("select courier_id, lower(code) from master.courier")
        self.courier = dict((r[1], r[0]) for r in c.fetchall())

    def get_state(self, name):
        key = name.lower()
        if key in self.state:
            return self.state[key]
        self.cur.execute(
            "insert into master.state (name, zone) values (%s, %s) returning state_id",
            (name, DEFAULT_ZONE.get(name, "Unassigned")))
        sid = self.cur.fetchone()[0]
        self.state[key] = sid
        self.report["created_states"].add(name)
        return sid

    def get_district(self, state_name, dname):
        if not dname:
            return None
        key = (state_name.lower(), dname.lower())
        if key in self.district:
            return self.district[key]
        sid = self.get_state(state_name)
        self.cur.execute(
            "insert into master.district (state_id, name) values (%s, %s) returning district_id",
            (sid, dname))
        did = self.cur.fetchone()[0]
        self.district[key] = did
        self.report["created_districts"].add(f"{dname} ({state_name})")
        return did

    def get_city(self, cname, state_name):
        if not cname:
            return None, self.get_state(state_name), None
        sid = self.get_state(state_name)
        key = (sid, cname.lower())
        did = self.get_district(state_name, CITY_DISTRICT.get(cname.lower()))
        if key in self.city:
            return self.city[key], sid, did
        self.cur.execute(
            "insert into master.city (name, state_id, district_id) values (%s,%s,%s) returning city_id",
            (cname, sid, did))
        cid = self.cur.fetchone()[0]
        self.city[key] = cid
        self.report["created_cities"].add(f"{cname} ({state_name})")
        return cid, sid, did

    def get_channel(self, code):
        key = (code or "").lower()
        if key in self.channel:
            return self.channel[key]
        self.cur.execute(
            "insert into master.channel (code, name) values (%s,%s) returning channel_id",
            (code, code))
        cid = self.cur.fetchone()[0]
        self.channel[key] = cid
        self.report["created_channels"].add(code)
        return cid

    def get_sub_channel(self, code, channel_id):
        if not code:
            return None
        code = SUB_CHANNEL_FIX.get(code.lower(), code)
        key = code.lower()
        if key in self.sub_channel:
            return self.sub_channel[key]
        self.cur.execute(
            "insert into master.sub_channel (code, name, channel_id) values (%s,%s,%s)"
            " returning sub_channel_id", (code, code, channel_id))
        sid = self.cur.fetchone()[0]
        self.sub_channel[key] = sid
        self.report["created_sub_channels"].add(code)
        return sid

    def get_brand(self, name):
        key = (name or "").lower()
        if key in self.brand:
            return self.brand[key]
        self.cur.execute(
            "insert into master.brand (code, name) values (%s,%s) returning brand_id",
            (name[:2].upper(), name))
        bid = self.cur.fetchone()[0]
        self.brand[key] = bid
        self.report["created_brands"].add(name)
        return bid

    def get_person(self, name, channel_id):
        if not name:
            return None
        key = name.lower()
        if key in self.person:
            return self.person[key]
        self.cur.execute(
            "insert into master.sales_person (full_name, channel_id, is_real_person)"
            " values (%s,%s,%s) returning person_id",
            (name, channel_id, key not in NOT_PEOPLE))
        pid = self.cur.fetchone()[0]
        self.person[key] = pid
        if key in NOT_PEOPLE:
            self.report["placeholder_people"].add(name)
        else:
            self.report["created_people"].add(name)
        return pid

    def get_courier(self, code):
        if not code:
            return None
        key = code.lower()
        if key in self.courier:
            return self.courier[key]
        self.cur.execute(
            "insert into master.courier (code, name) values (%s,%s) returning courier_id",
            (code, code))
        cid = self.cur.fetchone()[0]
        self.courier[key] = cid
        self.report["created_couriers"].add(code)
        return cid

    def get_customer(self, name, sub_channel_id, city_id):
        key = (name or "").lower()
        if key in self.customer:
            return self.customer[key]
        self.cur.execute(
            "insert into master.customer (name, sub_channel_id, city_id)"
            " values (%s,%s,%s) returning customer_id", (name, sub_channel_id, city_id))
        cid = self.cur.fetchone()[0]
        self.customer[key] = cid
        self.report["created_customers"].add(name)
        return cid


# -------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("excel", help="path to the DST & TT workbook")
    ap.add_argument("--sheet", default=SHEET)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"),
                    help="postgresql://... (default: $DATABASE_URL)")
    ap.add_argument("--source", default="sheets_migration",
                    choices=["sheets_migration", "excel_import"])
    ap.add_argument("--dry-run", action="store_true",
                    help="parse and report, then roll back")
    args = ap.parse_args()

    if not args.dsn:
        sys.exit("no DATABASE_URL set and no --dsn given")

    wb = openpyxl.load_workbook(args.excel, read_only=True, data_only=True)
    if args.sheet not in wb.sheetnames:
        sys.exit(f"sheet '{args.sheet}' not found. present: {wb.sheetnames}")
    ws = wb[args.sheet]

    rows = ws.iter_rows(values_only=True)
    hdr = [clean(h) for h in next(rows)]
    ix = {h: i for i, h in enumerate(hdr) if h}

    def col(*names):
        """The sheet has renamed columns over time; accept any known spelling."""
        for n in names:
            if n in ix:
                return ix[n]
        return None

    C = {
        "sno":    col("S.NO"),
        "date":   col("Tax Invoice Date", "Invoice Date"),
        "pi":     col("PI Number"),
        "inv":    col("Tax Invoice No.", "Tax Invoice No"),
        "cust":   col("Supplier Name", "Customer Name"),
        "chan":   col("Channels", "Region"),
        "person": col("Execuitve", "Executive"),
        "city":   col("City"),
        "state":  col("State"),
        "sub":    col("SUB Channels", "Direct /Distribution /Group"),
        "brand":  col("DT / TT"),
        "order":  col("Order"),
        "pqty":   col("DT/TT Primary Order qty"),
        "ptax":   col("DT/TT Primary Taxable Value ( Without Tax)"),
        "pval":   col("DT/TT Primary Sale Value (With Tax)"),
        "sqty":   col("D2R Secondary Order Qty"),
        "sval":   col("D2R Secondary Sale Value"),
        "tnos":   col("R2C Tertiary Sale Nos"),
        "tval":   col("R2C Tertiary Sale Value"),
        "credit": col("Credit Period"),
        "ship":   col("Stock Supplied Date"),
        "paid":   col("Payment Recieved Date", "Payment Received Date"),
        # Added later than the rest of the sheet, so both are optional: a
        # workbook saved before the columns existed still loads.
        "courier":  col("Courier Type", "Courier", "Couries Type"),
        "docket":   col("Courier Number", "Docket No", "Docket Number",
                        "AWB", "AWB No", "Couries Number"),
    }
    missing = [k for k in ("date", "cust", "chan", "brand", "ptax") if C[k] is None]
    if missing:
        sys.exit(f"workbook is missing required columns for: {missing}\n"
                 f"columns present: {[h for h in hdr if h]}")

    report = defaultdict(set)
    counts = Counter()
    fixes = Counter()
    problems = []
    anomalies = []
    line_seq = Counter()     # (invoice_no, brand_id) -> lines seen so far
    invoice_head = {}        # (invoice_no, brand_id) -> (customer, date) of line 1

    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False
    cur = conn.cursor()
    cache = Cache(cur, report)

    payload = []
    for n, row in enumerate(rows, start=2):

        def g(key):
            i = C[key]
            return row[i] if i is not None and i < len(row) else None

        inv_date = as_date(g("date"))
        taxable = num(g("ptax"))
        qty = num(g("pqty"))

        # Spacer and total rows have no S.NO. Skipping them is right, but a
        # row carrying money and no number is an invoice someone forgot to
        # number, and dropping it silently is how a month goes missing.
        if C["sno"] is not None and not clean(g("sno")):
            if taxable or qty:
                problems.append((n, "unnumbered row carrying value", f"taxable={taxable:,.0f}"))
                counts["unnumbered_with_value"] += 1
            else:
                counts["blank_skipped"] += 1
            continue

        if inv_date is None:
            problems.append((n, "no readable invoice date", repr(g("date"))))
            counts["no_date"] += 1
            continue

        # -------- geography
        state = clean(g("state")) or "Unknown"
        if state.lower() in STATE_FIX:
            fixes[f"state: {state} -> {STATE_FIX[state.lower()]}"] += 1
            state = STATE_FIX[state.lower()]

        city = clean(g("city"))
        if city and city.lower() in CITY_FIX:
            fixes[f"city: {city} -> {CITY_FIX[city.lower()]}"] += 1
            city = CITY_FIX[city.lower()]
        if city and city.lower() in CITY_STATE_FIX and CITY_STATE_FIX[city.lower()] != state:
            fixes[f"state of {city}: {state} -> {CITY_STATE_FIX[city.lower()]}"] += 1
            state = CITY_STATE_FIX[city.lower()]
        if city and city.lower() not in CITY_DISTRICT:
            report["cities_without_district"].add(f"{city} ({state})")

        city_id, state_id, district_id = cache.get_city(city, state)

        # -------- the rest of the dimensions
        chan = clean(g("chan")) or "Unknown"
        if chan.lower() in CHANNEL_FIX and CHANNEL_FIX[chan.lower()] != chan:
            fixes[f"channel: {chan} -> {CHANNEL_FIX[chan.lower()]}"] += 1
        chan = CHANNEL_FIX.get(chan.lower(), chan)
        channel_id = cache.get_channel(chan)
        sub_channel_id = cache.get_sub_channel(clean(g("sub")), channel_id)
        person_id = cache.get_person(clean(g("person")), channel_id)
        brand_id = cache.get_brand(clean(g("brand")) or "Unknown")
        customer = clean(g("cust")) or "Unknown"
        customer_id = cache.get_customer(customer, sub_channel_id, city_id)

        order_type = clean(g("order")) or "Re-order"
        if order_type.lower() in ORDER_FIX and ORDER_FIX[order_type.lower()] != order_type:
            fixes[f"order: {order_type} -> {ORDER_FIX[order_type.lower()]}"] += 1
        order_type = ORDER_FIX.get(order_type.lower(), "Re-order")

        credit = g("credit")
        try:
            credit = int(num(credit)) or None
        except (TypeError, ValueError):
            credit = None

        # -------- courier
        courier = clean(g("courier"))
        if courier:
            if courier.lower() in COURIER_FIX and COURIER_FIX[courier.lower()] != courier:
                fixes[f"courier: {courier} -> {COURIER_FIX[courier.lower()]}"] += 1
            courier = COURIER_FIX.get(courier.lower(), courier)
        courier_id = cache.get_courier(courier)

        # Docket numbers are long digit strings. Excel stores them as floats
        # and openpyxl hands back 12345678901.0, which would be written to
        # the database with a '.0' on the end and never match a search.
        docket = g("docket")
        if isinstance(docket, float) and docket.is_integer():
            docket = str(int(docket))
        docket = clean(docket)

        # -------- invoice line number
        # A second row for the same invoice and brand is normally a second SKU
        # on the same bill, so it gets line 2 rather than being rejected. But
        # if it also names a different customer or a different date it is not
        # another line, it is the same invoice number used twice -- which is a
        # real error. Load it, and say so.
        invoice_no = clean(g("inv"))
        line_no = 1
        if invoice_no:
            k = (invoice_no.lower(), brand_id)
            line_seq[k] += 1
            line_no = line_seq[k]
            if line_no == 1:
                invoice_head[k] = (customer, inv_date)
            else:
                first_cust, first_date = invoice_head[k]
                if first_cust != customer or first_date != inv_date:
                    anomalies.append((n, invoice_no,
                                      f"line 1 = {first_cust[:28]} {first_date}",
                                      f"line {line_no} = {customer[:28]} {inv_date}"))

        payload.append((
            inv_date, clean(g("pi")), invoice_no, line_no,
            customer_id, channel_id, sub_channel_id, person_id, brand_id,
            city_id, state_id, district_id, order_type,
            qty, taxable, num(g("pval")),
            num(g("sqty")), num(g("sval")), num(g("tnos")), num(g("tval")),
            credit, as_date(g("ship")), courier_id, docket,
            as_date(g("paid")), args.source,
        ))
        counts["parsed"] += 1

    # The unique index on (lower(invoice_no), brand_id) makes the re-run safe.
    sql = """
        insert into txn.sales_entry
            (invoice_date, pi_number, invoice_no, line_no,
             customer_id, channel_id, sub_channel_id, person_id, brand_id,
             city_id, state_id, district_id, order_type,
             primary_qty, primary_taxable, primary_with_tax,
             secondary_qty, secondary_value, tertiary_nos, tertiary_value,
             credit_days, stock_supplied_on, courier_id, courier_docket_no,
             payment_received_on, source)
        values %s
        on conflict (lower(invoice_no), brand_id, line_no)
            where invoice_no is not null and not is_deleted
        do nothing
    """
    before = 0
    cur.execute("select count(*) from txn.sales_entry where not is_deleted")
    before = cur.fetchone()[0]

    psycopg2.extras.execute_values(cur, sql, payload, page_size=500)

    cur.execute("select count(*) from txn.sales_entry where not is_deleted")
    after = cur.fetchone()[0]
    counts["inserted"] = after - before
    counts["skipped_duplicate"] = counts["parsed"] - counts["inserted"]

    cur.execute("select coalesce(sum(primary_taxable),0), coalesce(sum(primary_with_tax),0)"
                " from txn.sales_entry where not is_deleted")
    tot_tax, tot_val = cur.fetchone()

    # -------- report
    w = sys.stdout.write
    w("\n" + "=" * 68 + "\n")
    w(f"  {'DRY RUN -- nothing written' if args.dry_run else 'LOAD COMPLETE'}\n")
    w("=" * 68 + "\n\n")
    w(f"  rows parsed          {counts['parsed']:>8,}\n")
    w(f"  rows inserted        {counts['inserted']:>8,}\n")
    w(f"  already in ledger    {counts['skipped_duplicate']:>8,}\n")
    w(f"  blank/spacer rows    {counts['blank_skipped']:>8,}\n")
    w(f"  ledger now holds     {after:>8,}  rows\n")
    w(f"  taxable value        {tot_tax:>14,.0f}\n")
    w(f"  value with tax       {tot_val:>14,.0f}\n")

    if fixes:
        w("\n  spellings corrected on the way in\n")
        for k, v in sorted(fixes.items(), key=lambda x: -x[1]):
            w(f"     {v:>5}  {k}\n")

    for label, key in [("states", "created_states"), ("districts", "created_districts"),
                       ("cities", "created_cities"), ("channels", "created_channels"),
                       ("sub channels", "created_sub_channels"), ("brands", "created_brands"),
                       ("couriers", "created_couriers"),
                       ("sales people", "created_people"), ("customers", "created_customers")]:
        if report[key]:
            vals = sorted(report[key])
            shown = ", ".join(vals[:12]) + (f" ... (+{len(vals)-12} more)" if len(vals) > 12 else "")
            w(f"\n  new {label} ({len(vals)})\n     {shown}\n")

    if report["placeholder_people"]:
        w("\n  ACTION NEEDED -- these name a channel, not a person, and are\n"
          "  flagged is_real_person = false. Reassign their rows to the\n"
          "  executive who actually owns them:\n")
        for p in sorted(report["placeholder_people"]):
            w(f"     {p}\n")

    if report["cities_without_district"]:
        vals = sorted(report["cities_without_district"])
        w(f"\n  no district mapped ({len(vals)}) -- fill these in the app, or add\n"
          f"  them to CITY_DISTRICT in this script:\n     "
          + ", ".join(vals[:20]) + (f" ... (+{len(vals)-20} more)" if len(vals) > 20 else "") + "\n")

    if C["courier"] is None and C["docket"] is None:
        w("\n  no Courier Type / Courier Number columns in this workbook --\n"
          "  those fields were left blank. Add the columns to the sheet, or\n"
          "  update existing entries through the web form. Re-importing an\n"
          "  existing invoice line does not update its courier fields.\n")
    else:
        cur.execute("""select count(*) filter (where courier_id is not null),
                              count(*) filter (where courier_docket_no is not null)
                       from txn.sales_entry where not is_deleted""")
        n_cour, n_dock = cur.fetchone()
        w(f"\n  courier recorded on   {n_cour:>6,} rows\n"
          f"  docket recorded on    {n_dock:>6,} rows\n")

    multi = sum(1 for v in line_seq.values() if v > 1)
    if multi:
        w(f"\n  {multi} invoice(s) carry more than one line for the same brand.\n"
          f"  That is normal -- two SKUs on one bill -- and all lines are loaded.\n")

    if anomalies:
        w(f"\n  SAME INVOICE NUMBER, DIFFERENT CUSTOMER OR DATE ({len(anomalies)})\n"
          f"  These are loaded, but one of the two rows is probably mis-keyed:\n")
        for n, inv, a, b in anomalies[:15]:
            w(f"     row {n:<5} {inv}\n         {a}\n         {b}\n")
        if len(anomalies) > 15:
            w(f"     ... and {len(anomalies)-15} more\n")

    if problems:
        w(f"\n  ROWS NOT LOADED ({len(problems)})\n")
        for n, why, detail in problems[:25]:
            w(f"     row {n:<5} {why:<32} {detail}\n")
        if len(problems) > 25:
            w(f"     ... and {len(problems)-25} more\n")

    w("\n")

    if args.dry_run:
        conn.rollback()
        w("  rolled back.\n\n")
    else:
        conn.commit()
        w("  committed.\n\n")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
