"""Read-only primary-sales breakdowns for dashboard achievement links.

Register with ``register(app, current_user, login_redirect, page)``. Repeated
channel/month/executive/brand/distribution/state/city parameters are unions; distinct
dimensions intersect. Only the filtered invoice table is paginated.
"""

import csv
import io
import json
import re
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

import auth
import pgdata
import dashboard_policy
from entry_dates import format_date
from entry_styles import STYLE
from entry_web import EXPORT_FIELDS, csv_cell, number, section_access


FILTERS = ('channel', 'month', 'executive', 'brand', 'distribution', 'state', 'city', 'customer')
SCOPE_FILTERS = ('scope_city', 'scope_state')
CHANNELS = {re.sub(r'\s+', '', name).lower(): code for code, display in pgdata.REGION_OF.items()
            for name in (code, display)}
BRANDS = {'dt': 'Doctor Towel', 'doctortowel': 'Doctor Towel', 'doctortowels': 'Doctor Towel',
          'tt': 'Thirsty Towel', 'thirstytowel': 'Thirsty Towel', 'thirstytowels': 'Thirsty Towel'}
RETURN_PAGES = {'home', 'lfo', 'llfo', 'drs', 'drn', 'saloon', 'balance', 'map', 'state', 'store'}
DETAIL_PAGES = {'home', 'lfo', 'llfo', 'drs', 'drn', 'saloon'}
VALUE_BANDS = {
    '0-10000': (0, 10000, '₹0 to below ₹10,000'),
    '10000-25000': (10000, 25000, '₹10,000 to below ₹25,000'),
    '25000-50000': (25000, 50000, '₹25,000 to below ₹50,000'),
    '50000-100000': (50000, 100000, '₹50,000 to below ₹1,00,000'),
    '100000-plus': (100000, None, '₹1,00,000 and above'),
}
GROUPS = (('month', 'Month', 'period_month'), ('executive', 'Executive', 'sales_person'),
          ('customer', 'Customer', 'customer'), ('channel', 'Channel', 'channel'),
          ('state', 'State', 'state'), ('district', 'District', 'district'), ('city', 'City / location', 'city'),
          ('brand', 'Brand', 'brand'), ('distribution', 'Sub-channel', 'sub_channel'))
LABELS = {'entry_id': 'Entry', 'invoice_date': 'Invoice date', 'invoice_no': 'Invoice number',
          'line_no': 'Line', 'pi_number': 'PI number', 'customer': 'Customer',
          'channel': 'Channel', 'sub_channel': 'Sub-channel', 'sales_person': 'Executive',
          'brand': 'Brand', 'order_type': 'Order type', 'state': 'State', 'district': 'District',
          'city': 'City / location', 'primary_qty': 'Primary units',
          'primary_taxable': 'Taxable sales (₹)', 'primary_with_tax': 'Sales incl. GST (₹)',
          'secondary_qty': 'Secondary units', 'secondary_value': 'Secondary sales (₹)',
          'tertiary_nos': 'Tertiary units', 'tertiary_value': 'Tertiary sales (₹)',
          'credit_days': 'Credit days', 'payment_received_on': 'Payment received date',
          'stock_supplied_on': 'Stock supplied date', 'courier': 'Courier type',
          'weight_kg': 'Weight (kg)', 'transport_service': 'Transport service',
          'courier_docket_no': 'Courier number', 'remarks': 'Remarks'}
NUMBERS = {'line_no', 'entry_id', 'primary_qty', 'primary_taxable', 'primary_with_tax',
           'secondary_qty', 'secondary_value', 'tertiary_nos', 'tertiary_value', 'credit_days', 'weight_kg'}
CSV_ONLY_COLUMNS = {'entry_id', 'line_no', 'credit_days', 'payment_received_on'}
CSS = """<style>
.sales-breakdown .entry-stats{grid-template-columns:repeat(4,minmax(0,1fr))}
.sales-breakdown .sb-context{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 22px}
.sales-breakdown .sb-chip{padding:7px 10px;background:#fff;border:1px solid #e6e8ed;border-radius:7px;font-size:12px}
.sales-breakdown .sb-jumps{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin:0 0 22px;font-size:12px}
.sales-breakdown .sb-jumps a{padding:6px 10px;border-radius:6px;background:#fff;border:1px solid #e6e8ed;text-decoration:none;color:#444b59}
.sales-breakdown .sb-jumps a:hover{background:#f4f5f7}.sales-breakdown .sb-jumps a:focus-visible{outline:3px solid #c8102e55;outline-offset:2px}
.sales-breakdown section[id]{scroll-margin-top:20px}
.sales-breakdown .sb-groups{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin-bottom:24px;align-items:start}
.sales-breakdown .sb-customer-focus{grid-column:1 / -1}
.sales-breakdown .sb-group{max-height:440px;overflow:auto}.sales-breakdown table{border-collapse:collapse;width:100%;margin:0;font-size:12px}
.sales-breakdown th,.sales-breakdown td{padding:10px 12px;border-bottom:1px solid #edf0f4;text-align:left;vertical-align:top}
.sales-breakdown th{background:#f8f9fb;color:#626b7b;position:sticky;top:0;z-index:1;font-size:11px}
.sales-breakdown .num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.sales-breakdown .sb-label{overflow-wrap:anywhere}.sales-breakdown .sb-invoices{overflow:auto;max-height:680px}
.sales-breakdown .sb-invoices table{min-width:max-content}.sales-breakdown .sb-invoices td{max-width:320px;white-space:normal;overflow-wrap:anywhere}
.sales-breakdown .sb-note{color:#6c7181;font-size:12px;line-height:1.55}.sales-breakdown .entry-panel-heading{padding:17px 18px}
.sales-breakdown .sb-info{margin:0 0 18px;color:#6c7181;font-size:12px;line-height:1.55}
.sales-breakdown .sb-info summary{width:fit-content;cursor:pointer}.sales-breakdown .sb-info summary:focus-visible{outline:3px solid #c8102e55;outline-offset:3px}
.sales-breakdown .sb-info p{margin:8px 0 0}
@media(max-width:800px){.sales-breakdown .sb-groups{grid-template-columns:1fr}.sales-breakdown .entry-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.sales-breakdown .entry-heading{align-items:flex-start;flex-direction:column}}
</style>"""


class InvalidFilters(ValueError):
    pass


def esc(value):
    return escape('' if value is None else str(value), quote=True)


def _values(params, key, *, preserve_whitespace=False):
    values = params.getlist(key) if hasattr(params, 'getlist') else params.get(key, [])
    if isinstance(values, str):
        values = [values]
    if len(values) > 100:
        raise InvalidFilters(f'Too many {key} selections.')
    cleaned = []
    for value in values:
        if not isinstance(value, str) or len(value) > 200 or any(ord(c) < 32 for c in value):
            raise InvalidFilters(f'Invalid {key} selection.')
        if not preserve_whitespace:
            value = value.strip()
        if value.strip() and value not in cleaned:
            cleaned.append(value)
    return [] if 'All' in cleaned else cleaned


def parse_filters(params):
    years = _values(params, 'fy')
    if len(years) > 1:
        raise InvalidFilters('Choose one financial year.')
    today = date.today()
    raw_fy = years[0] if years else str(today.year - (today.month < 4))
    if not re.fullmatch(r'20\d{2}', raw_fy):
        raise InvalidFilters('Financial year must be its starting year, for example 2026.')
    result = {'fy': int(raw_fy), **{
        key: _values(params, key, preserve_whitespace=key in ('city', 'customer', *SCOPE_FILTERS))
        for key in (*FILTERS, *SCOPE_FILTERS)}}
    # Keep each executive paired with their assigned location. Separate lists
    # of executives and places would admit another executive's unassigned sales.
    assignments = params.getlist('scope_assignment') if hasattr(params, 'getlist') else params.get('scope_assignment', [])
    if isinstance(assignments, str):
        assignments = [assignments]
    if not isinstance(assignments, (list, tuple)) or len(assignments) > 100:
        raise InvalidFilters('Too many executive assignments.')
    result['scope_assignment'] = []
    for encoded in assignments:
        try:
            if not isinstance(encoded, str) or len(encoded) > 1500:
                raise ValueError()
            assignment = json.loads(encoded)
            if (not isinstance(assignment, list) or len(assignment) != 3
                    or any(not isinstance(value, str) or len(value) > 200
                           or any(ord(c) < 32 for c in value) for value in assignment)):
                raise ValueError()
            person, kind, place = assignment
            if (not person.strip() or kind not in ('city', 'state', 'channel')
                    or (kind == 'channel' and place != '')
                    or (kind != 'channel' and not place.strip())):
                raise ValueError()
        except (ValueError, TypeError):
            raise InvalidFilters('Invalid executive assignment.') from None
        if assignment not in result['scope_assignment']:
            result['scope_assignment'].append(assignment)
    for key, aliases in (('channel', CHANNELS), ('brand', BRANDS)):
        normalized = []
        for value in result[key]:
            canonical = aliases.get(re.sub(r'\s+', '', value).lower())
            if canonical is None:
                raise InvalidFilters(f'Unknown {key}: {value}.')
            if canonical not in normalized:
                normalized.append(canonical)
        result[key] = normalized
    months = set()
    for value in result['month']:
        parsed = None
        for pattern in ('%b-%Y', '%Y-%m', '%Y-%m-%d'):
            try:
                parsed = datetime.strptime(value, pattern).date()
                break
            except ValueError:
                pass
        if parsed is None or parsed.day != 1:
            raise InvalidFilters('Use a calendar month such as Sep-2026 or 2026-09.')
        if parsed.year - (parsed.month < 4) != result['fy']:
            raise InvalidFilters('Selected months must belong to the selected financial year.')
        months.add(parsed)
    result['month'] = sorted(months)
    for key, default in (('page', 1), ('page_size', 25)):
        raw = str(params.get(key, default))
        if not raw.isascii() or not raw.isdigit() or len(raw) > 8 or int(raw) < 1:
            raise InvalidFilters(f'Invalid {key.replace("_", " ")}.')
        result[key] = int(raw)
    if result['page_size'] not in (25, 50):
        raise InvalidFilters('Page size must be 25 or 50.')
    result['return_page'] = params.get('return_page') if params.get('return_page') in RETURN_PAGES else 'home'
    result['return_view'] = params.get('return_view') if params.get('return_view') in ('overview', 'detail') else 'overview'
    bands = params.getlist('value_band') if hasattr(params, 'getlist') else params.get('value_band', [])
    if isinstance(bands, str):
        bands = [bands]
    if not isinstance(bands, (list, tuple)) or len(bands) > 1:
        raise InvalidFilters('Choose one order value band.')
    band = bands[0] if bands else ''
    if not isinstance(band, str) or (band and band not in VALUE_BANDS):
        raise InvalidFilters('Unknown order value band.')
    result['focus'] = 'customer' if params.get('focus') == 'customer' else ''
    result['value_band'] = band
    if params.get('format', '') not in ('', 'html', 'csv'):
        raise InvalidFilters('Unsupported report format.')
    return result


def query_scope(filters):
    clauses, args = ['fy = %s'], [filters['fy']]
    for key, column in (('channel', 'channel'), ('month', 'period_month'),
                        ('executive', "coalesce(sales_person, 'Unknown')"),
                        ('distribution', "coalesce(sub_channel, 'Unknown')"), ('state', 'state'), ('city', 'city'), ('customer', 'customer')):
        if filters[key]:
            clauses.append(column + ' = ANY(%s)')
            args.append(filters[key])
    if filters['brand']:
        clauses.append('brand = ANY(%s)')
        args.append(filters['brand'])
    assigned = []
    for key, column in (('scope_city', 'city'), ('scope_state', 'state')):
        if filters[key]:
            assigned.append(column + ' = ANY(%s)')
            args.append(filters[key])
    if assigned:
        clauses.append('(' + ' OR '.join(assigned) + ')')
    paired_scopes = []
    for person, kind, place in filters.get('scope_assignment', []):
        terms = ["coalesce(sales_person, 'Unknown') = %s"]
        args.append(person)
        if kind in ('city', 'state'):
            terms.append(kind + ' = %s')
            args.append(place)
        paired_scopes.append('(' + ' AND '.join(terms) + ')')
    if paired_scopes:
        clauses.append('(' + ' OR '.join(paired_scopes) + ')')
    if filters['value_band']:
        lower, upper, _ = VALUE_BANDS[filters['value_band']]
        # Dashboard entries use round(float(primary_taxable)). Every band edge
        # is even, so a tie at edge - 0.5 rounds to that edge. The float cast
        # matches Python's conversion before rounding rather than NUMERIC math.
        clauses.append('primary_taxable::double precision >= %s')
        args.append(lower - 0.5)
        if upper is not None:
            clauses.append('primary_taxable::double precision < %s')
            args.append(upper - 0.5)
    # These predicates come exclusively from the current account, never URL
    # parameters. Keep empty allowed sets: ANY([]) must match nothing.
    for column, values in filters.get('_access_predicates', []):
        clauses.append(column + ' = ANY(%s)')
        args.append(values)
    return ' where ' + ' and '.join(clauses), args


def apply_dashboard_access(filters, user):
    """Attach mandatory account scope to every report query, including CSV."""
    policy = dashboard_policy.policy_for(user)
    drilldowns = set(policy['drilldowns'])
    if not policy['pages'] or not (
        'sales' in drilldowns or
        ('state' in drilldowns and filters.get('focus') == 'customer' and filters.get('state'))
    ):
        raise PermissionError('Sales breakup access is not enabled for this account.')
    predicates = []
    columns = {'channel': 'channel', 'executive': "coalesce(sales_person, 'Unknown')",
               'customer': 'customer', 'brand': 'brand',
               'distribution': "coalesce(sub_channel, 'Unknown')", 'state': 'state'}
    for key, column in columns.items():
        values = policy['scopes'][key]
        if values is None:
            continue
        if key == 'channel':
            values = [CHANNELS.get(re.sub(r'\s+', '', value).lower(), value) for value in values]
        predicates.append((column, values))
    allowed_months = policy['scopes']['month']
    if allowed_months is not None:
        months = []
        for month in allowed_months:
            number = datetime.strptime(month, '%b').month
            months.append(date(filters['fy'] + (number < 4), number, 1))
        predicates.append(('period_month', months))
    filters['_access_predicates'] = predicates
    # A return link is navigation only; it must not offer a disabled page.
    if filters.get('return_page') not in policy['pages']:
        filters['return_page'] = policy['pages'][0]
    return filters


def report_url(filters, **changes):
    pairs = [('fy', str(filters['fy']))]
    for key in (*FILTERS, *SCOPE_FILTERS):
        pairs.extend((key, value.strftime('%b-%Y') if isinstance(value, date) else value)
                     for value in filters[key])
    pairs.extend(('scope_assignment', json.dumps(value, ensure_ascii=False, separators=(',', ':')))
                 for value in filters.get('scope_assignment', []))
    pairs.extend((key, str(changes.get(key, filters[key])))
                 for key in ('page', 'page_size', 'return_page', 'return_view'))
    if filters['value_band']:
        pairs.append(('value_band', filters['value_band']))
    if filters.get('focus'):
        pairs.append(('focus', filters['focus']))
    if changes.get('format'):
        pairs.append(('format', changes['format']))
    return '/reports/sales-breakdown?' + urlencode(pairs)


def back_url(filters):
    pairs = [('page', filters['return_page']), ('fy', str(filters['fy'])), ('drilldown', '1')]
    if filters['return_view'] == 'detail' and filters['return_page'] in DETAIL_PAGES:
        pairs.append(('view', 'detail'))
    for key, parameter in (('month', 'month'), ('executive', 'exec'), ('brand', 'dtt'),
                           ('distribution', 'dist'), ('state', 'state'), ('customer', 'customer')):
        pairs.extend((parameter, value.strftime('%b-%Y') if isinstance(value, date) else value)
                     for value in filters[key])
    return '/?' + urlencode(pairs)


def permitted_columns(user):
    if not auth.can(user, 'registers.view'):
        return []
    return ['entry_id', *(key for section, fields in EXPORT_FIELDS.items()
                          if section_access(user, section) for key in fields)]


def load_report(conn, filters, user):
    where, args = query_scope(filters)
    columns = [key for key in permitted_columns(user) if key not in CSV_ONLY_COLUMNS]
    with conn.cursor() as cur:
        cur.execute('select count(*),coalesce(sum(primary_qty),0),coalesce(sum(primary_taxable),0)'
                    ' from rpt.v_sales' + where, args)
        entries, units, taxable = cur.fetchone()
        pages = max(1, (entries + filters['page_size'] - 1) // filters['page_size'])
        current = min(filters['page'], pages)
        groups = []
        for key, label, column in GROUPS:
            if key == 'customer' and not (columns and section_access(user, 'invoice')):
                continue
            order = 'taxable desc,' + column + ' asc nulls last'
            cur.execute(f'select {column} as label,count(*),coalesce(sum(primary_qty),0),'
                        f'coalesce(sum(primary_taxable),0) as taxable from rpt.v_sales' + where +
                        f' group by {column} order by {order}', args)
            groups.append((key, label, cur.fetchall()))
        invoice_rows = []
        if columns:
            cur.execute('select ' + ','.join(columns) + ' from rpt.v_sales' + where +
                        ' order by invoice_date desc,entry_id desc limit %s offset %s',
                        [*args, filters['page_size'], (current - 1) * filters['page_size']])
            invoice_rows = cur.fetchall()
    # The dashboard rounds each entry with Python's round(float(value)) before
    # adding it. Reproduce that display total separately; keep ledger sums exact.
    dashboard_taxable = dashboard_units = 0
    with conn.cursor(name='sales_breakdown_dashboard_totals') as cur:
        cur.itersize = 1000
        cur.execute('select primary_qty,primary_taxable from rpt.v_sales' + where, args)
        for qty, amount in cur:
            dashboard_units += round(float(qty))
            dashboard_taxable += round(float(amount))
    return dict(entries=entries, units=units, taxable=taxable, groups=groups, columns=columns,
                dashboard_taxable=dashboard_taxable, dashboard_units=dashboard_units,
                invoice_rows=invoice_rows, page=current, pages=pages)


def _display(value, key=''):
    if value is None:
        return '—'
    if isinstance(value, date):
        return value.strftime('%b %Y') if key == 'month' else format_date(value)
    if key == 'channel':
        return pgdata.REGION_OF.get(value, value)
    if key in NUMBERS:
        return number(value, 3 if key == 'weight_kg' else 0 if key in ('entry_id', 'line_no', 'credit_days') else 2)
    return str(value)


def sales_percentage(amount, total):
    """Share of the full filtered ledger total, never the current invoice page."""
    total = Decimal(str(total))
    if total <= 0:
        return None
    return (Decimal(str(amount)) * Decimal(100) / total).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)


def render_report(filters, data, user):
    labels = {'channel': 'Channel', 'month': 'Month', 'executive': 'Executive', 'brand': 'Brand',
              'distribution': 'Sub-channel', 'state': 'State', 'city': 'City / location', 'customer': 'Customer'}
    chips = [f'<span class="sb-chip"><strong>Financial year</strong> {filters["fy"]}–{str(filters["fy"]+1)[-2:]}</span>']
    for key in FILTERS:
        values = [_display(value, key) for value in filters[key]]
        chips.append(f'<span class="sb-chip"><strong>{labels[key]}</strong> {esc(", ".join(values) or "All")}</span>')
    for key, label in (('scope_city', 'Assigned cities'), ('scope_state', 'Assigned states')):
        if filters[key]:
            chips.append(f'<span class="sb-chip"><strong>{label}</strong> {esc(", ".join(filters[key]))}</span>')
    if filters.get('scope_assignment'):
        assignments = [f'{person}: {place if kind != "channel" else "whole channel"}'
                       for person, kind, place in filters['scope_assignment']]
        chips.append('<span class="sb-chip"><strong>Executive assignments</strong> ' +
                     esc('; '.join(assignments)) + '</span>')
    if filters['value_band']:
        chips.append('<span class="sb-chip"><strong>Order value band</strong> ' +
                     esc(VALUE_BANDS[filters['value_band']][2]) + '</span>')
    export = ('<a class="entry-btn" href="' + esc(report_url(filters, format='csv')) + '">Export matching entries</a>'
              if permitted_columns(user) and auth.can(user, 'exports.access') else '')
    heading = 'State customer sales breakdown' if filters.get('focus') == 'customer' else 'Primary sales breakdown'
    body = STYLE + CSS + '<main class="entries-page sales-breakdown"><header class="entry-heading"><div><h1>' + heading + '</h1><p>The invoice entries behind this achievement selection.</p></div><div class="entry-heading-actions"><a class="entry-btn" href="' + esc(back_url(filters)) + '">Back to dashboard</a>' + export + '</div></header>'
    body += '<div class="sb-context">' + ''.join(chips) + '</div><nav class="sb-jumps" aria-label="Report sections"><strong>Jump to</strong>'
    if data['columns']:
        body += '<a href="#invoice-entries">Invoice entries</a>'
    body += ''.join(f'<a href="#breakdown-{esc(key)}">{esc(label)}</a>' for key, label, _ in data['groups'])
    body += '</nav><section class="entry-stats" aria-label="Full selection totals">'
    for label, value in (('Dashboard achieved', '₹ ' + number(data['dashboard_taxable'], 0)),
                         ('Exact taxable sales before GST', '₹ ' + number(data['taxable'])),
                         ('Primary units', number(data['units'])), ('Invoice entries', number(data['entries'], 0))):
        body += f'<div class="entry-stat"><div class="entry-stat-label">{label}</div><strong>{value}</strong><small>Entire filtered selection</small></div>'
    body += '</section><details class="sb-info"><summary>About these totals</summary><p>Breakdowns and Sales % use exact stored taxable sales from every matching entry; only the invoice list is paginated. Each invoice line counts as one entry. Dashboard achieved rounds each entry to whole rupees before adding (difference from exact sales: ₹ ' + number(data['dashboard_taxable'] - data['taxable']) + '; dashboard units: ' + number(data['dashboard_units'], 0) + '). Sales % is unavailable when the filtered taxable total is zero or negative.</p></details><div class="sb-groups">'
    groups = sorted(data['groups'], key=lambda group: group[0] != 'customer') if filters.get('focus') == 'customer' else data['groups']
    for key, label, rows in groups:
        body += f'<section class="entry-panel{" sb-customer-focus" if key == "customer" and filters.get("focus") else ""}" id="breakdown-{esc(key)}"><div class="entry-panel-heading"><h2>By {esc(label.lower())}</h2></div><div class="sb-group"><table><thead><tr><th scope="col">{esc(label)}</th><th scope="col" class="num">Entries</th><th scope="col" class="num">Units</th><th scope="col" class="num" aria-sort="descending">Taxable sales (₹)</th><th scope="col" class="num" title="Share of exact taxable sales across the entire filtered report">Sales %</th></tr></thead><tbody>'
        for value, entries, units, taxable in rows:
            share = sales_percentage(taxable, data['taxable'])
            share_text = f'{share:.1f}%' if share is not None else '<abbr title="Not available: the filtered taxable total is zero or negative">N/A</abbr>'
            body += f'<tr><td class="sb-label">{esc(_display(value, key))}</td><td class="num">{number(entries, 0)}</td><td class="num">{number(units)}</td><td class="num">{number(taxable)}</td><td class="num">{share_text}</td></tr>'
        body += ('<tr><td colspan="5">No matching entries.</td></tr>' if not rows else '') + '</tbody></table></div></section>'
    body += '</div>'
    if not data['columns']:
        return body + '<p class="sb-note">Invoice details and customer breakdowns require Sales register access and the relevant sales-section permissions. Ask your administrator if you need those details.</p></main>'
    body += '<section class="entry-panel" id="invoice-entries"><div class="entry-panel-heading"><div><h2>Invoice entries</h2><p>Columns follow your sales-section permissions.</p></div><span class="sb-note">' + str(filters['page_size']) + ' per page</span></div><div class="sb-invoices"><table><thead><tr>'
    body += ''.join('<th>' + esc(LABELS.get(key, key)) + '</th>' for key in data['columns']) + '</tr></thead><tbody>'
    for row in data['invoice_rows']:
        body += '<tr>' + ''.join(f'<td{" class=num" if key in NUMBERS else ""}>{esc(_display(value, key))}</td>'
                                for key, value in zip(data['columns'], row)) + '</tr>'
    if not data['invoice_rows']:
        body += f'<tr><td colspan="{len(data["columns"])}">No matching entries.</td></tr>'
    body += '</tbody></table></div><footer class="entry-pagination"><span>Page ' + str(data['page']) + ' of ' + str(data['pages']) + ' · ' + number(data['entries'], 0) + ' matching entries</span><div class="entry-page-controls">'
    for label, current in (('Previous', data['page'] - 1), ('Next', data['page'] + 1)):
        if 1 <= current <= data['pages']:
            body += f'<a class="entry-btn" href="{esc(report_url(filters, page=current))}#invoice-entries">{label}</a>'
    for size in (25, 50):
        body += f'<a class="entry-btn" href="{esc(report_url(filters, page=1, page_size=size))}#invoice-entries">{size} per page</a>'
    return body + '</div></footer></section></main>'


def register(app, current_user, login_redirect, page):
    @app.get('/reports/sales-breakdown')
    def breakdown(request: Request):
        user = current_user(request)
        if not user:
            return login_redirect(request)

        def error(message, status):
            return HTMLResponse(page('Sales breakdown', '<h1>Sales breakdown</h1><p>' + esc(message) + '</p>', user),
                                status_code=status, headers={'Cache-Control': 'no-store'})

        if not auth.can(user, 'dashboard.view'):
            return error('Dashboard access is required.', 403)
        exporting = request.query_params.get('format') == 'csv'
        if exporting and not (auth.can(user, 'registers.view') and auth.can(user, 'exports.access')):
            return error('Sales register and Export access are required to download invoice entries.', 403)
        try:
            filters = parse_filters(request.query_params)
            apply_dashboard_access(filters, user)
            with closing(pgdata.connect()) as conn:
                conn.set_session(readonly=True, isolation_level='REPEATABLE READ', autocommit=False)
                if exporting:
                    columns = permitted_columns(user)
                    where, args = query_scope(filters)
                    output = io.StringIO(newline='')
                    writer = csv.writer(output)
                    writer.writerow(columns)
                    with conn.cursor() as cur:
                        cur.execute('select ' + ','.join(columns) + ' from rpt.v_sales' + where +
                                    ' order by invoice_date,entry_id', args)
                        for row in cur:
                            writer.writerow([csv_cell(value) for value in row])
                    return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8',
                                    headers={'Content-Disposition': 'attachment; filename="sales-breakdown.csv"',
                                             'Cache-Control': 'no-store'})
                data = load_report(conn, filters, user)
            return HTMLResponse(page('Primary sales breakdown', render_report(filters, data, user), user),
                                headers={'Cache-Control': 'no-store'})
        except InvalidFilters as exc:
            return error(str(exc), 400)
        except PermissionError as exc:
            return error(str(exc), 403)
        except Exception:
            return error('Sales data is temporarily unavailable. Please try again or contact your administrator.', 503)
