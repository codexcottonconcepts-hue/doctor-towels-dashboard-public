"""Independent Online Sales source, PostgreSQL import and dashboard projection.

Only dated input columns in Daily Sales and Monthly Sales are accepted. Sheet
totals and weekly subtotals never become facts; weekly reports use daily facts.
"""
import calendar
import hashlib
import json
import os
import re
from collections import defaultdict
from itertools import islice
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# Supply your own dedicated online-sales workbook; the public source ships
# without a connection to any live business spreadsheet.
SOURCE_ID = os.environ.get("ONLINE_SALES_SOURCE_ID", "").strip()
DAILY_TAB = "Daily Sales"
MONTHLY_TAB = "Monthly Sales"
TIMEZONE = "Asia/Kolkata"
MIGRATION = "19_online_sales.sql"
CENT = Decimal("0.01")


class OnlineSalesValidationError(ValueError):
    def __init__(self, message):
        self.public_message = message
        super().__init__(message)


def _date(value, monthly=False):
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.replace(day=1) if monthly else value
    if not isinstance(value, str):
        return None
    for fmt in (("%b-%Y", "%B-%Y", "%Y-%m") if monthly else
                ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d-%b-%Y")):
        try:
            parsed = datetime.strptime(value.strip(), fmt).date()
            return parsed.replace(day=1) if monthly else parsed
        except ValueError:
            continue
    return None


def _amount(value, location):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise OnlineSalesValidationError(f"{location} must contain a sales amount or be blank.")
    # Numeric strings are accepted for manually pasted input, using Indian or
    # international digit grouping. Formulas need a numeric cached XLSX value.
    if isinstance(value, str):
        value = value.strip().replace(",", "")
        if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
            raise OnlineSalesValidationError(f"{location} must contain a sales amount or be blank.")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or abs(amount) > Decimal("999999999999.99"):
            raise InvalidOperation()
        return amount.quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise OnlineSalesValidationError(f"{location} contains an invalid sales amount.") from None


def _tab(worksheet, monthly=False):
    if (worksheet.max_row or 0) > 1000 or (worksheet.max_column or 0) > 1000:
        raise OnlineSalesValidationError(f"{worksheet.title} exceeds the supported input size.")
    # Google exports can omit worksheet dimensions; read-only openpyxl then
    # reports None. Bound the iterator itself rather than trusting dimensions.
    rows = list(islice(worksheet.iter_rows(values_only=True), 1001))
    if len(rows) > 1000 or any(len(row) > 1000 for row in rows):
        raise OnlineSalesValidationError(f"{worksheet.title} exceeds the supported input size.")
    candidates = []
    for index, row in enumerate(rows[:10]):
        columns = [(col, _date(value, monthly)) for col, value in enumerate(row) if col >= 2]
        columns = [(col, day) for col, day in columns if day is not None]
        if columns:
            candidates.append((index, columns))
    if len(candidates) != 1:
        raise OnlineSalesValidationError(f"{worksheet.title} needs one unambiguous row of dated input columns.")
    header_index, columns = candidates[0]
    dates = [day for _, day in columns]
    if len(dates) != len(set(dates)):
        raise OnlineSalesValidationError(f"{worksheet.title} contains duplicate date columns.")
    if any(day.year < 2000 or day.year > 2099 for day in dates):
        raise OnlineSalesValidationError(f"{worksheet.title} contains an unsupported date.")
    platforms, records, total_found = [], [], False
    seen = set()
    for row_index, row in enumerate(rows[header_index + 1:], header_index + 2):
        raw_platform = row[0] if row else None
        if raw_platform is None or not str(raw_platform).strip():
            # Blank decoration/notes below the table are safe; an unnamed input
            # row is ambiguous and must never silently lose sales.
            if any(col < len(row) and row[col] is not None for col, _ in columns):
                raise OnlineSalesValidationError(f"{worksheet.title} row {row_index} has sales without a platform.")
            continue
        platform = " ".join(str(raw_platform).split())
        if platform.casefold() in {"total", "grand total", "overall sales"}:
            total_found = True
            continue
        if total_found:
            raise OnlineSalesValidationError(f"{worksheet.title} has an unexpected platform below Total.")
        if len(platform) > 100 or platform.casefold() in seen:
            raise OnlineSalesValidationError(f"{worksheet.title} contains an invalid or duplicate platform.")
        seen.add(platform.casefold())
        platforms.append(platform)
        for column, day in columns:
            value = row[column] if column < len(row) else None
            amount = _amount(value, f"{worksheet.title} row {row_index}, column {column + 1}")
            if amount is not None:
                records.append({"period": day, "platform": platform, "amount": amount})
    if not platforms or not total_found:
        raise OnlineSalesValidationError(f"{worksheet.title} needs platform rows followed by a Total row.")
    return {"dates": sorted(dates), "platforms": platforms, "records": records}


def parse_workbook(workbook):
    """Validate both inputs fully before allowing any database mutation."""
    if DAILY_TAB not in workbook.sheetnames or MONTHLY_TAB not in workbook.sheetnames:
        raise OnlineSalesValidationError("Online sales requires both Daily Sales and Monthly Sales tabs.")
    daily = _tab(workbook[DAILY_TAB])
    monthly = _tab(workbook[MONTHLY_TAB], monthly=True)
    daily_names = {name.casefold(): name for name in daily["platforms"]}
    if set(daily_names) != {name.casefold() for name in monthly["platforms"]}:
        raise OnlineSalesValidationError("Daily Sales and Monthly Sales platform lists must match.")
    for row in monthly["records"]:
        row["platform"] = daily_names[row["platform"].casefold()]
    if not daily["records"] and not monthly["records"]:
        raise OnlineSalesValidationError("Online sales inputs are all blank. Previous online sales were preserved.")
    warnings = []
    if not monthly["records"]:
        warnings.append("Monthly Sales inputs are blank. Monthly amounts are derived from recorded daily sales.")
    return {"daily": daily["records"], "monthly": monthly["records"],
            "dailyDates": daily["dates"], "monthlyDates": monthly["dates"],
            "platforms": daily["platforms"], "warnings": warnings}


def _canonical(parsed):
    return json.dumps(parsed, default=lambda v: v.isoformat() if isinstance(v, date) else str(v),
                      sort_keys=True, separators=(",", ":"))


def sync_workbook(workbook, source_id, conn):
    """Atomically replace visible source periods, preserving historical periods.

    Validation, download and database failures leave the prior committed import
    intact. Blank inputs clear only their currently visible header partitions.
    """
    if not SOURCE_ID or source_id != SOURCE_ID:
        raise OnlineSalesValidationError("The online sales spreadsheet ID does not match its dedicated source.")
    parsed = parse_workbook(workbook)
    digest = hashlib.sha256(_canonical(parsed).encode("utf-8")).hexdigest()
    result = {"busy": False, "changed": False, "daily_cells": len(parsed["daily"]),
              "monthly_cells": len(parsed["monthly"]), "warnings": parsed["warnings"]}
    with conn:
        with conn.cursor() as cur:
            cur.execute("select pg_try_advisory_xact_lock(hashtext('online-sales-sheet-sync'))")
            if not cur.fetchone()[0]:
                return {**result, "busy": True}
            cur.execute("set local lock_timeout='5s'")
            cur.execute("select to_regclass('online_sales.source_snapshot'), "
                        "to_regclass('online_sales.daily_sale'), to_regclass('online_sales.monthly_sale')")
            if not all(cur.fetchone()):
                raise OnlineSalesValidationError(f"Online sales migration {MIGRATION} is not installed.")
            cur.execute("select source_fingerprint from online_sales.source_snapshot where source_id=%s for update", (source_id,))
            previous = cur.fetchone()
            if previous and previous[0].strip() == digest:
                return result
            metadata = json.dumps({"dailyDates": [day.isoformat() for day in parsed["dailyDates"]],
                                   "monthlyDates": [day.isoformat() for day in parsed["monthlyDates"]],
                                   "platforms": parsed["platforms"], "warnings": parsed["warnings"]})
            cur.execute("""insert into online_sales.source_snapshot(source_id,source_fingerprint,metadata)
                values(%s,%s,%s::jsonb) on conflict(source_id) do update
                set source_fingerprint=excluded.source_fingerprint,metadata=excluded.metadata,imported_at=now()""",
                        (source_id, digest, metadata))
            for table, date_key, records_key in (("daily_sale", "dailyDates", "daily"),
                                                  ("monthly_sale", "monthlyDates", "monthly")):
                cur.execute(f"delete from online_sales.{table} where source_id=%s and period=any(%s)",
                            (source_id, parsed[date_key]))
                records = parsed[records_key]
                if records:
                    cur.executemany(f"insert into online_sales.{table}(source_id,period,platform,amount) values(%s,%s,%s,%s)",
                                    [(source_id, row["period"], row["platform"], row["amount"]) for row in records])
            result["changed"] = True
    return result


def unavailable(reason):
    return {"available": False, "reason": reason, "daily": [], "monthly": [], "platforms": [],
            "financialYears": [], "warnings": [], "timezone": TIMEZONE}


def dashboard_payload(daily_records, monthly_records, metadata, imported_at, source_id=SOURCE_ID):
    """Use monthly entered actuals per platform/month; otherwise derive daily sums."""
    daily = [{"date": day.isoformat(), "platform": platform, "sales": float(amount)}
             for day, platform, amount in daily_records]
    aggregate, days = defaultdict(Decimal), defaultdict(set)
    for day, platform, amount in daily_records:
        key = (day.replace(day=1), platform)
        aggregate[key] += Decimal(str(amount))
        days[key].add(day)
    entered = {(month, platform): Decimal(str(amount)) for month, platform, amount in monthly_records}
    monthly = []
    for month, platform in sorted(aggregate.keys() | entered.keys()):
        key = (month, platform)
        from_sheet = key in entered
        monthly.append({"month": month.strftime("%Y-%m"), "platform": platform,
                        "sales": float(entered[key] if from_sheet else aggregate[key]),
                        "source": "monthly-entered" if from_sheet else "daily-derived",
                        "daysReported": None if from_sheet else len(days[key]),
                        "daysInMonth": calendar.monthrange(month.year, month.month)[1]})
    reported = sorted({row[0] for row in daily_records})
    headers = sorted(metadata.get("dailyDates", []))
    derived = sum(row["source"] == "daily-derived" for row in monthly)
    warnings = list(metadata.get("warnings", []))
    if derived and not warnings:
        warnings.append("Blank Monthly Sales inputs use recorded daily sales; daily coverage may be incomplete.")
    all_periods = [row[0] for row in daily_records] + [row[0] for row in monthly_records]
    return {"available": True, "source": {"id": source_id,
            "url": f"https://docs.google.com/spreadsheets/d/{source_id}/edit",
            "dailyTab": DAILY_TAB, "monthlyTab": MONTHLY_TAB}, "timezone": TIMEZONE,
            "importedAt": imported_at.isoformat() if hasattr(imported_at, "isoformat") else str(imported_at),
            "daily": daily, "monthly": monthly,
            "platforms": list(dict.fromkeys(metadata.get("platforms", []) + sorted({row[1] for row in daily_records + monthly_records}))),
            "financialYears": sorted({str(day.year if day.month >= 4 else day.year - 1) for day in all_periods}),
            "coverage": {"dailyStart": headers[0] if headers else None,
                         "dailyEnd": headers[-1] if headers else None,
                         "latestReportedDate": reported[-1].isoformat() if reported else None,
                         "dailyDays": len(reported), "monthlyEnteredCells": len(entered),
                         "monthlyDerivedCells": derived}, "warnings": warnings}


def fetch_dashboard(conn):
    """Read within the caller's read-only snapshot; missing migration is optional."""
    if not SOURCE_ID:
        return unavailable("Online sales is not configured. Set ONLINE_SALES_SOURCE_ID to your own workbook ID.")
    with conn.cursor() as cur:
        cur.execute("select to_regclass('online_sales.source_snapshot'), "
                    "to_regclass('online_sales.daily_sale'), to_regclass('online_sales.monthly_sale')")
        if not all(cur.fetchone()):
            return unavailable(f"Online sales migration {MIGRATION} is not installed.")
        cur.execute("select metadata,imported_at from online_sales.source_snapshot where source_id=%s", (SOURCE_ID,))
        snapshot = cur.fetchone()
        if snapshot is None:
            return unavailable("Online sales is waiting for its first successful import.")
        metadata, imported_at = snapshot
        cur.execute("select period,platform,amount from online_sales.daily_sale where source_id=%s order by period,platform", (SOURCE_ID,))
        daily = cur.fetchall()
        cur.execute("select period,platform,amount from online_sales.monthly_sale where source_id=%s order by period,platform", (SOURCE_ID,))
        monthly = cur.fetchall()
    return dashboard_payload(daily, monthly, metadata, imported_at)
