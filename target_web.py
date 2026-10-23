"""Authenticated monthly target management and achievement reports.

Register against the existing application with ``register(app, current_user,
login_redirect, page, invalidate)``. Database credentials stay in pgdata; the
HTML contains only reference IDs and display labels.
"""

import csv
import io
import re
import secrets
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation
from html import escape
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

import pgdata
import auth


METRICS = {
    "primary_taxable": "Primary sales before GST (₹)",
    "primary_with_tax": "Primary sales including GST (₹)",
    "primary_qty": "Primary quantity (units)",
    "secondary_value": "Secondary sales (₹)",
    "tertiary_value": "Tertiary sales (₹)",
}
DIMENSIONS = (
    ("person_id", "Salesperson", "sales_person"),
    ("channel_id", "Channel", "channel"),
    ("sub_channel_id", "Sub-channel", "sub_channel"),
    ("state_id", "State", "state"),
    ("district_id", "District", "district"),
    ("city_id", "City / location", "city"),
    ("brand_id", "Brand", "brand"),
)
DB_MESSAGE = (
    "PostgreSQL target management is not available. Ask your administrator to "
    "configure DATABASE_URL, install the application requirements, and apply "
    "the database migrations. The existing dashboard remains available."
)
CSS = """
.tw{--line:#e2e8f0}.tw .intro{margin-bottom:20px}.tw .muted{color:#64748b}
.tw .grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.tw .grid label{margin:0 0 5px}.tw .grid .wide{grid-column:1/-1}
.tw input:not([type=hidden]),.tw select,.tw textarea{font:inherit;width:100%;
 padding:10px;border:1px solid #cbd5e1;border-radius:7px;background:#fff;color:#0f172a}
.tw input:focus,.tw select:focus,.tw textarea:focus{outline:2px solid #c8102e}
.tw textarea{min-height:75px;resize:vertical}.tw fieldset{border:0;margin:0;padding:0}
.tw fieldset:disabled{opacity:.55}.tw .card{margin:18px 0}.tw h2{margin-top:0}
.tw .note{background:#f1f5f9;padding:12px 14px;border-radius:7px;margin:14px 0}
.tw .scroll{overflow-x:auto}.tw td,.tw th{white-space:nowrap}.tw td.scope{white-space:normal;min-width:210px}
.tw .actions{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:15px}
.tw .actions button{margin-top:0}.tw .pill{display:inline-block;padding:3px 9px;
 border-radius:20px;background:#f1f5f9;font-size:12px;font-weight:600}
.tw .pill.active{background:#dcfce7;color:#166534}.tw .num{text-align:right;font-variant-numeric:tabular-nums}
.tw small{display:block;color:#64748b;margin-top:5px}.tw .err,.tw .ok{margin:16px 0}
.tw .stats{display:flex;gap:12px;flex-wrap:wrap;margin-top:14px}.tw .stats>span{background:#f1f5f9;padding:9px 12px;border-radius:7px}
.tw .inline-form{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap}
.tw .inline-form>div{flex:1;min-width:160px}.tw .inline-form button{margin:0}
@media(max-width:720px){.tw .grid{grid-template-columns:1fr 1fr}.tw .card{padding:16px}}
@media(max-width:480px){.tw .grid{grid-template-columns:1fr}}
"""


class InvalidTarget(ValueError):
    pass


def esc(value):
    return escape(str(value if value is not None else ""), quote=True)


def _text(form, name, limit=2000):
    value = form.get(name, "")
    if not isinstance(value, str) or len(value) > limit:
        raise InvalidTarget(f"Invalid or too long {name.replace('_', ' ')}.")
    return value.strip()


def _id(value, required=False):
    if value in (None, "") and not required:
        return None
    value = str(value)
    if not re.fullmatch(r"[1-9][0-9]{0,9}", value) or int(value) > 2147483647:
        raise InvalidTarget("Choose a valid item from the list.")
    return int(value)


def _month(value):
    if not re.fullmatch(r"\d{4}-\d{2}", str(value)):
        raise InvalidTarget("Choose a valid month.")
    try:
        return date.fromisoformat(str(value) + "-01")
    except ValueError:
        raise InvalidTarget("Choose a valid month.") from None


def _value(value):
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise InvalidTarget("Enter a valid target amount.") from None
    if not amount.is_finite() or amount < 0 or amount >= Decimal("1000000000000"):
        raise InvalidTarget("Target must be a finite number from 0 to 999,999,999,999.99.")
    if amount != amount.quantize(Decimal("0.01")):
        raise InvalidTarget("Target can have at most two decimal places.")
    return amount


def _csrf(request):
    if not request.session.get("csrf_token"):
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    return request.session["csrf_token"]


def _valid_csrf(request, form):
    given = form.get("csrf_token", "")
    expected = request.session.get("csrf_token", "")
    return (isinstance(given, str) and isinstance(expected, str) and bool(expected)
            and secrets.compare_digest(given.encode("utf-8"), expected.encode("utf-8")))


def _hidden(token):
    return f'<input type="hidden" name="csrf_token" value="{esc(token)}">'


def _rows(cur):
    names = [column[0] for column in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _actor(cur, user):
    cur.execute("select set_config('app.username', %s, true)", (user["username"],))


def _masters(cur):
    queries = {
        "person_id": "select person_id, full_name from master.sales_person where is_active order by full_name",
        "channel_id": "select channel_id, code from master.channel where is_active order by sort_order, code",
        "sub_channel_id": "select sub_channel_id, code from master.sub_channel where is_active order by code",
        "state_id": "select state_id, name from master.state order by name",
        "district_id": "select d.district_id, d.name || ' — ' || s.name, d.state_id from master.district d join master.state s using(state_id) order by d.name, s.name",
        "city_id": "select c.city_id, c.name || ' — ' || s.name, c.state_id, c.district_id from master.city c join master.state s using(state_id) where c.is_active order by c.name, s.name",
        "brand_id": "select brand_id, name from master.brand where is_active order by name",
    }
    result = {}
    for key, sql in queries.items():
        cur.execute(sql)
        result[key] = cur.fetchall()
    return result


def _options(items, selected=None, empty="Any"):
    result = [f'<option value="">{esc(empty)}</option>'] if empty is not None else []
    for item in items:
        value, label = item[:2]
        chosen = " selected" if str(value) == str(selected) else ""
        attrs = ""
        if len(item) > 2:
            attrs += f' data-state="{esc(item[2])}"'
        if len(item) > 3:
            attrs += f' data-district="{esc(item[3])}"'
        result.append(f'<option value="{esc(value)}"{chosen}{attrs}>{esc(label)}</option>')
    return "".join(result)


def _report_values(params, name, parse):
    """Read repeated report filters while preserving older scalar links.

    Empty/All alone is unrestricted. Concrete values take precedence if a
    request mixes them with All, and every concrete value is still validated.
    """
    raw = params.getlist(name) if hasattr(params, "getlist") else [params.get(name, "")]
    values = []
    for value in raw:
        if value in ("", "All", None):
            continue
        parsed = parse(value)
        if parsed not in values:
            values.append(parsed)
    return values


def _report_metric(value):
    if value not in METRICS:
        raise InvalidTarget("Choose a supported target measure.")
    return value


def _report_options(items, selected, all_label):
    chosen = {str(value) for value in selected}
    options = [f'<option value="All"{" selected" if not chosen else ""}>{esc(all_label)}</option>']
    options.extend(
        f'<option value="{esc(value)}"{" selected" if str(value) in chosen else ""}>{esc(label)}</option>'
        for value, label in items
    )
    return "".join(options)


def _money(value, metric):
    return ("" if metric == "primary_qty" else "₹ ") + f"{Decimal(value):,.2f}"


def _wrap(page, title, body, user, status=200):
    html = page(title, f'<main class="tw">{body}</main>', user)
    return HTMLResponse(html.replace("</head>", f"<style>{CSS}</style></head>", 1), status_code=status)


def _error(page, user, message, status=400, href="/targets"):
    return _wrap(page, "Target request could not be saved", f"""
        <div class="card"><h1>Request could not be completed</h1>
        <p class="err" role="alert">{esc(message)}</p>
        <p>No changes were saved.</p><a href="{esc(href)}">Return to targets</a></div>""", user, status)


def _validate_scope(cur, form):
    masters = _masters(cur)
    scope = {}
    for field, label, _ in DIMENSIONS:
        value = _id(_text(form, field, 12))
        if value is not None and not any(row[0] == value for row in masters[field]):
            raise InvalidTarget(f"Choose an available {label.lower()}.")
        scope[field] = value
    state, district, city = (scope[key] for key in ("state_id", "district_id", "city_id"))
    if district:
        district_row = next(row for row in masters["district_id"] if row[0] == district)
        if state and district_row[2] != state:
            raise InvalidTarget("The district must belong to the selected state.")
    if city:
        city_row = next(row for row in masters["city_id"] if row[0] == city)
        if state and city_row[2] != state:
            raise InvalidTarget("The city must belong to the selected state.")
        if district and city_row[3] != district:
            raise InvalidTarget("The city must belong to the selected district.")
    return scope


def _plan_for_write(cur, plan_id):
    cur.execute("select plan_id, fy, status from tgt.target_plan where plan_id=%s for update", (plan_id,))
    plan = cur.fetchone()
    if not plan:
        raise InvalidTarget("That target plan no longer exists.")
    if plan[2] == "archived":
        raise InvalidTarget("Archived plans cannot be edited. Create a new draft or reactivate this plan.")
    return plan


def _save_target(cur, form, user):
    plan_id = _id(_text(form, "plan_id", 12), required=True)
    plan = _plan_for_write(cur, plan_id)
    value = _value(_text(form, "target_value", 40))
    notes = _text(form, "notes") or None
    target_id = _id(_text(form, "target_id", 12))
    if target_id:
        # Scope/month/metric are immutable when editing. The two IDs guard
        # against accidentally updating a row in a different selected plan.
        cur.execute("""update tgt.target set target_value=%s, notes=%s,
                       updated_by=%s, updated_at=now()
                       where target_id=%s and plan_id=%s returning target_id""",
                    (value, notes, user["username"], target_id, plan_id))
        if not cur.fetchone():
            raise InvalidTarget("That target does not belong to the selected plan.")
    else:
        month = _month(_text(form, "period_month", 7))
        if not date(plan[1], 4, 1) <= month < date(plan[1] + 1, 4, 1):
            raise InvalidTarget("The target month must fall within the plan's April–March financial year.")
        metric = _text(form, "metric", 30)
        if metric not in METRICS:
            raise InvalidTarget("Choose a supported target measure.")
        scope = _validate_scope(cur, form)
        cur.execute("""insert into tgt.target
            (plan_id,period_month,metric,person_id,channel_id,sub_channel_id,
             state_id,district_id,city_id,brand_id,target_value,notes,created_by)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict do nothing returning target_id""",
                    (plan_id, month, metric, *(scope[key] for key, _, _ in DIMENSIONS),
                     value, notes, user["username"]))
        if not cur.fetchone():
            raise InvalidTarget("A target already exists for this month, measure and exact scope. Use its Edit link to change the value.")
    return plan_id


def _activate(cur, form):
    plan_id = _id(_text(form, "plan_id", 12), required=True)
    cur.execute("select fy from tgt.target_plan where plan_id=%s", (plan_id,))
    row = cur.fetchone()
    if not row:
        raise InvalidTarget("That target plan no longer exists.")
    fy = row[0]
    # All web activations for one financial year serialize before row locks.
    cur.execute("select pg_advisory_xact_lock(731826, %s)", (fy,))
    cur.execute("select plan_id from tgt.target_plan where fy=%s order by plan_id for update", (fy,))
    cur.fetchall()
    cur.execute("select count(*) from tgt.target where plan_id=%s", (plan_id,))
    if cur.fetchone()[0] == 0:
        raise InvalidTarget("Add at least one target before activating a plan.")
    cur.execute("update tgt.target_plan set status='archived' where fy=%s and status='active' and plan_id<>%s", (fy, plan_id))
    cur.execute("update tgt.target_plan set status='active' where plan_id=%s and fy=%s", (plan_id, fy))
    if cur.rowcount != 1:
        raise InvalidTarget("The selected plan changed. Reload and try again.")
    return plan_id


def _write_action(form, action, user):
    """Run the complete transaction in one worker, leaving ASGI responsive."""
    with closing(pgdata.connect()) as conn, conn:
        with conn.cursor() as cur:
            _actor(cur, user)
            if action == "plan":
                fy = _id(_text(form, "fy", 4), required=True)
                name = _text(form, "name", 120)
                notes = _text(form, "notes") or None
                if not name:
                    raise InvalidTarget("Enter a name for the plan.")
                cur.execute("select 1 from master.financial_year where fy=%s", (fy,))
                if not cur.fetchone():
                    raise InvalidTarget("Choose a configured financial year.")
                cur.execute("""insert into tgt.target_plan(fy,name,notes,created_by)
                    values(%s,%s,%s,%s) on conflict do nothing returning plan_id""",
                            (fy, name, notes, user["username"]))
                row = cur.fetchone()
                if not row:
                    raise InvalidTarget("A plan with that name already exists in this financial year. Select it or use a different name.")
                return row[0]
            if action == "save":
                return _save_target(cur, form, user)
            if action == "delete":
                plan_id = _id(_text(form, "plan_id", 12), required=True)
                target_id = _id(_text(form, "target_id", 12), required=True)
                _plan_for_write(cur, plan_id)
                cur.execute("delete from tgt.target where target_id=%s and plan_id=%s returning target_id", (target_id, plan_id))
                if not cur.fetchone():
                    raise InvalidTarget("That target no longer belongs to this plan. Reload and try again.")
                return plan_id
            return _activate(cur, form)


def _csv_cell(value):
    text = str(value if value is not None else "")
    # Labels/notes may originate in a spreadsheet. Prevent formulas when the
    # exported report is opened in Excel; numeric columns are written directly.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def register(app, current_user, login_redirect, page, invalidate):
    @app.get("/targets", response_class=HTMLResponse)
    def targets(request: Request):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        if not auth.can(user, "targets.view"):
            return _error(page, user, "Targets View permission is required.", 403)
        if request.query_params.get("edit") and not auth.can(user, "targets.edit"):
            return _error(page, user, "Targets Edit permission is required.", 403)
        token = _csrf(request)
        plans, years, masters, rows = [], [], {}, []
        plan = edit = None
        available = True
        message = ""
        try:
            plan_id = _id(request.query_params.get("plan_id"))
            edit_id = _id(request.query_params.get("edit"))
            with closing(pgdata.connect()) as conn, conn.cursor() as cur:
                cur.execute("select fy,label from master.financial_year order by fy desc")
                years = cur.fetchall()
                cur.execute("select plan_id,fy,name,status from tgt.target_plan order by fy desc, (status='active') desc, plan_id desc")
                plans = _rows(cur)
                plan = next((p for p in plans if p["plan_id"] == plan_id), None) if plan_id else (plans[0] if plans else None)
                if plan_id and not plan:
                    raise InvalidTarget("That plan does not exist. Select an available plan.")
                if auth.can(user, "targets.edit"):
                    masters = _masters(cur)
                if plan:
                    cur.execute("select target_id,period_month,metric,scope_label,scope_level,target_value,notes from rpt.v_target where plan_id=%s order by period_month,metric,scope_label,target_id", (plan["plan_id"],))
                    rows = _rows(cur)
                if edit_id:
                    edit = next((r for r in rows if r["target_id"] == edit_id), None)
                    if not edit:
                        raise InvalidTarget("That target does not belong to the selected plan.")
        except InvalidTarget as exc:
            return _error(page, user, str(exc))
        except Exception:
            available = False
            message = DB_MESSAGE
            plans, years, masters, rows, plan, edit = [], [], {}, [], None, None

        body = '<div class="intro"><h1>Advanced target settings</h1><p class="muted">Manage draft, active and archived plans, additional measures and combined target scopes.</p><a href="/targets/planner">← Back to Target planner</a>' + (' · <a href="/reports/targets">View achievement report →</a>' if auth.can(user, 'target_reports.view') else '') + '</div>'
        if message:
            body += f'<p class="err" role="alert">{esc(message)}</p>'
        if request.query_params.get("saved") == "1":
            body += '<p class="ok" role="status">Saved successfully.</p>'
        disabled = " disabled" if not available else ""
        if auth.can(user, "targets.edit"):
            body += f'''<details class="card"{' open' if not plans else ''}><summary><strong>Create a target plan</strong></summary>
                <p class="muted">A draft holds the monthly targets for one financial year. Activate it when it is ready.</p>
                <form method="post" action="/targets/plans">{_hidden(token)}<fieldset{disabled}>
                <div class="grid"><div><label for="fy">Financial year</label><select id="fy" name="fy" required>{_options(years, empty="Select year")}</select></div>
                <div><label for="plan-name">Plan name</label><input id="plan-name" type="text" name="name" maxlength="120" placeholder="FY 2026–27 sales plan" required></div>
                <div><label for="plan-notes">Plan notes</label><input id="plan-notes" type="text" name="notes" maxlength="2000"></div></div>
                <button>Create draft</button></fieldset></form></details>'''
        elif available:
            body += '<p class="note">Your access allows viewing targets. Editing requires Targets Edit permission.</p>'
        if plans:
            plan_options = [(p["plan_id"], f'{p["name"]} · FY {p["fy"]}–{str(p["fy"] + 1)[2:]} · {p["status"]}') for p in plans]
            body += f'''<div class="card"><form method="get" action="/targets" class="inline-form"><div><label for="plan">Target plan</label><select id="plan" name="plan_id">{_options(plan_options, plan["plan_id"], empty=None)}</select></div><button>Open plan</button></form></div>'''
        if plan:
            pid, status, fy = plan["plan_id"], plan["status"], plan["fy"]
            body += f'<div class="card"><h2>{esc(plan["name"])} <span class="pill {esc(status)}">{esc(status)}</span></h2><p class="muted">FY {fy}–{str(fy + 1)[2:]} · {len(rows)} monthly targets</p>'
            if status != "active" and auth.can(user, "targets.edit"):
                body += f'''<p class="note">Activating this plan replaces the active plan for this financial year. Its targets will be used in reports; the previous plan is kept in the archive.</p><form method="post" action="/targets/activate">{_hidden(token)}<input type="hidden" name="plan_id" value="{pid}"><button{' disabled' if not rows else ''}>Activate this plan</button></form>'''
            if status == "archived":
                body += '<p class="note">This archived plan is read-only. Its targets are excluded from the active achievement report.</p>'
            if rows:
                body += '<div class="scroll"><table><thead><tr><th>Month</th><th>Measure</th><th>Scope</th><th class="num">Target</th><th></th></tr></thead><tbody>'
                for row in rows:
                    link = f'<a href="/targets?plan_id={pid}&amp;edit={row["target_id"]}#target-form">Edit</a>' if auth.can(user, "targets.edit") and status != "archived" else ""
                    if auth.can(user, "targets.delete") and status != "archived":
                        link += (f'<form method="post" action="/targets/delete" onsubmit="return confirm(\'Delete this monthly target?\')">{_hidden(token)}'
                                 f'<input type="hidden" name="plan_id" value="{pid}"><input type="hidden" name="target_id" value="{row["target_id"]}">'
                                 '<button class="sec">Delete</button></form>')
                    body += f'<tr><td>{esc(row["period_month"].strftime("%b %Y"))}</td><td>{esc(METRICS[row["metric"]])}</td><td class="scope">{esc(row["scope_label"])}<small>{esc(row["scope_level"])}</small></td><td class="num">{esc(_money(row["target_value"], row["metric"]))}</td><td>{link}</td></tr>'
                body += '</tbody></table></div>'
            else:
                body += '<p>No monthly targets yet. Add a scope and a target below.</p>'
            body += '</div>'

        if auth.can(user, "targets.edit") and (not plan or plan["status"] != "archived"):
            can_edit = available and plan is not None
            title = "Edit target value" if edit else "Add a monthly target"
            body += f'<div class="card" id="target-form"><h2>{title}</h2>'
            if not plan:
                body += '<p class="muted">Create or select a plan first.</p>'
            body += f'<form method="post" action="/targets/save">{_hidden(token)}<input type="hidden" name="plan_id" value="{plan["plan_id"] if plan else ""}"><fieldset{" disabled" if not can_edit else ""}>'
            if edit:
                body += f'<input type="hidden" name="target_id" value="{edit["target_id"]}"><p class="note">{esc(edit["period_month"].strftime("%b %Y"))} · {esc(METRICS[edit["metric"]])}<br>{esc(edit["scope_label"])}<br><small>Editing changes only this row’s value and notes. Add a new target for a different month, measure or scope.</small></p><div class="grid">'
            else:
                fy = plan["fy"] if plan else date.today().year
                today = date.today().replace(day=1)
                month = today if date(fy, 4, 1) <= today < date(fy + 1, 4, 1) else date(fy, 4, 1)
                body += f'''<p class="muted">Leave a dimension as Any to include all values. All dimensions left as Any means a company-wide target.</p><div class="grid">
                    <div><label for="target-month">Month</label><input id="target-month" name="period_month" type="month" min="{fy}-04" max="{fy + 1}-03" value="{month:%Y-%m}" required></div>
                    <div><label for="metric">Target measure</label><select id="metric" name="metric" required>{_options(list(METRICS.items()), "primary_taxable", empty=None)}</select></div>'''
                for field, label, _ in DIMENSIONS:
                    body += f'<div><label for="{field}">{esc(label)}</label><select id="{field}" name="{field}">{_options(masters.get(field, []))}</select></div>'
            body += f'''<div><label for="target-value">Target value</label><input id="target-value" name="target_value" type="number" min="0" max="999999999999.99" step="0.01" value="{esc(edit["target_value"] if edit else "")}" required><small>Rupees for sales measures; units for quantity. Zero displays achievement as N/A.</small></div>
                <div class="wide"><label for="target-notes">Notes</label><textarea id="target-notes" name="notes" maxlength="2000">{esc(edit["notes"] if edit else "")}</textarea></div></div>
                <div class="actions"><button>{"Save target changes" if edit else "Add target"}</button>{f'<a href="/targets?plan_id={plan["plan_id"]}#target-form">Cancel edit</a>' if edit else ''}</div></fieldset></form></div>'''
            if not edit:
                body += '''<script>(()=>{const state=document.getElementById('state_id'),district=document.getElementById('district_id'),city=document.getElementById('city_id');if(!state)return;
                    function filter(){for(const o of district.options){const hide=!!(o.value&&state.value&&o.dataset.state!==state.value);o.hidden=hide;o.disabled=hide;if(hide&&o.selected)district.value='';}
                    for(const o of city.options){const hide=!!(o.value&&((state.value&&o.dataset.state!==state.value)||(district.value&&o.dataset.district!==district.value)));o.hidden=hide;o.disabled=hide;if(hide&&o.selected)city.value='';}}
                    state.addEventListener('change',filter);district.addEventListener('change',filter);filter();})();</script>'''
        return _wrap(page, "Advanced target settings", body, user)

    async def mutate(request, action):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        permission = "targets.delete" if action == "delete" else "targets.edit"
        if not auth.can(user, permission):
            return _error(page, user, "You do not have permission to change these targets.", 403)
        form = await request.form()
        if not _valid_csrf(request, form):
            return _error(page, user, "Your form session expired. Reload the targets page and try again.", 403)
        try:
            plan_id = await run_in_threadpool(_write_action, form, action, user)
        except InvalidTarget as exc:
            return _error(page, user, str(exc))
        except pgdata.NotConfigured:
            return _error(page, user, DB_MESSAGE, 503)
        except Exception:
            return _error(page, user, "The database could not save this request. Reload the plan and try again, or ask your administrator to check the connection and migrations.", 503)
        invalidate()
        return RedirectResponse(f"/targets?plan_id={plan_id}&saved=1", status_code=303)

    @app.post("/targets/plans")
    async def create_plan(request: Request):
        return await mutate(request, "plan")

    @app.post("/targets/save")
    async def save_target(request: Request):
        return await mutate(request, "save")

    @app.post("/targets/activate")
    async def activate_plan(request: Request):
        return await mutate(request, "activate")

    @app.post("/targets/delete")
    async def delete_target(request: Request):
        return await mutate(request, "delete")

    @app.get("/reports/targets", response_class=HTMLResponse)
    def achievement(request: Request):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        if not auth.can(user, "target_reports.view"):
            return _error(page, user, "Target Reports permission is required.", 403)
        params = request.query_params
        if params.get("format") == "csv" and not auth.can(user, "exports.access"):
            return _error(page, user, "Export access is required.", 403)
        plan_ids, months, metrics = [], [], []
        plans, rows = [], []
        message, unavailable = "", False
        try:
            plan_ids = _report_values(params, "plan_id", _id)
            months = _report_values(params, "month", _month)
            metrics = _report_values(params, "metric", _report_metric)
            with closing(pgdata.connect()) as conn, conn.cursor() as cur:
                cur.execute("select plan_id,fy,name from tgt.target_plan where status='active' order by fy desc")
                plans = _rows(cur)
                active_ids = {p["plan_id"] for p in plans}
                if any(plan_id not in active_ids for plan_id in plan_ids):
                    raise InvalidTarget("Select an active target plan for this report.")
                where, values = ["plan_status='active'"], []
                for column, selected in (("plan_id", plan_ids), ("period_month", months), ("metric", metrics)):
                    if selected:
                        where.append(f"{column}=ANY(%s)")
                        values.append(selected)
                cur.execute("select target_id,plan_name,fy,period_month,metric,scope_level,scope_label,sales_person,channel,sub_channel,state,district,city,brand,target_value,achieved,achieved_pct from rpt.v_target_achievement where " + " and ".join(where) + " order by period_month desc,plan_name,metric,scope_label,target_id", tuple(values))
                rows = _rows(cur)
        except InvalidTarget as exc:
            return _error(page, user, str(exc), href="/reports/targets")
        except Exception:
            unavailable, message = True, DB_MESSAGE
            plans, rows = [], []

        if params.get("format") == "csv" and not unavailable:
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(["Plan", "Financial year", "Month", "Measure", "Unit", "Scope type", "Scope", "Salesperson", "Channel", "Sub-channel", "State", "District", "City", "Brand", "Target", "Actual", "Gap (target - actual)", "Achievement %"])
            for row in rows:
                labels = [row["plan_name"], str(row["fy"]), row["period_month"].strftime("%Y-%m"), METRICS[row["metric"]], "units" if row["metric"] == "primary_qty" else "INR", row["scope_level"], row["scope_label"], *(row[key] for _, _, key in DIMENSIONS)]
                writer.writerow([*(_csv_cell(v) for v in labels), row["target_value"], row["achieved"], row["target_value"] - row["achieved"], row["achieved_pct"] if row["target_value"] > 0 else "N/A"])
            return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="target-achievement.csv"', "Cache-Control": "no-store"})

        body = '<div class="intro"><h1>Target achievement</h1><p class="muted">Monthly sales matched to each target’s measure and exact scope. Only active plans appear here.</p>' + ('<a href="/targets/planner">Open Target planner →</a>' if auth.can(user, 'targets.view') else '') + '</div>'
        if message:
            body += f'<p class="err" role="alert">{esc(message)}</p>'
        choices = [(p["plan_id"], f'{p["name"]} · FY {p["fy"]}–{str(p["fy"] + 1)[2:]}') for p in plans]
        # Offer complete April–March periods even where targets have not been set.
        # Keep a valid month from an older direct link selectable if outside them.
        month_choices = dict.fromkeys(
            date(int(p["fy"]) + (month < 4), month, 1)
            for p in plans for month in (4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3)
        )
        month_choices.update(dict.fromkeys(months))
        month_options = [(month.strftime("%Y-%m"), month.strftime("%b %Y")) for month in month_choices]
        month_values = [month.strftime("%Y-%m") for month in months]
        body += f'''<div class="card"><form method="get" action="/reports/targets"><div class="grid"><div><label for="report-plan">Active plan</label><select name="plan_id" id="report-plan" multiple data-multi-filter>{_report_options(choices, plan_ids, "All active plans")}</select></div><div><label for="report-month">Month</label><select name="month" id="report-month" multiple data-multi-filter>{_report_options(month_options, month_values, "All months")}</select></div><div><label for="report-metric">Measure</label><select name="metric" id="report-metric" multiple data-multi-filter>{_report_options(list(METRICS.items()), metrics, "All measures")}</select></div></div><small>Choose one or more options in each filter. All leaves that filter unrestricted.</small><div class="actions"><button>Apply filters</button><a href="/reports/targets">Clear filters</a></div></form></div>'''
        body += '<p class="note">Targets can overlap: a salesperson’s sales may also belong to a channel or state target. Each row is evaluated separately; these rows must not be added together. Gap = target − actual; a negative gap means the target has been exceeded. Quantity uses units; sales values use ₹.</p>'
        if not unavailable:
            if not plans:
                body += '<div class="card"><h2>No active target plans</h2><p>An authorized user can add targets in Target planner, or manage draft plans and activation through its Advanced target settings.</p></div>'
            elif not rows:
                body += '<div class="card"><h2>No targets match these filters</h2><p>Choose another month or measure, or add targets to an active plan.</p></div>'
            else:
                achieved = sum(1 for row in rows if row["target_value"] > 0 and row["achieved"] >= row["target_value"])
                zero = sum(1 for row in rows if row["target_value"] == 0)
                query = urlencode({"plan_id": plan_ids, "month": month_values, "metric": metrics, "format": "csv"}, doseq=True)
                export_link = f'<p><a href="/reports/targets?{esc(query)}">Download these results as CSV</a></p>' if auth.can(user, "exports.access") else ""
                body += f'<div class="card"><h2>Results</h2><div class="stats"><span>{len(rows)} targets</span><span>{achieved} achieved</span><span>{zero} zero targets (N/A)</span></div>{export_link}<div class="scroll"><table><thead><tr><th>Month / plan</th><th>Scope</th><th>Measure</th><th class="num">Target</th><th class="num">Actual</th><th class="num">Gap</th><th class="num">Achievement</th></tr></thead><tbody>'
                for row in rows:
                    pct = f'{row["achieved_pct"]}%' if row["target_value"] > 0 else "N/A"
                    body += f'<tr><td>{esc(row["period_month"].strftime("%b %Y"))}<small>{esc(row["plan_name"])}</small></td><td class="scope">{esc(row["scope_label"])}<small>{esc(row["scope_level"])}</small></td><td>{esc(METRICS[row["metric"]])}</td><td class="num">{esc(_money(row["target_value"], row["metric"]))}</td><td class="num">{esc(_money(row["achieved"], row["metric"]))}</td><td class="num">{esc(_money(row["target_value"] - row["achieved"], row["metric"]))}</td><td class="num">{esc(pct)}</td></tr>'
                body += '</tbody></table></div></div>'
        return _wrap(page, "Target achievement", body, user, 503 if unavailable and params.get("format") == "csv" else 200)
