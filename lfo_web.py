"""Authenticated monthly secondary sales for every channel's distributor/store hierarchy.

One implementation serves every channel. Each has its own URLs (/lfo/stores,
/llfo/stores, /saloon/stores, /drs/stores, /drn/stores), its own permissions
(lfo.*, llfo.*, saloon.*, ...) and its own data; a form for one channel can
neither read nor write another's stores. LFO and L-LFO come from their
Distributed tabs; the others are set up in the Target planner. Adding a branch
/ retailer is one page for every channel (/stores/new), where the operator
chooses the channel; each channel still needs its own stores.create permission.
"""

import re
import secrets
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

import auth
import lfo_data
import pgdata
from entry_styles import STYLE
from entry_web import number


DB_MESSAGE = "LFO store sales are temporarily unavailable. Please try again or ask your administrator to check PostgreSQL."


@dataclass(frozen=True)
class Channel:
    code: str            # lfo_data channel: 'LFO' or 'L-LFO'
    slug: str            # URL prefix, permission prefix and dashboard page id
    sheet: str           # source tab in the Google Sheet
    order: tuple         # preferred distributor order, casefolded names
    target_note: str     # how this channel's targets are set, for the forms
    reconcile_note: str  # intro to the branch target comparison
    register_note: str   # target note under the store register

    @property
    def base(self):
        return f"/{self.slug}/stores"

    def perm(self, name):
        return f"{self.slug}.{name}"

    @property
    def db_message(self):
        return DB_MESSAGE.replace("LFO store sales", f"{self.code} store sales")


LFO = Channel(
    code="LFO", slug="lfo", sheet="LFO Distributed", order=("lulu", "reliance", "nilkamal homes"),
    target_note=("Branch monthly targets use the exact amounts listed for each branch in LFO Distributed. "
                 "Distributor targets keep their own monthly plan. Primary targets cover DT + TT. "
                 "“Others” records unallocated sales and is excluded from store counts and targets."),
    reconcile_note="Amounts as listed in LFO Distributed. Difference = distributor target − all listed branch targets.",
    register_note=("Branch targets keep the exact monthly and yearly amounts listed in LFO Distributed. Distributor yearly "
                   "targets sum their own monthly plan. Primary targets cover DT + TT; changes to Primary targets do not "
                   "change Secondary targets. “Others” is excluded from store counts and targets."))
LLFO = Channel(
    code="L-LFO", slug="llfo", sheet="L-LFO Distributed",
    order=("the chennai silks", "pothys", "naidu hall", "gm", "chera", "kc", "rmkv"),
    target_note=("Branch monthly targets are each Location’s Secondary Target in L-LFO Distributed. "
                 "A group’s target is the sum of its branches’ targets. Primary targets cover DT + TT."),
    reconcile_note=("Amounts as listed in L-LFO Distributed. A group’s target is the sum of its branch targets, "
                    "so the difference is zero unless a branch target is missing."),
    register_note=("Branch targets are each Location’s Secondary Target in L-LFO Distributed; a branch’s year is twelve "
                   "months of it. A group’s target is the sum of its branches’ targets. Primary targets cover DT + TT; "
                   "changes to Primary targets do not change Secondary targets."))

# Channels with no Distributed tab: sub-channels, branches and their targets are
# set up in the Target planner (/targets/planner). sheet names that source.
def _planned(code, slug):
    return Channel(
        code=code, slug=slug, sheet="Target planner", order=(),
        target_note=("Branch and sub-channel monthly targets are set in the Target planner. "
                     "Primary targets cover DT + TT and are separate."),
        reconcile_note=("Targets from the Target planner. Difference = sub-channel target − all listed branch targets; "
                        "it is zero when the sub-channel target is the sum of its branches."),
        register_note=("Branch targets are set in the Target planner; a branch’s year is twelve months of its monthly "
                       "target. Primary targets cover DT + TT; changes to Primary targets do not change Secondary targets."))


SALOON = _planned("Saloon", "saloon")
DRS = _planned("D&R-S", "drs")
DRN = _planned("D&R-N", "drn")
CHANNELS = (LFO, LLFO, SALOON, DRS, DRN)

# One Add branch / retailer page serves every channel; the operator picks the
# channel on the form. Permission stays per channel (lfo.stores.create, ...).
BRANCH_NEW = "/stores/new"
BRANCH_CREATE = "/stores/create"
BRANCH_TITLE = "Add branch / retailer"
BRANCH_PERMISSIONS = tuple(ch.perm("stores.create") for ch in CHANNELS)


def branch_channels(user):
    """Channels in which user may add a branch / retailer, in menu order."""
    return [ch for ch in CHANNELS if auth.can(user, ch.perm("stores.create"))]


def _channel(slug):
    return next((ch for ch in CHANNELS if ch.slug == slug), None)


def _new_url(ch, month=None, distributor=None):
    args = {"channel": ch.slug}
    if distributor:
        args["distributor"] = distributor
    if month:
        args["month"] = month.strftime("%Y-%m") if isinstance(month, date) else month
    return BRANCH_NEW + "?" + urlencode(args)


CSS = """<style>
.lfo-page .entry-stats{grid-template-columns:repeat(3,minmax(0,1fr))}
.lfo-page .lfo-filters{display:grid;grid-template-columns:160px 1fr 1.3fr auto;gap:16px;align-items:end;padding:20px 24px;background:#fcfcfd;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.lfo-page .lfo-panel{margin-bottom:24px}.lfo-page .lfo-form{padding:22px 24px}
.lfo-page .lfo-form-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:end}.lfo-page .lfo-form-grid .lfo-wide{grid-column:1/-1}
.lfo-page .lfo-form p{color:var(--muted);font-size:13px;line-height:1.6}
.lfo-page .lfo-form .entry-form-buttons{margin-top:20px;flex-wrap:wrap}
.lfo-page .lfo-source{font-size:11px;color:var(--muted)}
.lfo-page .lfo-tag{display:inline-block;background:#f1f3f7;color:#616b7a;font-size:10px;padding:3px 7px;border-radius:4px;margin:3px 0}
.lfo-page .lfo-note{margin:0 0 20px;color:var(--muted);font-size:12px;line-height:1.6}
.lfo-page .lfo-total{font-weight:650;background:#f8f9fb}
.lfo-page .lfo-distributors{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;margin:22px 0}
.lfo-page .lfo-distributor{display:block;border:1px solid var(--line);background:white;border-radius:10px;padding:26px;color:var(--ink);text-decoration:none;box-shadow:0 2px 5px #20233103}
.lfo-page .lfo-distributor:hover{border-color:var(--accent);text-decoration:none;box-shadow:0 4px 12px #20233109}.lfo-page .lfo-distributor h2{font-size:20px;margin:0 0 12px}.lfo-page .lfo-distributor p{font-size:13px;color:var(--muted);margin:8px 0}.lfo-page .lfo-distributor strong{display:block;font-size:22px;margin:18px 0 8px}.lfo-page .lfo-distributor .lfo-open{color:var(--accent);font-weight:600;font-size:13px}
.lfo-page .lfo-month-filter{display:flex;gap:14px;align-items:end;flex-wrap:wrap;padding:20px 24px}.lfo-page .lfo-month-filter .entry-field{flex:1;min-width:150px}.lfo-page .lfo-month-filter .entry-field:first-child{flex:0 0 170px}
.lfo-page .lfo-batch-table input[type=number]{text-align:right;max-width:210px;font-variant-numeric:tabular-nums}.lfo-page .lfo-batch-table th:nth-child(1){width:29%}.lfo-page .lfo-batch-table th:nth-child(2){width:21%}.lfo-page .lfo-batch-table th:nth-child(3){width:25%}.lfo-page .lfo-batch-table th:nth-child(4){width:15%}.lfo-page .lfo-batch-table th:nth-child(5){width:10%}
.lfo-page .lfo-batch-note{padding:0 24px 18px;margin:0;color:var(--muted);font-size:12px;line-height:1.7}.lfo-page .lfo-batch-actions{padding:20px 24px;display:flex;gap:16px;align-items:center;flex-wrap:wrap;border-top:1px solid var(--line);background:#fcfcfd}.lfo-page .lfo-batch-actions small{color:var(--muted);font-size:12px}.lfo-page .lfo-batch-table tr:target{background:#fff8eb}
.lfo-page .lfo-reconciliation .entry-table th:first-child{width:25%}.lfo-page .lfo-unmatched{padding:16px 24px;background:#fffaf0;border-top:1px solid var(--line);font-size:12px;color:#75521a;line-height:1.7}.lfo-page .lfo-unmatched ul{margin:8px 0;padding-left:20px}.lfo-page .lfo-unmatched p{margin:6px 0 0}
@media(max-width:800px){.lfo-page .lfo-filters{grid-template-columns:1fr 1fr}.lfo-page .lfo-filters .lfo-store-field{grid-column:1/-1}.lfo-page .lfo-filters button{grid-column:1/-1}.lfo-page .entry-stats{grid-template-columns:1fr}.lfo-page .entry-stat{border-right:0;border-bottom:1px solid var(--line)}}
@media(max-width:800px){.lfo-page .lfo-distributors{grid-template-columns:1fr}.lfo-page .lfo-distributor{padding:22px}.lfo-page .lfo-month-filter .entry-field:first-child{flex:1}}
@media(max-width:480px){.lfo-page .lfo-filters,.lfo-page .lfo-form-grid{grid-template-columns:1fr}.lfo-page .lfo-form{padding:18px}}
</style>"""
FILTER_SCRIPT = """<script>(()=>{
 const distributor=document.getElementById('lfo-distributor'),store=document.getElementById('lfo-store');
 if(!distributor||!store)return;
 const filter=()=>{for(const option of store.options){const hide=!!(option.value&&distributor.value&&option.dataset.distributor!==distributor.value);option.hidden=hide;option.disabled=hide;if(hide&&option.selected)store.value='';}};
 distributor.addEventListener('change',filter);filter();
})();</script>"""
BATCH_SCRIPT = """<script>(()=>{
 const form=document.getElementById('lfo-monthly-batch');if(!form)return;
 const output=document.getElementById('lfo-preview-total');
 const preview=()=>{let total=0,reported=false,valid=true;
  for(const cell of form.querySelectorAll('[data-lfo-sales]')){
   const input=cell.querySelector('input[type=number]');
   const raw=input&&input.value.trim()!==''?input.value:cell.dataset.lfoSales;
   if(raw==='')continue;const value=Number(raw);if(!Number.isFinite(value)||value<0){valid=false;continue;}
   reported=true;total+=value;
  }
  output.textContent=valid?(reported?new Intl.NumberFormat('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2}).format(total):'Unreported'):'Check amounts';
 };
 form.addEventListener('input',preview);preview();
})();</script>"""


class FormError(ValueError):
    pass


def esc(value):
    return escape(str(value if value is not None else ""), quote=True)


def may_view(user, ch=LFO):
    return any(auth.can(user, ch.perm(f"sales.{action}")) for action in ("view", "create", "edit"))


def _id(value, required=False):
    if value in (None, "") and not required:
        return None
    text = str(value)
    if not re.fullmatch(r"[1-9][0-9]{0,9}", text) or int(text) > 2147483647:
        raise FormError("Choose a valid distributor and store.")
    return int(text)


def _month(value):
    text = str(value or "")
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}", text):
            return date.fromisoformat(text + "-01")
        if re.fullmatch(r"[A-Za-z]{3}-[0-9]{4}", text):
            return datetime.strptime(text, "%b-%Y").date()
    except ValueError:
        pass
    raise FormError("Choose a valid sales month.")


def _amount(value):
    raw = str(value or "").strip()
    if not raw:
        raise FormError("Enter the monthly sales amount. Enter 0 only for a confirmed month with no sales.")
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        raise FormError("Enter a valid monthly sales amount.") from None
    if not amount.is_finite() or amount < 0 or amount >= Decimal("1000000000000"):
        raise FormError("Monthly sales must be from 0 to 999,999,999,999.99.")
    if amount != amount.quantize(Decimal("0.01")):
        raise FormError("Monthly sales can have at most two decimal places.")
    return amount


def _revision(value):
    text = str(value or "")
    if not re.fullmatch(r"[0-9]{1,18}", text):
        raise FormError("Reload the monthly sales entry before saving.")
    return int(text)


def _csrf(request):
    if not request.session.get("csrf_token"):
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    return request.session["csrf_token"]


def _valid_csrf(request, form):
    given, expected = form.get("csrf_token", ""), request.session.get("csrf_token", "")
    return (isinstance(given, str) and isinstance(expected, str) and bool(expected)
            and secrets.compare_digest(given.encode("utf-8"), expected.encode("utf-8")))


def _url(month, distributor=None, store=None, *, ch=LFO, **extra):
    args = {"month": month.strftime("%Y-%m")}
    if distributor:
        args["distributor"] = distributor
    if store:
        args["store"] = store
    args.update(extra)
    return ch.base + "?" + urlencode(args)


def _load(ch=LFO):
    with closing(pgdata.connect()) as conn:
        return lfo_data.fetch_dashboard(conn, channel=ch.code)


def _selection(data, params, ch=LFO):
    month = _month(params.get("month") or date.today().strftime("%Y-%m"))
    distributor_id = _id(params.get("distributor"))
    store_id = _id(params.get("store"))
    distributors = {int(d["id"]): d for d in data.get("distributors", [])}
    stores = {int(s["id"]): s for s in data.get("stores", [])}
    if distributor_id and distributor_id not in distributors:
        raise FormError(f"The selected {ch.code} distributor was not found.")
    if store_id:
        store = stores.get(store_id)
        if not store or int(store["distributorId"]) not in distributors:
            raise FormError(f"The selected {ch.code} store was not found.")
        if distributor_id and int(store["distributorId"]) != distributor_id:
            raise FormError("That store does not belong to the selected distributor.")
        distributor_id = int(store["distributorId"])
    return month, distributor_id, store_id


def _save(form, user, ch=LFO):
    month = _month(form.get("period_month"))
    distributor_id = _id(form.get("distributor_id"), required=True)
    store_id = _id(form.get("store_id"), required=True)
    revision = _revision(form.get("revision"))
    amount = _amount(form.get("amount"))
    # Check both the current row and the revision. A forged revision must never
    # turn an update into a create and bypass the operator's edit permission.
    with closing(pgdata.connect()) as conn:
        data = lfo_data.fetch_dashboard(conn, channel=ch.code)
        if not data.get("available"):
            raise FormError(f"The {ch.code} store list has not been imported yet.")
        _selection(data, {"month": month.strftime("%Y-%m"), "distributor": distributor_id, "store": store_id}, ch)
        current = next((sale for sale in data.get("sales", [])
                        if int(sale["storeId"]) == store_id and sale["month"] == month.strftime("%b-%Y")), None)
        action = "edit" if current else "create"
        if not auth.can(user, ch.perm(f"sales.{action}")):
            raise PermissionError(f"{ch.code} store sales {action.title()} permission is required.")
        expected = int(current["revision"]) if current else 0
        if revision != expected:
            raise lfo_data.LFOConflictError()
        result = lfo_data.save_monthly_sale(conn, store_id, month, amount, revision, user["username"])
    return result, month, distributor_id, store_id


def _save_batch(form, user, ch=LFO):
    month = _month(form.get("period_month"))
    distributor_id = _id(form.get("distributor_id"), required=True)
    with closing(pgdata.connect()) as conn:
        data = lfo_data.fetch_dashboard(conn, channel=ch.code)
        if not data.get("available"):
            raise FormError(f"The {ch.code} store list has not been imported yet.")
        _selection(data, {"month": month.strftime("%Y-%m"), "distributor": distributor_id}, ch)
        stores = {int(s["id"]): s for s in data.get("stores", [])}
        sales = {int(s["storeId"]): s for s in data.get("sales", []) if s["month"] == month.strftime("%b-%Y")}
        fields = [key for key in form if key.startswith("amount_")]
        if len(fields) > 500:
            raise FormError("Save at most 500 stores at a time.")
        entries = []
        for key in fields:
            store_id = _id(key.removeprefix("amount_"), required=True)
            store = stores.get(store_id)
            if not store or int(store["distributorId"]) != distributor_id:
                raise FormError("Every monthly entry must belong to the selected distributor.")
            if hasattr(form, "getlist") and len(form.getlist(key)) != 1:
                raise FormError("A store can appear only once in the monthly form.")
            if not str(form.get(key, "")).strip():
                continue  # Blank preserves an existing amount and leaves a new month unreported.
            amount = _amount(form.get(key))
            current = sales.get(store_id)
            if current and amount == Decimal(str(current["amount"])):
                continue  # Unchanged imported amounts must retain their sheet provenance.
            action = "edit" if current else "create"
            if not auth.can(user, ch.perm(f"sales.{action}")):
                raise PermissionError(f"{ch.code} store sales {action.title()} permission is required.")
            revision = _revision(form.get(f"revision_{store_id}"))
            expected = int(current["revision"]) if current else 0
            if revision != expected:
                raise lfo_data.LFOConflictError()
            entries.append({"store_id": store_id, "amount": amount, "expected_revision": revision})
        if not entries:
            return {"saved": 0}, month, distributor_id
        result = lfo_data.save_monthly_sales(conn, distributor_id, month, entries, user["username"],
                    allow_create=auth.can(user, ch.perm("sales.create")), allow_edit=auth.can(user, ch.perm("sales.edit")),
                    channel=ch.code)
    return result, month, distributor_id


def _create_store(form, user, ch=LFO):
    distributor_id = _id(form.get("distributor_id"), required=True)
    month = _month(form.get("period_month") or date.today().strftime("%Y-%m"))
    name = form.get("name", "")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise FormError("Enter a branch or retailer name of 1 to 200 characters.")
    with closing(pgdata.connect()) as conn:
        # channel keeps each channel's form from adding a branch under the other
        # channel's distributor, even when that ID is typed into the request.
        result = lfo_data.create_store(conn, distributor_id, name, user["username"], channel=ch.code)
    return result, month, distributor_id


def _chooser(distributors, stores, sales, month, user, ch=LFO):
    url = lambda *a, **k: _url(*a, ch=ch, **k)
    body = f'<section class="entry-panel"><form class="lfo-month-filter" method="get" action="{esc(ch.base)}">'
    body += f'<div class="entry-field"><label for="lfo-month">Sales month</label><input type="month" id="lfo-month" name="month" value="{month:%Y-%m}" required></div><button class="entry-btn" type="submit">Change month</button></form></section>'
    body += '<h2 style="font-size:18px;margin-top:26px">Choose a distributor</h2><p class="lfo-note">Open a distributor to enter monthly sales for all its branches together.</p><div class="lfo-distributors">'
    order = {name: rank for rank, name in enumerate(ch.order)}
    for did, distributor in sorted(distributors.items(), key=lambda item: (order.get(item[1]["name"].casefold(), len(order)), item[1]["name"])):
        branches = [s for s in stores if int(s["distributorId"]) == did]
        count = sum(not s.get("isAggregate") for s in branches)
        reported = [sales[int(s["id"])] for s in branches if int(s["id"]) in sales]
        amount = sum((Decimal(str(s["amount"])) for s in reported), Decimal(0))
        body += f'<a class="lfo-distributor" href="{esc(url(month, did))}"><h2>{esc(distributor["name"])}</h2><p>{count} listed stores · {len(reported)} monthly entries</p><strong>{"₹" + number(amount) if reported else "Unreported"}</strong><p>{esc(month.strftime("%B %Y"))} secondary sales</p><span class="lfo-open">Open monthly sales →</span></a>'
    body += '</div>'
    if auth.can(user, ch.perm("stores.create")):
        body += f'<a class="entry-btn" href="{esc(_new_url(ch, month))}">Add branch / retailer</a>'
    return body


def _target_total_label(stores, targets):
    named = [int(store["id"]) for store in stores if not store.get("isAggregate")]
    known = [targets[ident] for ident in named if ident in targets]
    if not known:
        return "Not set"
    total = number(sum(known, Decimal(0)))
    return total if len(known) == len(named) else total + " (partial)"


def _branch_target_label(store, targets, currency=False):
    if store.get("isAggregate"):
        return "—"
    target = targets.get(int(store["id"]))
    if target is None:
        return "Not set"
    return ("₹" if currency else "") + number(target)


def _target_reconciliation(data, month, distributor_id, ch=LFO):
    fy = month.year if month.month >= 4 else month.year - 1
    plans = [plan for plan in data.get("branchTargetPlans", [])
             if int(plan["distributorId"]) == distributor_id and int(plan["fy"]) == fy]
    labels = {date(fy if m >= 4 else fy + 1, m, 1).strftime("%b-%Y") for m in (4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3)}
    targets = {target["month"]: Decimal(str(target["amount"])) for target in data.get("distributorTargets", [])
               if int(target["distributorId"]) == distributor_id and target["month"] in labels and target.get("amount") is not None}

    def total(values, expected):
        known = [Decimal(str(value)) for value in values if value is not None]
        amount = sum(known, Decimal(0))
        complete = bool(expected) and len(known) == expected
        label = "Not set" if not known else number(amount) + ("" if complete else " (partial)")
        return amount, complete, label

    rows = [(month.strftime("%B %Y"), total([targets.get(month.strftime("%b-%Y"))], 1),
             total([plan.get("monthlyTarget") for plan in plans], len(plans))),
            (f"FY {fy}–{str(fy + 1)[2:]}", total(list(targets.values()), 12),
             total([plan.get("yearlyTarget") for plan in plans], len(plans)))]
    body = f'<section class="entry-panel lfo-panel lfo-reconciliation"><div class="entry-panel-heading"><div><h2>Branch target comparison</h2><p>{esc(ch.reconcile_note)}</p></div></div><table class="entry-table"><thead><tr><th>Period</th><th class="entry-money">Distributor target (₹)</th><th class="entry-money">All listed branch targets (₹)</th><th class="entry-money">Difference (₹)</th></tr></thead><tbody>'
    for label, distributor, branches in rows:
        difference = number(distributor[0] - branches[0]) if distributor[1] and branches[1] else "Not set"
        body += f'<tr><td data-label="Period">{esc(label)}</td><td data-label="Distributor target (₹)" class="entry-money">{distributor[2]}</td><td data-label="All listed branch targets (₹)" class="entry-money">{branches[2]}</td><td data-label="Difference (₹)" class="entry-money">{difference}</td></tr>'
    body += '</tbody></table><p class="lfo-batch-note">The comparison includes every listed branch target, including names awaiting a store match. The sales register below totals only targets matched to its stores.</p>'
    unmatched = [plan for plan in plans if plan.get("storeId") is None]
    if unmatched:
        body += '<div class="lfo-unmatched"><strong>Branch targets awaiting a store match</strong><ul>'
        for plan in unmatched:
            monthly = "₹" + number(Decimal(str(plan["monthlyTarget"]))) if plan.get("monthlyTarget") is not None else "Not set"
            yearly = "₹" + number(Decimal(str(plan["yearlyTarget"]))) if plan.get("yearlyTarget") is not None else "Not set"
            refs = ' / '.join(str(plan.get(key) or '') for key in ("sourceMonthCell", "sourceYearCell")).strip(' /')
            body += f'<li>{esc(plan["sourceName"])} — {monthly} monthly · {yearly} yearly{f" ({esc(refs)})" if refs else ""}</li>'
        body += '</ul><p>These are target-plan rows only; they do not add stores or sales records.</p></div>'
    return body + '</section>'


def _batch_register(user, token, month, distributor, stores, sales, targets, values, conflict, ch=LFO):
    url = lambda *a, **k: _url(*a, ch=ch, **k)
    may_save = any(auth.can(user, ch.perm(f"sales.{action}")) for action in ("create", "edit"))
    did = int(distributor["id"])
    body = '<section class="entry-panel" id="monthly-entry"><div class="entry-panel-heading"><div><h2>Store sales register</h2>'
    body += f'<p>{esc(distributor["name"])} · {esc(month.strftime("%B %Y"))}</p></div>'
    if auth.can(user, ch.perm("stores.create")):
        body += f'<a class="entry-btn" href="{esc(_new_url(ch, month, did))}">Add branch / retailer</a>'
    body += '</div><p class="lfo-batch-note">Enter monthly sales for each branch, then save together. <strong>Blank leaves an existing amount unchanged</strong> and a new entry unreported. Enter <strong>0</strong> for a confirmed month with no sales. Only changed amounts are saved.</p>'
    body += (f'<form method="post" action="{esc(ch.base)}/save-month" id="lfo-monthly-batch"><input type="hidden" name="csrf_token" value="{esc(token)}"><input type="hidden" name="distributor_id" value="{did}"><input type="hidden" name="period_month" value="{month:%Y-%m}">'
             if may_save else '<div id="lfo-monthly-batch">')
    body += '<table class="entry-table lfo-batch-table"><thead><tr><th>Store / retailer</th><th class="entry-money">Monthly target (₹)</th><th class="entry-money">Secondary sales (₹)</th><th>Source</th><th>Entry</th></tr></thead><tbody>'
    for store in stores:
        sid = int(store["id"])
        sale = sales.get(sid)
        editable = auth.can(user, ch.perm("sales.edit" if sale else "sales.create"))
        old_amount = sale["amount"] if sale else ""
        amount = values.get(f"amount_{sid}", old_amount) if values is not None else old_amount
        revision = values.get(f"revision_{sid}", sale["revision"] if sale else 0) if values is not None else (sale["revision"] if sale else 0)
        source = "Dashboard" if sale and sale.get("origin") == "manual" else "Google Sheet" if sale else "—"
        body += f'<tr id="store-row-{sid}"><td data-label="Store / retailer">{esc(store["name"])}'
        if store.get("isAggregate"):
            body += '<br><span class="lfo-tag">Unallocated · not a store</span>'
        body += f'</td><td data-label="Monthly target (₹)" class="entry-money">{_branch_target_label(store, targets)}</td><td data-label="Secondary sales (₹)" class="entry-money" data-lfo-sales="{esc(old_amount)}">'
        if editable:
            body += f'<input type="number" name="amount_{sid}" aria-label="{esc(store["name"])} secondary sales" min="0" max="999999999999.99" step="0.01" placeholder="Not entered" value="{esc(amount)}"><input type="hidden" name="revision_{sid}" value="{esc(revision)}">'
        else:
            body += number(Decimal(str(old_amount))) if sale else '<span class="entry-empty-value">Not entered</span>'
        body += f'</td><td data-label="Source" class="lfo-source">{source}</td><td class="entry-actions"><a class="entry-edit" href="{esc(url(month, did, sid))}#monthly-entry">Open</a></td></tr>'
    count = sum(not s.get("isAggregate") for s in stores)
    saved = [sales[int(s["id"])] for s in stores if int(s["id"]) in sales]
    total = sum((Decimal(str(s["amount"])) for s in saved), Decimal(0))
    target_label = _target_total_label(stores, targets)
    scope_label = f'{count} stores' + (' + unallocated sales' if any(s.get("isAggregate") for s in stores) else '')
    body += f'<tr class="lfo-total"><td>{scope_label}</td><td data-label="Monthly target (₹)" class="entry-money">{target_label}</td><td data-label="Sales preview (₹)" class="entry-money" id="lfo-preview-total">{number(total) if saved else "Unreported"}</td><td></td><td></td></tr></tbody></table><div class="lfo-batch-actions">'
    if may_save:
        body += f'<button class="entry-btn primary" type="submit"{" disabled" if conflict else ""}>Save monthly sales</button>'
    body += f'<a class="entry-btn" href="{esc(url(month, did))}">Reload entries</a><small>Sales preview includes saved amounts retained by blank fields.</small></div>'
    body += ('</form>' if may_save else '</div>') + '</section>'
    return body + BATCH_SCRIPT


def _body(data, user, token, selected, values=None, error=None, saved=False, conflict=False, ch=LFO):
    url = lambda *a, **k: _url(*a, ch=ch, **k)
    month, distributor_id, store_id = selected
    month_label = month.strftime("%b-%Y")
    distributors = {int(d["id"]): d for d in data.get("distributors", [])}
    stores = [s for s in data.get("stores", []) if not distributor_id or int(s["distributorId"]) == distributor_id]
    stores.sort(key=lambda s: (distributors[int(s["distributorId"])]["name"].casefold(), bool(s.get("isAggregate")), s["name"].casefold()))
    sales = {int(s["storeId"]): s for s in data.get("sales", []) if s["month"] == month_label}
    targets = {int(t["storeId"]): Decimal(str(t["amount"])) for t in data.get("targets", [])
               if t["month"] == month_label and t.get("amount") is not None}
    count = sum(not s.get("isAggregate") for s in stores)
    entered = sum(int(s["id"]) in sales for s in stores)
    total = sum((Decimal(str(sales[int(s["id"])]["amount"])) for s in stores if int(s["id"]) in sales), Decimal(0))
    total_label = number(total) if entered else "Unreported"
    body = f'<div class="entries-page lfo-page"><div class="entry-breadcrumb">{esc(ch.code)} channel <span>/</span> Secondary sales</div><div class="entry-heading"><div><h1>{esc(ch.code)} store monthly sales</h1><p>Record monthly secondary sales by distributor and branch or retailer.</p></div></div>'
    if error:
        body += f'<p class="err" role="alert">{esc(error)}</p>'
    if saved:
        body += f'<p class="ok" role="status">Monthly store sales saved. The {esc(ch.code)} dashboard will reflect this update.</p>'
    body += f'''<div class="entry-stats"><div class="entry-stat"><div class="entry-stat-label">Listed stores</div><strong>{count}</strong><small>Named branches and retailers</small></div><div class="entry-stat"><div class="entry-stat-label">Monthly entries</div><strong>{entered} / {len(stores)}</strong><small>{esc(month.strftime('%B %Y'))} · includes unallocated sales</small></div><div class="entry-stat"><div class="entry-stat-label">Secondary sales (₹)</div><strong>{'₹' if entered else ''}{total_label}</strong><small>Selected distributor scope</small></div></div>'''
    if not distributor_id:
        return STYLE + CSS + body + _chooser(distributors, stores, sales, month, user, ch) + '</div>'
    if not store_id:
        dashboard_url = '/?' + urlencode({'page': ch.slug, 'view': 'secondary', 'distributor': distributor_id, 'month': month.strftime('%Y-%m')})
        body += f'<p class="lfo-note"><a href="{esc(url(month))}">← All distributors</a> · <a href="{esc(dashboard_url)}">{esc(ch.code)} Secondary dashboard</a></p>'
        body += f'<section class="entry-panel lfo-panel"><form class="lfo-month-filter" method="get" action="{esc(ch.base)}">'
        body += f'<div class="entry-field"><label for="lfo-month">Sales month</label><input type="month" id="lfo-month" name="month" value="{month:%Y-%m}" required></div><div class="entry-field"><label for="lfo-distributor">Distributor</label><select id="lfo-distributor" name="distributor">'
        for ident, distributor in distributors.items():
            body += f'<option value="{ident}"{" selected" if ident == distributor_id else ""}>{esc(distributor["name"])}</option>'
        body += '</select></div><button class="entry-btn" type="submit">Open month</button></form></section>'
        body += f'<p class="lfo-note">{esc(ch.target_note)}</p>'
        body += _target_reconciliation(data, month, distributor_id, ch)
        body += _batch_register(user, token, month, distributors[distributor_id], stores, sales, targets, values, conflict, ch)
        return STYLE + CSS + body + '</div>'
    body += f'<section class="entry-panel lfo-panel"><div class="entry-panel-heading"><div><h2>Choose month and store</h2><p>Filter the register or select a store to open its monthly entry.</p></div></div><form class="lfo-filters" method="get" action="{esc(ch.base)}">'
    body += f'<div class="entry-field"><label for="lfo-month">Sales month</label><input type="month" id="lfo-month" name="month" value="{month:%Y-%m}" required></div><div class="entry-field"><label for="lfo-distributor">Distributor</label><select id="lfo-distributor" name="distributor"><option value="">All distributors</option>'
    for ident, distributor in distributors.items():
        body += f'<option value="{ident}"{" selected" if ident == distributor_id else ""}>{esc(distributor["name"])}</option>'
    body += '</select></div><div class="entry-field lfo-store-field"><label for="lfo-store">Store / retailer</label><select id="lfo-store" name="store"><option value="">All stores</option>'
    for store in data.get("stores", []):
        ident, did = int(store["id"]), int(store["distributorId"])
        name = str(store["name"]) + (" · Unallocated" if store.get("isAggregate") else "")
        body += f'<option value="{ident}" data-distributor="{did}"{" selected" if ident == store_id else ""}>{esc(distributors[did]["name"])} — {esc(name)}</option>'
    body += '</select></div><button class="entry-btn primary" type="submit">Open selection</button></form></section>'
    if store_id:
        store = next(s for s in stores if int(s["id"]) == store_id)
        sale = sales.get(store_id)
        action = "edit" if sale else "create"
        can_save = auth.can(user, ch.perm(f"sales.{action}"))
        revision = values.get("revision", "") if values is not None else (sale["revision"] if sale else 0)
        amount = values.get("amount", "") if values is not None else (sale["amount"] if sale else "")
        body += f'<section class="entry-panel lfo-panel" id="monthly-entry"><div class="entry-panel-heading entry-form-heading"><div><h2>{"Edit" if sale else "Add"} monthly secondary sales</h2><p>{esc(distributors[distributor_id]["name"])} / {esc(store["name"])} · {esc(month.strftime("%B %Y"))}</p></div></div><div class="lfo-form">'
        if store.get("isAggregate"):
            body += '<p>This is unallocated distributor sales. It is not a named store and receives no store target.</p>'
        if can_save:
            body += f'''<form method="post" action="{esc(ch.base)}/save"><input type="hidden" name="csrf_token" value="{esc(token)}"><input type="hidden" name="distributor_id" value="{distributor_id}"><input type="hidden" name="store_id" value="{store_id}"><input type="hidden" name="period_month" value="{month:%Y-%m}"><input type="hidden" name="revision" value="{esc(revision)}"><div class="lfo-form-grid"><div class="entry-field"><label for="lfo-amount">Secondary sales (₹)<span class="entry-required">*</span></label><input id="lfo-amount" name="amount" type="number" min="0" max="999999999999.99" step="0.01" value="{esc(amount)}" required><small>Enter the total for this store and month. Use 0 for confirmed no sales; blank means not entered.</small></div><div><p>Branch monthly target<br><strong>{_branch_target_label(store, targets, currency=True)}</strong></p></div></div><div class="entry-form-buttons"><button class="entry-btn primary" type="submit"{' disabled' if conflict else ''}>Save monthly sales</button><a class="entry-btn" href="{esc(url(month, distributor_id, store_id))}">Reload entry</a></div></form>'''
            body += '<p class="lfo-source">Saved dashboard entries are preserved when this sheet is imported again.</p>'
        else:
            body += f'<p>Secondary sales: <strong>{"₹" + number(Decimal(str(sale["amount"]))) if sale else "Not entered"}</strong></p><p>{esc(ch.code)} store sales {action.title()} permission is required to save this entry.</p>'
        body += '</div></section>'
    body += f'<p class="lfo-note">{esc(ch.register_note)}</p>'
    body += _target_reconciliation(data, month, distributor_id, ch)
    body += '<section class="entry-panel"><div class="entry-panel-heading"><div><h2>Store sales register</h2><p>One entry per store and month. Amounts are secondary sales in INR.</p></div></div><table class="entry-table"><thead><tr><th>Distributor</th><th>Store / retailer</th><th class="entry-money">Monthly target (₹)</th><th class="entry-money">Secondary sales (₹)</th><th>Source</th><th>Action</th></tr></thead><tbody>'
    for store in stores:
        ident, did = int(store["id"]), int(store["distributorId"])
        sale = sales.get(ident)
        action = "edit" if sale else "create"
        link_label = ("Edit" if sale else "Add sales") if auth.can(user, ch.perm(f"sales.{action}")) else "Open"
        source = "Dashboard" if sale and sale.get("origin") == "manual" else "Google Sheet" if sale else "—"
        body += f'''<tr><td data-label="Distributor">{esc(distributors[did]['name'])}</td><td data-label="Store / retailer">{esc(store['name'])}{'<br><span class="lfo-tag">Unallocated · not a store</span>' if store.get('isAggregate') else ''}</td><td data-label="Monthly target (₹)" class="entry-money">{_branch_target_label(store, targets)}</td><td data-label="Secondary sales (₹)" class="entry-money">{number(Decimal(str(sale['amount']))) if sale else '<span class="entry-empty-value">Not entered</span>'}</td><td data-label="Source" class="lfo-source">{source}</td><td class="entry-actions"><a class="entry-edit" href="{esc(url(month, did, ident))}#monthly-entry">{link_label}</a></td></tr>'''
    if not stores:
        how = ("Add sub-channels and branches in the Target planner." if ch.sheet == "Target planner"
               else f"Import {ch.sheet} to load the distributor and store list.")
        body += f'<tr><td class="entry-empty-state" colspan="6">No {esc(ch.code)} stores are available. {esc(how)}</td></tr>'
    scope_label = f'{count} stores' + (' + unallocated sales' if any(s.get("isAggregate") for s in stores) else '')
    body += f'<tr class="lfo-total"><td>Total</td><td>{scope_label}</td><td data-label="Monthly target (₹)" class="entry-money">{_target_total_label(stores, targets)}</td><td data-label="Secondary sales (₹)" class="entry-money">{total_label}</td><td></td><td></td></tr></tbody></table></section></div>'
    return STYLE + CSS + body + FILTER_SCRIPT


CHANNEL_SCRIPT = """<script>(()=>{
 const channel=document.getElementById('lfo-channel'),parent=document.getElementById('lfo-parent');
 if(!channel||!parent)return;
 const filter=()=>{for(const group of parent.querySelectorAll('optgroup')){const hide=group.dataset.channel!==channel.value;group.hidden=hide;
  for(const option of group.children){option.hidden=hide;option.disabled=hide;if(hide&&option.selected)parent.value='';}}};
 channel.addEventListener('change',filter);filter();
})();</script>"""


def _new_store_body(loaded, token, values, error=None):
    """One form for every channel: loaded is [(channel, payload)] the user may add to."""
    slug = str(values.get("channel") or "")
    ch = next((c for c, _ in loaded if c.slug == slug), loaded[0][0])
    distributor_id = str(values.get("distributor_id") or "")
    month = _month(values.get("period_month") or date.today().strftime("%Y-%m"))
    body = ('<div class="entries-page lfo-page"><div class="entry-breadcrumb">Secondary sales <span>/</span> Stores</div>'
            f'<div class="entry-heading"><div><h1>{BRANCH_TITLE}</h1><p>Create a named branch or retailer under a distributor or group in any channel.</p></div></div>')
    if error:
        body += f'<p class="err" role="alert">{esc(error)}</p>'
    body += f'<section class="entry-panel"><div class="lfo-form"><form method="post" action="{BRANCH_CREATE}"><input type="hidden" name="csrf_token" value="{esc(token)}"><input type="hidden" name="period_month" value="{month:%Y-%m}"><div class="lfo-form-grid">'
    body += '<div class="entry-field"><label for="lfo-channel">Channel<span class="entry-required">*</span></label><select id="lfo-channel" name="channel" required>'
    for c, _ in loaded:
        body += f'<option value="{esc(c.slug)}"{" selected" if c is ch else ""}>{esc(c.code)}</option>'
    body += '</select></div><div class="entry-field"><label for="lfo-parent">Distributor / group<span class="entry-required">*</span></label><select id="lfo-parent" name="distributor_id" required><option value="">Choose a distributor / group</option>'
    for c, data in loaded:
        order = {name: rank for rank, name in enumerate(c.order)}
        distributors = sorted(data.get("distributors", []), key=lambda d: (order.get(str(d["name"]).casefold(), len(order)), str(d["name"])))
        body += f'<optgroup label="{esc(c.code)}" data-channel="{esc(c.slug)}">'
        for distributor in distributors:
            ident = str(distributor["id"])
            body += f'<option value="{esc(ident)}"{" selected" if c is ch and ident == distributor_id else ""}>{esc(distributor["name"])}</option>'
        body += '</optgroup>'
    body += f'</select></div><div class="entry-field lfo-wide"><label for="lfo-new-name">Branch / retailer name<span class="entry-required">*</span></label><input id="lfo-new-name" name="name" type="text" maxlength="200" value="{esc(values.get("name", ""))}" placeholder="Enter the store name" required></div></div>'
    sheets = " and ".join(c.sheet for c, _ in loaded if c.sheet != "Target planner")
    source = (f"matching named rows in the channel’s sheet ({sheets}), or the Target planner"
              if sheets else "the Target planner")
    body += f'<p>Branch targets come from {esc(source)}. Creating a branch does not redistribute existing branch or distributor targets. Its target remains Not set until one is set for it.</p><p>Creating a branch leaves its monthly sales unreported until you enter an amount.</p>'
    back = _url(month, distributor_id if distributor_id.isdigit() else None, ch=ch)
    body += f'<div class="entry-form-buttons"><button class="entry-btn primary" type="submit">{BRANCH_TITLE}</button><a class="entry-btn" href="{esc(back)}">Back to monthly sales</a></div></form></div></section></div>'
    return STYLE + CSS + body + CHANNEL_SCRIPT


def register(app, current_user, login_redirect, page, invalidate):
    """Mount the monthly-entry routes once per channel (/lfo/stores, /llfo/stores)
    and the one Add branch / retailer page shared by every channel."""
    create_branch = _register_branches(app, current_user, login_redirect, page, invalidate)
    for ch in CHANNELS:
        _register_channel(app, ch, current_user, login_redirect, page, invalidate, create_branch)


def _link_values(params, loaded, channels):
    """Form values from a link such as /stores/new?channel=llfo&distributor=4.

    Returns the Channel instead when the link names a channel the user may not
    add to. A link naming only a distributor opens that distributor's channel.
    """
    values = {"channel": params.get("channel", ""), "distributor_id": params.get("distributor", ""),
              "period_month": params.get("month", "")}
    ch = _channel(values["channel"])
    if values["channel"] and ch is None:
        raise FormError("Choose a valid channel.")
    if ch is not None and ch not in channels:
        return ch
    if values["distributor_id"]:
        if ch is None:
            ident = _id(values["distributor_id"])
            ch = next((c for c, data in loaded if any(int(d["id"]) == ident for d in data.get("distributors", []))), None)
            if ch is None:
                raise FormError("The selected distributor was not found.")
            values["channel"] = ch.slug
        data = dict(loaded).get(ch)
        if data is None:
            raise FormError(f"{ch.code} branches cannot be added until its distributor list is imported.")
        _selection(data, {"month": values["period_month"], "distributor": values["distributor_id"]}, ch)
    return values


def _register_branches(app, current_user, login_redirect, page, invalidate):
    def response(user, body, status=200):
        return HTMLResponse(page(BRANCH_TITLE, body, user), status_code=status,
                            headers={"Cache-Control": "no-store"})

    def error_response(user, message, status):
        return response(user, f'<h1>{BRANCH_TITLE}</h1><p class="err" role="alert">' + esc(message)
                        + f'</p><a href="{BRANCH_NEW}">Back to {BRANCH_TITLE}</a>', status)

    def denied(user, ch=None):
        what = f"{ch.code} branch / retailer" if ch else "Branch / retailer"
        return error_response(user, f"{what} creation permission is required.", 403)

    def render_new(request, user, values=None, error=None, status=200):
        channels = branch_channels(user)
        try:
            with closing(pgdata.connect()) as conn:
                loaded = [(ch, lfo_data.fetch_dashboard(conn, channel=ch.code)) for ch in channels]
            loaded = [(ch, data) for ch, data in loaded if data.get("available")]
            if not loaded:
                return error_response(user, "Import the distributor list before adding branches.", 503)
            # After a failed save the form comes back exactly as typed, with the
            # error above it. A link is checked before the form opens.
            if values is None:
                values = _link_values(request.query_params, loaded, channels)
                if isinstance(values, Channel):
                    return denied(user, values)
            return response(user, _new_store_body(loaded, _csrf(request), values, error), status)
        except FormError as exc:
            return error_response(user, str(exc), 400)
        except Exception:
            return error_response(user, DB_MESSAGE.replace("LFO store sales are", "Branches and retailers are"), 503)

    @app.get(BRANCH_NEW, response_class=HTMLResponse, name="new_branch")
    def new_branch(request: Request):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        if not branch_channels(user):
            return denied(user)
        return render_new(request, user)

    async def create(request, fixed=None):
        """Create a branch in the channel chosen on the form (or fixed by a per-channel URL)."""
        user = current_user(request)
        if not user:
            return login_redirect(request)
        if fixed is not None and not auth.can(user, fixed.perm("stores.create")):
            return denied(user, fixed)
        if not branch_channels(user):
            return denied(user)
        form = await request.form()
        if not _valid_csrf(request, form):
            return error_response(user, "Your form session expired. Reload the branch form and try again.", 403)
        ch = fixed or _channel(form.get("channel"))
        values = {**form, "channel": ch.slug if ch else ""}
        if ch is None:
            return render_new(request, user, values, "Choose a channel.", 400)
        if not auth.can(user, ch.perm("stores.create")):
            return denied(user, ch)
        try:
            result, month, distributor = await run_in_threadpool(_create_store, form, user, ch)
        except (FormError, lfo_data.LFOValidationError) as exc:
            return render_new(request, user, values, str(exc), 400)
        except Exception:
            return render_new(request, user, values, ch.db_message, 503)
        invalidate()
        return RedirectResponse(_url(month, distributor, ch=ch, created=1) + f'#store-row-{int(result["id"])}',
                                status_code=303, headers={"Cache-Control": "no-store"})

    @app.post(BRANCH_CREATE, name="create_branch")
    async def create_branch(request: Request):
        return await create(request)

    return create


def _register_channel(app, ch, current_user, login_redirect, page, invalidate, create_branch):
    title = f"{ch.code} store monthly sales"

    def response(user, body, status=200):
        return HTMLResponse(page(title, body, user), status_code=status,
                            headers={"Cache-Control": "no-store"})

    def error_response(user, message, status):
        return response(user, f'<h1>{esc(title)}</h1><p class="err" role="alert">' + esc(message)
                        + f'</p><a href="{esc(ch.base)}">Open store sales</a>', status)

    def url(*args, **kwargs):
        return _url(*args, ch=ch, **kwargs)

    def render(request, user, values=None, error=None, status=200):
        try:
            data = _load(ch)
            params = ({"month": values.get("period_month"), "distributor": values.get("distributor_id"), "store": values.get("store_id")} if values is not None else request.query_params)
            selected = _selection(data, params, ch)
            if not data.get("available"):
                return error_response(user, f"The {ch.code} store list has not been imported yet.", 503)
            notice = ('<p class="ok" role="status">No changed amounts to save. Existing sales remain unchanged.</p>'
                      if values is None and request.query_params.get("unchanged") == "1" else
                      '<p class="ok" role="status">Branch added. Enter its monthly sales below.</p>'
                      if values is None and request.query_params.get("created") == "1" else '')
            return response(user, notice + _body(data, user, _csrf(request), selected, values, error,
                                       saved=values is None and request.query_params.get("saved") == "1",
                                       conflict=status == 409, ch=ch), status)
        except FormError as exc:
            return error_response(user, str(exc), 400)
        except Exception:
            return error_response(user, ch.db_message, 503)

    def may_save(user):
        return any(auth.can(user, ch.perm(f"sales.{action}")) for action in ("create", "edit"))

    @app.get(ch.base, response_class=HTMLResponse, name=f"{ch.slug}_stores")
    def stores(request: Request):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        if not may_view(user, ch):
            return error_response(user, f"{ch.code} store sales View permission is required.", 403)
        return render(request, user)

    # Older links and bookmarks open the shared page with this channel chosen.
    @app.get(ch.base + "/new", response_class=HTMLResponse, name=f"{ch.slug}_new_store")
    def new_store(request: Request):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        if not auth.can(user, ch.perm("stores.create")):
            return error_response(user, f"{ch.code} branch / retailer creation permission is required.", 403)
        params = request.query_params
        return RedirectResponse(_new_url(ch, params.get("month"), params.get("distributor")), status_code=303,
                                headers={"Cache-Control": "no-store"})

    # A branch form opened before the shared page existed still saves here.
    @app.post(ch.base + "/create", name=f"{ch.slug}_create_store")
    async def create_store(request: Request):
        return await create_branch(request, ch)

    @app.post(ch.base + "/save-month", name=f"{ch.slug}_save_month")
    async def save_month(request: Request):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        if not may_save(user):
            return error_response(user, f"{ch.code} store sales Create or Edit permission is required.", 403)
        form = await request.form()
        if not _valid_csrf(request, form):
            return error_response(user, "Your form session expired. Reload monthly sales and try again.", 403)
        try:
            result, month, distributor = await run_in_threadpool(_save_batch, form, user, ch)
        except PermissionError as exc:
            return error_response(user, str(exc), 403)
        except lfo_data.LFOConflictError:
            return render(request, user, form, "A monthly entry changed since this form was opened. Nothing was saved. Your amounts are shown below; reload and review the latest entries before saving.", 409)
        except (FormError, lfo_data.LFOValidationError) as exc:
            return render(request, user, form, str(exc), 400)
        except Exception:
            return render(request, user, form, ch.db_message, 503)
        changed = bool(result.get("saved"))
        if changed:
            invalidate()
        return RedirectResponse(url(month, distributor, **({"saved": 1} if changed else {"unchanged": 1})) + "#monthly-entry",
                                status_code=303, headers={"Cache-Control": "no-store"})

    @app.post(ch.base + "/save", name=f"{ch.slug}_save")
    async def save(request: Request):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        if not may_save(user):
            return error_response(user, f"{ch.code} store sales Create or Edit permission is required.", 403)
        form = await request.form()
        if not _valid_csrf(request, form):
            return error_response(user, "Your form session expired. Reload the store sales page and try again.", 403)
        try:
            _, month, distributor, store = await run_in_threadpool(_save, form, user, ch)
        except PermissionError as exc:
            return error_response(user, str(exc), 403)
        except lfo_data.LFOConflictError:
            return render(request, user, form, "This month's entry changed since it was opened. Your amount is shown below. Reload the entry and review the latest amount before saving.", 409)
        except (FormError, lfo_data.LFOValidationError) as exc:
            return render(request, user, form, str(exc), 400)
        except Exception:
            return render(request, user, form, ch.db_message, 503)
        invalidate()
        return RedirectResponse(url(month, distributor, store, saved=1) + "#monthly-entry", status_code=303,
                                headers={"Cache-Control": "no-store"})
