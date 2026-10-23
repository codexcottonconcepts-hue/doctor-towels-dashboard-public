"""Load targets from a flat CSV into a named plan.

One row per target. Every dimension column may be left blank, and blank
means 'any value of that dimension'. That is the whole trick: the same file
carries a company target, a channel target, a sub-channel target, a
person-in-a-city target and a channel-in-a-state target without needing a
different shape for each.

    set DATABASE_URL=postgresql://user:pw@host:5432/dstt
    python load_targets.py targets.example.csv --fy 2026 --plan "Fictional demo"
    python load_targets.py your-private-targets.csv --fy 2026 --plan "Your plan" --activate

Columns (header row required, order does not matter):
    period_month   2026-04-01, or 'Apr-2026', or 'Apr'
    metric         primary_taxable (default) | primary_with_tax | primary_qty
                   | secondary_value | tertiary_value
    channel        LFO, D&R-S, L-LFO, D&R-N, Saloon ...   blank = any
    sub_channel    the retailer or distributor code       blank = any
    person         the executive's name                   blank = any
    state          Tamil Nadu, Kerala ...                 blank = any
    district                                              blank = any
    city           Chennai, Trichy ...                    blank = any
    brand          Doctor Towel | Thirsty Towel           blank = any
    target_value   the number
    is_rollup      Y for the rows that add up to the company total, else N
    notes          free text

On is_rollup: targets overlap on purpose. A channel-wide plan and
an executive's city plan can cover the same invoices, so adding every row
together would double count. Flag the one non-overlapping set that should
make up the company total, and leave everything else as a drill-down.

Re-running against the same plan updates the values in place; it does not
create duplicates.

Validation is atomic: unknown or ambiguous dimensions, invalid periods or
values, and conflicting duplicate scopes reject the whole file. Zero is a
valid target. No partial plan is committed or activated.
"""

import argparse
import csv
import os
import sys
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation

try:
    import psycopg2
    import psycopg2.extras
except ImportError as e:
    sys.exit(f"missing dependency: {e}.  pip install -r requirements.txt")

MON = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
METRICS = {'primary_taxable', 'primary_with_tax', 'primary_qty',
           'secondary_value', 'tertiary_value'}


def parse_month(v, fy):
    """Accept 2026-04-01, 01-04-2026, 'Apr-2026' or bare 'Apr'."""
    s = str(v or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%b-%Y", "%b %Y", "%B %Y", "%Y-%m"):
        try:
            d = datetime.strptime(s, fmt).date()
            return d.replace(day=1)
        except ValueError:
            pass
    # Bare month name: place it in the right half of the financial year.
    key = s[:3].lower()
    if key in MON:
        m = MON[key]
        year = fy if m >= 4 else fy + 1
        return datetime(year, m, 1).date()
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_file")
    ap.add_argument("--fy", type=int, required=True, help="2026 means FY 2026-27")
    ap.add_argument("--plan", required=True, help="plan name, e.g. 'FY26-27 Base'")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--activate", action="store_true",
                    help="make this the one active plan for the year")
    ap.add_argument("--replace", action="store_true",
                    help="delete the plan's existing rows first")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.dsn:
        sys.exit("no DATABASE_URL set and no --dsn given")

    conn = psycopg2.connect(args.dsn)
    cur = conn.cursor()

    cur.execute("select start_date, end_date from master.financial_year where fy = %s", (args.fy,))
    year_dates = cur.fetchone()
    if not year_dates:
        sys.exit(f"financial year {args.fy} is not in master.financial_year -- run 02_seed.sql")

    # ---- lookups, by lower(name or code)
    def lookup(sql):
        cur.execute(sql)
        return {r[1]: r[0] for r in cur.fetchall()}

    L = {
        "channel":     lookup("select channel_id, lower(code) from master.channel"),
        "sub_channel": lookup("select sub_channel_id, lower(code) from master.sub_channel"),
        "person":      lookup("select person_id, lower(full_name) from master.sales_person"),
        "state":       lookup("select state_id, lower(name) from master.state"),
        "brand":       lookup("select brand_id, lower(name) from master.brand"),
    }
    # Brands are written both ways in practice.
    cur.execute("select brand_id, lower(code) from master.brand")
    L["brand"].update({r[1]: r[0] for r in cur.fetchall()})
    # Names repeat across states. Retain every candidate, then resolve each
    # row using the state/district supplied in that row; never let a dict's
    # last matching name silently choose a different location.
    cur.execute("select district_id, lower(name), state_id from master.district")
    districts = cur.fetchall()
    cur.execute("select city_id, lower(name), state_id, district_id from master.city")
    cities = cur.fetchall()
    cur.execute("select c.city_id, lower(a.alias), c.state_id, c.district_id"
                " from master.city_alias a join master.city c using (city_id)")
    cities.extend(cur.fetchall())

    # ---- plan
    cur.execute("""insert into tgt.target_plan (fy, name) values (%s, %s)
                   on conflict (fy, lower(name)) do update set name = excluded.name
                   returning plan_id, status""", (args.fy, args.plan))
    plan_id, status = cur.fetchone()

    if args.replace:
        cur.execute("delete from tgt.target where plan_id = %s", (plan_id,))
        print(f"  cleared {cur.rowcount} existing rows from plan '{args.plan}'")

    # ---- rows
    counts = Counter()
    unresolved = Counter()
    payload = []
    seen = {}        # scope key -> the record kept for it
    seen_at = {}     # scope key -> its index in payload

    with open(args.csv_file, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = {(h or '').strip().lower() for h in (reader.fieldnames or [])}
        if not {'period_month', 'target_value'} <= fields or len(fields) != len(reader.fieldnames or []):
            conn.rollback()
            conn.close()
            sys.exit('Target CSV requires unique headers including period_month and target_value; nothing committed.')
        for n, row in enumerate(reader, start=2):
            if None in row:
                unresolved[f"row {n}: more values than column headers"] += 1
                continue
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            if not any(row.values()):
                counts['blank_skipped'] += 1
                continue

            month = parse_month(row.get("period_month"), args.fy)
            if month is None:
                unresolved[f"row {n}: unreadable month {row.get('period_month')!r}"] += 1
                counts["bad_month"] += 1
                continue
            if not year_dates[0] <= month <= year_dates[1]:
                unresolved[f"row {n}: month {month} is outside FY {args.fy}"] += 1
                continue

            metric = row.get('metric') or 'primary_taxable'
            if metric not in METRICS:
                unresolved[f"row {n}: unsupported metric {metric!r}"] += 1
                continue
            rollup = row.get('is_rollup', '').upper()
            if rollup not in ('', 'Y', 'YES', 'TRUE', '1', 'N', 'NO', 'FALSE', '0'):
                unresolved[f"row {n}: is_rollup must be Y or N"] += 1
                continue

            try:
                value = Decimal(row.get("target_value", "").replace(",", ""))
            except InvalidOperation:
                unresolved[f"row {n}: invalid value {row.get('target_value')!r}"] += 1
                counts["bad_value"] += 1
                continue
            if not value.is_finite() or value < 0 or value > Decimal('999999999999.99'):
                unresolved[f"row {n}: target value must be a finite nonnegative amount within numeric(14,2)"] += 1
                continue
            if value != value.quantize(Decimal('0.01')):
                unresolved[f"row {n}: target value has more than two decimal places"] += 1
                continue

            ids, bad = {}, False
            for dim in ("channel", "sub_channel", "person", "state", "brand"):
                name = row.get(dim, "")
                if not name:
                    ids[dim] = None
                    continue
                key = name.lower()
                if key not in L[dim]:
                    unresolved[f"row {n}: unknown {dim}: {name}"] += 1
                    bad = True
                    break
                ids[dim] = L[dim][key]
            if bad:
                counts["unresolved"] += 1
                continue

            district_name, city_name = row.get('district', '').lower(), row.get('city', '').lower()
            d_candidates = [d for d in districts if d[1] == district_name
                            and (ids['state'] is None or d[2] == ids['state'])]
            c_candidates = {c[0]: c for c in cities if c[1] == city_name
                            and (ids['state'] is None or c[2] == ids['state'])
                            and (not district_name or c[3] in {d[0] for d in d_candidates})}
            if district_name and city_name:
                d_candidates = [d for d in d_candidates
                                if d[0] in {c[3] for c in c_candidates.values()}]
            if district_name and len(d_candidates) != 1:
                unresolved[f"row {n}: unknown, inconsistent or ambiguous district {row['district']!r}; supply its state"] += 1
                bad = True
            if city_name and len(c_candidates) != 1:
                unresolved[f"row {n}: unknown, inconsistent or ambiguous city {row['city']!r}; supply state/district"] += 1
                bad = True
            if bad:
                counts['unresolved'] += 1
                continue
            ids['district'] = d_candidates[0][0] if district_name else None
            ids['city'] = next(iter(c_candidates)) if city_name else None

            rec = (
                plan_id, month, metric,
                ids["channel"], ids["sub_channel"], ids["person"],
                ids["state"], ids["district"], ids["city"], ids["brand"],
                value,
                rollup in ('Y', 'YES', 'TRUE', '1'),
                row.get("notes") or None,
            )
            # Two rows describing the same scope in one file would make
            # Postgres reject the whole statement ('cannot affect row a second
            # time'), so exact duplicates are merged and conflicting values
            # are rejected. The legacy workbook did this a lot -- its
            # LFO, D&R-N and Saloon tabs repeat the Main File columns exactly.
            key = rec[1:10]
            if key in seen:
                prev = seen[key]
                if prev[10] != value:
                    unresolved[f"row {n}: duplicate scope has conflicting target values"] += 1
                else:
                    counts["duplicate_scope"] += 1
                # Identical values merge with is_rollup OR-ed rather than
                # overwritten. One tab of the legacy workbook flags a scope as
                # part of the company total and another repeats the same scope
                # without the flag; letting the second silently clear it drops
                # whole channels out of the company number.
                rec = rec[:11] + (prev[11] or rec[11],) + rec[12:]
                seen[key] = rec
                payload[seen_at[key]] = rec
            else:
                seen[key] = rec
                seen_at[key] = len(payload)
                payload.append(rec)
                counts["ok"] += 1

    if unresolved:
        conn.rollback()
        cur.close()
        conn.close()
        print('\nTARGET IMPORT REJECTED — no targets or plan status changes were committed.')
        for issue, count in sorted(unresolved.items()):
            print(f'  {count:>5}  {issue}')
        sys.exit(1)
    if not payload:
        conn.rollback()
        cur.close()
        conn.close()
        sys.exit('Target CSV contains no target rows; no plan was created, replaced or activated.')

    sql = """
        insert into tgt.target
            (plan_id, period_month, metric, channel_id, sub_channel_id, person_id,
             state_id, district_id, city_id, brand_id, target_value, is_rollup, notes)
        values %s
        on conflict (plan_id, period_month, metric,
                     coalesce(channel_id,0), coalesce(sub_channel_id,0), coalesce(person_id,0),
                     coalesce(state_id,0), coalesce(district_id,0), coalesce(city_id,0),
                     coalesce(brand_id,0))
        do update set target_value = excluded.target_value,
                      is_rollup    = excluded.is_rollup,
                      notes        = excluded.notes,
                      updated_by   = current_user,
                      updated_at   = now()
    """
    psycopg2.extras.execute_values(cur, sql, payload, page_size=500)

    if args.activate:
        cur.execute("update tgt.target_plan set status = 'archived'"
                    " where fy = %s and plan_id <> %s and status = 'active'", (args.fy, plan_id))
        cur.execute("update tgt.target_plan set status = 'active' where plan_id = %s", (plan_id,))

    # ---- report
    cur.execute("""select count(*), coalesce(sum(target_value),0),
                          coalesce(sum(target_value) filter (where is_rollup), 0)
                   from tgt.target where plan_id = %s""", (plan_id,))
    n_rows, total, rollup_total = cur.fetchone()

    w = sys.stdout.write
    w("\n" + "=" * 68 + "\n")
    w(f"  {'DRY RUN -- nothing written' if args.dry_run else 'TARGETS LOADED'}"
      f"   plan '{args.plan}'  FY {args.fy}-{str(args.fy+1)[2:]}\n")
    w("=" * 68 + "\n\n")
    w(f"  rows accepted        {counts['ok']:>8,}\n")
    w(f"  blank rows skipped   {counts['blank_skipped']:>8,}\n")
    w(f"  repeated scope       {counts['duplicate_scope']:>8,}  (same scope, same value)\n")
    w(f"  unresolved names     {counts['unresolved']:>8,}\n")
    w(f"  unreadable month     {counts['bad_month']:>8,}\n")
    w(f"\n  plan now holds       {n_rows:>8,}  target rows\n")
    w(f"  sum of all targets   {total:>14,.0f}   (overlapping -- do not quote this)\n")
    w(f"  company total        {rollup_total:>14,.0f}   (is_rollup rows only)\n")

    if args.activate and not args.dry_run:
        w(f"\n  plan '{args.plan}' is now the ACTIVE plan for FY {args.fy}.\n")

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
