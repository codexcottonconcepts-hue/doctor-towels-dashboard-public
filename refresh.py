"""Rebuild the dashboard using PostgreSQL as the only runtime data source.

Sales, targets, territories and retained supplementary datasets all come from
PostgreSQL. Workbook parsers below are kept for explicit, offline imports and
reconciliation only; the running dashboard never downloads Google Sheets.
Run: python refresh.py
"""
import base64
import collections
import datetime
import io
import json
import os
import re
import sys
import pgdata
import dropdown_ui

HERE = os.path.dirname(os.path.abspath(__file__))

# Optional second copy of the finished page, for a plain web server to serve.
# Off by default: app.py serves the page itself behind a login, so a copy sitting
# in a web root is the same figures with no login in front of them. Set
# PUBLISH_DIR to a path to turn it back on -- never to a folder holding this
# source, which would put session.key and users.db on the web too. When it is on
# and the destination is offline or read-only, that is a note, never a failed run.
PUBLISH_DIR = os.environ.get("PUBLISH_DIR", "")


def logo_uri():
    """Inline logo.png as a data URI, or "" when there is none.

    Inlined rather than linked because the page is a single self-contained file
    by design: it gets opened straight off disk and published to places that
    carry the HTML and nothing else, where a linked image would just be missing.
    Base64 costs about a third in size, which is why a tidy logo file is worth
    more here than anywhere else.
    """
    return png_uri("logo.png")          # "" with no logo: the sidebar keeps its lettermark


def favicon_link():
    """The browser-tab icon as a <link> tag, inlined for the same reason as the logo.

    favicon.png is the logo squared off with rounded corners at 64px, so it reads
    at tab size; without it the logo itself is used, and with neither the tab
    keeps the browser's default icon.
    """
    uri = png_uri("favicon.png") or png_uri("logo.png")
    return f'<link rel="icon" type="image/png" href="{uri}">' if uri else ""


def png_uri(name):
    """A PNG beside this file as a data URI, or "" when it is missing."""
    try:
        with io.open(os.path.join(HERE, name), "rb") as fh:
            return "data:image/png;base64," + base64.b64encode(fh.read()).decode()
    except OSError:
        return ""


def publish(html, log=print):
    """Drop the page into the server folder as index.html, written to a temp name
    first so a viewer never catches a half-written file."""
    if not PUBLISH_DIR:
        return None
    try:
        if not os.path.isdir(PUBLISH_DIR):
            log(f"    note: publish folder not reachable ({PUBLISH_DIR})")
            return None
        dest = os.path.join(PUBLISH_DIR, "index.html")
        tmp = dest + ".new"
        io.open(tmp, "w", encoding="utf-8").write(html)
        os.replace(tmp, dest)
        log(f"    published {dest} ({os.path.getsize(dest):,} bytes)")
        return dest
    except Exception as e:
        log(f"    note: could not publish to {PUBLISH_DIR} ({e})")
        return None


class TabProblem(Exception):
    """A malformed legacy workbook tab encountered during an offline import."""


LEDGER_TAB = "Direct Sales Report"
EXEC_TAB = "Executive"
ROTN_TAB = "ROTN Live"
CHANNEL_TABS = {           # tab name -> the key the dashboard uses
    "LFO": "LFO Primary",
    "D&R-S": "D&R-S",
    "L-LFO": "L-LFO",
    "D&R-N": "North",
    "Saloon": "Saloon",
}

# The financial year is read from the data, never hardcoded, so a sheet that keeps
# collecting rows rolls into 2027-28 and beyond with no code change.
FY_FIRST, FY_LAST = 2020, 2099
MORD = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
MN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
LONG_MONTH = {"April": "Apr", "May": "May", "June": "Jun", "July": "Jul", "August": "Aug",
              "Sept": "Sep", "September": "Sep", "Oct": "Oct", "October": "Oct",
              "Nov": "Nov", "November": "Nov", "Dec": "Dec", "December": "Dec",
              "Jan": "Jan", "January": "Jan", "Feb": "Feb", "February": "Feb",
              "March": "Mar", "Mar": "Mar"}
def fy_of(label):
    """'Apr-2026'..'Dec-2026' -> FY 2026;  'Jan-2027'..'Mar-2027' -> FY 2026."""
    try:
        mn, yr = str(label).split("-")
        yr = int(yr)
    except Exception:
        return None
    if mn not in MORD or not (FY_FIRST <= yr <= FY_LAST):
        return None
    return yr if MORD.index(mn) <= MORD.index("Dec") else yr - 1


def fy_label(fy):
    return "%d-%s" % (fy, str(fy + 1)[-2:])


def fy_months(fy):
    return [m + "-" + str(fy) for m in MORD[:9]] + [m + "-" + str(fy + 1) for m in MORD[9:]]


STATE_FIX = {"Telengana": "Telangana", "Odissa": "Odisha",
             "Aurangabad": "Maharashtra", "Tamil": "Tamil Nadu"}


def num(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip().replace(",", "").replace("₹", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def mlabel(v):
    if isinstance(v, datetime.datetime):
        return f"{MN[v.month - 1]}-{v.year}"
    return str(v or "").strip()


def norm(h):
    """Collapse runs of whitespace - the two workbooks differ in how many spaces
    sit inside 'DT/TT Primary Sale Value    (With Tax)'."""
    return re.sub(r"\s+", " ", str(h or "").strip())


# ------------------------------------------------------------------ primary
def parse_ledger(wb):
    ws = wb[LEDGER_TAB]
    hdr = [norm(c.value) for c in ws[1]]
    ix = {h: i for i, h in enumerate(hdr)}
    QTY = "DT/TT Primary Order qty"
    TAX = "DT/TT Primary Taxable Value ( Without Tax)"
    SALE = "DT/TT Primary Sale Value (With Tax)"
    need = ["S.NO", "Month", "Week", "Region", "Execuitve", "City", "State",
            "Direct /Distribution /Group", "DT / TT", QTY, TAX, SALE]
    missing = [c for c in need if c not in ix]
    if missing:
        sys.exit(f"FAILED: '{LEDGER_TAB}' is missing expected columns: {missing}\n"
                 f"        columns present: {[h for h in hdr if h]}")

    def g(row, name):
        i = ix[name]
        return row[i] if i < len(row) else None

    recs, skipped = [], collections.Counter()
    unnum, unnum_tx = 0, 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[ix["S.NO"]] is None or not str(row[ix["S.NO"]]).strip():
            # A blank S.NO is how the sheet marks its spacer and total rows, so
            # skipping is right. But a row carrying billing is a real invoice
            # someone forgot to number, and dropping it silently is how a day's
            # sales go missing with nothing anywhere to show for it. Count those
            # and report them; the ones with no money in them stay quiet.
            tx = round(num(g(row, TAX)))
            if tx or round(num(g(row, QTY))):
                unnum += 1
                unnum_tx += tx
            continue
        m = mlabel(g(row, "Month"))
        fy = fy_of(m)
        if fy is None:
            skipped[m or "(blank)"] += 1     # unreadable month, not a different year
            continue
        st = str(g(row, "State") or "").strip()
        st = STATE_FIX.get(st, st) or "Unknown"
        recs.append([
            m,
            str(g(row, "Region") or "").strip() or "Unknown",
            str(g(row, "Execuitve") or "").strip() or "Unknown",
            st,
            str(g(row, "City") or "").strip() or "Unknown",
            str(g(row, "DT / TT") or "").strip() or "Unknown",
            str(g(row, "Direct /Distribution /Group") or "").strip() or "Unknown",
            str(g(row, "Week") or "").strip() or "Unknown",
            round(num(g(row, QTY))), round(num(g(row, TAX))), round(num(g(row, SALE))),
            fy,                                  # index 11 - the row's financial year
        ])
    return recs, skipped, (unnum, unnum_tx)


def month_year(v, default_fy=None):
    """Read a month cell however it is written and return ('Apr', financial year).

    The sheet holds real dates (2027-04-01), which is better than a separate Year
    column because the year travels with the month. Plain text still works:
    'Apr-2027', 'April 2027', or a bare 'Apr' that takes `default_fy`.
    """
    if isinstance(v, datetime.datetime):
        mon = MN[v.month - 1]
        return mon, fy_of("%s-%d" % (mon, v.year))
    t = norm(v)
    if not t:
        return None, None
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", t)          # 2027-04-01
    if m:
        mon = MN[int(m.group(2)) - 1]
        return mon, fy_of("%s-%d" % (mon, int(m.group(1))))
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", t)          # 01/04/2027
    if m:
        mon = MN[int(m.group(2)) - 1]
        return mon, fy_of("%s-%d" % (mon, int(m.group(3))))
    m = re.match(r"^([A-Za-z]+)[\s\-/,]+(\d{4})$", t)                 # Apr-2027
    if m:
        mon = LONG_MONTH.get(m.group(1), m.group(1)[:3].title())
        if mon in MORD:
            return mon, fy_of("%s-%d" % (mon, int(m.group(2))))
    mon = LONG_MONTH.get(t, t)                                        # bare 'Apr'
    if mon in MORD:
        return mon, default_fy
    return None, None


def year_col(hdr):
    """An optional Year/FY column is what lets one tab hold several years."""
    for i, h in enumerate(hdr):
        if str(h).strip().lower() in ("year", "fy", "financial year"):
            return i
    return None


def parse_channel_targets(wb, default_fy):
    """Each target tab is Category | Month | Target, one row per month. An optional
    Year column carries further years in the same tab; without one every row is read
    as `default_fy`, and a repeated month is reported rather than silently merged."""
    byfy = {}
    for tab, key in CHANNEL_TABS.items():
        if tab not in wb.sheetnames:
            raise TabProblem(f"target tab '{tab}' not found (tabs: {wb.sheetnames})")
        ws = wb[tab]
        hdr = [norm(c.value) for c in ws[1]]
        try:
            mi, ti = hdr.index("Month"), hdr.index("Target")
        except ValueError:
            raise TabProblem(f"tab '{tab}' needs 'Month' and 'Target' headers; got {hdr}")
        yi = year_col(hdr)
        dupes, kept, seen = set(), 0, []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[mi] is None:
                continue
            mk, fy = month_year(row[mi], default_fy)
            if mk is None:
                seen.append(str(row[mi])[:24])
                continue
            if yi is not None and yi < len(row) and row[yi] is not None:
                fy = fy_of("%s-%d" % (mk, int(num(row[yi])))) or fy
            if fy is None:
                continue
            kept += 1
            slot = byfy.setdefault(fy, {}).setdefault(key, {})
            if mk in slot:
                dupes.add(mk)
            slot[mk] = round(num(row[ti]))
        if not kept:
            raise TabProblem("tab '%s' has a Month column nothing could be read from "
                             "(saw %s) - use a real date like 2027-04-01, or 'Apr-2027'"
                             % (tab, seen[:3]))
        if dupes and yi is None:
            print("    WARNING: tab '%s' repeats %s and has no Year column - later rows "
                  "overwrote earlier ones. Add a Year column to hold two financial years."
                  % (tab, sorted(dupes)))
        for fy in byfy:
            found = [m for m in MORD if m in byfy[fy].get(key, {})]
            if len(found) != 12:
                print("    WARNING: tab '%s' has %d of 12 months for FY%s"
                      % (tab, len(found), fy_label(fy)))

    # roll-ups the dashboard expects, derived rather than read
    for fy, chan in byfy.items():
        def g(k, m, _c=chan):
            return _c.get(k, {}).get(m, 0)
        chan["D&R - S TOTAL"] = {m: g("D&R-S", m) + g("L-LFO", m) for m in MORD}
        chan["D&R TOTAL"] = {m: chan["D&R - S TOTAL"][m] + g("North", m) for m in MORD}
        chan["ALL TOTAL"] = {m: chan["D&R TOTAL"][m] + g("Saloon", m) + g("LFO Primary", m)
                             for m in MORD}
    return byfy


def parse_exec_targets(wb, default_fy):
    """Executive tab: Person Name | City | Apr..Mar -> tidy rows, optionally with a
    Year column so one tab can carry more than one financial year."""
    if EXEC_TAB not in wb.sheetnames:
        return {}
    ws = wb[EXEC_TAB]
    raw = [c.value for c in ws[1]]
    hdr = [norm(v) for v in raw]
    # each month column is (financial year, month), so one tab spans several years
    cols = {}
    for j, v in enumerate(raw):
        mon, fy = month_year(v, default_fy)
        if mon and fy is not None:
            cols.setdefault(fy, {})[mon] = j
    if not cols:
        print("    WARNING: '%s' tab has no readable month columns (saw %s) - "
              "use dates like 2027-04-01 in the header" % (EXEC_TAB, hdr[2:5]))
    yi = year_col(hdr)
    out = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0] or not str(row[0]).strip():
            continue
        rowfy = None
        if yi is not None and yi < len(row) and row[yi] is not None:
            rowfy = fy_of("Apr-%d" % int(num(row[yi])))
        for fy, mcols in cols.items():
            if rowfy is not None and fy != rowfy:
                continue
            cell = lambda m: (round(num(row[mcols[m]]))
                              if m in mcols and mcols[m] < len(row) else 0)
            full = {m: cell(m) for m in MORD}
            if not any(full.values()):
                continue                    # nothing set for this person in this year
            out.setdefault(fy, []).append({
                "person": str(row[0]).strip(),
                "city": str(row[1] or "").strip(),
                "t": [full[m] for m in ["Apr", "May", "Jun", "Jul"]],
                "a": [None, None, None, None],   # no achieved column in this workbook
                "full": full,
            })
    return out


def parse_rotn(wb, default_fy):
    """Read only the sales-person / territory map from the ROTN Live tab.

    Targets are NOT read here. They live in the Executive tab (person + city +
    twelve months) and in the channel tabs, which are the sheets the business
    maintains; the ROTN tab exists because nothing else records which territories
    sit under which executive. Reading targets from both places is what let them
    drift - ROTN's own grid was missing Trichy's May figure that the Executive tab
    carries, so its year total came out 3,00,000 short.

    The layout is found rather than assumed: any row whose cells mention the
    ledger name or the territories is treated as the header, and the map is the
    rows under it. A tab trimmed down to just those three columns reads the same
    as one with numbered sections above it.
    """
    if ROTN_TAB not in wb.sheetnames:
        raise TabProblem(f"'{ROTN_TAB}' tab not found")
    ws = wb[ROTN_TAB]

    head = None
    for r in range(1, min(ws.max_row, 30) + 1):
        cells = [norm(ws.cell(r, c).value).lower() for c in (1, 2, 3)]
        if any("ledger" in c for c in cells) and any("territor" in c for c in cells):
            head = r
            break
    if head is None:
        raise TabProblem(f"'{ROTN_TAB}' has no header row naming the ledger name "
                         f"and the territories owned")

    people = []
    r = head + 1
    while r <= ws.max_row:
        name = norm(ws.cell(r, 1).value)
        if not name:
            break
        people.append({
            "name": name,
            "ledger": norm(ws.cell(r, 2).value) or name,
            "terrs": [t.strip() for t in
                      str(ws.cell(r, 3).value or "").split(",") if t.strip()],
        })
        r += 1
    if not people:
        raise TabProblem(f"'{ROTN_TAB}' lists no sales people under its header")
    return {"tab": ROTN_TAB, "people": people}


# ------------------------------------------------------------ supplementary
def supp_history(wb):
    """Twelve months of the two prior years, and what those two years are called.

    The labels are read from the header rather than assumed. This block is
    maintained by hand once a year, and a page naming the years it is actually
    showing beats one naming the years it happened to be written for -- which is
    what pinned the Year on Year page to 2024-25/2025-26 until now. The keys stay
    y2425/y2526 because they are only positions; the labels carry the meaning.
    """
    tab = "D&R - LFO & Saloon"
    if tab not in wb.sheetnames:
        return [], []
    g = [list(r) for r in wb[tab].iter_rows(values_only=True)]

    def label(col):
        for r in range(min(5, len(g))):          # the header sits above the data
            v = str((g[r][col] if col < len(g[r]) else "") or "").strip()
            m = re.match(r"^(\d{4})\s*[-/]\s*(\d{2}|\d{4})$", v)
            if m:
                return "%s-%s" % (m.group(1), m.group(2)[-2:])
        return None

    rows = [{"m": MORD[k],
             "y2425": round(num(g[5 + k][0])) if len(g) > 5 + k else 0,
             "y2526": round(num(g[5 + k][1])) if len(g) > 5 + k else 0} for k in range(12)]
    return rows, [label(0), label(1)]


def supp_lfo2(wb):
    if "LFO" not in wb.sheetnames:
        return []
    g = [list(r) for r in wb["LFO"].iter_rows(values_only=True)]
    out = []
    for k in range(12):
        row = g[5 + k] if 5 + k < len(g) else []
        cell = lambda i: round(num(row[i])) if len(row) > i else 0
        out.append({"m": MORD[k], "ptgt": cell(8), "stgt": cell(9),
                    "sach": cell(10), "dt": cell(11), "lulu": cell(12)})
    return out


def supp_stores(wb):
    terr, cat = collections.Counter(), collections.Counter()
    if "Category" not in wb.sheetnames:
        return [], []
    for row in wb["Category"].iter_rows(min_row=12, values_only=True):
        if len(row) < 4 or not row[1] or not str(row[1]).strip():
            continue
        terr[str(row[3] or "Unspecified").strip()] += 1
        cat[str(row[2] or "Unspecified").strip()] += 1
    return terr.most_common(), cat.most_common()


def supp_pipeline(wb):
    tab = "D&R - Tamil Nadu"
    if tab not in wb.sheetnames:
        return []
    g = [list(r) for r in wb[tab].iter_rows(values_only=True)]
    out = []
    for row in g[4:20]:
        if len(row) < 29 or not row[27] or not str(row[27]).strip():
            continue
        amt = round(num(row[28]))
        if amt > 0:
            out.append({"k": str(row[27]).strip(), "amt": amt})
    return out


def supp_mtd(wb):
    tab = "Company MTD"
    if tab not in wb.sheetnames:
        return None
    g = [list(r) for r in wb[tab].iter_rows(values_only=True)]
    fmt = lambda v: v.strftime("%d %b %Y") if isinstance(v, datetime.datetime) else str(v or "")
    mtd = {"month": str(g[1][1] or "").strip(), "through": fmt(g[1][3]),
           "throughDate": fmt(g[1][3]), "updated": fmt(g[1][5]),
           "company": round(num(g[4][0])), "retail": round(num(g[4][2])),
           "online": round(num(g[4][4])),
           "retailShare": round(num(g[4][6]) * 100, 1),
           "onlineShare": round(num(g[4][8]) * 100, 1),
           "counts": [str(g[5][0] or ""), str(g[5][2] or ""), str(g[5][4] or "")],
           "basis": str(g[6][1] or "").replace("�", "-")}

    def block(r0, c0, n):
        out = []
        for i in range(r0, min(r0 + n, len(g))):
            row = g[i]
            if c0 >= len(row) or row[c0] is None or not str(row[c0]).strip():
                continue
            out.append({"k": str(row[c0]).strip(), "inv": round(num(row[c0 + 1])),
                        "units": round(num(row[c0 + 2])), "rev": round(num(row[c0 + 3]))})
        return out

    mtd["byRegion"] = block(9, 0, 6)
    mtd["byProduct"] = block(9, 5, 3)
    mtd["byOrderType"] = block(13, 5, 3)
    daily = []
    for row in g[17:]:
        if not isinstance(row[0], datetime.datetime):
            continue
        daily.append({"d": row[0].strftime("%d %b"), "st": str(row[1] or "").strip(),
                      "ri": round(num(row[2])), "ru": round(num(row[3])),
                      "rr": round(num(row[4])), "oo": round(num(row[5])),
                      "orv": round(num(row[6])), "cr": round(num(row[7])),
                      "cum": round(num(row[10])) if len(row) > 10 else 0})
    mtd["daily"] = daily
    return mtd


def build_data(log=print):
    """Read one database snapshot and assemble the dashboard; never fetch Sheets."""
    lines = []

    def say(msg):
        lines.append(msg)
        if log:
            log(msg)

    try:
        recs, chan_by_fy, city_by_fy, rotn, pgmeta = pgdata.build(log=say)
    except Exception as exc:
        # Do not expose connection-string details in a served error message.
        raise SystemExit(
            "FAILED: could not read PostgreSQL. Check the database connection and "
            "required migrations. Google Sheets is disconnected. "
            f"({type(exc).__name__})"
        ) from None
    if not recs:
        raise SystemExit("FAILED: PostgreSQL holds no sales rows; refusing to publish an empty dashboard.")

    pgmeta = dict(pgmeta)
    supplementary = pgmeta.pop("supplementary")
    secondary = {key: pgmeta.pop(key, {"available": False}) for key in pgdata.SECONDARY_KEYS.values()}
    online_sales = pgmeta.pop("onlineSales", {"available": False})
    colour_list, colour_rows = pgmeta.pop("colourList", []), pgmeta.pop("colourRows", [])
    product_data = {key: pgmeta.pop(key, default) for key, default in (("productSalesAvailable", False), ("productRows", []), ("productCoverageRows", []), ("primaryRetailerTargets", []))}
    hist, histlab = supplementary["hist"], supplementary["histLabels"]
    lfo2, terr, cat = supplementary["lfo2"], supplementary["terr"], supplementary["cat"]
    pipeline, mtd = supplementary["pipeline"], supplementary["mtd"]
    today = datetime.date.today().strftime("%d %b %Y")
    sold = sorted({r[11] for r in recs})
    cur = sold[-1]
    # A later year with targets but no sales yet is a planned year. It is listed
    # with all twelve months so Target Balance and the Secondary pages can show
    # its plan; the dashboard still opens on the latest year with sales.
    planned = {int(fy) for fy in chan_by_fy if int(fy) > cur}
    planned |= {int(fy) for payload in secondary.values() for fy in payload.get("financialYears", []) if int(fy) > cur}
    fys = sorted(set(sold) | planned)
    months_by_fy = {str(fy): fy_months(fy) if fy in planned else
                    [m for m in fy_months(fy) if any(r[0] == m for r in recs)]
                    for fy in fys}
    data = {
        "rows": recs, "monthOrder": MORD,
        "fys": [str(f) for f in fys], "fy": str(cur),
        "fyLabels": {str(f): fy_label(f) for f in fys},
        "monthsByFY": months_by_fy, "targetsByFY": chan_by_fy, "execByFY": city_by_fy,
        "months": months_by_fy[str(cur)],
        "chanTargets": chan_by_fy.get(str(cur), {}),
        "cityTargets": city_by_fy.get(str(cur), []),
        "acctTargets": {}, "splitSource": f"PostgreSQL {pgmeta['database']}",
        "source": "postgres", "pg": pgmeta,
        "fromSnapshot": [], "snapshotDate": None,
        "outletsByTerritory": terr, "outletsByCategory": cat,
        "hist": hist, "histLabels": histlab, "lfo2": lfo2,
        "plannedFys": [str(f) for f in sorted(planned)],
        "colourList": colour_list, "colourRows": colour_rows,
        **product_data,
        **secondary,
        "onlineSales": online_sales,
        "pipeline": pipeline, "mtd": mtd, "rotn": rotn,
        "primaryId": "", "suppId": "", "tabs": None,
        "fetched": today, "logo": logo_uri(),
        # The full logo on a transparent background, for the dark sidebar.
        "wordmark": png_uri("logo-wordmark.png"),
    }
    with open(os.path.join(HERE, "india_paths.json"), encoding="utf-8") as stream:
        data["map"] = json.load(stream)
    data["map"]["nameFix"] = {"Daman & Diu": "Dadra and Nagar Haveli and Daman and Diu"}

    for fy in fys:
        rr = [r for r in recs if r[11] == fy]
        ms = [m for m in fy_months(fy) if any(r[0] == m for r in rr)]
        target = sum(chan_by_fy.get(str(fy), {}).get("ALL TOTAL", {}).values())
        tax = sum(r[9] for r in rr)
        star = " <- opens here" if fy == cur else ""
        say(f"OK  FY{fy_label(fy)}  rows={len(rr)}  qty={sum(r[8] for r in rr):,}  "
            f"taxable={tax:,}  withtax={sum(r[10] for r in rr):,}{star}")
        say("      orders/month: " + "  ".join(
            f"{m.split('-')[0]}={sum(1 for r in rr if r[0] == m)}" for m in ms))
        say(f"      months live: {len(ms)} of 12; target={target:,}")
        say(f"      exec/city target rows: {len(city_by_fy.get(str(fy), []))}")
        if not target:
            say(f"    WARNING: no channel targets for FY{fy_label(fy)} in the active target plan.")
    if rotn:
        names = {person["ledger"] for person in rotn["people"]}
        rrows = [row for row in recs if row[11] == cur and row[2] in names]
        say(f"    ROTN: {len(rotn['people'])} people; rows={len(rrows)}; "
            f"achieved={sum(row[9] for row in rrows):,}; targets from PostgreSQL.")
    else:
        say("    note: no territories assigned in master.person_territory; the ROTN page is hidden.")
    say("    supplementary: 7 datasets read from PostgreSQL aux.dashboard_dataset.")
    say("    Google Sheets disconnected: no remote downloads or local snapshot fallback.")
    return data, lines


def render_html(data):
    """Inject the data into the template - the page is self-contained afterwards."""
    tpl = io.open(os.path.join(HERE, "template.html"), encoding="utf-8").read()
    executive_detail = io.open(os.path.join(HERE, "executive_detail.js"), encoding="utf-8").read()
    tpl = tpl.replace("/*__EXECUTIVE_DETAILS__*/", executive_detail)
    for marker, filename in (("/*__ONLINE_FONTS__*/", "satoshi_fonts.css"),
                             ("/*__ONLINE_STYLES__*/", "online_sales.css")):
        with io.open(os.path.join(HERE, filename), encoding="utf-8") as asset:
            tpl = tpl.replace(marker, asset.read())
    for marker, filename in (("/*__DASHBOARD_ACCESS__*/", "dashboard_access.js"),
                             ("/*__CHART_DRILLDOWN__*/", "chart_drilldown.js"),
                             ("/*__SECONDARY_BREAKDOWN__*/", "secondary_breakdown.js"),
                             ("/*__RETAILER_TARGETS__*/", "retailer_targets.js"),
                             ("/*__ONLINE_SALES__*/", "online_sales.js")):
        module = io.open(os.path.join(HERE, filename), encoding="utf-8").read()
        tpl = tpl.replace(marker, module)
    tpl = tpl.replace("<!--__FAVICON__-->", favicon_link(), 1)
    tpl = tpl.replace("/*__SALESVIEWER_BRAND__*/", png_uri("salesviewer-brand.png"))
    if "/*__DATA__*/" not in tpl:
        sys.exit("FAILED: template.html has no /*__DATA__*/ placeholder.")
    # Imported names must remain JSON values, including a literal </script>.
    payload = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    return tpl.replace("/*__DATA__*/", payload) + dropdown_ui.assets()


def main():
    data, _ = build_data()
    html = render_html(data)
    dest = os.path.join(HERE, "dashboard.html")
    io.open(dest, "w", encoding="utf-8").write(html)
    print(f"    wrote {dest} ({os.path.getsize(dest):,} bytes)")
    publish(html)


if __name__ == "__main__":
    main()
