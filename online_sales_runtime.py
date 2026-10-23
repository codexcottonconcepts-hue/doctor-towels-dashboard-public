"""Dedicated one-way Online Sales worker, independent of Direct Sales sync."""
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
import online_sales_data
import online_sales_store

CONFIG_PATH = Path(__file__).resolve().with_name("online-sales-sync.json")
MAX_DOWNLOAD = 30_000_000


def load_config(path=CONFIG_PATH):
    path = Path(path)
    if not path.exists():
        return None
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise ValueError("The online sales sync configuration cannot be read.") from None
    if not isinstance(config, dict) or type(config.get("enabled", False)) is not bool:
        raise ValueError("Online sales enabled must be true or false.")
    if not config.get("enabled", False):
        return None
    source_id = config.get("source_id")
    if (not online_sales_data.SOURCE_ID or not isinstance(source_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", source_id)
            or source_id != online_sales_data.SOURCE_ID):
        raise ValueError("Set ONLINE_SALES_SOURCE_ID and the sync configuration to your own dedicated Google spreadsheet ID.")
    if config.get("daily_tab", online_sales_data.DAILY_TAB) != online_sales_data.DAILY_TAB or config.get("monthly_tab", online_sales_data.MONTHLY_TAB) != online_sales_data.MONTHLY_TAB:
        raise ValueError("Online sales requires the Daily Sales and Monthly Sales tabs.")
    interval = config.get("interval_seconds", 120)
    if type(interval) is not int or not 30 <= interval <= 86400:
        raise ValueError("Online sales sync interval must be between 30 and 86400 seconds.")
    return {"source_id": config["source_id"], "interval_seconds": interval}


def _fetch_workbook(source_id):
    # Only the separately configured online workbook can be fetched here.
    if (not online_sales_data.SOURCE_ID or not isinstance(source_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", source_id)
            or source_id != online_sales_data.SOURCE_ID):
        raise ValueError("Invalid online sales source.")
    request = Request(f"https://docs.google.com/spreadsheets/d/{source_id}/export?format=xlsx",
                      headers={"User-Agent": "DoctorTowels-OnlineSalesSync/1.0", "Cache-Control": "no-cache"})
    with urlopen(request, timeout=30) as response:
        content = response.read(MAX_DOWNLOAD + 1)
    if len(content) > MAX_DOWNLOAD or not content.startswith(b"PK"):
        raise ValueError("The online Google Sheets export is not a supported workbook.")
    return openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SyncManager:
    def __init__(self, config_path=CONFIG_PATH):
        self.config_path = Path(config_path)
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._status = {"state": "idle", "enabled": False, "interval_seconds": 120,
                        "last_attempt": None, "last_success": None, "error": None,
                        "counters": {}, "warnings": []}

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
                         last_attempt=_now(), error=None)
            workbook = _fetch_workbook(config["source_id"])
            with closing(online_sales_store.connect()) as conn:
                result = online_sales_data.sync_workbook(workbook, config["source_id"], conn)
            if result.get("busy"):
                self._update(state="idle", error="Another online sales sync is running. This worker will retry on schedule.")
                return self.snapshot()
            self._update(state="success", last_success=_now(), error=None,
                         counters={key: value for key, value in result.items() if type(value) is int},
                         warnings=result.get("warnings", []))
            if result.get("changed") and on_change:
                try:
                    on_change()
                except Exception:
                    self._update(state="warning", error="Online sales was imported, but the dashboard refresh failed. Reload the dashboard.")
        except Exception as exc:
            if isinstance(exc, HTTPError):
                message = f"Online Google Sheets export returned HTTP {exc.code}. Previous online sales were preserved."
            elif isinstance(exc, (URLError, TimeoutError)):
                message = "Online Google Sheets could not be reached. Previous online sales were preserved; the next sync will retry."
            else:
                message = getattr(exc, "public_message", None) or "Online sales sync failed. Previous online sales were preserved. Check its source, configuration and database migration."
            self._update(state="error", error=message)
        finally:
            try:
                if workbook is not None:
                    workbook.close()
            except Exception:
                pass
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

            self._thread = threading.Thread(target=run, name="online-sales-sync", daemon=True)
            self._thread.start()
        return True

    def stop(self, timeout=5):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout)


manager = SyncManager()
