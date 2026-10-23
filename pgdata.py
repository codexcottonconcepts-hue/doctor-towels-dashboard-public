"""All dashboard data is read from PostgreSQL, including supplementary datasets.

DATABASE_URL or private database.url is required. DATABASE_URL_FILE can name
another private file. Missing configuration or a failed database connection
never falls back to Google Sheets. Legacy supplementary shapes are preserved
in aux.dashboard_dataset; sales, targets and territories use relational tables.
"""

import os
from pathlib import Path
import lfo_data
import online_sales_store

try:
    import psycopg2
except ImportError:
    psycopg2 = None


MORD = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        "Jan", "Feb", "Mar"]

# template.html matches ledger rows on the channel spelling the sheet used,
# spaces around the dash and all: PAGES[].region is 'D&R - S', not 'D&R-S'.
# The database stores the tidy code, so it is translated back on the way out.
# Changing the template instead would be the cleaner fix, and this map is
# where to delete from when that happens.
REGION_OF = {
    "LFO":      "LFO",
    "L-LFO":    "L - LFO",
    "D&R-S":    "D&R - S",
    "D&R-N":    "D&R - N",
    "Saloon":   "Saloon",
    "Hospital": "Hospital",
}

# Each channel's secondary payload, by the DATA key template.html's SECONDARY
# map reads. LFO and L-LFO come from their Distributed tabs; the others are set
# up in the Target planner.
SECONDARY_KEYS = {
    "LFO":    "lfoSecondary",
    "L-LFO":  "llfoSecondary",
    "Saloon": "saloonSecondary",
    "D&R-S":  "drsSecondary",
    "D&R-N":  "drnSecondary",
}

# Database channel code -> the key the dashboard's target lookup uses.
TKEY_OF = {
    "LFO":    "LFO Primary",
    "L-LFO":  "L-LFO",
    "D&R-S":  "D&R-S",
    "D&R-N":  "North",
    "Saloon": "Saloon",
}


class NotConfigured(Exception):
    """Missing/unreadable database configuration, or psycopg2 is not installed."""


def _dsn_file():
    """A private configuration file, independent of the launch directory."""
    override = os.environ.get("DATABASE_URL_FILE", "").strip()
    return Path(override) if override else Path(__file__).resolve().with_name("database.url")


def dsn():
    value = os.environ.get("DATABASE_URL", "").strip()
    if value:
        return value
    try:
        value = _dsn_file().read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError):
        raise NotConfigured(
            "Cannot read the PostgreSQL configuration file. Check DATABASE_URL_FILE "
            "and the file permissions/UTF-8 encoding."
        ) from None
    if not value:
        raise NotConfigured(
            "The PostgreSQL configuration file is empty. Save the application "
            "connection string. PostgreSQL is required for dashboard data."
        )
    return value


def enabled():
    if psycopg2 is None:
        return False
    if os.environ.get("DATABASE_URL", "").strip():
        return True
    try:
        _dsn_file().stat()
    except FileNotFoundError:
        return False
    except OSError:
        # A configured but unreadable file must never silently select Sheets.
        return True
    return True


def connect():
    if psycopg2 is None:
        raise NotConfigured("psycopg2 is not installed (pip install psycopg2-binary)")
    connection_string = dsn()
    if not connection_string:
        raise NotConfigured("Set DATABASE_URL or save the application connection string in database.url")
    return psycopg2.connect(connection_string)


# ------------------------------------------------------------------ ledger

ROWS_SQL = """
    select to_char(period_month, 'Mon-YYYY')      as month_label,
           channel,
           coalesce(sales_person, 'Unknown')      as person,
           state,
           coalesce(city, 'Unknown')              as city,
           brand,
           coalesce(sub_channel, 'Unknown')       as sub_channel,
           coalesce(week_label, 'Unknown')        as week_label,
           primary_qty,
           primary_taxable,
           primary_with_tax,
           fy,
           coalesce(customer, '')                as supplier_name
    from rpt.v_sales
    order by invoice_date, entry_id
"""


def fetch_rows(conn):
    """The ledger, as the thirteen-element lists template.html indexes into.

        0 month label 'Apr-2026'   6 sub channel
        1 channel ('D&R - S')      7 week label
        2 sales person             8 qty
        3 state                    9 taxable      <- every target is measured on this
        4 city                    10 value with tax
        5 brand (DT/TT)           11 financial year
                                 12 supplier/customer name
    """
    with conn.cursor() as cur:
        cur.execute(ROWS_SQL)
        return _ledger_rows(cur.fetchall())


def _ledger_rows(records):
    # Match refresh.parse_ledger's whole-unit display rounding, including for
    # product coverage. Exact item amounts are kept separately in productRows.
    # PostgreSQL round() treats half units differently from Python.
    return [[r[0], REGION_OF.get(r[1], r[1]), *r[2:8],
             round(float(r[8])), round(float(r[9])), round(float(r[10])),
             int(r[11]), r[12]] for r in records]


# ----------------------------------------------------------------- targets

# Channel-level targets only: every other dimension must be null, or a
# person's slice of a channel would be added to the channel's own number.
CHANNEL_TARGET_SQL = """
    select p.fy, ch.code, to_char(t.period_month, 'Mon'), t.target_value
    from tgt.target t
    join tgt.target_plan p  on p.plan_id    = t.plan_id
    join master.channel  ch on ch.channel_id = t.channel_id
    where p.status = 'active'
      and t.metric = 'primary_taxable'
      and t.channel_id     is not null
      and t.sub_channel_id is null
      and t.person_id      is null
      and t.state_id       is null
      and t.district_id    is null
      and t.city_id        is null
      and t.brand_id       is null
"""


def fetch_channel_targets(conn, log=None):
    """{fy: {target key: {'Apr': n, ...}}}, with the roll-ups derived.

    The three totals are computed the same way the workbook did, so a page
    reading 'ALL TOTAL' gets the same number it always got.
    """
    byfy = {}
    with conn.cursor() as cur:
        cur.execute(CHANNEL_TARGET_SQL)
        for fy, code, mon, val in cur.fetchall():
            key = TKEY_OF.get(code)
            if key is None:                  # a channel the dashboard has no page for
                continue
            byfy.setdefault(int(fy), {}).setdefault(key, {})[mon] = int(round(float(val)))

    for fy, chan in byfy.items():
        def g(k, m, _c=chan):
            return _c.get(k, {}).get(m, 0)
        chan["D&R - S TOTAL"] = {m: g("D&R-S", m) + g("L-LFO", m) for m in MORD}
        chan["D&R TOTAL"] = {m: chan["D&R - S TOTAL"][m] + g("North", m) for m in MORD}
        chan["ALL TOTAL"] = {m: chan["D&R TOTAL"][m] + g("Saloon", m) + g("LFO Primary", m)
                             for m in MORD}

    if log:
        for fy in sorted(byfy):
            thin = [k for k in TKEY_OF.values()
                    if len([m for m in MORD if byfy[fy].get(k, {}).get(m)]) not in (0, 12)]
            if thin:
                log("    WARNING: FY%s has a partial year of targets for %s"
                    % (fy, ", ".join(sorted(thin))))
            absent = [k for k in TKEY_OF.values() if k not in byfy[fy]]
            if absent:
                log("    note: no channel target in the active plan for %s"
                    % ", ".join(sorted(absent)))
    return byfy


PRIMARY_RETAILER_TARGET_SQL = """
    select p.fy,
           to_char(t.period_month, 'Mon-YYYY'),
           ch.code,
           sc.code,
           b.name,
           t.target_value
    from tgt.target t
    join tgt.target_plan p on p.plan_id = t.plan_id
    join master.channel ch on ch.channel_id = t.channel_id
    join master.sub_channel sc on sc.sub_channel_id = t.sub_channel_id
                              and sc.channel_id = t.channel_id
    join master.brand b on b.brand_id = t.brand_id
    where p.status = 'active'
      and t.metric = 'primary_taxable'
      and t.person_id is null
      and t.state_id is null
      and t.district_id is null
      and t.city_id is null
    order by p.fy, t.period_month, ch.code, sc.code, b.name
"""


def fetch_primary_retailer_targets(conn):
    """Only explicit retailer + brand primary-sales budgets from active plans.

    Joining all three dimensions excludes broader channel budgets. Secondary
    distributor weights and branch plans are separate measures, never a source
    for these allocations. Missing rows remain missing; explicit zero survives.
    """
    with conn.cursor() as cur:
        cur.execute(PRIMARY_RETAILER_TARGET_SQL)
        return [{"fy": int(fy), "month": month, "channel": channel,
                 "retailer": retailer, "brand": brand, "amount": float(amount)}
                for fy, month, channel, retailer, brand, amount in cur.fetchall()]


# Person targets that name a place. A person + city row is the usual case;
# a person + state row covers a broader assignment. Legacy dashboards display
# either in the city field. Keep its scope too
# so new reports can match state assignments against ledger states.
EXEC_TARGET_SQL = """
    select p.fy,
           sp.full_name,
           coalesce(ci.name, st.name)  as place,
           to_char(t.period_month, 'Mon'),
           t.target_value,
           case when t.city_id is not null then 'city'
                when t.state_id is not null then 'state' else 'channel' end as scope_type,
           case when t.city_id is null then st.name else null end as state_name,
           ch.code as channel_code
    from tgt.target t
    join tgt.target_plan    p  on p.plan_id   = t.plan_id
    join master.sales_person sp on sp.person_id = t.person_id
    left join master.city   ci on ci.city_id  = t.city_id
    left join master.state  st on st.state_id = t.state_id
    left join master.channel ch on ch.channel_id = t.channel_id
    where p.status = 'active'
      and t.metric = 'primary_taxable'
      and t.person_id is not null
      and (t.city_id is not null or t.state_id is not null or t.channel_id is not null)
      and not t.is_rollup
      and t.sub_channel_id is null
      and t.district_id    is null
      and t.brand_id       is null
"""


def fetch_exec_targets(conn):
    """Executive plans, retaining the legacy city/t/a/full fields.

    't' is the first four months and 'a' the achieved column beside it, which
    this source has never carried -- the dashboard computes achievement from
    the ledger. Both are kept so the shape matches what the sheet produced.
    'reportedMonths' distinguishes an entered zero from a missing target;
    'scopeType' and (for state plans) 'state' preserve the place's meaning.
    Explicit channel assignments add channel/region; legacy unscoped records
    retain their existing shape for the older consumers.
    """
    acc = {}
    with conn.cursor() as cur:
        cur.execute(EXEC_TARGET_SQL)
        for fy, person, place, mon, val, scope_type, state, channel in cur.fetchall():
            key = (int(fy), person, place or "", scope_type, state or "", channel or "")
            acc.setdefault(key, {})[mon] = int(round(float(val)))

    out = {}
    for (fy, person, place, scope_type, state, channel), months in sorted(acc.items()):
        full = {m: months.get(m, 0) for m in MORD}
        plan = {
            "person": person,
            "city": place,
            "t": [full[m] for m in ("Apr", "May", "Jun", "Jul")],
            "a": [None, None, None, None],
            "full": full,
            "reportedMonths": [m for m in MORD if m in months],
            "scopeType": scope_type,
        }
        if scope_type == "state":
            plan["state"] = state
        if channel:
            plan["channel"] = channel
            plan["region"] = REGION_OF.get(channel, channel)
        out.setdefault(fy, []).append(plan)
    return out


# -------------------------------------------------------------------- ROTN

def fetch_rotn(conn):
    """{'tab': ..., 'people': [{'name', 'ledger', 'terrs': [...]}]} or None.

    None when nobody has been given a territory, which is how refresh.py
    already asks for the ROTN page to be hidden.
    """
    with conn.cursor() as cur:
        cur.execute("select name, ledger, territories from rpt.v_person_territory"
                    " order by lower(name)")
        people = [{"name": n, "ledger": l, "terrs": list(t or [])}
                  for n, l, t in cur.fetchall()]
    if not people:
        return None
    return {"tab": "master.person_territory", "people": people}


# ----------------------------------------------------------------- colours

# Product-invoice lines with a colour, summed by the same dimensions the
# ledger filters on. Only lines with a colour are read; amounts never feed totals.
COLOUR_ROWS_SQL = """
    select to_char(v.period_month, 'Mon-YYYY'),
           v.channel,
           coalesce(v.sales_person, 'Unknown'),
           v.state,
           coalesce(v.city, 'Unknown'),
           v.brand,
           coalesce(v.sub_channel, 'Unknown'),
           i.colour,
           i.product_name,
           sum(i.quantity),
           sum(i.taxable_amount),
           v.fy,
           coalesce(v.customer, '')
    from txn.sales_invoice_item i
    join rpt.v_sales v on v.entry_id = i.entry_id
    where i.colour is not null
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13
"""


def fetch_colours(conn):
    """(colour list, colour rows); both empty before migration 18 is applied.

    Rows keep the ledger's first seven positions so the dashboard filters them
    the same way: 7 colour, 8 product, 9 units, 10 taxable, 11 financial year,
    and 12 customer (the original first twelve positions are unchanged).
    """
    with conn.cursor() as cur:
        cur.execute("select to_regclass('master.product_colour') is not null")
        if not cur.fetchone()[0]:
            return [], []
        cur.execute("select name, hex from master.product_colour order by name")
        colours = [{"name": name, "hex": hex_code} for name, hex_code in cur.fetchall()]
        cur.execute(COLOUR_ROWS_SQL)
        rows = [[r[0], REGION_OF.get(r[1], r[1]), r[2], r[3], r[4], r[5], r[6], r[7], r[8],
                 float(r[9]), round(float(r[10])), int(r[11]), r[12]] for r in cur.fetchall()]
    return colours, rows


# -------------------------------------------------------- product movement

# Keep product sales separate from the parent ledger: joining line items into
# ROWS_SQL would repeat each invoice's totals. No catalog rows or customer
# labels stand in for product sales, and a colour is not required.
PRODUCT_ROWS_SQL = """
    select to_char(v.period_month, 'Mon-YYYY'),
           v.channel,
           coalesce(v.sales_person, 'Unknown'),
           v.state,
           coalesce(v.city, 'Unknown'),
           v.brand,
           coalesce(v.sub_channel, 'Unknown'),
           coalesce(v.week_label, 'Unknown'),
           sum(i.quantity),
           sum(i.taxable_amount),
           sum(i.total_amount),
           v.fy,
           coalesce(v.customer, ''),
           i.product_id,
           i.catalog_key,
           i.product_name,
           i.unit
    from txn.sales_invoice_item i
    join rpt.v_sales v on v.entry_id = i.entry_id
    group by 1, 2, 3, 4, 5, 6, 7, 8, 12, 13, 14, 15, 16, 17
    order by 12, 1, 2, 3, 13, 15, 16, 17
"""

PRODUCT_COVERAGE_SQL = """
    select to_char(v.period_month, 'Mon-YYYY'),
           v.channel,
           coalesce(v.sales_person, 'Unknown'),
           v.state,
           coalesce(v.city, 'Unknown'),
           v.brand,
           coalesce(v.sub_channel, 'Unknown'),
           coalesce(v.week_label, 'Unknown'),
           v.primary_qty,
           v.primary_taxable,
           v.primary_with_tax,
           v.fy,
           coalesce(v.customer, '')
    from rpt.v_sales v
    where exists (select 1 from txn.sales_invoice_item i where i.entry_id = v.entry_id)
    order by v.invoice_date, v.entry_id
"""


def fetch_products(conn):
    """Return (available, product rows, covered ledger rows).

    Product rows preserve ledger indices 0..12, followed by product_id at 13,
    catalog_key at 14, saved product_name at 15, and optional unit at 16.
    Quantities and rupee amounts retain two decimals, without ledger rounding.
    Grouping keeps every header filter, including customer, and each saved name
    snapshot. A frontend may roll up stable product IDs for a ranked view.

    Coverage contains each visible parent entry once, using the same shape and
    rounding as fetch_rows. Filtering both lists identically gives coverage for
    the current selection without counting multi-product invoices repeatedly.
    Historical summary sales have no item detail and are not product sales.
    """
    with conn.cursor() as cur:
        cur.execute("select to_regclass('txn.sales_invoice_item') is not null")
        if not cur.fetchone()[0]:
            return False, [], []
        cur.execute(PRODUCT_ROWS_SQL)
        rows = [[r[0], REGION_OF.get(r[1], r[1]), *r[2:8],
                 float(r[8]), float(r[9]), float(r[10]), int(r[11]), r[12],
                 int(r[13]), r[14], r[15], r[16]] for r in cur.fetchall()]
        cur.execute(PRODUCT_COVERAGE_SQL)
        coverage = _ledger_rows(cur.fetchall())
    return True, rows, coverage


# ------------------------------------------------------------------ bundle

def fetch_supplementary(conn):
    """Return all seven imported datasets, failing closed if migration is incomplete."""
    with conn.cursor() as cur:
        cur.execute('''select dataset_key, payload, source_recorded_date, imported_at,
                             source_file, source_sha256
                      from aux.dashboard_dataset order by dataset_key''')
        records = cur.fetchall()
    expected = {'hist', 'histLabels', 'lfo2', 'terr', 'cat', 'pipeline', 'mtd'}
    data = {key: value for key, value, *_ in records}
    missing = expected - data.keys()
    if missing:
        raise RuntimeError('PostgreSQL supplementary migration is incomplete: ' + ', '.join(sorted(missing)))
    sources = [{'dataset': key, 'recordedDate': recorded.isoformat() if recorded else None,
                'importedAt': imported.isoformat(), 'sourceFile': filename,
                'sourceSha256': digest}
               for key, _, recorded, imported, filename, digest in records]
    return data, sources


def build(log=None):
    """Everything build_data() needs, from one consistent PostgreSQL snapshot."""
    conn = connect()
    try:
        conn.set_session(isolation_level='REPEATABLE READ', readonly=True)
        recs = fetch_rows(conn)
        chan = fetch_channel_targets(conn, log=log)
        primary_retailer_targets = fetch_primary_retailer_targets(conn)
        execs = fetch_exec_targets(conn)
        rotn = fetch_rotn(conn)
        supplementary, supplementary_sources = fetch_supplementary(conn)
        secondary = {key: lfo_data.fetch_dashboard(conn, channel=code)
                     for code, key in SECONDARY_KEYS.items()}
        colours, colour_rows = fetch_colours(conn)
        products_available, product_rows, product_coverage = fetch_products(conn)
        with conn.cursor() as cur:
            cur.execute("select current_database(), "
                        "       (select count(*) from txn.sales_entry where not is_deleted),"
                        "       (select name from tgt.target_plan where status='active'"
                        "         order by fy desc limit 1)")
            dbname, nrows, plan = cur.fetchone()
    finally:
        conn.close()

    # Online Sales uses its own database and snapshot. Its optional failure is
    # contained by the store after the primary snapshot has closed.
    online_sales = online_sales_store.fetch_dashboard()
    meta = {"database": dbname, "ledgerRows": nrows, "activePlan": plan,
            "supplementary": supplementary, "supplementarySources": supplementary_sources,
            "colourList": colours, "colourRows": colour_rows,
            "productSalesAvailable": products_available, "productRows": product_rows,
            "productCoverageRows": product_coverage,
            "onlineSales": online_sales,
            "primaryRetailerTargets": primary_retailer_targets, **secondary}
    if log:
        log("    source: PostgreSQL database '%s' -- %s ledger rows, "
            "active target plan %s" % (dbname, f"{nrows:,}",
                                       repr(plan) if plan else "NONE"))
        if not plan:
            log("    WARNING: no target plan is active, so every target reads zero. "
                "Load one with etl/load_targets.py --activate")
    # Keys match what the sheet path returns, so build_data() can use either.
    return recs, {str(k): v for k, v in chan.items()}, \
           {str(k): v for k, v in execs.items()}, rotn, meta
