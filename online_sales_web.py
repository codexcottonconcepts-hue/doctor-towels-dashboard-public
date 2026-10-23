"""Administrative controls for the independent online-sales importer."""
import secrets
from datetime import datetime, timedelta, timezone
from html import escape

from fastapi import Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool
import online_sales_data


def esc(value):
    return escape(str(value), quote=True)


def time_label(value):
    if not value:
        return 'Not yet'
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(
            timezone(timedelta(hours=5, minutes=30))).strftime('%d %b %Y, %H:%M:%S IST')
    except (ValueError, TypeError):
        return 'Not yet'


def register(app, current_user, login_redirect, page, manager, invalidate):
    def allowed(request):
        user = current_user(request)
        if not user:
            return None, login_redirect(request)
        if not user['is_admin']:
            return None, HTMLResponse(page('Access denied', '<div class="card">Only an administrator can manage online sales sync.</div>', user),
                                      status_code=403, headers={'Cache-Control': 'no-store'})
        return user, None

    def render(request, user, error=None):
        status = manager.snapshot()
        token = request.session.setdefault('online_sales_csrf', secrets.token_urlsafe(32))
        message = error or status.get('error')
        notice = f'<div class="err" role="alert">{esc(message)}</div>' if message else ''
        counts = status.get('counters', {})
        rows = ''.join(f'<tr><th>{esc(str(key).replace("_", " ").capitalize())}</th><td>{esc(value)}</td></tr>'
                       for key, value in counts.items())
        warnings = ''.join(f'<li>{esc(value)}</li>' for value in status.get('warnings', [])[:20])
        source_link = (f'<p><a href="https://docs.google.com/spreadsheets/d/{esc(online_sales_data.SOURCE_ID)}/edit" '
                       'target="_blank" rel="noopener noreferrer">Open the configured online sales sheet</a></p>'
                       if online_sales_data.SOURCE_ID else
                       '<p>Set ONLINE_SALES_SOURCE_ID to your own workbook ID to configure this source.</p>')
        body = (f'<h1>Online sales sync</h1>{notice}'
                f'<div class="card"><h2>Online sales: {esc(status.get("state", "idle"))}</h2>'
                f'<p>Checks the online sales sheet every {int(status.get("interval_seconds", 120))} seconds.</p>'
                f'<p>Last successful sync: <b>{esc(time_label(status.get("last_success")))}</b><br>'
                f'Last attempt: {esc(time_label(status.get("last_attempt")))}</p>'
                '<form method="post" action="/admin/online-sales-sync">'
                f'<input type="hidden" name="csrf" value="{esc(token)}"><button>Sync online sales now</button></form></div>'
                '<div class="card"><h2>Sales source</h2>'
                + source_link +
                '<p>Daily Sales supplies daily figures and Monday–Sunday weekly totals. Monthly Sales supplies entered monthly amounts. '
                'When a monthly cell is blank, the dashboard shows the sum of available daily sales for that platform and month.</p>'
                '<p>Blank inputs remain unreported. Daily and monthly figures are not added together.</p>'
                '<p>This importer runs independently of Direct Sales and does not change primary sales or targets.</p></div>'
                + (f'<div class="card"><h2>Last completed import</h2><table>{rows}</table></div>' if rows else '')
                + (f'<div class="card"><h2>Source notes</h2><ul>{warnings}</ul></div>' if warnings else '')
                + '<p><a href="/?page=online">Open Online sales</a> · <a href="/admin/sales-sync">Open Direct Sales sync</a></p>')
        return HTMLResponse(page('Online sales sync', body, user), status_code=403 if error else 200,
                            headers={'Cache-Control': 'no-store'})

    @app.get('/admin/online-sales-sync')
    def status_page(request: Request):
        user, response = allowed(request)
        return response if response is not None else render(request, user)

    @app.post('/admin/online-sales-sync')
    async def sync_now(request: Request):
        user, response = allowed(request)
        if response is not None:
            return response
        form = await request.form()
        expected = request.session.get('online_sales_csrf', '')
        supplied = str(form.get('csrf', ''))
        if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
            return render(request, user, 'Your form session expired. Reload this page and try again.')
        await run_in_threadpool(manager.sync_once, invalidate)
        return render(request, user)
