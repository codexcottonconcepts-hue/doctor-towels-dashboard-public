"""Database Management dashboard, behind a login.

Business data is read exclusively from PostgreSQL. Accounts carry explicit
module and sales-section permissions checked on every request.
A background thread rebuilds the dashboard every REFRESH_SECONDS.
An optional Direct Sales importer updates PostgreSQL from the configured Google Sheet.

    python app.py

Settings, all optional, from the environment:
    PORT=8090                 port to listen on
    BIND=0.0.0.0              0.0.0.0 serves the LAN and any forwarded public IP
    REFRESH_SECONDS=120       how stale the data may get before a re-read
    PUBLISH_DIR=D:\\DST&TT     where the rendered index.html is also copied
    DASH_DB / DASH_SECRET     override the users.db and session.key locations
    DATABASE_URL             PostgreSQL application connection string
    HTTPS_ONLY=true          secure session cookies behind an HTTPS proxy

Routes:
    /                 the dashboard                     (login required)
    /login /logout    session login
    /admin/users      users and access                  (users.manage)
    /entries          sales register and entry form     (granular permissions)
    /targets/planner  primary target-planning page      (target permissions)
    /targets          advanced target settings          (target permissions)
    /reports/targets  per-scope target achievement and CSV
    /stores/new       add a branch / retailer in any channel (per-channel permission)
    /refresh          force a rebuild                   (login required)
    /healthz          {"ok": bool} public; full detail once logged in

Use local HTTP for development. For access from anywhere, deploy behind HTTPS
and set HTTPS_ONLY=true. Follow START_HERE.md for migration and hosting.
"""

import os
import json
import sys
import threading
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth
import refresh as R
import profile_ui
import dropdown_ui
import dashboard_policy
import dashboard_data_access

PORT = int(os.environ.get("PORT", "8090"))
BIND = os.environ.get("BIND", "0.0.0.0")
# Re-read PostgreSQL periodically; entry and target writes invalidate this cache.
TTL = int(os.environ.get("REFRESH_SECONDS", "120"))
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "12"))
HTTPS_ONLY = os.environ.get("HTTPS_ONLY", "false").lower() in ("true", "1", "yes")

# Read once at import rather than per request -- it is a base64 blob of a file
# that does not change while the process runs. Swapping logo.png needs a restart.
# Empty when there is no logo.png, and every use below is guarded on that.
LOGO = R.logo_uri()
FAVICON = R.favicon_link()              # the browser-tab icon, read once like the logo
# The full "doctor towels / IT'S SAFE" logo for the sign-in page; "" without the file.
SIGNIN_LOGO = R.png_uri("logo-full.png")

@asynccontextmanager
async def lifespan(_app):
    """Bootstrap on the way up, whichever way the app was launched.

    Replaces @app.on_event("startup"), which FastAPI has deprecated. Blocking
    here is deliberate: the first build reads PostgreSQL, and nothing
    should be served until it has, or the first viewer gets an empty page.
    """
    startup()
    try:
        yield
    finally:
        sheet_sync_runtime.manager.stop()
        online_sales_runtime.manager.stop()


app = FastAPI(title="Doctor Towels", docs_url=None, redoc_url=None,
              lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    SessionMiddleware,
    secret_key=auth.session_key(),
    session_cookie="dash_session",
    max_age=SESSION_HOURS * 3600,
    same_site="lax",
    https_only=HTTPS_ONLY,
)

_lock = threading.Lock()
_state = {"html": None, "data": None, "built": 0.0, "error": None, "summary": [], "builds": 0}


# --- build ----------------------------------------------------------------

def _build():
    """Re-read PostgreSQL and render. A failure keeps the previous page on air."""
    try:
        data, lines = R.build_data(log=None)
        html = R.render_html(data)
        _state.update(html=html, data=data, built=time.time(), error=None, summary=lines,
                      builds=_state["builds"] + 1)
        R.publish(html, log=lambda m: print(m.rstrip()))
        # Name the source rather than assume it. Which one served a given
        # rebuild is the first thing worth knowing when a number looks wrong,
        # and during the parallel run the two are both live.
        print("[%s] rebuilt from %s (%d bytes, %s rows)"
              % (time.strftime("%H:%M:%S"), data.get("splitSource", "PostgreSQL"),
                 len(html), f"{len(data.get('rows', [])):,}"))
    except SystemExit as exc:                  # refresh.py exits on a fatal problem
        _state["error"] = str(exc)
        print("[%s] REFRESH FAILED: %s" % (time.strftime("%H:%M:%S"), exc))
    except Exception as exc:                   # network blip, malformed cell, ...
        _state["error"] = "%s: %s" % (type(exc).__name__, exc)
        print("[%s] REFRESH ERROR: %s" % (time.strftime("%H:%M:%S"), _state["error"]))


def ensure(force: bool = False):
    with _lock:
        fresh = _state["html"] and (time.time() - _state["built"]) < TTL
        if force or not fresh:
            _build()
        if _state["html"] is None:             # first build failed - last file on disk
            path = os.path.join(R.HERE, "dashboard.html")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    _state["html"] = fh.read()
                _state["built"] = os.path.getmtime(path)
                print("    serving the last dashboard.html on disk while PostgreSQL is unavailable")
        return _state["html"]


def keep_warm():
    """Rebuild a little before the page goes stale, so no viewer waits on a fetch."""
    while True:
        time.sleep(max(15, TTL // 4))   # check often enough that TTL is the real bound
        try:
            if _state["built"] and (time.time() - _state["built"]) >= TTL:
                with _lock:
                    _build()
        except Exception as exc:
            print("    keep-warm error: %r" % (exc,))


# --- session helpers ------------------------------------------------------

def current_user(request: Request):
    """The logged-in user, or None. A user disabled mid-session loses it here on
    their next request rather than at the next restart."""
    name = request.session.get("user")
    if not name:
        return None
    row = auth.get_user(name)
    if not row or row["disabled"]:
        request.session.clear()
        return None
    return row


def _login_redirect(request: Request):
    nxt = request.url.path
    suffix = f"?next={nxt}" if nxt and nxt != "/" else ""
    return RedirectResponse(f"/login{suffix}", status_code=303)


# --- chrome ---------------------------------------------------------------

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f6f6f7;color:#0f172a;
     font:14px/1.45 "Segoe UI",system-ui,-apple-system,sans-serif}
a{color:#c8102e}
.wrap{max-width:960px;margin:0 auto;padding:28px 20px 48px}
.bar{background:#101012;color:#fff;padding:12px 20px;display:flex;
     align-items:center;gap:12px}
.bar .mk{width:30px;height:30px;border-radius:7px;background:#c8102e;color:#fff;
     font-weight:700;display:flex;align-items:center;justify-content:center}
.bar b{font-size:15px;letter-spacing:-.02em}
.bar .sp{margin-left:auto;font-size:13px;opacity:.85}
.bar a{color:#f4a9b3}
.card{background:#fff;border:1px solid #e5eaf2;border-radius:10px;padding:22px;
      box-shadow:0 1px 2px rgba(15,42,82,.06),0 6px 18px -12px rgba(15,42,82,.28)}
h1{font-size:19px;margin:0 0 4px}
h2{font-size:15px;margin:26px 0 10px}
p.muted{color:#64748b;margin:0 0 18px}
label{display:block;font-weight:600;margin:14px 0 5px}
input[type=text],input[type=password]{width:100%;padding:9px 11px;font-size:14px;
      border:1px solid #cbd5e1;border-radius:7px;background:#fff}
input:focus{outline:2px solid #c8102e;outline-offset:0;border-color:#c8102e}
button{margin-top:16px;padding:9px 16px;font-size:14px;font-weight:600;color:#fff;
       background:#c8102e;border:0;border-radius:7px;cursor:pointer}
button:hover{background:#a30d25}
button.sec{background:#fff;color:#0f172a;border:1px solid #cbd5e1;margin:0}
button.sec:hover{background:#f1f5f9}
.err{background:#fef2f2;border:1px solid #fecaca;color:#b91c1c;padding:10px 12px;
     border-radius:7px;margin:16px 0 0}
.ok{background:#f0fdf4;border:1px solid #bbf7d0;color:#15803d;padding:10px 12px;
    border-radius:7px;margin:16px 0 0}
.ok code{font-size:15px;font-weight:700;letter-spacing:.02em}
table{width:100%;border-collapse:collapse;margin-top:6px;font-size:13px}
th{text-align:left;color:#64748b;font-weight:600;border-bottom:1px solid #e5eaf2;
   padding:8px 10px}
td{border-bottom:1px solid #f1f5f9;padding:8px 10px;vertical-align:middle}
td form{display:inline;margin:0}
.tag{font-size:11px;font-weight:700;padding:2px 7px;border-radius:99px}
.tag.a{background:#e4e4e7;color:#26262b}
.tag.d{background:#fee2e2;color:#b91c1c}
.row{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap}
.row>div{flex:1;min-width:160px}
.chk{display:flex;align-items:center;gap:7px;margin-top:14px;font-weight:600}
.chk input{width:auto}
/* Logo in place of the lettermark, black behind it to match the artwork's own
   background, and `contain` so the mark is never cropped. Both mean any
   letterboxing is black on black and cannot be seen. */
.bar .mk.logo{background:#000;padding:0;overflow:hidden}
.bar .mk.logo img{display:block;width:100%;height:100%;object-fit:contain}
"""


# The tile in the bar: the logo when there is one, the DT lettermark otherwise.
BAR_MARK = (f'<span class="mk logo"><img src="{LOGO}" alt=""></span>' if LOGO
            else '<span class="mk">DT</span>')


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def access_links(user):
    links = []
    for permission, href, label in (
        ("dashboard.view", "/", "Dashboard"),
        ("registers.view", "/entries", "Sales register"),
        ("sales.create", "/entries?view=new", "New sales entry"),
        ("targets.view", "/targets/planner", "Target planner"),
        ("target_reports.view", "/reports/targets", "Target reports"),
        # One entry for every channel's branches; any channel's permission shows it.
        (lfo_web.BRANCH_PERMISSIONS, lfo_web.BRANCH_NEW, lfo_web.BRANCH_TITLE),
        ("users.manage", "/admin/users", "Users & access"),
    ):
        keys = (permission,) if isinstance(permission, str) else permission
        if any(auth.can(user, key) for key in keys):
            links.append((href, label))
    if user and user["is_admin"]:
        links.append(("/admin/sales-sync", "Data sync"))
        links.append(("/admin/online-sales-sync", "Online sales sync"))
    return links


def home_url(user):
    links = access_links(user)
    if links:
        return links[0][0]
    # Operators can retain a permitted landing page without a profile-menu item.
    for ch in lfo_web.CHANNELS:
        if auth.can(user, ch.perm("sales.view")):
            return ch.base
    return "/"


def page(title: str, body: str, user=None, bar: bool = True) -> str:
    """A server page. bar=False leaves out the top bar, for the sign-in page whose
    own card carries the full logo."""
    nav = ""
    if user:
        nav = profile_ui.render(user["username"], user["is_admin"], access_links(user))
    top = f'<div class="bar">{BAR_MARK}<b>DOCTOR TOWELS</b>{nav}</div>' if bar else ""
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title>{FAVICON}<style>{CSS}{profile_ui.CSS}</style></head><body>'
            f'{top}<div class="wrap">{body}</div>{dropdown_ui.assets()}</body></html>')


# --- login ----------------------------------------------------------------

SIGNIN_CSS = """<style>
.signin{max-width:400px;margin:8vh auto 0;padding:0;overflow:hidden}
.signin-brand{background:#000;padding:26px 30px 22px}
.signin-brand img{display:block;width:100%;max-width:300px;height:auto;margin:0 auto}
.signin-form{padding:20px 24px 24px}
.signin-form h1{margin-bottom:2px}
.signin-form button{width:100%;margin-top:20px;padding:11px 16px}
@media(max-width:480px){.signin{margin-top:3vh}.signin-brand{padding:22px 22px 18px}.signin-form{padding:18px}}
</style>"""


def _signin_page(next_url, username="", err=""):
    """The sign-in page: the full logo on its own black above the form. Without
    logo-full.png it is the plain card under the usual top bar."""
    form = f"""
        <h1>Sign in</h1>
        <form method="post" action="/login">
          <input type="hidden" name="next" value="{esc(next_url)}">
          <label for="u">Username</label>
          <input id="u" name="username" type="text" autocomplete="username"
                 autocapitalize="none"{f' value="{esc(username)}"' if username else ''} autofocus required>
          <label for="p">Password</label>
          <input id="p" name="password" type="password"
                 autocomplete="current-password" required>
          <button type="submit">Sign in</button>
          {f'<div class="err">{esc(err)}</div>' if err else ''}
        </form>"""
    if not SIGNIN_LOGO:
        return page("Sign in", f'<div class="card" style="max-width:400px;margin:6vh auto 0">{form}</div>')
    return page("Sign in", SIGNIN_CSS + f"""
      <div class="card signin">
        <div class="signin-brand"><img src="{SIGNIN_LOGO}" alt="Doctor Towels — It's safe" width="720" height="289"></div>
        <div class="signin-form">{form}</div>
      </div>""", bar=False)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    user = current_user(request)
    if user:
        return RedirectResponse(home_url(user), status_code=303)
    return HTMLResponse(_signin_page(next))


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(""),
                       password: str = Form(""), next: str = Form("/")):
    user, err = auth.verify(username, password)
    if err:
        return HTMLResponse(_signin_page(next, username, err), status_code=401)

    request.session.clear()
    request.session["user"] = user["username"]
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    if target == "/":
        target = home_url(user)
    return RedirectResponse(target, status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# --- dashboard ------------------------------------------------------------

def _inject_chrome(html: str, user) -> str:
    """Add permission-filtered account controls after the shared page is built."""
    flag = lambda permission: "true" if auth.can(user, permission) else "false"
    # Each channel's Secondary page shows its entry links from its own flags
    # (LFO_ENTRY_ACCESS, LLFO_STORE_CREATE, SALOON_ENTRY_ACCESS, ...).
    flags = "".join(f"window.{ch.slug.upper()}_ENTRY_ACCESS={flag(ch.perm('sales.view'))};"
                    f"window.{ch.slug.upper()}_STORE_CREATE={flag(ch.perm('stores.create'))};"
                    for ch in lfo_web.CHANNELS)
    # Target Balance links to the Target planner.
    flags += f"window.TARGETS_VIEW={flag('targets.view')};window.TARGETS_EDIT={flag('targets.edit')};"
    # Existing TV clients compare these compatible boolean markers on each
    # authenticated poll, so they load the new reports without a manual refresh.
    flags += "window.ONLINE_HISTORY=true;window.ONLINE_COMBINED=true;window.ONLINE_RANKED=true;"
    policy_json = json.dumps(dashboard_policy.policy_for(user), separators=(",", ":")).replace("<", "\\u003c")
    flags += f"window.DASHBOARD_ACCESS={policy_json};"
    entry_script = f"<script>{flags}</script>"
    # The dashboard template is an HTML fragment with no explicit head tag.
    html = (html.replace("</head>", entry_script + "</head>", 1)
            if "</head>" in html else entry_script + html)
    html = html.replace('<!--__SALES_SYNC_STATUS__-->', sheet_sync_web.footer(sheet_sync_runtime.manager.snapshot()))
    block = profile_ui.render(user["username"], user["is_admin"], access_links(user))
    styles = f'<style>{profile_ui.CSS}</style>'
    if '<!--__USER_PROFILE__-->' in html:
        return html.replace('<!--__USER_PROFILE__-->', styles + block, 1)
    # Cached/fallback documents may predate the profile placeholder.
    block = styles + block.replace('class="account-profile"', 'class="account-profile account-profile--floating"', 1)
    return html.replace("</body>", block + "</body>", 1) if "</body>" in html else html + block


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    user = current_user(request)
    if not user:
        return _login_redirect(request)
    if not auth.can(user, "dashboard.view"):
        return HTMLResponse(page("Access denied", '<div class="card"><h1>Dashboard access is not enabled</h1><p>Use an available link above or ask your administrator to update your access.</p></div>', user), status_code=403)
    policy = dashboard_policy.policy_for(user)
    requested_page = request.query_params.get("page")
    if (requested_page and requested_page not in policy["pages"]) or not policy["pages"]:
        return HTMLResponse(page("Access denied", '<div class="card"><h1>This dashboard page is not enabled</h1><p>Ask your administrator to update your dashboard access.</p></div>', user), status_code=403, headers={"Cache-Control": "no-store"})
    html = ensure()
    if not dashboard_policy.is_unrestricted(user):
        # The shared HTML and disk fallback contain company-wide data. They must
        # never bypass section-specific online access or restricted selections.
        with _lock:
            source = _state.get("data")
        if source is None:
            return PlainTextResponse("Your dashboard data is temporarily unavailable. Please try again.", status_code=503, headers={"Cache-Control": "no-store"})
        html = R.render_html(dashboard_data_access.project_data(source, policy))
    if html is None:
        return PlainTextResponse(
            "The dashboard could not be built from PostgreSQL.\n\n%s"
            % (_state["error"] or "unknown error"), status_code=503)
    return HTMLResponse(_inject_chrome(html, user), headers={
        "Cache-Control": "no-store",
        "X-Data-Age-Seconds": str(int(time.time() - _state["built"])),
    })


@app.post("/refresh")
@app.get("/refresh")
def force_refresh(request: Request):
    user = current_user(request)
    if not user:
        return _login_redirect(request)
    if not auth.can(user, "dashboard.view"):
        return JSONResponse({"error": "Dashboard access is required."}, status_code=403)
    ensure(force=True)
    if not dashboard_policy.is_unrestricted(user):
        return JSONResponse({"rebuilt": not bool(_state["error"]),
                             "error": "Dashboard refresh is temporarily unavailable." if _state["error"] else None},
                            headers={"Cache-Control": "no-store"})
    return JSONResponse({"rebuilt": not bool(_state["error"]), "error": _state["error"],
                         "summary": _state["summary"][:4]}, headers={"Cache-Control": "no-store"})


@app.get("/healthz")
def healthz(request: Request):
    """Public answer is a bare ok/not-ok so uptime checks work without a login.
    The detail -- row counts, turnover, the last error -- is behind the session,
    because on a public IP that summary is the financials in miniature."""
    age = int(time.time() - _state["built"]) if _state["built"] else None
    # Holding a page is not the same as the data still arriving. A refresh that
    # keeps failing leaves the last good HTML on air, which is right for viewers
    # and wrong for a monitor - so age counts, not just the presence of a page.
    STALE_AFTER = max(3 * TTL, 3600)
    stale = age is None or age > STALE_AFTER
    ok = _state["html"] is not None and not stale
    health_user = current_user(request)
    if not auth.can(health_user, "dashboard.view") or not dashboard_policy.is_unrestricted(health_user):
        return JSONResponse({"ok": ok, "stale": stale})
    return JSONResponse({
        "ok": ok, "stale": stale, "stale_after_seconds": STALE_AFTER,
        "data_age_seconds": age, "refresh_seconds": TTL,
        "builds": _state["builds"], "last_error": _state["error"],
        "summary": _state["summary"][:4],
    })


# --- PostgreSQL entry and target pages ------------------------------------

def invalidate_dashboard():
    with _lock:
        _state["built"] = 0.0


import entry_web
import target_web
import user_web
import sheet_sync_runtime
import sheet_sync_web
import online_sales_runtime
import online_sales_web
import lfo_web
import target_planner
import sales_breakdown


def sales_sync_changed():
    invalidate_dashboard()
    ensure(force=True)

def dashboard_access_options():
    """Internal source for the account editor; never serves the raw payload."""
    ensure()
    with _lock:
        return _state.get("data") or {}


user_web.register(app, current_user, _login_redirect, page, dashboard_data=dashboard_access_options)
entry_web.register(app, current_user, _login_redirect, page, invalidate_dashboard)
target_web.register(app, current_user, _login_redirect, page, invalidate_dashboard)
lfo_web.register(app, current_user, _login_redirect, page, invalidate_dashboard)
target_planner.register(app, current_user, _login_redirect, page, invalidate_dashboard)
sales_breakdown.register(app, current_user, _login_redirect, page)
sheet_sync_web.register(app, current_user, _login_redirect, page,
                        sheet_sync_runtime.manager, sales_sync_changed)
online_sales_web.register(app, current_user, _login_redirect, page,
                          online_sales_runtime.manager, sales_sync_changed)


# --- startup --------------------------------------------------------------

_started = False


def startup():
    """Run from the lifespan handler rather than __main__, so that launching
    with `uvicorn app:app` -- which is what a service wrapper does -- still
    creates the first admin and starts the refresh thread."""
    global _started
    if _started:
        return
    _started = True

    auth.init()
    if HTTPS_ONLY and not auth.user_count() and not auth.INITIAL_ADMIN_PASSWORD:
        raise RuntimeError("Create the initial administrator with auth.py, or set "
                           "DASH_ADMIN_PASSWORD in the hosting secret settings before first startup.")
    fresh = auth.bootstrap_admin()
    if fresh:
        name, pw = fresh
        print("\n" + "=" * 64)
        print("  FIRST RUN - an administrator account has been created")
        print("=" * 64)
        print(f"    username : {name}")
        if auth.INITIAL_ADMIN_PASSWORD:
            print("    password : supplied through DASH_ADMIN_PASSWORD (not logged)")
        else:
            print(f"    password : {pw}")
        print("\n  Sign in and add your users at /admin/users.")
        print("=" * 64 + "\n")

    # Repeated every start, not just the first, because the shipped default is a
    # string every credential scanner already tries. Silence it by changing the
    # password, not by deleting this.
    if auth.uses_password(auth.INITIAL_ADMIN, "Admin@123"):
        print("!" * 64)
        print(f"  WARNING: '{auth.INITIAL_ADMIN}' still has the default password.")
        print("  Change it at /admin/users before this port is reachable from")
        print("  outside the office.")
        print("!" * 64 + "\n")

    print("Building the first page from PostgreSQL ...")
    ensure(force=True)
    if _state["error"] and _state["html"] is None:
        sys.exit("FAILED to build at startup: %s" % _state["error"])
    threading.Thread(target=keep_warm, daemon=True).start()
    sheet_sync_runtime.manager.start(sales_sync_changed)
    online_sales_runtime.manager.start(sales_sync_changed)


if __name__ == "__main__":
    print(f"\n  dashboard   http://{BIND}:{PORT}/")
    print(f"  users       http://{BIND}:{PORT}/admin/users")
    print(f"  data reread every {TTL} s from PostgreSQL")
    print(f"  publish dir {R.PUBLISH_DIR or '(off)'}")
    print("\n  Ctrl+C to stop.\n")
    uvicorn.run(app, host=BIND, port=PORT, log_level="info")
