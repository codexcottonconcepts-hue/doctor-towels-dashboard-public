"""Background, one-way Direct Sales import; dashboards still read PostgreSQL."""
import json
import re
import threading
from contextlib import closing
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import openpyxl
import pgdata
import sheet_sales_sync
import lfo_data

CONFIG_PATH = Path(__file__).resolve().parent / "sheets-sales-sync.json"
MAX_DOWNLOAD = 30_000_000


def load_config(path=CONFIG_PATH):
    path = Path(path)
    if not path.exists():
        return None
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError("The sales sync configuration cannot be read.") from exc
    if not isinstance(config, dict) or not isinstance(config.get("enabled", False), bool):
        raise ValueError("Sales sync enabled must be true or false.")
    if not config.get("enabled", False):
        return None
    source_id = config.get("source_id", "")
    if not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", source_id):
        raise ValueError("Sales sync requires a valid Google spreadsheet ID.")
    if not lfo_data.SOURCE_ID or source_id != lfo_data.SOURCE_ID:
        raise ValueError("Set SALES_SOURCE_ID and the sync configuration to your own Google spreadsheet ID.")
    if config.get("sheet_name", "Direct Sales Report") != "Direct Sales Report":
        raise ValueError("Only the Direct Sales Report tab may be synced.")
    interval = config.get("interval_seconds", 120)
    if type(interval) is not int or not 30 <= interval <= 86400:
        raise ValueError("Sales sync interval must be between 30 and 86400 seconds.")
    lfo_enabled = config.get("lfo_secondary_enabled", False)
    if not isinstance(lfo_enabled, bool):
        raise ValueError("LFO secondary sync enabled must be true or false.")
    llfo_enabled = config.get("llfo_secondary_enabled", False)
    if not isinstance(llfo_enabled, bool):
        raise ValueError("L-LFO secondary sync enabled must be true or false.")
    # Only used when L-LFO Distributed names no financial year itself.
    llfo_fy = config.get("llfo_secondary_fy")
    if llfo_fy is not None and (type(llfo_fy) is not int or not 2000 <= llfo_fy <= 2098):
        raise ValueError("L-LFO secondary financial year must be a year such as 2026, or omitted.")
    return {"source_id": source_id, "sheet_name": "Direct Sales Report", "interval_seconds": interval,
            "lfo_secondary_enabled": lfo_enabled, "llfo_secondary_enabled": llfo_enabled,
            "llfo_secondary_fy": llfo_fy}


def _fetch_workbook(source_id):
    if (not lfo_data.SOURCE_ID or not isinstance(source_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", source_id)
            or source_id != lfo_data.SOURCE_ID):
        raise ValueError("Invalid or unconfigured sales source.")
    url = f"https://docs.google.com/spreadsheets/d/{source_id}/export?format=xlsx"
    request = Request(url, headers={"User-Agent": "DoctorTowels-SalesSync/1.0", "Cache-Control": "no-cache"})
    with urlopen(request, timeout=30) as response:
        content = response.read(MAX_DOWNLOAD + 1)
    if len(content) > MAX_DOWNLOAD or not content.startswith(b"PK"):
        raise ValueError("The Google Sheets export is not a supported workbook.")
    return openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validation_diagnostics(exc):
    """Expose source counts and locations, never workbook values or exception data."""
    if not isinstance(exc, sheet_sales_sync.SyncValidationError):
        return {}
    supplied = getattr(exc, "diagnostics", {})
    result = {key: supplied[key] for key in ("source_rows", "invoice_rows", "ignored_blank_rows")
              if type(supplied.get(key)) is int and supplied[key] >= 0}
    ignored = []
    for row in supplied.get("ignored_rows", []):
        if not isinstance(row, dict):
            continue
        key, location = row.get("source_key"), row.get("sheet_row")
        if isinstance(key, str) and re.fullmatch(r"[1-9][0-9]{0,14}", key) and type(location) is int and location > 0:
            ignored.append({"source_key": key, "sheet_row": location})
    if ignored:
        result["ignored_rows"] = ignored
    return result


class SyncManager:
    def __init__(self, config_path=CONFIG_PATH):
        self.config_path = Path(config_path)
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._status = {"state": "idle", "enabled": False, "interval_seconds": 120,
                        "last_attempt": None, "last_success": None, "error": None,
                        "counters": {}, "issues": [], "attempt_diagnostics": {},
                        "lfo": {"state": "disabled", "last_success": None, "counters": {}, "warnings": [], "error": None},
                        "llfo": {"state": "disabled", "last_success": None, "counters": {}, "warnings": [], "error": None}}

    def snapshot(self):
        with self._lock:
            return json.loads(json.dumps(self._status))

    def _update(self, **changes):
        with self._lock:
            self._status.update(changes)

    def sync_once(self, on_change=None):
        if not self._run_lock.acquire(blocking=False):
            return self.snapshot()
        workbook = None
        try:
            config = load_config(self.config_path)
            if config is None:
                self._update(state="disabled", enabled=False, error=None)
                return self.snapshot()
            self._update(state="running", enabled=True, interval_seconds=config["interval_seconds"],
                         last_attempt=_now(), error=None, issues=[], attempt_diagnostics={})
            workbook = _fetch_workbook(config["source_id"])
            direct_error = None
            result = {}
            try:
                with closing(pgdata.connect()) as conn:
                    result = sheet_sales_sync.sync_workbook(workbook, config["source_id"], conn)
            except Exception as exc:
                direct_error = exc
            lfo_changed = False
            if config["lfo_secondary_enabled"]:
                previous_lfo = self.snapshot()["lfo"]
                try:
                    with closing(pgdata.connect()) as conn:
                        lfo_result = lfo_data.import_workbook(workbook, conn, actor="google-sheet-sync")
                    lfo_counters = {k: v for k, v in lfo_result.items() if type(v) is int}
                    lfo_changed = bool(lfo_result.get("changed") or any(lfo_counters.get(key) for key in
                                       ("inserted", "updated", "stores_inserted", "weights_updated")))
                    self._update(lfo={"state": "success", "last_success": _now(),
                                      "counters": lfo_counters, "warnings": lfo_result.get("warnings", []), "error": None})
                except Exception:
                    self._update(lfo={**previous_lfo, "state": "error",
                                      "error": "LFO Distributed could not be imported. Previous store sales were preserved. Check the sheet and database migration."})
            else:
                self._update(lfo={"state": "disabled", "last_success": None, "counters": {}, "warnings": [], "error": None})
            # L-LFO Distributed, from the same download. Its own status, its own
            # switch, and its own failure: a problem in that tab never stops the
            # invoice or LFO imports above, and never erases earlier L-LFO data.
            if config.get("llfo_secondary_enabled"):
                previous_llfo = self.snapshot()["llfo"]
                try:
                    with closing(pgdata.connect()) as conn:
                        llfo_result = lfo_data.import_workbook(workbook, conn, actor="google-sheet-sync",
                                                               channel="L-LFO", fy=config.get("llfo_secondary_fy"))
                    llfo_counters = {k: v for k, v in llfo_result.items() if type(v) is int}
                    if llfo_result.get("changed") or any(llfo_counters.get(key) for key in
                                                         ("inserted", "updated", "stores_inserted", "weights_updated")):
                        lfo_changed = True        # the dashboard rebuilds for either channel
                    self._update(llfo={"state": "success", "last_success": _now(),
                                       "counters": llfo_counters, "warnings": llfo_result.get("warnings", []), "error": None})
                except Exception as exc:
                    # A sheet problem is worth naming exactly; anything else stays generic.
                    reason = getattr(exc, "public_message", None) if isinstance(exc, lfo_data.LFOValidationError) else None
                    self._update(llfo={**previous_llfo, "state": "error",
                                       "error": "L-LFO Distributed could not be imported. Previous store sales were preserved. "
                                                + (reason or "Check the sheet and database migration 14.")})
            else:
                self._update(llfo={"state": "disabled", "last_success": None, "counters": {}, "warnings": [], "error": None})
            if direct_error is not None:
                if lfo_changed and on_change:
                    on_change()
                raise direct_error
            if result.get("busy"):
                self._update(state="idle", error="Another sales sync is already running. This worker will retry on schedule.")
                if lfo_changed and on_change:
                    on_change()
                return self.snapshot()
            counters = {k: v for k, v in result.items() if type(v) is int}
            issues = result.get("issues", [])
            self._update(state="warning" if counters.get("conflicts", 0) else "success",
                         counters=counters, issues=issues, error=None, last_success=_now())
            if on_change and (counters.get("inserted", 0) or counters.get("updated", 0) or lfo_changed):
                try:
                    on_change()
                except Exception:
                    self._update(state="warning", error="Sales were imported, but the dashboard could not be refreshed. Reload the dashboard.")
        except Exception as exc:
            if isinstance(exc, HTTPError):
                message = f"Google Sheets export returned HTTP {exc.code}. Check that the source can be read."
            elif isinstance(exc, (URLError, TimeoutError)):
                message = "Google Sheets could not be reached. The next scheduled sync will retry."
            else:
                message = exc.public_message if isinstance(exc, sheet_sales_sync.SyncValidationError) else "Sales sync could not complete. Check the source tab, sync configuration and PostgreSQL connection."
            self._update(state="error", error=message,
                         issues=exc.issues if isinstance(exc, sheet_sales_sync.SyncValidationError) else [],
                         attempt_diagnostics=_validation_diagnostics(exc))
        finally:
            try:
                if workbook is not None:
                    workbook.close()
            except Exception:
                pass  # A cleanup failure must never leave the recurring worker locked.
            finally:
                self._run_lock.release()
        return self.snapshot()

    def start(self, on_change=None):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()

            def run():
                while not self._stop.is_set():
                    self.sync_once(on_change)
                    if self._stop.wait(self.snapshot()["interval_seconds"]):
                        break

            self._thread = threading.Thread(target=run, name="direct-sales-sync", daemon=True)
            self._thread.start()
        return True

    def stop(self, timeout=5):
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout)


manager = SyncManager()
