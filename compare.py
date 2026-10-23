"""Compare PostgreSQL with an explicitly selected LOCAL workbook export.

  python compare.py --workbook path/to/approved-export.xlsx

This offline reconciliation never connects to Google Sheets or changes the
application source. The workbook must be selected deliberately; old local
exports are not assumed to represent the current database.
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def build(source, workbook=None):
    """Read PostgreSQL or a local export; no automatic or remote source switch."""
    import refresh
    if source == "postgres":
        data, _ = refresh.build_data(log=None)
        return data
    if source != "workbook" or not workbook:
        raise ValueError("Google Sheets is disconnected. Select a local workbook explicitly.")
    import openpyxl
    wb = openpyxl.load_workbook(workbook, data_only=True)
    try:
        rows, _, _ = refresh.parse_ledger(wb)
        if not rows:
            raise ValueError("Selected workbook has no usable sales rows.")
        fys = sorted({row[11] for row in rows})
        return {"source": "local-workbook", "rows": rows, "fys": list(map(str, fys)),
                "targetsByFY": {str(k): v for k, v in refresh.parse_channel_targets(wb, fys[-1]).items()},
                "execByFY": {str(k): v for k, v in refresh.parse_exec_targets(wb, fys[-1]).items()}}
    finally:
        wb.close()


def totals(data, fy):
    """Taxable, qty and row count per month, and per channel, for one year."""
    rows = [r for r in data["rows"] if str(r[11]) == str(fy)]
    by_month, by_chan, by_person = {}, {}, {}
    for r in rows:
        by_month[r[0]] = by_month.get(r[0], 0) + r[9]
        by_chan[r[1]] = by_chan.get(r[1], 0) + r[9]
        by_person[r[2]] = by_person.get(r[2], 0) + r[9]
    return {
        "rows": len(rows),
        "qty": sum(r[8] for r in rows),
        "taxable": sum(r[9] for r in rows),
        "withtax": sum(r[10] for r in rows),
        "byMonth": by_month,
        "byChannel": by_chan,
        "byPerson": by_person,
        "targets": data.get("targetsByFY", {}).get(str(fy), {}),
        # 'person / city' -> the full-year target, so a person who has one on
        # one side and not the other shows up rather than being averaged away.
        "execTargets": {
            f"{e.get('person','')} / {e.get('city','')}": sum((e.get("full") or {}).values())
            for e in data.get("execByFY", {}).get(str(fy), [])
        },
    }


def diff_map(label, a, b, out):
    keys = sorted(set(a) | set(b))
    bad = [(k, a.get(k, 0), b.get(k, 0)) for k in keys if a.get(k, 0) != b.get(k, 0)]
    if not bad:
        out.append(f"  {label:<14} identical ({len(keys)} keys)")
        return 0
    out.append(f"  {label:<14} {len(bad)} of {len(keys)} differ")
    for k, x, y in bad[:15]:
        out.append(f"       {str(k):<22} postgres {x:>14,}   sheet {y:>14,}   "
                   f"diff {x - y:>+12,}")
    if len(bad) > 15:
        out.append(f"       ... and {len(bad) - 15} more")
    return len(bad)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook', required=True, help='Approved local .xlsx export to compare')
    args = parser.parse_args()
    print("building from PostgreSQL ...")
    pg = build("postgres")
    print("reading selected local workbook ...")
    sh = build("workbook", args.workbook)

    fys = sorted(set(pg["fys"]) | set(sh["fys"]))
    print("\n" + "=" * 74)
    print("  POSTGRES  vs  SELECTED LOCAL WORKBOOK")
    print("=" * 74)
    print(f"\n  financial years   postgres {pg['fys']}   sheet {sh['fys']}")

    problems = 0
    for fy in fys:
        p, s = totals(pg, fy), totals(sh, fy)
        print(f"\n  ---- FY {fy} " + "-" * 56)
        for k in ("rows", "qty", "taxable", "withtax"):
            mark = "" if p[k] == s[k] else f"   <-- differs by {p[k] - s[k]:+,}"
            print(f"  {k:<14} postgres {p[k]:>14,}   sheet {s[k]:>14,}{mark}")
            if k != "rows" and p[k] != s[k]:
                problems += 1
        out = []
        problems += diff_map("by month", p["byMonth"], s["byMonth"], out)
        problems += diff_map("by channel", p["byChannel"], s["byChannel"], out)
        problems += diff_map("by person", p["byPerson"], s["byPerson"], out)
        pt = {k: sum(v.values()) for k, v in (p["targets"] or {}).items()}
        st = {k: sum(v.values()) for k, v in (s["targets"] or {}).items()}
        problems += diff_map("targets (year)", pt, st, out)
        problems += diff_map("exec targets", p["execTargets"], s["execTargets"], out)
        print("\n".join(out))

    print("\n" + "=" * 74)
    if problems:
        print(f"  {problems} difference(s) between PostgreSQL and the selected export.")
        print("  Row-count differences alone are expected -- the sheet drops rows with")
        print("  a blank S.NO. Money differences are not.")
    else:
        print("  No differences. PostgreSQL is the application's only source.")
    print("=" * 74 + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
