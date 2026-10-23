"""Administrator status page for the Direct Sales importer."""
import re
import secrets
from datetime import datetime
from html import escape

from fastapi import Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool


def esc(value):
    return escape(str(value), quote=True)


def time_label(value):
    if not value:
        return "Not yet"
    return datetime.fromisoformat(value).astimezone().strftime("%d %b %Y, %H:%M:%S %Z")


FIELD_LABELS = {"source_key": "S.NO", "invoice_no": "invoice number", "invoice_date": "invoice date",
                "customer": "customer", "channel": "channel", "brand": "brand", "state": "state",
                "city": "city", "person": "executive", "pi_number": "PI number",
                "sub_channel": "sub-channel", "order_type": "order type", "primary_qty": "primary quantity",
                "primary_taxable": "primary taxable value", "primary_with_tax": "primary value with tax",
                "secondary_qty": "secondary quantity", "secondary_value": "secondary value",
                "tertiary_nos": "tertiary quantity", "tertiary_value": "tertiary value",
                "credit_days": "credit days", "stock_supplied_on": "stock supplied date",
                "payment_received_on": "payment received date", "courier": "courier",
                "courier_docket_no": "courier docket number", "remarks": "remarks"}


def issue_label(issue):
    """Describe known importer diagnostics without displaying raw source values."""
    if not isinstance(issue, dict):
        return str(issue)  # Compatibility with older worker status messages.
    locations = issue.get("sheet_rows") or [issue.get("sheet_row")]
    locations = [str(row) for row in locations if type(row) is int and row > 0]
    context = ("Worksheet row" + ("s " if len(locations) > 1 else " ") + ", ".join(locations)) if locations else ""
    key = str(issue.get("source_key", ""))
    if re.fullmatch(r"[1-9][0-9]{0,14}", key):
        context += (" · " if context else "") + "S.NO " + key
    code = issue.get("code")
    messages = {
        "duplicate_source_key": "Duplicate S.NO. Give each billed row a unique S.NO and keep existing linked S.NO values unchanged.",
        "missing_or_invalid_source_key": "S.NO must be a positive whole number.",
        "serial_only_placeholder": "Empty pre-numbered row ignored; it contains only S.NO.",
        "linked_source_row_blank": "A previously linked invoice now contains only S.NO. Its saved sales and link were preserved; restore the invoice details or review the entry.",
        "missing_direct_sales_sheet": "The Direct Sales Report tab is missing.",
        "empty_source": "No billed rows were found. Previous saved sales were preserved.",
        "invalid_number": "A sales quantity or amount is invalid. Use a non-negative number within the supported range.",
        "invalid_date": "A date is invalid. Check the invoice, dispatch and payment dates.",
        "invalid_credit_days": "Credit days must be a whole number between 0 and 32767.",
        "payment_before_dispatch": "The payment received date is before the stock supplied date.",
        "invalid_order_type": "The order type is not recognized.",
        "invalid_financial_year": "The invoice financial year is outside the supported range.",
        "ambiguous_legacy_match": "More than one existing sales entry may match this row. Review the existing entry before retrying.",
        "existing_invoice_requires_review": "This invoice already exists under a different or protected entry. Review its S.NO and existing link.",
        "conflicting_master_value": "A linked master record has conflicting values. Review the source and saved record.",
        "unmatched_legacy_rows": "Some existing imported entries could not be linked. Check for missing rows or changed invoice identifiers.",
        "database_row_deleted": "The linked sales entry was deleted in the dashboard and was not restored.",
        "concurrent_edit": "The same field changed in the sheet and the dashboard. The saved entry was preserved for review.",
    }
    if code in ("missing_column", "duplicate_column"):
        field = FIELD_LABELS.get(issue.get("field"), "required field")
        message = ("Missing column: " if code == "missing_column" else "Duplicate columns for: ") + field + "."
    elif isinstance(code, str) and code.startswith("missing_") and code[8:] in FIELD_LABELS:
        message = "Missing " + FIELD_LABELS[code[8:]] + ". Complete this invoice row before retrying."
    else:
        message = messages.get(code, "This row requires review before it can be imported.")
    return (context + ": " if context else "") + message


def issue_list(issues, limit=50):
    return ''.join(f'<li>{esc(issue_label(issue))}</li>' for issue in issues[:limit])


def footer(status):
    if status["state"] == "disabled":
        return ""
    state = status["state"]
    if state == "error":
        message = "Direct Sales sync needs attention"
    elif state == "warning":
        message = "Direct Sales synced · " + ("conflicts need review" if status.get("counters", {}).get("conflicts") else "needs attention")
    elif status.get("last_success"):
        message = "Direct Sales synced " + time_label(status["last_success"])
    else:
        message = "Direct Sales sync starting"
    return f'<span class="sales-sync-info" style="display:block;margin-top:4px;font-size:10px;line-height:1.4">{esc(message)}</span>'


def register(app, current_user, login_redirect, page, manager, invalidate):
    def allowed(request):
        user = current_user(request)
        if not user:
            return None, login_redirect(request)
        if not user["is_admin"]:
            return None, HTMLResponse(page("Access denied", '<div class="card">Only an administrator can manage the sales sync.</div>', user), status_code=403)
        return user, None

    def render(request, user, error=None):
        status = manager.snapshot()
        token = request.session.setdefault("sales_sync_csrf", secrets.token_urlsafe(32))
        labels = {"source_rows": "Valid billed rows", "source_total_rows": "Nonempty source rows",
                  "invoice_rows": "Rows with an invoice number", "ignored_blank_rows": "Empty pre-numbered rows ignored",
                  "attached": "Existing entries linked", "inserted": "New entries added",
                  "updated": "Entries updated", "unchanged": "Unchanged", "preserved_local": "Local edits preserved",
                  "bootstrap_preserved": "Existing corrections preserved", "conflicts": "Conflicts", "missing_source": "Linked rows missing from source"}
        rows = ''.join(f'<tr><th>{label}</th><td>{int(status["counters"].get(key, 0))}</td></tr>' for key, label in labels.items())
        current_issues = status.get("issues", [])
        attempt = ''
        if status["state"] == "error":
            diagnostics = status.get("attempt_diagnostics", {})
            attempt_labels = {"source_rows": "Nonempty source rows checked", "invoice_rows": "Rows with an invoice number",
                              "ignored_blank_rows": "Empty pre-numbered rows ignored"}
            attempt_rows = ''.join(f'<tr><th>{label}</th><td>{int(diagnostics[key])}</td></tr>'
                                   for key, label in attempt_labels.items() if key in diagnostics)
            failure_issues = issue_list(current_issues)
            ignored = issue_list([{"code": "serial_only_placeholder", **row} for row in diagnostics.get("ignored_rows", [])])
            if attempt_rows or failure_issues or ignored:
                attempt = ('<div class="card"><h2>Latest failed Direct Sales attempt</h2>'
                           '<p>These details describe the latest failed attempt. Counters below describe the last completed sync.</p>'
                           + (f'<table>{attempt_rows}</table>' if attempt_rows else '')
                           + (f'<h3>Issues to correct</h3><ul>{failure_issues}</ul>' if failure_issues else '')
                           + (f'<h3>Ignored empty rows</h3><ul>{ignored}</ul>' if ignored else '') + '</div>')
            current_issues = []
        notes = issue_list([issue for issue in current_issues if isinstance(issue, dict) and issue.get("code") == "serial_only_placeholder"])
        issues = issue_list([issue for issue in current_issues if not isinstance(issue, dict) or issue.get("code") != "serial_only_placeholder"])
        completed = (f'<table>{rows}</table>' if status.get("last_success") else '<p>No Direct Sales sync has completed yet.</p>')
        completed += f'<h3>Issues to review</h3><ul>{issues}</ul>' if issues else ''
        completed += f'<h3>Notes from this import</h3><ul>{notes}</ul>' if notes else ''
        message = error or status.get("error")
        notice = f'<div class="err" role="alert">{esc(message)}</div>' if message else ""
        lfo = status.get("lfo", {"state": "disabled"})
        lfo_notice = ''
        if lfo.get("state") != "disabled":
            counts = lfo.get("counters", {})
            lfo_notice = (f'<div class="card"><h2>LFO Distributed sync: {esc(lfo["state"])}</h2>'
                          f'<p>Last successful sync: <b>{esc(time_label(lfo.get("last_success")))}</b></p>'
                          f'<p>Monthly sales added: {int(counts.get("inserted", 0))} · Updated: {int(counts.get("updated", 0))}</p>'
                          '<p>Imports distributor and store records and monthly secondary sales. Dashboard edits are retained when the sheet changes.</p>'
                          '<p>Secondary targets come from each distributor’s monthly targets in LFO Distributed. Distributor yearly targets sum those months. Branch targets keep the exact monthly and yearly amounts listed for each branch, and differences from distributor totals are shown. Primary targets cover DT + TT and remain separate.</p>'
                          + (f'<p class="err">{esc(lfo["error"])}</p>' if lfo.get("error") else '')
                          + '<p><a href="/lfo/stores">Open LFO monthly store sales</a></p></div>')
        llfo = status.get("llfo", {"state": "disabled"})
        llfo_notice = ''
        if llfo.get("state") != "disabled":
            counts = llfo.get("counters", {})
            llfo_warnings = ''.join(f'<li>{esc(w)}</li>' for w in llfo.get("warnings", [])[:12])
            llfo_notice = (f'<div class="card"><h2>L-LFO Distributed sync: {esc(llfo["state"])}</h2>'
                           f'<p>Last successful sync: <b>{esc(time_label(llfo.get("last_success")))}</b></p>'
                           f'<p>Monthly sales added: {int(counts.get("inserted", 0))} · Updated: {int(counts.get("updated", 0))}</p>'
                           '<p>Imports each Group as a distributor and each Location as a branch / retailer, with monthly secondary sales from the month columns.</p>'
                           '<p>Branch targets are each Location’s Secondary Target per month. A group’s target is the sum of its branches’ targets, because the sheet has no separate group figure. Primary targets remain separate.</p>'
                           + (f'<p class="err">{esc(llfo["error"])}</p>' if llfo.get("error") else '')
                           + (f'<h3>Notes from the last import</h3><ul>{llfo_warnings}</ul>' if llfo_warnings else '')
                           + '<p><a href="/llfo/stores">Open L-LFO monthly store sales</a></p></div>')
        tabs = ['<b>Direct Sales Report</b>'] + (['<b>LFO Distributed</b>'] if lfo.get("state") != "disabled" else []) \
             + (['<b>L-LFO Distributed</b>'] if llfo.get("state") != "disabled" else [])
        source_label = ', '.join(tabs[:-1]) + ' and ' + tabs[-1] if len(tabs) > 1 else tabs[0]
        body = (f'<h1>Google Sheets → PostgreSQL</h1><p>Imports {source_label}. Dashboards continue to read PostgreSQL.</p>{notice}'
                f'<div class="card"><h2>Direct Sales sync: {esc(status["state"])}</h2>'
                f'<p>Checks every {status["interval_seconds"]} seconds while the dashboard server is running.</p>'
                f'<p>Last successful sync: <b>{esc(time_label(status["last_success"]))}</b><br>Last attempt: {esc(time_label(status["last_attempt"]))}</p>'
                f'<form method="post" action="/admin/sales-sync"><input type="hidden" name="csrf" value="{esc(token)}"><button>Sync now</button></form></div>'
                f'{attempt}<div class="card"><h2>Last completed sync</h2>{completed}</div>'
                f'{lfo_notice}{llfo_notice}'
                '<div class="card"><h2>How to update sales</h2><p>Add or edit rows in Google Sheets. Give each new row a unique S.NO and keep existing S.NO values unchanged, including when sorting rows.</p>'
                '<p>A pre-numbered row is ignored only when every other worksheet cell is empty. Any invoice details, amounts or other content make it a row that must pass validation, including having an invoice number. Duplicate S.NO values on billed rows stop the Direct Sales import.</p>'
                '<p>Sheet changes update matching PostgreSQL entries. Entries created in the dashboard are kept separate. Local corrections are retained; changes to the same field in both places require review.</p>'
                '<p>Removing a sheet row does not delete its PostgreSQL entry. Use the authorized sales-entry Delete action for intentional deletion. Primary channel targets remain managed in Targets. Secondary distributor and branch targets come from LFO Distributed; changes to Primary targets do not change Secondary targets.</p></div>')
        return HTMLResponse(page("Direct Sales sync", body, user), status_code=403 if error else 200, headers={"Cache-Control": "no-store"})

    @app.get("/admin/sales-sync")
    def status_page(request: Request):
        user, response = allowed(request)
        return response if response is not None else render(request, user)

    @app.post("/admin/sales-sync")
    async def sync_now(request: Request):
        user, response = allowed(request)
        if response is not None:
            return response
        form = await request.form()
        expected = request.session.get("sales_sync_csrf", "")
        supplied = str(form.get("csrf", ""))
        if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
            return render(request, user, "Your form session expired. Reload this page and try again.")
        await run_in_threadpool(manager.sync_once, invalidate)
        return render(request, user)
