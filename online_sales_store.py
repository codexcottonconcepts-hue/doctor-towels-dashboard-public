"""Connections and optional dashboard reads for the dedicated Online Sales DB.

Credentials remain in the existing private PostgreSQL configuration. Only the
database name changes; Online Sales never reads or writes the primary database.
"""
from contextlib import closing
from copy import deepcopy
import threading

import online_sales_data

DATABASE_NAME = "dstt_online"
CONNECT_TIMEOUT_SECONDS = 5
_cache_lock = threading.Lock()
_last_good = None
_UNAVAILABLE = "Online sales database is temporarily unavailable."
_STALE = "Online sales database is temporarily unavailable. Showing the last successful import."


def _dsn():
    # Import lazily: pgdata builds the primary payload and calls this module.
    # Never save or expose the derived connection string in a public receipt.
    import pgdata
    from psycopg2.extensions import make_dsn

    value = pgdata.dsn()
    if not value:
        raise pgdata.NotConfigured("The private PostgreSQL configuration is missing.")
    return make_dsn(value, dbname=DATABASE_NAME)


def connect():
    """Use the existing runtime role against dstt_online on the same cluster."""
    import pgdata

    if pgdata.psycopg2 is None:
        raise pgdata.NotConfigured("psycopg2 is not installed (pip install psycopg2-binary)")
    return pgdata.psycopg2.connect(_dsn(), connect_timeout=CONNECT_TIMEOUT_SECONDS)


def _unavailable():
    with _cache_lock:
        previous = deepcopy(_last_good)
    if previous is not None:
        previous.update(stale=True, staleReason=_STALE)
        return previous
    return {**online_sales_data.unavailable(_UNAVAILABLE),
            "database": DATABASE_NAME, "stale": False}


def fetch_dashboard():
    """An independent snapshot; an Online DB failure cannot fail Direct Sales.

    A process-local copy of the last successful import remains available during
    an Online database outage. It is explicitly marked stale for presentation.
    """
    global _last_good
    try:
        with closing(connect()) as conn:
            conn.set_session(isolation_level="REPEATABLE READ", readonly=True)
            payload = online_sales_data.fetch_dashboard(conn)
        if not payload.get("available"):
            return _unavailable()
        payload = {**payload, "database": DATABASE_NAME, "stale": False}
        with _cache_lock:
            _last_good = deepcopy(payload)
        return payload
    except Exception:
        # Driver messages can include private connection details. The Online
        # slice is optional, so neither those messages nor the failure escape.
        return _unavailable()
