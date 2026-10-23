"""Users, passwords and login throttling for the dashboard.

The business data lives in PostgreSQL. These credentials do not -- they
sit in a small SQLite file beside the app, each password scrypt-hashed with its
own salt, so a copy of users.db yields nothing usable.

There is no default account and no password in the source. On first run app.py
mints a single admin and prints the password once; that is the only time it is
ever shown.
"""

import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import string
import sys
import time
import dashboard_policy

_PRESERVE_DASHBOARD_ACCESS = object()

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DASH_DB", os.path.join(HERE, "users.db"))
KEY_PATH = os.environ.get("DASH_SECRET", os.path.join(HERE, "session.key"))

# ~16 MB and roughly 50 ms per hash: slow enough to matter against an offline
# crack, fast enough that a login does not feel laggy.
SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)

# Failed attempt N against one username is refused until this long after the
# last one. The port is public, so an unthrottled login form is a standing
# invitation to a dictionary run.
BACKOFF = [0, 0, 0, 2, 5, 15, 60, 300]
MAX_BACKOFF = 900

PW_ALPHABET = string.ascii_letters + string.digits

# First-run identity; generate a password unless explicitly configured.
# Existing users and passwords are never changed during application startup.
INITIAL_ADMIN = os.environ.get("DASH_ADMIN_USER", "admin").strip().lower()
INITIAL_ADMIN_PASSWORD = os.environ.get("DASH_ADMIN_PASSWORD", "")

# Permission names are shared by the account UI and server-side route/field
# checks. Administrators retain full access; other accounts need explicit keys.
SALES_SECTIONS = ("invoice", "classification", "location", "values", "dispatch")
PERMISSION_GROUPS = (
    {"label": "Dashboard", "view": "dashboard.view"},
    {"label": "Sales entries", "create": "sales.create", "edit": "sales.edit", "delete": "sales.delete"},
    {"label": "Invoice & customer", "view": "sales.invoice.view", "edit": "sales.invoice.edit"},
    {"label": "Sales classification", "view": "sales.classification.view", "edit": "sales.classification.edit"},
    {"label": "Location", "view": "sales.location.view", "edit": "sales.location.edit"},
    {"label": "Sales values", "view": "sales.values.view", "edit": "sales.values.edit"},
    {"label": "Dispatch details", "view": "sales.dispatch.view", "edit": "sales.dispatch.edit"},
    {"label": "Courier, weight & transport", "edit": "sales.courier.edit"},
    {"label": "Stock supplied date", "edit": "sales.stock_supplied.edit"},
    {"label": "View registers", "view": "registers.view"},
    {"label": "Export access", "access": "exports.access"},
    {"label": "Targets", "view": "targets.view", "edit": "targets.edit", "delete": "targets.delete"},
    {"label": "Target reports", "view": "target_reports.view"},
    {"label": "LFO monthly store sales", "view": "lfo.sales.view", "create": "lfo.sales.create", "edit": "lfo.sales.edit"},
    {"label": "LFO branches / retailers", "create": "lfo.stores.create"},
    # L-LFO mirrors LFO but is granted separately: entering one channel's
    # sales never implies access to the other's.
    {"label": "L-LFO monthly store sales", "view": "llfo.sales.view", "create": "llfo.sales.create", "edit": "llfo.sales.edit"},
    {"label": "L-LFO branches / retailers", "create": "llfo.stores.create"},
    # Channels whose sub-channels and branches are set up in the Target planner.
    {"label": "Saloon monthly store sales", "view": "saloon.sales.view", "create": "saloon.sales.create", "edit": "saloon.sales.edit"},
    {"label": "Saloon branches / retailers", "create": "saloon.stores.create"},
    {"label": "D&R-S monthly store sales", "view": "drs.sales.view", "create": "drs.sales.create", "edit": "drs.sales.edit"},
    {"label": "D&R-S branches / retailers", "create": "drs.stores.create"},
    {"label": "D&R-N monthly store sales", "view": "drn.sales.view", "create": "drn.sales.create", "edit": "drn.sales.edit"},
    {"label": "D&R-N branches / retailers", "create": "drn.stores.create"},
    {"label": "User creation & access management", "access": "users.manage"},
)
# Every channel with monthly secondary sales, by permission prefix.
SECONDARY_SLUGS = ("lfo", "llfo", "saloon", "drs", "drn")
PERMISSION_KEYS = frozenset(
    key for group in PERMISSION_GROUPS for action, key in group.items() if action != "label"
)
DEFAULT_PERMISSIONS = frozenset({
    "dashboard.view", "registers.view", "targets.view", "target_reports.view",
    *(f"sales.{section}.view" for section in SALES_SECTIONS),
})
# Previously every signed-in user could export. Keep that existing access only
# for accounts being migrated; new accounts require an explicit export grant.
LEGACY_PERMISSIONS = DEFAULT_PERMISSIONS | {"exports.access"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username    TEXT PRIMARY KEY,
    salt        TEXT NOT NULL,
    pw_hash     TEXT NOT NULL,
    is_admin    INTEGER NOT NULL DEFAULT 0,
    disabled    INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL,
    last_login  INTEGER,
    permissions_json TEXT,
    dashboard_access_json TEXT
);
"""

# username -> (consecutive failures, epoch seconds until the next attempt is
# allowed). In memory on purpose: a restart clearing it is acceptable, and it
# keeps a failed login from writing to disk.
_failures: dict[str, tuple[int, float]] = {}


@contextlib.contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with _connect() as conn:
        conn.executescript(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "permissions_json" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN permissions_json TEXT")
        if "dashboard_access_json" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN dashboard_access_json TEXT")
        conn.execute(
            "UPDATE users SET permissions_json = ? WHERE permissions_json IS NULL",
            (json.dumps(sorted(LEGACY_PERMISSIONS)),),
        )
        # One-time legacy migration: later empty policies remain empty.
        conn.execute("UPDATE users SET dashboard_access_json = ? WHERE dashboard_access_json IS NULL",
                     (json.dumps(dashboard_policy.full_policy(), separators=(',', ':')),))


def now() -> int:
    return int(time.time())


# --- passwords ------------------------------------------------------------

def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)


def generate_password(length: int = 16) -> str:
    """Letters and digits only -- deliberate. A password carrying %, &, ^ or $
    breaks silently when it is pasted into a .cmd launcher or a PowerShell
    string, and nobody enjoys debugging that."""
    return "".join(secrets.choice(PW_ALPHABET) for _ in range(length))


# --- users ----------------------------------------------------------------

def normalize_permissions(permissions) -> set[str]:
    """Validate keys and include the visibility required by an edit grant.

    Sales section rights deliberately remain independent: a dispatch operator
    can edit dispatch without gaining access to sales values or customer data.
    """
    if isinstance(permissions, (str, bytes)) or permissions is None:
        raise ValueError("Permissions must be a collection of permission names.")
    try:
        selected = set(permissions)
    except TypeError as exc:
        raise ValueError("Permissions must be a collection of permission names.") from exc
    if not all(isinstance(key, str) for key in selected):
        raise ValueError("Permissions must contain only permission names.")
    unknown = selected - PERMISSION_KEYS
    if unknown:
        raise ValueError("Unknown permissions: " + ", ".join(sorted(unknown)))
    for section in SALES_SECTIONS:
        if f"sales.{section}.edit" in selected:
            selected.add(f"sales.{section}.view")
    if selected & {"sales.courier.edit", "sales.stock_supplied.edit"}:
        selected.update({"sales.dispatch.view", "registers.view"})
    if selected & {"targets.edit", "targets.delete"}:
        selected.add("targets.view")
    for slug in SECONDARY_SLUGS:
        if selected & {f"{slug}.sales.create", f"{slug}.sales.edit", f"{slug}.stores.create"}:
            selected.add(f"{slug}.sales.view")
    return selected


def _user_value(user, key, default=None):
    if user is None:
        return default
    try:
        return user[key]
    except (KeyError, IndexError, TypeError):
        return default


def permissions_for(user) -> set[str]:
    """Return effective grants; missing or invalid stored permissions deny.

    Accepts the SQLite rows returned by get_user/all_users and plain mappings.
    Disabled accounts never gain rights, including administrator accounts.
    """
    if user is None or _user_value(user, "disabled", False):
        return set()
    if _user_value(user, "is_admin", False):
        return set(PERMISSION_KEYS)
    stored = _user_value(user, "permissions_json")
    if not isinstance(stored, str):
        return set()
    try:
        selected = json.loads(stored)
        if not isinstance(selected, list):
            return set()
        return normalize_permissions(selected)
    except (ValueError, TypeError):
        return set()


def can(user, key: str) -> bool:
    """Check a known permission against the current database user row."""
    return key in PERMISSION_KEYS and key in permissions_for(user)


def create_user(username: str, password: str, is_admin: bool = False,
                permissions=None, dashboard_access=None) -> None:
    username = username.strip().lower()
    if not username:
        raise ValueError("Username cannot be blank.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    selected = normalize_permissions(DEFAULT_PERMISSIONS if permissions is None else permissions)
    policy = dashboard_policy.normalize_policy(dashboard_policy.full_policy() if dashboard_access is None else dashboard_access)
    salt = os.urandom(16)
    with _connect() as conn:
        try:
            conn.execute(
                """INSERT INTO users (username, salt, pw_hash, is_admin, created_at, permissions_json, dashboard_access_json)
                   VALUES (?,?,?,?,?,?,?)""",
                (username, base64.b64encode(salt).decode(),
                 base64.b64encode(_hash(password, salt)).decode(),
                 1 if is_admin else 0, now(), json.dumps(sorted(selected)), json.dumps(policy,separators=(',', ':'))),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"User '{username}' already exists.")


def set_permissions(username: str, permissions, dashboard_access=_PRESERVE_DASHBOARD_ACCESS) -> None:
    """Replace grants without changing the account password or admin role."""
    selected = normalize_permissions(permissions)
    policy = None if dashboard_access is _PRESERVE_DASHBOARD_ACCESS else dashboard_policy.normalize_policy(dashboard_access)
    with _connect() as conn:
        if policy is None:
            changed = conn.execute(
                "UPDATE users SET permissions_json = ? WHERE username = ?",
                (json.dumps(sorted(selected)), username.strip().lower()),
            ).rowcount
        else:
            changed = conn.execute(
                "UPDATE users SET permissions_json = ?, dashboard_access_json = ? WHERE username = ?",
                (json.dumps(sorted(selected)), json.dumps(policy,separators=(',', ':')), username.strip().lower()),
            ).rowcount
    if not changed:
        raise ValueError(f"No such user '{username}'.")


def set_password(username: str, password: str) -> None:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    salt = os.urandom(16)
    with _connect() as conn:
        changed = conn.execute(
            "UPDATE users SET salt = ?, pw_hash = ? WHERE username = ?",
            (base64.b64encode(salt).decode(),
             base64.b64encode(_hash(password, salt)).decode(),
             username.strip().lower()),
        ).rowcount
    if not changed:
        raise ValueError(f"No such user '{username}'.")
    _failures.pop(username.strip().lower(), None)


def set_disabled(username: str, disabled: bool) -> None:
    with _connect() as conn:
        changed = conn.execute(
            "UPDATE users SET disabled = ? WHERE username = ?",
            (1 if disabled else 0, username.strip().lower()),
        ).rowcount
    if not changed:
        raise ValueError(f"No such user '{username}'.")


def get_user(username: str):
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip().lower(),)
        ).fetchone()


def all_users():
    with _connect() as conn:
        return conn.execute(
            "SELECT username, is_admin, disabled, created_at, last_login, permissions_json, dashboard_access_json "
            "FROM users ORDER BY username"
        ).fetchall()


def admin_count() -> int:
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE is_admin = 1 AND disabled = 0"
        ).fetchone()["n"]


def user_count() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


# --- login ----------------------------------------------------------------

def throttle_remaining(username: str) -> int:
    _, until = _failures.get(username.strip().lower(), (0, 0.0))
    return max(0, int(until - time.time()))


def _record_failure(username: str) -> None:
    fails, _ = _failures.get(username, (0, 0.0))
    fails += 1
    delay = BACKOFF[fails] if fails < len(BACKOFF) else MAX_BACKOFF
    _failures[username] = (fails, time.time() + delay)


def verify(username: str, password: str):
    """Return (user_row, error_message). Exactly one of the two is None.

    The error text never distinguishes an unknown user from a wrong password:
    saying which is which hands an attacker a free list of valid usernames.
    """
    username = (username or "").strip().lower()
    wait = throttle_remaining(username)
    if wait:
        return None, f"Too many attempts. Try again in {wait}s."

    row = get_user(username)
    if not row:
        # Hash anyway so a missing user does not answer measurably faster than
        # a wrong password.
        _hash(password or "", b"\x00" * 16)
        _record_failure(username)
        return None, "Incorrect username or password."

    salt = base64.b64decode(row["salt"])
    expected = base64.b64decode(row["pw_hash"])
    if not hmac.compare_digest(_hash(password or "", salt), expected):
        _record_failure(username)
        return None, "Incorrect username or password."

    if row["disabled"]:
        # Checked after the password so a disabled account cannot be used to
        # probe which usernames exist.
        return None, "This account has been disabled."

    _failures.pop(username, None)
    with _connect() as conn:
        conn.execute("UPDATE users SET last_login = ? WHERE username = ?",
                     (now(), username))
    return row, None


# --- session signing key --------------------------------------------------

def session_key() -> str:
    """Persist the cookie-signing key, or every restart logs everyone out."""
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "r", encoding="utf-8") as fh:
            key = fh.read().strip()
        if key:
            return key
    key = secrets.token_urlsafe(48)
    with open(KEY_PATH, "w", encoding="utf-8") as fh:
        fh.write(key)
    try:
        os.chmod(KEY_PATH, 0o600)
    except OSError:
        pass       # Windows ACLs do not map onto this; the folder is not shared
    return key


def bootstrap_admin():
    """First run only. Returns (username, password) if one was created."""
    if user_count():
        return None
    password = INITIAL_ADMIN_PASSWORD or generate_password()
    create_user(INITIAL_ADMIN, password, is_admin=True)
    return INITIAL_ADMIN, password


def _cli(argv) -> int:
    """Manage accounts without the web UI.

        python auth.py list
        python auth.py add <username> [password] [--admin]
        python auth.py passwd <username> [password]
        python auth.py disable <username>
        python auth.py enable  <username>

    The point of this is the locked-out case: with it, losing the last admin
    password costs one command instead of deleting users.db and every account
    in it. It edits the same file the app reads, so the app can be running.
    """
    init()
    cmd = (argv[0] if argv else "").lower()
    args = [a for a in argv[1:] if not a.startswith("--")]
    is_admin = "--admin" in argv[1:]

    if cmd == "list":
        rows = all_users()
        if not rows:
            print("No users yet. The app creates one on first start.")
            return 0
        print(f"{'USERNAME':<24}{'ROLE':<10}{'STATE':<10}LAST SIGNED IN")
        for u in rows:
            last = (time.strftime("%Y-%m-%d %H:%M", time.localtime(u["last_login"]))
                    if u["last_login"] else "never")
            print(f"{u['username']:<24}{'admin' if u['is_admin'] else 'user':<10}"
                  f"{'disabled' if u['disabled'] else 'active':<10}{last}")
        return 0

    if cmd in ("add", "passwd") and args:
        username = args[0]
        password = args[1] if len(args) > 1 else generate_password()
        shown = len(args) < 2
        try:
            if cmd == "add":
                create_user(username, password, is_admin=is_admin)
            else:
                set_password(username, password)
        except ValueError as exc:
            print(f"{exc}")
            return 1
        verb = "Created" if cmd == "add" else "Password reset for"
        print(f"{verb} {username.strip().lower()}"
              f"{' (administrator)' if cmd == 'add' and is_admin else ''}.")
        if shown:
            print(f"  password: {password}")
            print("  Not stored anywhere in readable form -- copy it now.")
        return 0

    if cmd in ("disable", "enable") and args:
        try:
            set_disabled(args[0], cmd == "disable")
        except ValueError as exc:
            print(f"{exc}")
            return 1
        print(f"{args[0].strip().lower()} {cmd}d.")
        return 0

    print(_cli.__doc__.split("\n\n")[1].strip())
    return 2


def uses_password(username: str, password: str) -> bool:
    """Does this account still have exactly this password?

    Side-effect free on purpose -- no throttle counter, no last_login write --
    because it is called at startup to check whether the shipped default is
    still in place, not as part of anyone signing in.
    """
    row = get_user(username)
    if not row:
        return False
    return hmac.compare_digest(
        _hash(password, base64.b64decode(row["salt"])),
        base64.b64decode(row["pw_hash"]))


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
