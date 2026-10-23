"""Self-test for the DST & TT database.

Checks the invariants the dashboard depends on. Run it after any migration,
after a bulk load, and on the server after a deploy.

    set DATABASE_URL=postgresql://user:pw@host:5432/dstt
    python selftest.py
    python selftest.py -v        # show every test, not just failures

Everything it writes happens inside a transaction that is rolled back, so it
is safe against the live database. It never modifies real rows.

Exit code is 0 when everything passes, 1 otherwise -- so a deploy script can
stop on it.
"""

import os
import sys
import traceback
from datetime import date
from uuid import uuid4

try:
    import psycopg2
except ImportError:
    sys.exit("psycopg2 is not installed.  pip install -r etl/requirements.txt")

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv

PASS, FAIL, INFO = [], [], []


def check(name, got, want, note=""):
    if got == want:
        PASS.append(name)
        if VERBOSE:
            print(f"  ok    {name}")
    else:
        FAIL.append((name, got, want, note))
        print(f"  FAIL  {name}\n          got  {got!r}\n          want {want!r}"
              + (f"\n          {note}" if note else ""))


def check_true(name, got, note=""):
    check(name, bool(got), True, note)


def raises(cur, sql, args=None):
    """True when the statement is refused. Each attempt gets its own savepoint,
    because a failed statement poisons the transaction until one is released."""
    cur.execute("savepoint s")
    try:
        cur.execute(sql, args or ())
    except psycopg2.Error:
        cur.execute("rollback to savepoint s")
        return True
    cur.execute("rollback to savepoint s")
    return False


# =====================================================================
#  reference ids, so the tests read as sentences
# =====================================================================

def ids(cur):
    """Read seeded dimensions and create isolated rollback-only fixtures."""
    def one(sql, *a):
        cur.execute(sql, a)
        r = cur.fetchone()
        return r[0] if r else None
    fixture = {
        "tag": "SELFTEST/" + uuid4().hex,
        "chan_drs":  one("select channel_id from master.channel where lower(code)='d&r-s'"),
        "chan_lfo":  one("select channel_id from master.channel where lower(code)='lfo'"),
        "chan_sal":  one("select channel_id from master.channel where lower(code)='saloon'"),
        "brand_dt":  one("select brand_id from master.brand where lower(code)='dt'"),
        "brand_tt":  one("select brand_id from master.brand where lower(code)='tt'"),
        "state_tn":  one("select state_id from master.state where lower(name)='tamil nadu'"),
        "state_kl":  one("select state_id from master.state where lower(name)='kerala'"),
        "courier":   one("select courier_id from master.courier where lower(code)='dtdc'"),
    }
    tag = fixture['tag']
    district = one("insert into master.district (name, state_id) values (%s,%s)"
                   " returning district_id", tag + '/district', fixture['state_tn'])
    for key in ('city_chn', 'city_mdu'):
        fixture[key] = one("insert into master.city (name, state_id, district_id)"
                           " values (%s,%s,%s) returning city_id", tag + '/' + key,
                           fixture['state_tn'], district)
    fixture['cust'] = one("insert into master.customer (name) values (%s) returning customer_id",
                          tag + '/customer')
    for key in ('person', 'person2'):
        fixture[key] = one("insert into master.sales_person (full_name) values (%s)"
                           " returning person_id", tag + '/' + key)
    fixture['plan'] = one("insert into tgt.target_plan (fy, name, status) values (2026,%s,'draft')"
                          " returning plan_id", tag + '/plan')
    return fixture


def mkentry(cur, I, **kw):
    """Insert one sales row, returning its id. Sensible defaults for everything."""
    row = dict(invoice_date=date(2026, 5, 12), invoice_no=None, line_no=1,
               customer_id=I["cust"], channel_id=I["chan_drs"], sub_channel_id=None,
               person_id=I["person"], brand_id=I["brand_dt"],
               city_id=I["city_chn"], state_id=I["state_tn"], district_id=None,
               order_type="Re-order", primary_qty=1, primary_taxable=1000,
               primary_with_tax=1050)
    row.update(kw)
    cols = ", ".join(row)
    vals = ", ".join("%%(%s)s" % k for k in row)
    cur.execute(f"insert into txn.sales_entry ({cols}) values ({vals}) returning entry_id", row)
    return cur.fetchone()[0]


# =====================================================================
#  the tests
# =====================================================================

def test_reference_data(cur, I):
    for k in ("chan_drs", "chan_lfo", "brand_dt", "brand_tt", "state_tn", "courier"):
        check_true(f"reference data present: {k}", I[k] is not None,
                   "run sql/02_seed.sql")
    cur.execute("select count(*) from master.financial_year where is_current")
    check("exactly one current financial year", cur.fetchone()[0], 1)
    cur.execute("select count(*) from master.channel")
    check_true("channels seeded", cur.fetchone()[0] >= 5)


def test_duplicate_invoice(cur, I):
    invoice = I['tag'] + '/DUP/1'
    mkentry(cur, I, invoice_no=invoice, line_no=1)
    check_true("same invoice + brand + line is refused",
               raises(cur, "insert into txn.sales_entry (invoice_date, invoice_no, line_no,"
                           " customer_id, channel_id, brand_id, city_id, state_id,"
                           " primary_taxable) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (date(2026, 5, 12), invoice, 1, I["cust"], I["chan_drs"],
                       I["brand_dt"], I["city_chn"], I["state_tn"], 500)),
               "this is the guard a spreadsheet cannot give you")

    # A genuine second SKU on the same bill must still go in.
    mkentry(cur, I, invoice_no=invoice, line_no=2, primary_taxable=500)
    cur.execute("select count(*) from txn.sales_entry where invoice_no=%s", (invoice,))
    check("a second line on the same invoice is accepted", cur.fetchone()[0], 2,
          "28 real invoices in the sheet carry two lines")

    # Case must not create a loophole.
    check_true("invoice number match ignores case",
               raises(cur, "insert into txn.sales_entry (invoice_date, invoice_no, line_no,"
                           " customer_id, channel_id, brand_id, city_id, state_id,"
                           " primary_taxable) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (date(2026, 5, 12), invoice.lower(), 1, I["cust"], I["chan_drs"],
                       I["brand_dt"], I["city_chn"], I["state_tn"], 500)))

    # The same number for the other brand is a different line and is fine.
    mkentry(cur, I, invoice_no=invoice, brand_id=I["brand_tt"])
    check_true("same invoice, other brand, is accepted", True)


def test_geography_trigger(cur, I):
    # State is derived from the city, never taken from the caller. Pass a
    # deliberately wrong state and it must be corrected, not stored.
    e = mkentry(cur, I, city_id=I["city_chn"], state_id=I["state_kl"])
    cur.execute("select state_id from txn.sales_entry where entry_id=%s", (e,))
    check("state is overwritten from the city", cur.fetchone()[0], I["state_tn"],
          "this is what stops 'Aurangabad' being filed as a state")

    cur.execute("select district_id from txn.sales_entry where entry_id=%s", (e,))
    cur.execute("select district_id from master.city where city_id=%s", (I["city_chn"],))
    want = cur.fetchone()[0]
    cur.execute("select district_id from txn.sales_entry where entry_id=%s", (e,))
    check("district is derived from the city", cur.fetchone()[0], want)

    check_true("a city that does not exist is refused",
               raises(cur, "insert into txn.sales_entry (invoice_date, customer_id,"
                           " channel_id, brand_id, city_id, state_id, primary_taxable)"
                           " values (%s,%s,%s,%s,%s,%s,%s)",
                      (date(2026, 5, 1), I["cust"], I["chan_drs"], I["brand_dt"],
                       -999, I["state_tn"], 100)))


def test_financial_year(cur, I):
    for d, want in ((date(2026, 4, 1), 2026), (date(2026, 12, 31), 2026),
                    (date(2027, 1, 1), 2026), (date(2027, 3, 31), 2026),
                    (date(2027, 4, 1), 2027)):
        e = mkentry(cur, I, invoice_date=d)
        cur.execute("select fy, period_month, week_no from txn.sales_entry where entry_id=%s", (e,))
        fy, pm, wk = cur.fetchone()
        check(f"FY of {d} is April-March", fy, want)
        check(f"period_month of {d}", pm, d.replace(day=1))

    for d, want in ((date(2026, 5, 1), 1), (date(2026, 5, 7), 1), (date(2026, 5, 8), 2),
                    (date(2026, 5, 22), 4), (date(2026, 5, 29), 5), (date(2026, 5, 31), 5)):
        e = mkentry(cur, I, invoice_date=d)
        cur.execute("select week_no from txn.sales_entry where entry_id=%s", (e,))
        check(f"week number of {d}", cur.fetchone()[0], want)

    # The label is what the dashboard shows; 'Week IIIIV' was a real bug here.
    cur.execute("select distinct week_label from rpt.v_sales order by 1")
    labels = [r[0] for r in cur.fetchall()]
    check_true("week labels are Week I..V only",
               set(labels) <= {"Week I", "Week II", "Week III", "Week IV", "Week V"},
               f"saw {labels}")


def test_constraints(cur, I):
    check_true("a negative value is refused",
               raises(cur, "insert into txn.sales_entry (invoice_date, customer_id,"
                           " channel_id, brand_id, state_id, primary_taxable)"
                           " values (%s,%s,%s,%s,%s,%s)",
                      (date(2026, 5, 1), I["cust"], I["chan_drs"], I["brand_dt"],
                       I["state_tn"], -1)))
    check_true("payment before dispatch is refused",
               raises(cur, "insert into txn.sales_entry (invoice_date, customer_id,"
                           " channel_id, brand_id, state_id, stock_supplied_on,"
                           " payment_received_on) values (%s,%s,%s,%s,%s,%s,%s)",
                      (date(2026, 5, 1), I["cust"], I["chan_drs"], I["brand_dt"],
                       I["state_tn"], date(2026, 5, 10), date(2026, 5, 2))))
    check_true("an unknown order type is refused",
               raises(cur, "insert into txn.sales_entry (invoice_date, customer_id,"
                           " channel_id, brand_id, state_id, order_type)"
                           " values (%s,%s,%s,%s,%s,%s)",
                      (date(2026, 5, 1), I["cust"], I["chan_drs"], I["brand_dt"],
                       I["state_tn"], "Maybe")))
    check_true("a target on a day other than the 1st is refused",
               raises(cur, "insert into tgt.target (plan_id, period_month, target_value)"
                           " values (%s, %s, 1)",
                      (I['plan'], date(2026, 5, 15))))


def test_soft_delete(cur, I):
    e = mkentry(cur, I, invoice_no=I['tag'] + '/SOFT/1', primary_taxable=777777)
    cur.execute("select count(*) from rpt.v_sales where entry_id=%s", (e,))
    check("a new row appears in the ledger view", cur.fetchone()[0], 1)
    cur.execute("update txn.sales_entry set is_deleted=true where entry_id=%s", (e,))
    cur.execute("select count(*) from rpt.v_sales where entry_id=%s", (e,))
    check("a soft-deleted row leaves the ledger view", cur.fetchone()[0], 0)
    cur.execute("select count(*) from txn.sales_entry where entry_id=%s", (e,))
    check("but the row itself is still there", cur.fetchone()[0], 1,
          "nothing is ever really removed")
    # And the number is now free to re-use, which is how a correction works.
    mkentry(cur, I, invoice_no=I['tag'] + '/SOFT/1')
    check_true("the invoice number can be re-entered after a soft delete", True)


def test_audit(cur, I):
    e = mkentry(cur, I, invoice_no=I['tag'] + '/AUD/1', primary_taxable=1234)
    cur.execute("select action from sec.audit_log where table_name='txn.sales_entry'"
                " and row_pk=%s", (str(e),))
    check("insert is audited", [r[0] for r in cur.fetchall()], ["INSERT"])

    cur.execute("update txn.sales_entry set primary_taxable=4321 where entry_id=%s", (e,))
    cur.execute("select old_row->>'primary_taxable', new_row->>'primary_taxable'"
                " from sec.audit_log where table_name='txn.sales_entry' and row_pk=%s"
                " and action='UPDATE'", (str(e),))
    old, new = cur.fetchone()
    check("update records the value before and after", (float(old), float(new)),
          (1234.0, 4321.0), "'who changed this number' has an answer")

    cur.execute("select updated_by is not null, updated_at is not null"
                " from txn.sales_entry where entry_id=%s", (e,))
    check("update stamps who and when", cur.fetchone(), (True, True))

    cur.execute("select current_setting('app.username', true)")
    previous_actor = cur.fetchone()[0] or ""
    cur.execute("select set_config('app.username', 'entry-audit-test', true)")
    web_entry = mkentry(cur, I)
    cur.execute("select created_by from txn.sales_entry where entry_id=%s", (web_entry,))
    check("web entry identifies the logged-in user", cur.fetchone()[0], "entry-audit-test")
    cur.execute("select changed_by from sec.audit_log where table_name='txn.sales_entry'"
                " and row_pk=%s", (str(web_entry),))
    check("audit identifies the logged-in user", cur.fetchone()[0], "entry-audit-test")
    cur.execute("select set_config('app.username', '', true)")
    fallback_entry = mkentry(cur, I)
    cur.execute("select created_by = current_user from txn.sales_entry where entry_id=%s",
                (fallback_entry,))
    check("non-web entries fall back to the database user", cur.fetchone()[0], True)
    cur.execute("select set_config('app.username', %s, true)", (previous_actor,))


def test_web_entry_integrity(cur, I):
    plan = I['plan']
    target_sql = ("insert into tgt.target (plan_id, period_month, target_value, state_id, city_id)"
                  " values (%s, %s, 100, %s, %s)")
    check_true("targets outside the plan year are refused", raises(
        cur, target_sql, (plan, date(2027, 4, 1), None, None)))
    check_true("target city and state must agree", raises(
        cur, target_sql, (plan, date(2026, 5, 1), I['state_kl'], I['city_chn'])))
    cur.execute("insert into master.district (state_id, name) values (%s, %s)"
                " returning district_id", (I['state_kl'], I['tag'] + '/district_kl'))
    district = cur.fetchone()[0]
    check_true("target district and state must agree", raises(cur,
        "insert into tgt.target (plan_id, period_month, target_value, state_id, district_id)"
        " values (%s, '2026-05-01', 100, %s, %s)", (plan, I['state_tn'], district)))
    check_true("target city and district must agree", raises(cur,
        "insert into tgt.target (plan_id, period_month, target_value, city_id, district_id)"
        " values (%s, '2026-05-01', 100, %s, %s)", (plan, I['city_chn'], district)))
    check_true("master city and district must agree", raises(cur,
        "insert into master.city (name, state_id, district_id) values ('__selftest_city', %s, %s)",
        (I['state_tn'], district)))
    check_true("sales without a city still require a consistent district", raises(cur,
        "insert into txn.sales_entry (invoice_date, customer_id, channel_id, brand_id, state_id, district_id)"
        " values ('2026-05-01', %s, %s, %s, %s, %s)",
        (I['cust'], I['chan_drs'], I['brand_dt'], I['state_tn'], district)))
    cur.execute("select set_config('app.username', 'target-audit-test', true)")
    cur.execute(target_sql + " returning target_id, created_by",
                (plan, date(2026, 5, 1), I['state_tn'], I['city_chn']))
    target_id, created_by = cur.fetchone()
    check("target identifies the logged-in user", created_by, 'target-audit-test')
    cur.execute("select changed_by from sec.audit_log where table_name='tgt.target' and row_pk=%s",
                (str(target_id),))
    check("target audit identifies the logged-in user", cur.fetchone()[0], 'target-audit-test')
    check_true("changing a plan year cannot strand existing targets", raises(cur,
        "update tgt.target_plan set fy=2027 where plan_id=%s", (plan,)))
    cur.execute("insert into tgt.target (plan_id, period_month, target_value, channel_id, sub_channel_id)"
                " select %s, '2026-05-01', 100, %s, sub_channel_id from master.sub_channel"
                " where lower(code)='lulu' returning target_id", (plan, I['chan_drs']))
    check_true("sub-channel may bill under a different usual channel", cur.fetchone() is not None)
    cur.execute("select code from master.courier where code in ('DTDC', 'OWN', 'RK', 'RKB')")
    check("new courier choices preserve historical RKB separately", sorted(r[0] for r in cur.fetchall()),
          ['DTDC', 'OWN', 'RK', 'RKB'])
    cur.execute("select has_schema_privilege('dstt_etl', 'sec', 'USAGE'),"
                " has_table_privilege('dstt_etl', 'sec.audit_log', 'INSERT'),"
                " has_sequence_privilege('dstt_etl', 'sec.audit_log_audit_id_seq', 'USAGE'),"
                " has_table_privilege('dstt_etl', 'tgt.target_plan', 'SELECT'),"
                " has_table_privilege('dstt_etl', 'tgt.target_plan', 'UPDATE')")
    check("ETL has target-plan and append-audit permissions", cur.fetchone(), (True,) * 5)


def test_target_engine(cur, I):
    """NULL on a target means 'any'. Prove it dimension by dimension."""
    # A scratch plan of its own. Borrowing the active one would collide with
    # the real targets already in it -- which is the unique constraint doing
    # its job, not a failure.
    plan = I['plan']
    m = date(2026, 5, 1)

    # Two sales, differing only in channel and city.
    mkentry(cur, I, invoice_date=m, channel_id=I["chan_drs"], city_id=I["city_chn"],
            person_id=I["person"], primary_taxable=100000, invoice_no=I['tag'] + '/ENG/1',
            primary_qty=3, primary_with_tax=105000, secondary_value=4000, tertiary_value=2000)
    mkentry(cur, I, invoice_date=m, channel_id=I["chan_lfo"], city_id=I["city_mdu"],
            person_id=I["person"], primary_taxable=300000, invoice_no=I['tag'] + '/ENG/2')

    def achieved(**scope):
        cols = ["plan_id", "period_month", "target_value"]
        vals = [plan, m, 1]
        for k, v in scope.items():
            cols.append(k)
            vals.append(v)
        cur.execute("savepoint t")
        cur.execute(f"insert into tgt.target ({', '.join(cols)}) values"
                    f" ({', '.join(['%s'] * len(vals))}) returning target_id", vals)
        tid = cur.fetchone()[0]
        cur.execute("select achieved from rpt.v_target_achievement where target_id=%s", (tid,))
        got = float(cur.fetchone()[0])
        cur.execute("rollback to savepoint t")
        return got

    def direct(**scope):
        """The same figure computed by hand. The engine's lateral join has to
        agree with a plain aggregate carrying the same filters -- checking
        against a constant would only work on an empty database."""
        where, args = ["period_month = %s", "not is_deleted"], [m]
        for k, v in scope.items():
            where.append(f"{k} = %s")
            args.append(v)
        cur.execute("select coalesce(sum(primary_taxable), 0) from txn.sales_entry"
                    " where " + " and ".join(where), args)
        return float(cur.fetchone()[0])

    scopes = [
        ("no dimensions matches every sale that month", {}),
        ("channel", {"channel_id": I["chan_lfo"]}),
        ("channel (the other one)", {"channel_id": I["chan_drs"]}),
        ("channel + city", {"channel_id": I["chan_drs"], "city_id": I["city_mdu"]}),
        ("channel + city (matching)", {"channel_id": I["chan_lfo"], "city_id": I["city_mdu"]}),
        ("person", {"person_id": I["person"]}),
        ("person + city", {"person_id": I["person"], "city_id": I["city_chn"]}),
        ("state", {"state_id": I["state_tn"]}),
        ("state (a quieter one)", {"state_id": I["state_kl"]}),
        ("brand", {"brand_id": I["brand_dt"]}),
        ("brand (the other one)", {"brand_id": I["brand_tt"]}),
        ("channel + state + brand", {"channel_id": I["chan_drs"],
                                     "state_id": I["state_tn"],
                                     "brand_id": I["brand_dt"]}),
    ]
    for label, scope in scopes:
        check(f"target scope agrees with a hand-written sum: {label}",
              achieved(**scope), direct(**scope), "NULL means any")

    # Each measure is matched against its own ledger column, including the
    # quantity measure; comparing taxable sales to every target is incorrect.
    for metric in ('primary_taxable', 'primary_with_tax', 'primary_qty',
                   'secondary_value', 'tertiary_value'):
        cur.execute(f"select coalesce(sum({metric}),0) from txn.sales_entry"
                    " where not is_deleted and period_month=%s", (m,))
        expected = float(cur.fetchone()[0])
        check(f"target measure uses the correct sales column: {metric}",
              achieved(metric=metric), expected)

    # And the whole point: a narrower scope can never exceed a wider one.
    check_true("narrowing a scope can only reduce the achievement",
               achieved(channel_id=I["chan_drs"], city_id=I["city_chn"])
               <= achieved(channel_id=I["chan_drs"]) <= achieved())

    # Arithmetic on the view itself.
    cur.execute("insert into tgt.target (plan_id, period_month, target_value, person_id)"
                " values (%s,%s,200000,%s) returning target_id", (plan, m, I['person']))
    target_id = cur.fetchone()[0]
    cur.execute("""select target_value, achieved, variance, achieved_pct
                   from rpt.v_target_achievement where target_id=%s""", (target_id,))
    r = cur.fetchone()
    if r:
        t, a, v, p = (float(x) for x in r)
        check("variance is achieved minus target", round(v, 2), round(a - t, 2))
        check("percentage is achieved over target", round(p, 1),
              round(a * 100.0 / t, 1))


def test_rollup(cur, I):
    """The company total sums only the rows flagged is_rollup."""
    plan = I['plan']
    cur.execute("""insert into tgt.target
                   (plan_id,period_month,target_value,channel_id,person_id,is_rollup) values
                   (%s,'2026-05-01',1000,%s,null,true),
                   (%s,'2026-05-01',2000,%s,null,true),
                   (%s,'2026-05-01',500,null,%s,false)""",
                (plan, I['chan_drs'], plan, I['chan_lfo'], plan, I['person']))

    cur.execute("""select coalesce(sum(target_value) filter (where is_rollup), 0),
                          coalesce(sum(target_value), 0)
                   from tgt.target where plan_id=%s and metric='primary_taxable'""",
                (plan,))
    roll, everything = (float(x) for x in cur.fetchone())
    check("fixture roll-up excludes overlapping person targets", (roll, everything),
          (3000.0, 3500.0))

    cur.execute("""select coalesce(sum(target_value),0) from rpt.v_company_month
                   where plan_id=%s""", (plan,))
    check("v_company_month sums exactly the roll-up rows",
          float(cur.fetchone()[0]), roll)

    # Other plans may legitimately use a company-level or geographic rollup.
    # A single company row is another valid non-overlapping rollup set.
    cur.execute("update tgt.target set is_rollup=false where plan_id=%s", (plan,))
    cur.execute("insert into tgt.target (plan_id,period_month,target_value,is_rollup)"
                " values (%s,'2026-05-01',4000,true)", (plan,))
    cur.execute("select target_value from rpt.v_company_month"
                " where plan_id=%s and period_month='2026-05-01'", (plan,))
    check("company-level rollup is supported", float(cur.fetchone()[0]), 4000.0)


def test_ytd(cur, I):
    cur.execute("""insert into tgt.target (plan_id,period_month,target_value,person_id) values
                   (%s,'2026-04-01',1000,%s),(%s,'2026-05-01',2000,%s)""",
                (I['plan'], I['person'], I['plan'], I['person']))
    mkentry(cur, I, invoice_date=date(2026, 4, 12), primary_taxable=400)
    mkentry(cur, I, invoice_date=date(2026, 5, 12), primary_taxable=700)
    cur.execute("""select achieved_ytd, target_ytd
                   from rpt.v_target_achievement_ytd
                   where plan_id=%s and period_month='2026-05-01'""", (I['plan'],))
    check("year-to-date combines both fixture months", cur.fetchone(), (1100, 3000))


def test_courier(cur, I):
    e = mkentry(cur, I, invoice_no=I['tag'] + '/COU/1', stock_supplied_on=date(2026, 5, 13),
                courier_id=I["courier"], courier_docket_no="7712345678901")
    cur.execute("select courier, courier_docket_no, tracking_url, dispatch_status"
                " from rpt.v_dispatch where entry_id=%s", (e,))
    cour, dock, url, status = cur.fetchone()
    check("courier resolves to its code", cour, "DTDC")
    check("dispatch status", status, "Dispatched")
    check_true("the docket becomes a tracking link", url and dock in url,
               f"got {url!r}")

    e2 = mkentry(cur, I, invoice_no=I['tag'] + '/COU/2', stock_supplied_on=date(2026, 5, 13))
    cur.execute("select dispatch_status from rpt.v_dispatch where entry_id=%s", (e2,))
    check("dispatched with no courier is flagged", cur.fetchone()[0], "Courier not recorded")

    e3 = mkentry(cur, I, invoice_no=I['tag'] + '/COU/3', courier_id=I["courier"],
                 courier_docket_no="999")
    cur.execute("select dispatch_status from rpt.v_dispatch where entry_id=%s", (e3,))
    check("a docket with no dispatch date is flagged", cur.fetchone()[0], "Not dispatched")

    cur.execute("select count(*) from rpt.v_data_quality"
                " where issue='Docket but no dispatch date' and entry_id=%s", (e3,))
    check("and the quality view lists it", cur.fetchone()[0], 1)


def test_views_present(cur, I):
    cur.execute("select table_name from information_schema.views"
                " where table_schema='rpt' order by 1")
    have = {r[0] for r in cur.fetchall()}
    want = {"v_sales", "v_target", "v_target_achievement", "v_target_achievement_ytd",
            "v_company_month", "v_month_by_channel", "v_month_by_sub_channel",
            "v_month_by_person", "v_month_by_geo", "v_month_by_brand",
            "v_customer_activity", "v_receivables", "v_dispatch",
            "v_month_by_courier", "v_person_territory", "v_data_quality"}
    check("every reporting view exists", sorted(want - have), [])
    for v in sorted(want & have):
        cur.execute(f"select count(*) from rpt.{v}")          # must at least run
    check_true("every reporting view executes", True)


# =====================================================================

def headline(cur):
    """Figures to eyeball, not assertions -- they change as data is added."""
    cur.execute("select count(*), coalesce(sum(primary_taxable),0),"
                " coalesce(sum(primary_with_tax),0) from txn.sales_entry where not is_deleted")
    n, tax, wt = cur.fetchone()
    INFO.append(f"ledger rows            {n:>12,}")
    INFO.append(f"taxable value          {float(tax):>12,.0f}")
    INFO.append(f"value with tax         {float(wt):>12,.0f}")
    cur.execute("select name, fy from tgt.target_plan where status='active'")
    r = cur.fetchone()
    INFO.append(f"active plan            {r[0]!r} for FY{r[1]}" if r
                else "active plan            NONE -- every target reads zero")
    cur.execute("""select coalesce(sum(target_value),0) from tgt.target t
                   join tgt.target_plan p using (plan_id)
                   where p.status='active' and t.is_rollup and t.metric='primary_taxable'""")
    INFO.append(f"company target (year)  {float(cur.fetchone()[0]):>12,.0f}")
    cur.execute("""select p.name from tgt.target_plan p where p.status='active'
                   and not exists (select 1 from tgt.target t where t.plan_id=p.plan_id
                                   and t.is_rollup and t.metric='primary_taxable')""")
    for (name,) in cur.fetchall():
        INFO.append(f"  plan {name!r} has no taxable company-rollup targets; configure if needed")
    cur.execute("select issue, count(*) from rpt.v_data_quality group by 1 order by 2 desc")
    for issue, c in cur.fetchall():
        INFO.append(f"  data quality: {issue:<34} {c:>5}")


def main():
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        sys.exit("DATABASE_URL is not set.")

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()

    print("\n" + "=" * 70)
    print("  DST & TT database self-test")
    print("=" * 70 + "\n")

    try:
        headline(cur)
        I = ids(cur)
        for fn in (test_reference_data, test_views_present, test_duplicate_invoice,
                   test_geography_trigger, test_financial_year, test_constraints,
                   test_soft_delete, test_audit, test_target_engine, test_rollup,
                   test_ytd, test_courier, test_web_entry_integrity):
            cur.execute("savepoint tc")
            try:
                fn(cur, I)
            except Exception:
                FAIL.append((fn.__name__, "raised", "clean run", ""))
                print(f"  FAIL  {fn.__name__} raised:")
                print("        " + traceback.format_exc().replace("\n", "\n        ")[:1200])
                cur.execute("rollback to savepoint tc")
            else:
                cur.execute("rollback to savepoint tc")
    finally:
        conn.rollback()          # nothing this script did is kept
        cur.close()
        conn.close()

    if INFO:
        print("\n  " + "-" * 60)
        for line in INFO:
            print("  " + line)

    print("\n" + "=" * 70)
    print(f"  {len(PASS)} passed, {len(FAIL)} failed")
    print("=" * 70 + "\n")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
