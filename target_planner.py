"""Target planner: one page to set a financial year's targets, now or ahead.

Primary targets are Doctor Towels' own invoices to each channel, measured on
the sales register before GST. The planner keeps them in the year's active
plan (tgt.target), in the two shapes the dashboard reads: a channel on its own,
and an executive in a city or a whole state (the Sales team pages).

Secondary targets are each channel's sales onward to its sub-channels (groups)
and their branches / retailers, measured on monthly secondary sales. lfo_data
stores them; a group and year saved here is never changed by a sheet import.

    GET  /targets/planner?fy=2027&step=secondary&channel=LFO
    POST /targets/planner/channels    channel targets for the year
    POST /targets/planner/team        executive + city / state targets
    POST /targets/planner/secondary   one channel's sub-channel and branch targets
    POST /targets/planner/group       add a sub-channel to a channel
    POST /targets/planner/branch      add a branch / retailer to a sub-channel

Viewing needs Targets View; saving needs Targets Edit, and clearing a saved
target needs Targets Delete. Adding a sub-channel or branch also needs that
channel's branch / retailer permission.
"""

import hashlib
import json
import re
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

import auth
import lfo_data
import lfo_web
import pgdata
import target_web
from entry_web import number


PATH = "/targets/planner"
TITLE = "Target planner"
MONTHS = ("Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar")
# The dashboard's menu order, for both primary and secondary.
CHANNELS = ("Saloon", "D&R-S", "D&R-N", "L-LFO", "LFO")
STEPS = (("channels", "Channel targets", "Primary"), ("team", "Sales team", "Primary"),
         ("secondary", "Sub-channels & branches", "Secondary"))
YEARS_AHEAD = 2
MAX_AMOUNT = Decimal("1000000000000")
DB_MESSAGE = ("The target planner cannot reach the database right now. Nothing was saved. "
              "Try again, or ask your administrator to check PostgreSQL.")


class PlannerError(ValueError):
    pass


def esc(value):
    return escape(str(value if value is not None else ""), quote=True)


def current_fy(today=None):
    today = today or date.today()
    return today.year if today.month >= 4 else today.year - 1


def fy_name(fy):
    return f"FY {fy}-{str(fy + 1)[2:]}"


def store_channel(code):
    return next(ch for ch in lfo_web.CHANNELS if ch.code == code)


# --------------------------------------------------------------- amounts

_AMOUNT = re.compile(r"(\d+(?:\.\d*)?|\.\d+)(crores?|cr|lakhs?|lacs?|l|k)?")


def parse_amount(value, label="a target"):
    """'3.2L' -> 320000, '1.5 Cr' -> 15000000, '2,50,000' -> 250000; blank -> None."""
    raw = re.sub(r"[\s,₹]", "", str(value if value is not None else "")).lower()
    if not raw:
        return None
    match = _AMOUNT.fullmatch(raw)
    if not match:
        raise PlannerError(f"{label}: enter an amount such as 250000, 2.5L or 1.2Cr.")
    unit = match.group(2) or ""
    factor = 10 ** 7 if unit.startswith("c") else 10 ** 5 if unit.startswith("l") else 1000 if unit == "k" else 1
    try:
        amount = (Decimal(match.group(1)) * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise PlannerError(f"{label}: enter a valid amount.") from None
    if amount >= MAX_AMOUNT:
        raise PlannerError(f"{label}: the amount is too large.")
    return amount


def plain(value):
    """An amount as typed back into a box: Indian grouping, paise only when present."""
    if value is None:
        return ""
    value = Decimal(value)
    return number(value, 0 if value == value.to_integral_value() else 2)


def short(value):
    if value is None:
        return "–"
    value = Decimal(value)
    cents = Decimal("0.01")
    if abs(value) >= 10 ** 7:
        return f"₹{(value / 10 ** 7).quantize(cents, ROUND_HALF_UP)} Cr"
    if abs(value) >= 10 ** 5:
        return f"₹{(value / 10 ** 5).quantize(cents, ROUND_HALF_UP)} L"
    return "₹" + number(value.quantize(Decimal(1), ROUND_HALF_UP), 0)


def total(values):
    known = [Decimal(v) for v in values if v is not None]
    return sum(known, Decimal(0)) if known else None


# ------------------------------------------------------------------- reads

CHANNEL_SQL = """
    select t.target_id, p.fy, t.channel_id, t.period_month, t.target_value
    from tgt.target t join tgt.target_plan p using (plan_id)
    where p.status = 'active' and p.fy = any(%s) and t.metric = 'primary_taxable'
      and t.channel_id is not null and t.sub_channel_id is null and t.person_id is null
      and t.state_id is null and t.district_id is null and t.city_id is null and t.brand_id is null
"""
TEAM_SQL = """
    select t.target_id, p.fy, t.person_id, t.state_id, t.city_id, t.period_month, t.target_value
    from tgt.target t join tgt.target_plan p using (plan_id)
    where p.status = 'active' and p.fy = any(%s) and t.metric = 'primary_taxable'
      and t.person_id is not null and (t.city_id is not null or t.state_id is not null)
      and t.channel_id is null and t.sub_channel_id is null and t.district_id is null and t.brand_id is null
"""


def _month_index(fy, month):
    return lfo_data.fy_months(fy).index(month)


def _token(rows):
    return hashlib.sha256(json.dumps(sorted(rows), default=str).encode("utf-8")).hexdigest()[:32]


def _channel_ids(cur):
    cur.execute("select code, channel_id from master.channel where code = any(%s)", (list(CHANNELS),))
    return dict(cur.fetchall())


def _places(cur):
    cur.execute("""select c.city_id, c.name, s.state_id, s.name from master.city c
                   join master.state s using (state_id) where c.is_active order by c.name, s.name""")
    cities = cur.fetchall()
    cur.execute("select state_id, name from master.state order by name")
    states = cur.fetchall()
    places = {f"c{cid}": (sid, cid, f"{name} — {state}") for cid, name, sid, state in cities}
    places.update({f"s{sid}": (sid, None, f"All of {name}") for sid, name in states})
    return places


def _people(cur):
    cur.execute("""select person_id, coalesce(display_name, full_name) from master.sales_person
                   where is_real_person and is_active order by 2""")
    return cur.fetchall()


def _place_key(state_id, city_id):
    return f"c{city_id}" if city_id else f"s{state_id}"


def load_primary(cur, fy):
    """Channel and team targets for fy and fy-1, with last year's achievement."""
    ids = _channel_ids(cur)
    cur.execute(CHANNEL_SQL, ([fy, fy - 1],))
    chan = {}
    token_rows = []
    for target_id, year, cid, month, value in cur.fetchall():
        chan.setdefault((year, cid), [None] * 12)[_month_index(year, month)] = value
        if year == fy:
            token_rows.append((target_id, cid, month.isoformat(), str(value)))
    cur.execute("""select channel_id, sum(primary_taxable) from txn.sales_entry
                   where fy = %s and not is_deleted group by 1""", (fy - 1,))
    achieved = dict(cur.fetchall())
    channels = [{"code": code, "id": ids[code], "months": chan.get((fy, ids[code]), [None] * 12),
                 "last": chan.get((fy - 1, ids[code]), [None] * 12), "achieved": achieved.get(ids[code])}
                for code in CHANNELS if code in ids]

    places, people = _places(cur), dict(_people(cur))
    cur.execute(TEAM_SQL, ([fy, fy - 1],))
    team, team_rows = {}, []
    for target_id, year, pid, sid, cid, month, value in cur.fetchall():
        key = (pid, _place_key(sid, cid))
        row = team.setdefault(key, {"months": [None] * 12, "last": [None] * 12})
        row["months" if year == fy else "last"][_month_index(year, month)] = value
        if year == fy:
            team_rows.append((target_id, pid, sid, cid, month.isoformat(), str(value)))
    cur.execute("""select person_id, state_id, city_id, sum(primary_taxable) from txn.sales_entry
                   where fy = %s and not is_deleted and person_id is not null group by 1, 2, 3""", (fy - 1,))
    sold = cur.fetchall()
    rows = []
    for (pid, place), row in team.items():
        state_id, city_id, label = places.get(place, (None, None, place))
        got = [a for p, s, c, a in sold if p == pid and (c == city_id if city_id else s == state_id)]
        rows.append({"person": pid, "personName": people.get(pid, f"#{pid}"), "place": place, "placeName": label,
                     "months": row["months"], "last": row["last"], "achieved": sum(got, Decimal(0)) if got else None})
    rows.sort(key=lambda r: (r["personName"].casefold(), r["placeName"].casefold()))
    cur.execute("select plan_id, name from tgt.target_plan where fy = %s and status = 'active'", (fy,))
    plan = cur.fetchone()
    return {"plan": plan, "channels": channels, "channelToken": _token(token_rows),
            "team": rows, "teamToken": _token(team_rows), "people": sorted(people.items(), key=lambda x: x[1].casefold()),
            "places": sorted(((k, v[2]) for k, v in places.items()), key=lambda x: (x[0][0] == "s", x[1].casefold()))}


def load_secondary(conn, fy):
    """planner_secondary for every channel, plus each group's sales last year."""
    result = {}
    for code in CHANNELS:
        data = lfo_data.planner_secondary(conn, code, fy)
        with conn.cursor() as cur:
            cur.execute("""select s.distributor_id, sum(m.amount) from lfo.monthly_sale m
                           join lfo.store s using (store_id) join lfo.distributor d using (distributor_id)
                           where d.channel = %s and m.period_month >= %s and m.period_month < %s group by 1""",
                        (code, date(fy - 1, 4, 1), date(fy, 4, 1)))
            sales = dict(cur.fetchall())
            cur.execute("""select s.code from master.sub_channel s join master.channel c using (channel_id)
                           where c.code = %s and s.is_active order by s.code""", (code,))
            suggestions = [r[0] for r in cur.fetchall()]
        for group in data["groups"]:
            group["lastSales"] = sales.get(group["id"])
        data["suggestions"] = [s for s in suggestions
                               if lfo_data.normalized_name(s) not in {lfo_data.normalized_name(g["name"]) for g in data["groups"]}]
        result[code] = data
    return result


def planner_years(cur, fy=None):
    now = current_fy()
    cur.execute("select fy from master.financial_year where fy >= %s", (now,))
    years = {row[0] for row in cur.fetchall()} | set(range(now, now + YEARS_AHEAD + 1))
    if fy is not None:
        years.add(fy)
    return sorted(years)


# ------------------------------------------------------------------ writes

def _ensure_year(cur, fy):
    cur.execute("select 1 from master.financial_year where fy = %s", (fy,))
    if cur.fetchone():
        return
    cur.execute("""insert into master.financial_year (fy, label, start_date, end_date)
                   values (%s, %s, %s, %s) on conflict do nothing""",
                (fy, f"{fy}-{str(fy + 1)[2:]}", date(fy, 4, 1), date(fy + 1, 3, 31)))


def _plan(cur, fy, username, create):
    """The year's active plan, created (active) the first time a target is saved."""
    cur.execute("select plan_id from tgt.target_plan where fy = %s and status = 'active' for update", (fy,))
    row = cur.fetchone()
    if row or not create:
        return row[0] if row else None
    _ensure_year(cur, fy)
    cur.execute("select lower(name) from tgt.target_plan where fy = %s", (fy,))
    taken = {r[0] for r in cur.fetchall()}
    name, n = f"{fy_name(fy)} targets", 1
    while name.lower() in taken:
        n += 1
        name = f"{fy_name(fy)} targets ({n})"
    cur.execute("""insert into tgt.target_plan (fy, name, status, notes, created_by)
                   values (%s, %s, 'active', %s, %s) returning plan_id""",
                (fy, name, "Created by the Target planner.", username))
    return cur.fetchone()[0]


def _apply(cur, plan_id, fy, changes, username, allow_clear):
    """changes: [(existing (target_id, value) | None, new value | None, scope dict)]."""
    months = lfo_data.fy_months(fy)
    counts = {"added": 0, "changed": 0, "cleared": 0}
    if any(old is not None and new is None for old, new, _, _ in changes) and not allow_clear:
        raise PlannerError("Clearing a saved target needs Targets Delete permission. Enter 0 instead, or ask an administrator.")
    if any(old is None and new is not None for old, new, _, _ in changes) and plan_id is None:
        plan_id = _plan(cur, fy, username, create=True)
    for old, new, index, scope in changes:
        if old is None and new is not None:
            cur.execute("""insert into tgt.target (plan_id, period_month, metric, channel_id, person_id, state_id,
                           city_id, target_value, notes, created_by) values (%s, %s, 'primary_taxable', %s, %s, %s, %s, %s, %s, %s)""",
                        (plan_id, months[index], scope.get("channel_id"), scope.get("person_id"), scope.get("state_id"),
                         scope.get("city_id"), new, "Target planner", username))
            counts["added"] += 1
        elif old is not None and new is None:
            cur.execute("delete from tgt.target where target_id = %s and plan_id = %s", (old[0], plan_id))
            counts["cleared"] += 1
        elif old is not None and Decimal(old[1]) != new:
            cur.execute("""update tgt.target set target_value = %s, updated_by = %s, updated_at = now()
                           where target_id = %s and plan_id = %s""", (new, username, old[0], plan_id))
            counts["changed"] += 1
    return counts


def _begin(cur, fy, username):
    if fy < current_fy():
        raise PlannerError(f"{fy_name(fy)} has ended. Past years are kept as they are; Advanced target settings shows them.")
    cur.execute("select set_config('app.username', %s, true)", (username,))
    # The same lock the Targets page takes to activate a plan for this year.
    cur.execute("select pg_advisory_xact_lock(731826, %s)", (fy,))


def _months_from(form, prefix, label):
    return [parse_amount(form.get(f"{prefix}_{i}"), f"{label}, {MONTHS[i]}") for i in range(12)]


def save_channels(form, user, allow_clear):
    fy = _fy_field(form)
    with closing(pgdata.connect()) as conn, conn:
        with conn.cursor() as cur:
            _begin(cur, fy, user["username"])
            ids = _channel_ids(cur)
            submitted = {}
            for code in CHANNELS:
                if code not in ids:
                    continue
                if not all(f"c{ids[code]}_{i}" in form for i in range(12)):
                    raise PlannerError("The channel targets form is incomplete. Reload the planner and try again.")
                submitted[ids[code]] = _months_from(form, f"c{ids[code]}", code)
            primary = load_primary(cur, fy)
            if form.get("token") != primary["channelToken"]:
                raise lfo_data.LFOConflictError()
            plan_id = primary["plan"][0] if primary["plan"] else None
            cur.execute(CHANNEL_SQL + " and p.plan_id = %s", ([fy], plan_id or 0))
            stored = {(cid, month): (target_id, value) for target_id, _, cid, month, value in cur.fetchall()}
            months = lfo_data.fy_months(fy)
            changes = [(stored.get((cid, months[i])), values[i], i, {"channel_id": cid})
                       for cid, values in submitted.items() for i in range(12)]
            return fy, _apply(cur, plan_id, fy, changes, user["username"], allow_clear)


def _team_rows(form, places, people):
    rows, seen = [], set()
    indexes = form.getlist("row") if hasattr(form, "getlist") else []
    if len(indexes) > 300:
        raise PlannerError("Save at most 300 sales team rows at a time.")
    for n in indexes:
        if not re.fullmatch(r"\d{1,4}", str(n)):
            raise PlannerError("The sales team form is incomplete. Reload the planner and try again.")
        person, place = str(form.get(f"r{n}_person", "")), str(form.get(f"r{n}_place", ""))
        if not person.isdigit() or int(person) not in people:
            raise PlannerError("Choose an executive for every sales team row.")
        if place not in places:
            raise PlannerError(f"Choose a city or state for {people[int(person)]}.")
        key = (int(person), place)
        if key in seen:
            raise PlannerError(f"{people[int(person)]} — {places[place][2]} appears twice. Keep one row for each executive and place.")
        seen.add(key)
        rows.append((key, _months_from(form, f"r{n}", f"{people[int(person)]} — {places[place][2]}")))
    return rows


def save_team(form, user, allow_clear):
    fy = _fy_field(form)
    with closing(pgdata.connect()) as conn, conn:
        with conn.cursor() as cur:
            _begin(cur, fy, user["username"])
            places, people = _places(cur), dict(_people(cur))
            submitted = _team_rows(form, places, people)
            primary = load_primary(cur, fy)
            if form.get("token") != primary["teamToken"]:
                raise lfo_data.LFOConflictError()
            plan_id = primary["plan"][0] if primary["plan"] else None
            cur.execute(TEAM_SQL + " and p.plan_id = %s", ([fy], plan_id or 0))
            stored = {}
            for target_id, _, pid, sid, cid, month, value in cur.fetchall():
                stored[((pid, _place_key(sid, cid)), month)] = (target_id, value)
            months = lfo_data.fy_months(fy)
            changes = []
            for key, values in submitted:
                state_id, city_id, _ = places[key[1]]
                scope = {"person_id": key[0], "state_id": state_id, "city_id": city_id}
                changes += [(stored.pop((key, months[i]), None), values[i], i, scope) for i in range(12)]
            # A row taken off the page clears that executive's targets for the year.
            changes += [(old, None, months.index(month), {}) for (_, month), old in stored.items()]
            return fy, _apply(cur, plan_id, fy, changes, user["username"], allow_clear)


def save_secondary(form, user, allow_clear):
    fy, code = _fy_field(form), _channel_field(form)
    groups = {}
    for key in form:
        match = re.fullmatch(r"g(\d{1,10})_(\d{1,2})", key)
        if match and int(match.group(2)) < 12:
            groups.setdefault(int(match.group(1)), {"months": [None] * 12, "branches": {}})
        match_b = re.fullmatch(r"g(\d{1,10})_b(\d{1,10})", key)
        if match_b:
            groups.setdefault(int(match_b.group(1)), {"months": [None] * 12, "branches": {}})
    if len(groups) > 200:
        raise PlannerError("Save at most 200 sub-channels at a time.")
    for did, values in groups.items():
        if not all(f"g{did}_{i}" in form for i in range(12)):
            raise PlannerError("The secondary targets form is incomplete. Reload the planner and try again.")
        values["months"] = _months_from(form, f"g{did}", "Sub-channel target")
    for key in form:
        match_b = re.fullmatch(r"g(\d{1,10})_b(\d{1,10})", key)
        if match_b:
            groups[int(match_b.group(1))]["branches"][int(match_b.group(2))] = parse_amount(form.get(key), "Branch target")
    if current_fy() > fy:
        raise PlannerError(f"{fy_name(fy)} has ended. Past years are kept as they are.")
    with closing(pgdata.connect()) as conn:
        counts = lfo_data.save_secondary_targets(conn, code, fy, groups, str(form.get("token", "")),
                                                 user["username"], allow_clear=allow_clear)
    return fy, counts


def _fy_field(form):
    text = str(form.get("fy", ""))
    if not re.fullmatch(r"20\d\d", text) or not current_fy() <= int(text) <= current_fy() + 10:
        raise PlannerError("Choose the current or a coming financial year.")
    return int(text)


def _channel_field(form):
    code = str(form.get("channel", ""))
    if code not in CHANNELS:
        raise PlannerError("Choose a channel.")
    return code


# ------------------------------------------------------------------ page

CSS = """
.wrap{max-width:1500px}
.tp h1{font-size:22px}.tp .lead{color:#475569;max-width:880px;margin:4px 0 16px}
.tp .years{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 16px}
.tp .years a{padding:7px 14px;border:1px solid #cbd5e1;border-radius:99px;background:#fff;color:#0f172a;text-decoration:none;font-weight:600}
.tp .years a[aria-current=page]{background:#101012;color:#fff;border-color:#101012}
.tp .years a small{font-weight:500;opacity:.75;margin-left:4px}
.tp .explain{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:0 0 16px}
.tp .explain div{background:#fff;border:1px solid #e5eaf2;border-radius:10px;padding:12px 14px;font-size:13px;color:#334155}
.tp .explain b{display:block;color:#0f172a;margin-bottom:2px}
.tp .sum{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:0 0 16px}
.tp .sum div{background:#fff;border:1px solid #e5eaf2;border-radius:10px;padding:12px 14px}
.tp .sum span{display:block;color:#64748b;font-size:12px;font-weight:600}
.tp .sum strong{display:block;font-size:20px;margin:2px 0}.tp .sum small{color:#64748b}
.tp .steps{display:flex;gap:0;border-bottom:2px solid #e5eaf2;margin:6px 0 0;flex-wrap:wrap}
.tp .steps button{margin:0 0 -2px;background:none;color:#475569;border:0;border-bottom:3px solid transparent;border-radius:0;padding:10px 16px;font-weight:600}
.tp .steps button small{display:block;font-size:11px;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em}
.tp .steps button[aria-selected=true]{color:#c8102e;border-bottom-color:#c8102e}
.tp .steps button:hover{background:#f8fafc}
.tp .panel{background:#fff;border:1px solid #e5eaf2;border-top:0;border-radius:0 0 10px 10px;padding:18px 20px 20px}
.tp .panel[hidden]{display:none}.tp .panel h2{margin:0 0 4px;font-size:17px}
.tp .note{color:#475569;font-size:13px;margin:0 0 12px}
.tp .tools{display:flex;gap:10px 18px;align-items:center;flex-wrap:wrap;background:#f8fafc;border:1px solid #e5eaf2;border-radius:8px;padding:10px 12px;margin:0 0 12px;font-size:13px}
.tp .tools label{display:inline;margin:0;font-weight:600}.tp .tools input{width:64px;padding:6px 8px;border:1px solid #cbd5e1;border-radius:6px;text-align:right}
.tp .tools select{padding:6px 8px;border:1px solid #cbd5e1;border-radius:6px;background:#fff}
.tp .tools button{margin:0;padding:7px 12px}
.tp .scroll{overflow-x:auto;border:1px solid #e5eaf2;border-radius:8px}
.tp table.g{margin:0;font-size:12.5px;min-width:1320px}
.tp table.g th{background:#f8fafc;font-size:11.5px;white-space:nowrap;padding:7px 6px;text-align:right}
.tp table.g th.nm,.tp table.g td.nm{text-align:left;position:sticky;left:0;background:#fff;z-index:1;min-width:190px;max-width:240px}
.tp table.g th.nm{background:#f8fafc}
.tp table.g td{padding:5px 4px;text-align:right;white-space:nowrap}
.tp table.g td.ref{color:#64748b;font-variant-numeric:tabular-nums}
.tp table.g td.chg{font-weight:600;font-size:12px}.tp .up{color:#15803d}.tp .down{color:#b91c1c}
.tp input.amt{width:92px;padding:6px 7px;border:1px solid #cbd5e1;border-radius:6px;text-align:right;font:inherit;font-variant-numeric:tabular-nums;background:#fff}
.tp input.amt.year{width:108px;font-weight:700;border-color:#94a3b8}
.tp input.amt[readonly]{background:#f1f5f9;color:#334155}
.tp input.amt:disabled{background:#f8fafc;color:#334155;border-color:#e2e8f0}
.tp input.amt.bad{border-color:#b91c1c;background:#fef2f2}
.tp tr.tot td{font-weight:700;background:#f8fafc;border-top:2px solid #e5eaf2;font-variant-numeric:tabular-nums}
.tp tr.tot td.nm{background:#f8fafc}
.tp td.nm small{display:block;color:#64748b;font-weight:500}
.tp .rm{margin:0 0 0 6px;padding:1px 7px;background:#fff;color:#b91c1c;border:1px solid #fecaca;font-size:12px}
.tp .add{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;margin:12px 0 0}
.tp .add label{margin:0 0 4px;font-size:12px}.tp .add select,.tp .add input[type=text]{padding:8px 9px;border:1px solid #cbd5e1;border-radius:7px;background:#fff;min-width:220px;font:inherit}
.tp .add button{margin:0}
.tp .save{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin:16px 0 0}
.tp .save button{margin:0;padding:10px 20px}.tp .save small{color:#64748b}
.tp .chan{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 14px}
.tp .chan button{margin:0;background:#fff;color:#0f172a;border:1px solid #cbd5e1;padding:7px 14px}
.tp .chan button[aria-selected=true]{background:#c8102e;color:#fff;border-color:#c8102e}
.tp .grp{border:1px solid #e5eaf2;border-radius:10px;margin:0 0 16px;overflow:hidden}
.tp .grp>header{display:flex;gap:10px 16px;align-items:center;flex-wrap:wrap;background:#f8fafc;padding:10px 12px;border-bottom:1px solid #e5eaf2}
.tp .grp>header h3{margin:0;font-size:15px}.tp .grp>header label{display:inline;margin:0;font-size:12.5px}
.tp .grp>header select{padding:5px 8px;border:1px solid #cbd5e1;border-radius:6px;background:#fff;font:inherit;font-size:12.5px}
.tp .src{font-size:11px;font-weight:700;padding:2px 8px;border-radius:99px;background:#e0e7ff;color:#3730a3}
.tp .src.sheet{background:#fef3c7;color:#92400e}.tp .src.none{background:#f1f5f9;color:#475569}
.tp .grp .scroll{border:0;border-radius:0}
.tp table.b{margin:0;font-size:12.5px}.tp table.b th{background:#fff;font-size:11.5px;text-align:right}.tp table.b th:first-child,.tp table.b td:first-child{text-align:left}
.tp table.b td{text-align:right;padding:5px 10px}
.tp .grp .foot{padding:8px 12px;font-size:12.5px;color:#475569;border-top:1px solid #f1f5f9;display:flex;gap:10px 18px;flex-wrap:wrap;align-items:center}
.tp .grp .foot form{display:flex;gap:8px;align-items:center;margin:0}
.tp .grp .foot input[type=text]{width:280px;max-width:100%;padding:6px 8px;border:1px solid #cbd5e1;border-radius:6px;font:inherit}
.tp .grp .foot input[type=text]:first-of-type{margin-left:auto}
.tp .grp .foot button{margin:0;padding:6px 12px}
.tp .add input[type=text]{width:280px;max-width:100%}
.tp .add .dt-select{min-width:260px}
.tp .warn{background:#fffbeb;border:1px solid #fde68a;color:#78350f;border-radius:8px;padding:9px 12px;font-size:12.5px;margin:0 0 12px}
@media(max-width:820px){.tp .explain,.tp .sum{grid-template-columns:1fr}.tp .panel{padding:14px}}
"""

SCRIPT = r"""<script>(()=>{
const parse=v=>{const s=String(v??'').trim().toLowerCase().replace(/[\s,₹]/g,'');if(!s)return null;
  const m=s.match(/^(\d+(?:\.\d*)?|\.\d+)(crores?|cr|lakhs?|lacs?|l|k)?$/);if(!m)return NaN;
  const u=m[2]||'',f=u.startsWith('c')?1e7:u.startsWith('l')?1e5:u==='k'?1e3:1;return Math.round(Number(m[1])*f*100)/100;};
const fmt=n=>n===null||n===undefined||Number.isNaN(n)?'':n.toLocaleString('en-IN',{maximumFractionDigits:2});
const short=n=>n===null?'–':Math.abs(n)>=1e7?'₹'+(n/1e7).toFixed(2)+' Cr':Math.abs(n)>=1e5?'₹'+(n/1e5).toFixed(2)+' L':'₹'+Math.round(n).toLocaleString('en-IN');
const val=i=>parse(i.value);
function split(totalValue,weights){
  const whole=Math.round(totalValue),ok=weights&&weights.length===12&&weights.every(x=>x!==null&&x>=0)&&weights.reduce((a,b)=>a+b,0)>0;
  const w=ok?weights:Array(12).fill(1),sum=w.reduce((a,b)=>a+b,0),exact=w.map(x=>whole*x/sum),base=exact.map(Math.floor);
  const order=exact.map((x,i)=>[x-base[i],i]).sort((a,b)=>b[0]-a[0]||a[1]-b[1]);
  for(let k=0;k<whole-base.reduce((a,b)=>a+b,0);k++)base[order[k][1]]++;return base;}
const monthsOf=row=>[...row.querySelectorAll('input.m')];
const lastOf=row=>monthsOf(row).map(i=>i.dataset.last===''||i.dataset.last===undefined?null:Number(i.dataset.last));
function refreshRow(row){
  const ms=monthsOf(row);if(!ms.length)return;const vals=ms.map(val),bad=vals.some(Number.isNaN);
  ms.forEach((i,k)=>i.classList.toggle('bad',Number.isNaN(vals[k])));
  const known=vals.filter(v=>v!==null&&!Number.isNaN(v)),sum=known.length?known.reduce((a,b)=>a+b,0):null;
  const year=row.querySelector('input.year');if(year&&document.activeElement!==year)year.value=bad?'':fmt(sum);
  const chg=row.querySelector('[data-change]'),last=lastOf(row).filter(v=>v!==null),lastSum=last.length?last.reduce((a,b)=>a+b,0):null;
  if(chg){if(sum===null||!lastSum){chg.textContent='–';chg.className='chg';}else{const p=(sum/lastSum-1)*100;chg.textContent=(p>=0?'+':'')+p.toFixed(1)+'%';chg.className='chg '+(p>=0?'up':'down');}}
  const sh=row.querySelector('[data-short]');if(sh)sh.textContent=sum===null?'Not set':short(sum)+' a year';
}
function refreshTable(table){
  const rows=[...table.querySelectorAll('tr[data-row]')];rows.forEach(refreshRow);
  const tot=table.querySelector('tr.tot');if(!tot)return;
  /* A channel's secondary total adds up its sub-channels, which sit in their own tables. */
  fillTotal(tot,tot.hasAttribute('data-channel-total')?[...table.closest('form').querySelectorAll('.grp tr[data-row]')]:rows);
}
function fillTotal(tot,rows){
  for(let k=0;k<12;k++){const cell=tot.querySelector(`[data-m="${k}"]`);if(!cell)continue;
    const vs=rows.map(r=>val(monthsOf(r)[k])).filter(v=>v!==null&&!Number.isNaN(v));cell.textContent=vs.length?short(vs.reduce((a,b)=>a+b,0)):'–';}
  const all=rows.flatMap(r=>monthsOf(r).map(val)).filter(v=>v!==null&&!Number.isNaN(v)),y=tot.querySelector('[data-year]');
  if(y)y.textContent=all.length?short(all.reduce((a,b)=>a+b,0)):'–';
  const lastAll=rows.flatMap(lastOf).filter(v=>v!==null),c=tot.querySelector('[data-change]');
  if(c){const s=all.reduce((a,b)=>a+b,0),l=lastAll.reduce((a,b)=>a+b,0);if(!all.length||!l){c.textContent='–';c.className='chg';}else{const p=(s/l-1)*100;c.textContent=(p>=0?'+':'')+p.toFixed(1)+'%';c.className='chg '+(p>=0?'up':'down');}}
}
/* A sub-channel's target can follow its branches: twelve months of their monthly targets. */
function refreshGroup(grp){
  const mode=grp.querySelector('select.mode'),row=grp.querySelector('tr[data-row]');
  const branches=[...grp.querySelectorAll('input.br')].map(val).filter(v=>v!==null&&!Number.isNaN(v));
  const perMonth=branches.length?branches.reduce((a,b)=>a+b,0):null;
  grp.querySelectorAll('input.br').forEach(i=>{const v=val(i);i.classList.toggle('bad',Number.isNaN(v));
    const y=i.closest('tr').querySelector('[data-byear]');if(y)y.textContent=v===null||Number.isNaN(v)?'–':short(v*12);});
  const out=grp.querySelector('[data-branch-sum]');if(out)out.textContent=perMonth===null?'No branch targets yet':short(perMonth)+' a month · '+short(perMonth*12)+' a year';
  if(mode&&mode.value==='sum'&&row){monthsOf(row).forEach(i=>{i.value=fmt(perMonth);});}
  if(mode&&row){const sum=mode.value==='sum';monthsOf(row).concat(row.querySelector('input.year')||[]).forEach(i=>{if(!i.disabled)i.readOnly=sum;});}
}
function refreshPanel(panel){panel.querySelectorAll('.grp').forEach(refreshGroup);panel.querySelectorAll('table.g').forEach(refreshTable);}
document.querySelectorAll('.tp form.targets').forEach(form=>{
  const panel=form.closest('.panel');
  /* An Add branch box sits inside this form's area but belongs to its own form. */
  const own=e=>!e.target.form||e.target.form===form;
  form.addEventListener('input',e=>{if(!own(e))return;form.dataset.dirty='1';if(!e.target.matches('input.year'))refreshPanel(panel);});
  form.addEventListener('change',e=>{
    if(!own(e))return;
    const t=e.target;
    if(t.matches('input.year')){const v=val(t);if(v===null||Number.isNaN(v)){t.classList.toggle('bad',Number.isNaN(v));return;}t.classList.remove('bad');
      const row=t.closest('tr'),mode=form.querySelector('select.split'),w=mode&&mode.value==='last'?lastOf(row):null;
      split(v,w).forEach((n,k)=>{monthsOf(row)[k].value=fmt(n);});form.dataset.dirty='1';}
    if(t.matches('input.amt')&&!t.matches('input.year')){const v=val(t);if(v!==null&&!Number.isNaN(v))t.value=fmt(v);}
    refreshPanel(panel);});
  form.querySelectorAll('[data-fill]').forEach(b=>b.addEventListener('click',()=>{
    const g=Number(form.querySelector('input.growth')?.value||0);if(!Number.isFinite(g)){return;}
    let filled=0;form.querySelectorAll('input.amt[data-last]').forEach(i=>{if(i.dataset.last===''||i.disabled||i.readOnly)return;
      i.value=fmt(Math.round(Number(i.dataset.last)*(1+g/100)));filled++;});
    form.dataset.dirty='1';refreshPanel(panel);
    const out=form.querySelector('[data-filled]');if(out)out.textContent=filled?`Filled ${filled} boxes. Review them, then save.`:'Nothing to copy: last year has no targets here.';}));
  form.addEventListener('submit',()=>{form.dataset.submitting='1';});
  refreshPanel(panel);
});
document.querySelectorAll('.tp select.mode').forEach(s=>s.addEventListener('change',()=>refreshPanel(s.closest('.panel'))));
window.addEventListener('beforeunload',e=>{
  if(document.querySelector('.tp form.structure[data-sent="1"]'))return;   /* already confirmed */
  if([...document.querySelectorAll('.tp form.targets')].some(f=>f.dataset.dirty==='1'&&f.dataset.submitting!=='1')){e.preventDefault();e.returnValue='';}});
/* Steps and channels switch in place and keep the address, so a reload opens the same view. */
function show(kind,value){
  document.querySelectorAll(`[data-${kind}-tab]`).forEach(b=>b.setAttribute('aria-selected',String(b.dataset[kind+'Tab']===value)));
  document.querySelectorAll(`[data-${kind}-panel]`).forEach(p=>{p.hidden=p.dataset[kind+'Panel']!==value;});
  const url=new URL(location.href);url.searchParams.set(kind,value);history.replaceState(null,'',url);}
document.querySelectorAll('[data-step-tab]').forEach(b=>b.addEventListener('click',()=>show('step',b.dataset.stepTab)));
document.querySelectorAll('[data-channel-tab]').forEach(b=>b.addEventListener('click',()=>show('channel',b.dataset.channelTab)));
/* Sales team: add and remove rows before saving. */
const team=document.getElementById('team-rows');
if(team){
  let next=Number(team.dataset.next||0);
  document.getElementById('team-add')?.addEventListener('click',()=>{
    const p=document.getElementById('team-person'),pl=document.getElementById('team-place'),msg=document.getElementById('team-msg');
    if(!p.value||!pl.value){msg.textContent='Choose an executive and a city or state.';return;}
    if(team.querySelector(`tr[data-key="${p.value}|${pl.value}"]`)){msg.textContent='That executive and place already has a row.';return;}
    const n=next++,tpl=document.getElementById('team-template').content.firstElementChild.cloneNode(true);
    const html=s=>s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    tpl.innerHTML=tpl.innerHTML.replaceAll('__N__',String(n)).replaceAll('__PERSON__',html(p.selectedOptions[0].text)).replaceAll('__PLACE__',html(pl.selectedOptions[0].text));
    tpl.dataset.key=p.value+'|'+pl.value;
    tpl.querySelector('input[name$="_person"]').value=p.value;tpl.querySelector('input[name$="_place"]').value=pl.value;
    team.querySelector('tr.tot').before(tpl);msg.textContent='';team.closest('form').dataset.dirty='1';refreshPanel(team.closest('.panel'));
    tpl.querySelector('input.year').focus();});
  team.addEventListener('click',e=>{const b=e.target.closest('.rm');if(!b)return;const row=b.closest('tr');
    if(!confirm('Remove '+row.querySelector('td.nm b').textContent+' from '+team.dataset.year+'? Saving clears that row’s targets.'))return;
    row.remove();team.closest('form').dataset.dirty='1';refreshPanel(team.closest('.panel'));});
}
/* Adding a sub-channel or branch reloads the page: say so when there are unsaved numbers. */
document.querySelectorAll('.tp form.structure').forEach(f=>f.addEventListener('submit',e=>{
  if([...document.querySelectorAll('.tp form.targets')].some(x=>x.dataset.dirty==='1')&&
     !confirm('Adding this reloads the planner and unsaved target changes will be lost. Continue?'))e.preventDefault();
  else f.dataset.sent='1';}));
})();</script>"""


def _can_edit(user):
    return auth.can(user, "targets.edit")


def _months_header(fy):
    return "".join(f'<th>{m} {str(fy + (1 if i > 8 else 0))[2:]}</th>' for i, m in enumerate(MONTHS))


def _row_cells(prefix, months, last, posted, label, disabled, cls="m"):
    cells = ""
    for i in range(12):
        name = f"{prefix}_{i}"
        value = posted.get(name, "") if posted is not None else plain(months[i])
        cells += (f'<td><input class="amt {cls}" name="{esc(name)}" value="{esc(value)}" inputmode="decimal" autocomplete="off"'
                  f' data-last="{esc(last[i] if last[i] is not None else "")}" aria-label="{esc(label)} {MONTHS[i]}"{disabled}></td>')
    return cells


def _growth_tools(fy, has_last, key, can_edit):
    """Copy last year with a growth %, and how a typed year total is split."""
    if not can_edit:
        return ''
    tools = f'<div class="tools"><span><b>Start from {fy_name(fy - 1)}:</b></span>'
    if has_last:
        tools += (f'<label for="growth-{key}">Growth</label><input class="growth" id="growth-{key}" type="text" inputmode="decimal" value="10"> %'
                  f'<button type="button" class="sec" data-fill>Fill from {fy_name(fy - 1)}</button><span data-filled class="note" style="margin:0"></span>')
    else:
        tools += f'<span>{fy_name(fy - 1)} has no targets here to copy.</span>'
    tools += (f'<span style="margin-left:auto"><label for="split-{key}">Split a typed total</label> '
              f'<select class="split" id="split-{key}"><option value="even">evenly over 12 months</option>'
              '<option value="last">like last year’s months</option></select></span>')
    return tools + '</div>'


def _save_bar(label, can_edit, fy):
    if not can_edit:
        return '<p class="note">Your access allows viewing targets. Saving needs Targets Edit permission.</p>'
    return (f'<div class="save"><button type="submit">{esc(label)}</button>'
            f'<small>Saves {fy_name(fy)} only. The dashboard shows the change straight away. Blank means no target; enter 0 for a zero target.</small></div>')


def _channels_panel(fy, primary, token, csrf, can_edit, posted):
    disabled = "" if can_edit else " disabled"
    has_last = any(v is not None for c in primary["channels"] for v in c["last"])
    body = (f'<h2>Primary — channel targets for {fy_name(fy)}</h2><p class="note">Doctor Towels’ invoices to each channel, '
            'before GST, month by month. These are the targets on the Dashboard, each channel page and Target Balance.</p>')
    body += f'<form method="post" action="{PATH}/channels" class="targets"><input type="hidden" name="csrf_token" value="{esc(csrf)}">'
    body += f'<input type="hidden" name="fy" value="{fy}"><input type="hidden" name="token" value="{esc(token)}">'
    body += _growth_tools(fy, has_last, "channels", can_edit)
    body += (f'<div class="scroll"><table class="g"><thead><tr><th class="nm">Channel</th><th>{fy_name(fy - 1)}<br>target</th>'
             f'<th>{fy_name(fy - 1)}<br>achieved</th><th>{fy_name(fy)}<br>year total</th><th>vs last<br>year</th>{_months_header(fy)}</tr></thead><tbody>')
    for c in primary["channels"]:
        prefix = f"c{c['id']}"
        body += (f'<tr data-row><td class="nm"><b>{esc(c["code"])}</b><small data-short></small></td>'
                 f'<td class="ref">{short(total(c["last"]))}</td><td class="ref">{short(c["achieved"])}</td>'
                 f'<td><input class="amt year" inputmode="decimal" autocomplete="off" aria-label="{esc(c["code"])} year total"{disabled}></td>'
                 f'<td class="chg" data-change></td>{_row_cells(prefix, c["months"], c["last"], posted, c["code"], disabled)}</tr>')
    last_total = total([v for c in primary["channels"] for v in c["last"]])
    got = total([c["achieved"] for c in primary["channels"]])
    body += (f'<tr class="tot"><td class="nm">All channels</td><td class="ref">{short(last_total)}</td><td class="ref">{short(got)}</td>'
             f'<td data-year></td><td class="chg" data-change></td>' + "".join(f'<td data-m="{i}"></td>' for i in range(12)) + '</tr>')
    body += '</tbody></table></div>' + _save_bar("Save channel targets", can_edit, fy) + '</form>'
    return body


def _team_row(n, person, person_name, place, place_name, months, last, achieved, posted, disabled):
    return (f'<tr data-row data-key="{esc(person)}|{esc(place)}"><td class="nm"><b>{esc(person_name)}</b>'
            + ('' if disabled else '<button type="button" class="rm" title="Remove this row">×</button>')
            + f'<small>{esc(place_name)}</small><input type="hidden" name="row" value="{n}">'
            f'<input type="hidden" name="r{n}_person" value="{esc(person)}"><input type="hidden" name="r{n}_place" value="{esc(place)}"></td>'
            f'<td class="ref">{short(total(last))}</td><td class="ref">{short(achieved)}</td>'
            f'<td><input class="amt year" inputmode="decimal" autocomplete="off" aria-label="{esc(person_name)} year total"{disabled}></td>'
            f'<td class="chg" data-change></td>{_row_cells(f"r{n}", months, last, posted, person_name, disabled)}</tr>')


def _team_panel(fy, primary, token, csrf, can_edit, posted):
    disabled = "" if can_edit else " disabled"
    places, people = dict(primary["places"]), dict(primary["people"])
    rows = [dict(r, n=index) for index, r in enumerate(primary["team"])]
    if posted is not None:
        rows = []
        for n in posted.getlist("row"):
            if not str(n).isdigit():
                continue
            person, place = str(posted.get(f"r{n}_person", "")), str(posted.get(f"r{n}_place", ""))
            old = next((r for r in primary["team"] if str(r["person"]) == person and r["place"] == place), None)
            rows.append({"n": n, "person": person, "personName": people.get(int(person), person) if person.isdigit() else person,
                         "place": place, "placeName": places.get(place, place), "months": [None] * 12,
                         "last": old["last"] if old else [None] * 12, "achieved": old["achieved"] if old else None})
    has_last = any(v is not None for r in rows for v in r["last"])
    following = max([int(r["n"]) for r in rows], default=-1) + 1
    body = (f'<h2>Primary — sales team targets for {fy_name(fy)}</h2><p class="note">Each executive’s target in a city, or across a whole state. '
            'They show on Sales team — executive details. They are part of the channel targets, so they are not added to the company total.</p>')
    body += f'<form method="post" action="{PATH}/team" class="targets"><input type="hidden" name="csrf_token" value="{esc(csrf)}">'
    body += f'<input type="hidden" name="fy" value="{fy}"><input type="hidden" name="token" value="{esc(token)}">'
    body += _growth_tools(fy, has_last, "team", can_edit)
    body += (f'<div class="scroll"><table class="g" id="team-rows" data-year="{fy_name(fy)}" data-next="{following}"><thead><tr><th class="nm">Executive · place</th>'
             f'<th>{fy_name(fy - 1)}<br>target</th><th>{fy_name(fy - 1)}<br>achieved</th><th>{fy_name(fy)}<br>year total</th><th>vs last<br>year</th>'
             f'{_months_header(fy)}</tr></thead><tbody>')
    for r in rows:
        body += _team_row(r["n"], r["person"], r["personName"], r["place"], r["placeName"], r["months"], r["last"],
                          r["achieved"], posted, disabled)
    body += ('<tr class="tot"><td class="nm">All sales team rows</td><td class="ref"></td><td class="ref"></td><td data-year></td><td class="chg" data-change></td>'
             + "".join(f'<td data-m="{i}"></td>' for i in range(12)) + '</tr></tbody></table></div>')
    body += '<template id="team-template">' + _team_row("__N__", "", "__PERSON__", "", "__PLACE__", [None] * 12, [None] * 12,
                                                        None, None, "") + '</template>'
    if can_edit:
        body += ('<div class="add"><div><label for="team-person">Executive</label><select id="team-person"><option value="">Choose…</option>'
                 + "".join(f'<option value="{pid}">{esc(name)}</option>' for pid, name in primary["people"])
                 + '</select></div><div><label for="team-place">City or whole state</label><select id="team-place"><option value="">Choose…</option>'
                 + "".join(f'<option value="{esc(k)}">{esc(v)}</option>' for k, v in primary["places"])
                 + '</select></div><button type="button" class="sec" id="team-add">Add row</button><span id="team-msg" class="note" style="margin:0"></span></div>')
    body += _save_bar("Save sales team targets", can_edit, fy) + '</form>'
    return body


def _follows_branches(months, branches):
    """True when every month equals the branches' monthly targets added up."""
    added = total(branches)
    return added is not None and all(m is not None and Decimal(m) == added for m in months)


def _group_block(fy, code, g, csrf, can_edit, can_add, posted):
    """One sub-channel: its monthly targets and its branches. Returns the block
    and the Add branch form, which must sit outside the targets form."""
    disabled = "" if can_edit else " disabled"
    prefix = f"g{g['id']}"
    months = g["months"]
    # A new year follows last year's way of setting this group; a group with no
    # history (a new channel) starts as the sum of its branches.
    if any(m is not None for m in months):
        mode_sum = _follows_branches(months, [b["monthly"] for b in g["branches"]])
    elif any(m is not None for m in g["lastMonths"]):
        mode_sum = _follows_branches(g["lastMonths"], [b["lastMonthly"] for b in g["branches"]])
    else:
        mode_sum = True
    badge = {"planner": ('src', 'Target planner'), "sheet": ('src sheet', 'Google Sheet'),
             None: ('src none', 'Not set yet')}[g["source"]]
    body = (f'<section class="grp" id="group-{g["id"]}"><header><h3>{esc(g["name"])}</h3><span class="{badge[0]}">{badge[1]}</span>'
            f'<label>Sub-channel target <select class="mode" aria-label="{esc(g["name"])} target"{disabled}>'
            f'<option value="sum"{" selected" if mode_sum else ""}>= sum of its branches</option>'
            f'<option value="month"{"" if mode_sum else " selected"}>set month by month</option></select></label>'
            f'<span class="note" style="margin:0">{fy_name(fy - 1)}: target {short(total(g["lastMonths"]))} · sales {short(g.get("lastSales"))}</span></header>')
    if g["source"] == "sheet":
        body += (f'<p class="warn" style="margin:10px 12px 0">These {fy_name(fy)} targets come from the Google Sheet. Saving a change here moves '
                 f'{esc(g["name"])}’s {fy_name(fy)} targets to the planner, and the Sheet stops changing them.'
                 + (f' {len(g["unmatched"])} Sheet target(s) without a matching branch will be set aside.' if g["unmatched"] else '') + '</p>')
    body += (f'<div class="scroll"><table class="g"><thead><tr><th class="nm">Sub-channel</th><th>{fy_name(fy)}<br>year total</th>'
             f'<th>vs last<br>year</th>{_months_header(fy)}</tr></thead><tbody>'
             f'<tr data-row><td class="nm"><b>{esc(g["name"])}</b><small data-short></small></td>'
             f'<td><input class="amt year" inputmode="decimal" autocomplete="off" aria-label="{esc(g["name"])} year total"{disabled}></td>'
             f'<td class="chg" data-change></td>{_row_cells(prefix, months, g["lastMonths"], posted, g["name"], disabled)}</tr></tbody></table></div>')
    body += (f'<div class="scroll"><table class="b"><thead><tr><th>Branch / retailer</th><th>{fy_name(fy - 1)} monthly</th>'
             f'<th>{fy_name(fy)} monthly target</th><th>Year (×12)</th></tr></thead><tbody>')
    for b in g["branches"]:
        name = f"{prefix}_b{b['id']}"
        value = posted.get(name, "") if posted is not None else plain(b["monthly"])
        body += (f'<tr><td>{esc(b["name"])}</td><td class="ref">{short(b["lastMonthly"])}</td>'
                 f'<td><input class="amt br" name="{esc(name)}" value="{esc(value)}" inputmode="decimal" autocomplete="off"'
                 f' data-last="{esc(b["lastMonthly"] if b["lastMonthly"] is not None else "")}" aria-label="{esc(b["name"])} monthly target"{disabled}></td>'
                 f'<td data-byear class="ref"></td></tr>')
    if not g["branches"]:
        body += '<tr><td colspan="4" class="note">No branches yet. Add one below; its target can then be set here.</td></tr>'
    body += f'</tbody></table></div><div class="foot"><span>Branches add up to <b data-branch-sum></b></span>'
    extra = ''
    if can_add:
        # The inputs sit here but belong, through form=, to a form outside the
        # targets form: forms cannot nest.
        fid = f"add-branch-{g['id']}"
        body += (f'<input type="text" name="name" form="{fid}" maxlength="200" placeholder="New branch / retailer name" '
                 f'aria-label="New branch or retailer under {esc(g["name"])}" required>'
                 f'<button type="submit" class="sec" form="{fid}">Add branch</button>')
        extra = (f'<form method="post" action="{PATH}/branch" class="structure" id="{fid}"><input type="hidden" name="csrf_token" value="{esc(csrf)}">'
                 f'<input type="hidden" name="fy" value="{fy}"><input type="hidden" name="channel" value="{esc(code)}">'
                 f'<input type="hidden" name="distributor_id" value="{g["id"]}"></form>')
    return body + '</div></section>', extra


def _secondary_panel(fy, secondary, csrf, can_edit, user, posted, open_channel):
    body = (f'<h2>Secondary — sub-channels and branches for {fy_name(fy)}</h2><p class="note">Each channel’s own sales onward: '
            'its sub-channels (groups such as Lulu or Naturals) and their branches / retailers. Measured on monthly secondary sales, '
            'separately from primary. A channel’s secondary target is the sum of its sub-channels.</p><div class="chan" role="tablist">')
    for code in CHANNELS:
        body += (f'<button type="button" role="tab" data-channel-tab="{esc(code)}" aria-selected="{str(code == open_channel).lower()}">'
                 f'{esc(code)}</button>')
    body += '</div>'
    for code in CHANNELS:
        data = secondary[code]
        ch = store_channel(code)
        can_add = can_edit and auth.can(user, ch.perm("stores.create"))
        mine = posted if posted is not None and posted.get("channel") == code else None
        body += f'<div data-channel-panel="{esc(code)}"{"" if code == open_channel else " hidden"}>'
        if not data["installed"]:
            body += (f'<p class="warn">{esc(code)} sub-channels need the database update. Run <b>apply-update.cmd</b> on the computer '
                     'that hosts the dashboard database, restart the dashboard, then reload this page.</p></div>')
            continue
        groups = data["groups"]
        has_last = any(v is not None for g in groups for v in g["lastMonths"] + [b["lastMonthly"] for b in g["branches"]])
        body += (f'<form method="post" action="{PATH}/secondary" class="targets"><input type="hidden" name="csrf_token" value="{esc(csrf)}">'
                 f'<input type="hidden" name="fy" value="{fy}"><input type="hidden" name="channel" value="{esc(code)}">'
                 f'<input type="hidden" name="token" value="{esc(data["token"])}">')
        if groups:
            body += _growth_tools(fy, has_last, ch.slug, can_edit)
        outside = ''
        for g in groups:
            block, extra = _group_block(fy, code, g, csrf, can_edit, can_add, mine)
            body += block
            outside += extra
        if groups:
            body += (f'<div class="scroll"><table class="g"><thead><tr><th class="nm">{esc(code)} secondary</th><th>{fy_name(fy)}<br>year total</th>'
                     f'<th>vs last<br>year</th>{_months_header(fy)}</tr></thead><tbody><tr class="tot" data-channel-total><td class="nm">All sub-channels</td>'
                     '<td data-year></td><td class="chg" data-change></td>' + "".join(f'<td data-m="{i}"></td>' for i in range(12))
                     + '</tr></tbody></table></div>')
            body += _save_bar(f"Save {code} secondary targets", can_edit, fy)
        else:
            body += f'<p class="note">{esc(code)} has no sub-channels yet. Add the first one below, then its branches / retailers.</p>'
        body += '</form>' + outside
        if can_add:
            listid = f"sub-{ch.slug}"
            body += (f'<form method="post" action="{PATH}/group" class="structure add"><input type="hidden" name="csrf_token" value="{esc(csrf)}">'
                     f'<input type="hidden" name="fy" value="{fy}"><input type="hidden" name="channel" value="{esc(code)}">'
                     f'<div><label for="new-{ch.slug}">New {esc(code)} sub-channel / group</label>'
                     f'<input type="text" id="new-{ch.slug}" name="name" list="{listid}" maxlength="120" placeholder="For example {esc(data["suggestions"][0] if data["suggestions"] else "Naturals")}" required>'
                     f'<datalist id="{listid}">' + "".join(f'<option value="{esc(s)}">' for s in data["suggestions"]) + '</datalist></div>'
                     '<button type="submit" class="sec">Add sub-channel</button></form>')
        elif can_edit:
            body += f'<p class="note">Adding {esc(code)} sub-channels or branches needs {esc(code)} branches / retailers permission.</p>'
        body += '</div>'
    return body


def _summary(fy, primary, secondary):
    chan = total([v for c in primary["channels"] for v in c["months"]])
    chan_last = total([v for c in primary["channels"] for v in c["last"]])
    team = total([v for r in primary["team"] for v in r["months"]])
    people = len({r["person"] for r in primary["team"] if any(v is not None for v in r["months"])})
    sec = total([v for data in secondary.values() for g in data["groups"] for v in g["months"]])
    sec_last = total([v for data in secondary.values() for g in data["groups"] for v in g["lastMonths"]])

    def versus(now, before):
        if now is None or not before:
            return f"{fy_name(fy - 1)}: {short(before)}"
        change = (Decimal(now) / Decimal(before) - 1) * 100
        return f'{"+" if change >= 0 else ""}{change:.1f}% on {fy_name(fy - 1)} ({short(before)})'
    return ('<div class="sum">'
            f'<div><span>Primary · all channels</span><strong>{short(chan) if chan is not None else "Not set"}</strong><small>{versus(chan, chan_last)}</small></div>'
            f'<div><span>Primary · sales team</span><strong>{short(team) if team is not None else "Not set"}</strong><small>{people} executives · inside the channel targets</small></div>'
            f'<div><span>Secondary · all channels</span><strong>{short(sec) if sec is not None else "Not set"}</strong><small>{versus(sec, sec_last)}</small></div>'
            '</div>')


def render(user, csrf, fy, step="channels", channel=None, notice="", error="", posted=None, posted_step=None):
    can_edit = _can_edit(user)
    with closing(pgdata.connect()) as conn:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            years = planner_years(cur, fy)
            primary = load_primary(cur, fy)
        secondary = load_secondary(conn, fy)
    open_channel = channel if channel in CHANNELS else next((c for c in CHANNELS if secondary[c]["groups"]), CHANNELS[0])
    now = current_fy()
    body = (f'<div class="tp"><h1>{TITLE}</h1><p class="lead">Set a financial year’s targets in one place — this year or the years ahead. '
            'Start from last year’s targets with a growth %, or type a year total and let it split across the months. '
            'Amounts can be typed as 250000, 2.5L or 1.2Cr.</p><nav class="years" aria-label="Financial year">')
    for year in years:
        tag = "this year" if year == now else "next year" if year == now + 1 else "planned" if year > now + 1 else ""
        body += (f'<a href="{PATH}?{urlencode({"fy": year, "step": step})}"{" aria-current=page" if year == fy else ""}>'
                 f'{fy_name(year)}<small>{tag}</small></a>')
    body += ('</nav><div class="explain"><div><b>Primary targets</b>What Doctor Towels invoices to each channel and its sales team, '
             'before GST. Measured on the sales register.</div><div><b>Secondary targets</b>What each channel sells onward to its '
             'sub-channels and branches / retailers. Measured on monthly secondary sales.</div></div>')
    if notice:
        body += f'<p class="ok" role="status">{esc(notice)}</p>'
    if error:
        body += f'<p class="err" role="alert">{esc(error)}</p>'
    body += _summary(fy, primary, secondary)
    body += '<div class="steps" role="tablist">'
    for n, (key, label, kind) in enumerate(STEPS, 1):
        body += (f'<button type="button" role="tab" data-step-tab="{key}" aria-selected="{str(key == step).lower()}">'
                 f'<small>{n} · {kind}</small>{esc(label)}</button>')
    body += '</div>'
    plan = primary["plan"]
    plan_note = (f'<p class="note">Saved in the plan “{esc(plan[1])}”, the one the dashboard uses for {fy_name(fy)}.</p>' if plan else
                 f'<p class="note">{fy_name(fy)} has no targets yet. The first save creates its plan, and the dashboard starts showing the year.</p>')
    panels = {
        "channels": plan_note + _channels_panel(fy, primary, primary["channelToken"], csrf, can_edit,
                                                posted if posted_step == "channels" else None),
        "team": _team_panel(fy, primary, primary["teamToken"], csrf, can_edit, posted if posted_step == "team" else None),
        "secondary": _secondary_panel(fy, secondary, csrf, can_edit, user, posted if posted_step == "secondary" else None,
                                      open_channel),
    }
    for key, _, _ in STEPS:
        body += f'<section class="panel" data-step-panel="{key}"{"" if key == step else " hidden"}>{panels[key]}</section>'
    body += ('<details class="note" style="margin-top:14px"><summary><strong>Advanced target settings</strong></summary>'
             '<p>Manage draft, active and archived plans, or set quantity, sales including GST and other target measures. '
             'Use combined scopes such as channel, salesperson, state, district, city and brand.</p>'
             '<p><a href="/targets">Open advanced target settings →</a></p></details>'
             '<p class="note"><a href="/">← Back to the dashboard</a></p></div>')
    return body + SCRIPT


# ------------------------------------------------------------------ routes

def register(app, current_user, login_redirect, page, invalidate):
    def respond(user, body, status=200):
        html = page(TITLE, body, user).replace("</head>", f"<style>{CSS}</style></head>", 1)
        return HTMLResponse(html, status_code=status, headers={"Cache-Control": "no-store"})

    def failure(user, message, status):
        return respond(user, f'<div class="card"><h1>{TITLE}</h1><p class="err" role="alert">{esc(message)}</p>'
                             f'<p><a href="{PATH}">Open the Target planner</a></p></div>', status)

    def selected_year(value):
        if value in (None, ""):
            return current_fy() + 1
        if not re.fullmatch(r"20\d\d", str(value)):
            raise PlannerError("Choose a financial year from the list.")
        fy = int(value)
        if fy < current_fy():
            raise PlannerError(f"{fy_name(fy)} has ended. Past years are kept as they are; Advanced target settings shows them.")
        if fy > current_fy() + 10:
            raise PlannerError("Plan up to ten years ahead.")
        return fy

    @app.get(PATH, response_class=HTMLResponse, name="target_planner")
    def planner(request: Request):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        if not auth.can(user, "targets.view"):
            return failure(user, "Targets View permission is required.", 403)
        params = request.query_params
        step = params.get("step") if params.get("step") in {s[0] for s in STEPS} else "channels"
        saved = params.get("saved", "")
        notice = {"channels": "Channel targets saved.", "team": "Sales team targets saved.",
                  "secondary": f"{params.get('channel', '')} secondary targets saved.",
                  "group": "Sub-channel added. Set its targets and add its branches below.",
                  "branch": "Branch / retailer added. Set its monthly target below.",
                  "none": "Nothing changed, so nothing was saved."}.get(saved, "")
        try:
            fy = selected_year(params.get("fy"))
            return respond(user, render(user, target_web._csrf(request), fy, step, params.get("channel"), notice))
        except PlannerError as exc:
            return failure(user, str(exc), 400)
        except Exception:
            return failure(user, DB_MESSAGE, 503)

    async def save(request, step, action):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        if not _can_edit(user):
            return failure(user, "Targets Edit permission is required to save targets.", 403)
        form = await request.form(max_fields=20000)
        if not target_web._valid_csrf(request, form):
            return failure(user, "Your form session expired. Reload the planner and try again.", 403)
        channel = form.get("channel") if step == "secondary" else None
        try:
            fy, counts = await run_in_threadpool(action, form, user, auth.can(user, "targets.delete"))
        except (PlannerError, lfo_data.LFOValidationError, lfo_data.LFOConflictError) as exc:
            if isinstance(exc, lfo_data.LFOConflictError):
                message = ("These targets were changed by someone else (or a Sheet import) after you opened the page. "
                           "Nothing was saved. Your numbers are shown below; reload to see the latest before saving again.")
                status = 409
            else:
                message, status = str(getattr(exc, "public_message", exc)), 400
            try:
                fy = selected_year(form.get("fy"))
                body = await run_in_threadpool(render, user, target_web._csrf(request), fy, step, channel,
                                               error=message, posted=form, posted_step=step)
                return respond(user, body, status)
            except PlannerError as again:
                return failure(user, str(again), 400)
            except Exception:
                return failure(user, message, status)
        except Exception as exc:
            code = getattr(exc, "pgcode", None)
            if code == "42501":           # insufficient privilege: migration 15 not applied
                return failure(user, "Planning a new financial year needs the database update. Run apply-update.cmd on the "
                                     "computer that hosts the dashboard database, restart the dashboard, and try again.", 503)
            return failure(user, DB_MESSAGE, 503)
        changed = any(counts.values())
        if changed:
            invalidate()
        query = {"fy": fy, "step": step, "saved": step if changed else "none"}
        if channel:
            query["channel"] = channel
        return RedirectResponse(f"{PATH}?{urlencode(query)}", status_code=303, headers={"Cache-Control": "no-store"})

    @app.post(PATH + "/channels", name="planner_channels")
    async def planner_channels(request: Request):
        return await save(request, "channels", save_channels)

    @app.post(PATH + "/team", name="planner_team")
    async def planner_team(request: Request):
        return await save(request, "team", save_team)

    @app.post(PATH + "/secondary", name="planner_secondary")
    async def planner_secondary(request: Request):
        return await save(request, "secondary", save_secondary)

    async def structure(request, kind):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        form = await request.form()
        if not target_web._valid_csrf(request, form):
            return failure(user, "Your form session expired. Reload the planner and try again.", 403)
        try:
            fy, code = selected_year(form.get("fy")), _channel_field(form)
        except PlannerError as exc:
            return failure(user, str(exc), 400)
        ch = store_channel(code)
        if not (_can_edit(user) and auth.can(user, ch.perm("stores.create"))):
            return failure(user, f"Adding {code} sub-channels or branches needs Targets Edit and {code} branches / retailers permission.", 403)

        def create():
            with closing(pgdata.connect()) as conn:
                if kind == "group":
                    return lfo_data.create_distributor(conn, code, str(form.get("name", "")), user["username"])["id"]
                did = lfo_web._id(form.get("distributor_id"), required=True)
                lfo_data.create_store(conn, did, str(form.get("name", "")), user["username"], channel=code)
                return did
        try:
            group = await run_in_threadpool(create)
        except (lfo_data.LFOValidationError, lfo_web.FormError) as exc:
            try:
                body = await run_in_threadpool(render, user, target_web._csrf(request), fy, "secondary", code,
                                               error=str(getattr(exc, "public_message", exc)))
                return respond(user, body, 400)
            except Exception:
                return failure(user, str(exc), 400)
        except Exception:
            return failure(user, DB_MESSAGE, 503)
        invalidate()
        query = urlencode({"fy": fy, "step": "secondary", "channel": code, "saved": kind})
        return RedirectResponse(f"{PATH}?{query}#group-{group}", status_code=303, headers={"Cache-Control": "no-store"})

    @app.post(PATH + "/group", name="planner_group")
    async def planner_group(request: Request):
        return await structure(request, "group")

    @app.post(PATH + "/branch", name="planner_branch")
    async def planner_branch(request: Request):
        return await structure(request, "branch")
