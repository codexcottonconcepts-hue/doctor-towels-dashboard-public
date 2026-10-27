"""Authenticated PostgreSQL sales entry, dispatch updates, and ledger export."""
import csv
import io
import json
import secrets
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation
from html import escape
from math import ceil
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from starlette.concurrency import run_in_threadpool

import auth
import dashboard_policy as dp
import pgdata
import invoice_items
import invoice_ui
import pi_numbers
import customer_details
import customer_ui
from entry_dates import parse_date, format_date
from entry_styles import STYLE, DATE_SCRIPT

AMOUNTS = {
    "primary_qty": "Primary quantity", "primary_taxable": "Sales before GST (₹)",
    "primary_with_tax": "Sales including GST (₹)", "secondary_qty": "Secondary quantity",
    "secondary_value": "Secondary sales (₹)", "tertiary_nos": "Tertiary quantity",
    "tertiary_value": "Tertiary sales (₹)",
}
SHIPPING_FIELDS = ('weight_kg', 'transport_service')
DISPATCH_REQUIREMENTS = {
    'stock_supplied_on': 'Stock supplied date', 'weight_kg': 'Weight (kg)',
    'transport_service': 'Transport service', 'courier_id': 'Courier type',
    'courier_docket_no': 'Courier number / AWB',
}
TRANSPORT_SERVICES = ('Cargo', 'OnRoad', 'Airway')
COURIER_CODES = ('DTDC', 'Bluedart', 'Pionexxco', 'RK')
COURIER_LABELS = {code.casefold(): code for code in COURIER_CODES}
DIMENSIONS = {
    "customer_id": ("customer", "name"), "channel_id": ("channel", "code"),
    "sub_channel_id": ("sub_channel", "code"), "person_id": ("sales_person", "full_name"),
    "brand_id": ("brand", "code"), "state_id": ("state", "name"),
    "district_id": ("district", "name"), "city_id": ("city", "name"),
    "courier_id": ("courier", "code"),
}
# Historical credit/payment columns remain in the database and exports, but
# are not writable from sales entry. Omitting them also preserves them on edits.
COLUMNS = ["invoice_date", "invoice_no", "line_no", "pi_number", *DIMENSIONS,
           "order_type", *AMOUNTS, "stock_supplied_on",
           *SHIPPING_FIELDS, "courier_docket_no", "remarks"]
PAGE_SIZE = 25
SECTION_FIELDS = {
    'invoice': ('invoice_date', 'invoice_no', 'pi_number', 'customer_id', 'new_customer'),
    'classification': ('channel_id', 'sub_channel_id', 'person_id', 'brand_id', 'order_type'),
    'location': ('state_id', 'district_id', 'city_id'),
    'values': tuple(AMOUNTS),
    'dispatch': ('stock_supplied_on', *SHIPPING_FIELDS, 'courier_id', 'courier_docket_no', 'remarks'),
}
CREATE_SECTIONS = ('invoice', 'classification', 'location', 'values')
INVOICE_FIELDS = ('entry_mode', 'price_list', 'invoice_items')
PRIMARY_AMOUNTS = ('primary_qty', 'primary_taxable', 'primary_with_tax')
# Retained for historical reports and sheet sync, but no longer entered here.
LEGACY_AMOUNTS = ('secondary_qty', 'secondary_value', 'tertiary_nos', 'tertiary_value')
COURIER_FIELDS = ('courier_id', 'courier_docket_no', *SHIPPING_FIELDS)
LIMITED_DISPATCH_FIELDS = {
    'sales.courier.edit': COURIER_FIELDS,
    'sales.stock_supplied.edit': ('stock_supplied_on',),
}
CREATE_MESSAGE = ('Creating a sales entry requires Create sales and Edit access to Invoice & customer, '
                  'Sales classification, Location and Sales values. Dispatch access is optional. '
                  'Ask your administrator to update your access.')


def section_access(user, section, action='view'):
    if user is None:  # Internal legacy callers; HTTP handlers always supply the authenticated user.
        return True
    return auth.can(user, f'sales.{section}.{action}') or (action == 'view' and auth.can(user, f'sales.{section}.edit'))


def may_create(user):
    return auth.can(user, 'sales.create') and all(section_access(user, s, 'edit') for s in CREATE_SECTIONS)


def may_edit(user):
    return (any(auth.can(user, permission) for permission in LIMITED_DISPATCH_FIELDS) or
            (auth.can(user, 'sales.edit') and any(section_access(user, s, 'edit') for s in SECTION_FIELDS)))


def may_view_customer(user):
    return (section_access(user, 'invoice') and section_access(user, 'location') and
            (user is None or may_create(user) or may_edit(user) or auth.can(user, 'registers.view')))


def may_manage_customer(user):
    return (section_access(user, 'invoice', 'edit') and section_access(user, 'location', 'edit') and
            (user is None or may_create(user) or auth.can(user, 'sales.edit')))


def limited_dispatch_only(user):
    return (user is not None and any(auth.can(user, permission) for permission in LIMITED_DISPATCH_FIELDS) and
            not (auth.can(user, 'sales.edit') and any(section_access(user, s, 'edit') for s in SECTION_FIELDS)))


def field_edit_access(user, section, key, existing=False):
    full_section = section_access(user, section, 'edit') and (not existing or user is None or auth.can(user, 'sales.edit'))
    return full_section or any(key in fields and auth.can(user, permission)
                               for permission, fields in LIMITED_DISPATCH_FIELDS.items())


def is_admin(user):
    return bool(user is not None and auth._user_value(user, 'is_admin', False))


def dispatch_only_editor(user):
    """A dispatch editor may also have read access to unrelated sales sections."""
    if user is None or is_admin(user):
        return False
    editable = {section for section, fields in SECTION_FIELDS.items()
                if any(field_edit_access(user, section, key, existing=True) for key in fields)}
    return editable == {'dispatch'}


def required_dispatch_fields(user, existing=False):
    if not existing or user is None or is_admin(user):
        return ()
    return tuple(key for key in DISPATCH_REQUIREMENTS
                 if field_edit_access(user, 'dispatch', key, existing=True))


def validate_required_dispatch(values, user, existing=False):
    missing = [DISPATCH_REQUIREMENTS[key] for key in required_dispatch_fields(user, existing)
               if values.get(key) is None or not str(values[key]).strip()]
    if missing:
        raise EntryError('Complete the required dispatch fields: ' + ', '.join(missing) + '.')


def visible_sections(user):
    return {s for s in SECTION_FIELDS if section_access(user, s)}


def restricted_form(form, user, original=None):
    """Preserve retired amounts and sections the caller cannot edit."""
    merged = {key: form.get(key, '') for key in ('entry_id', 'revision', 'submission_id')}
    for section, fields in SECTION_FIELDS.items():
        for key in fields:
            editable = key not in LEGACY_AMOUNTS and field_edit_access(user, section, key, existing=original is not None)
            source = form if editable else (original or {})
            if editable and key not in source:
                if original is not None and key in DISPATCH_REQUIREMENTS:
                    merged[key] = '' if original.get(key) is None else original[key]
                continue  # Preserve parser defaults for optional, omitted inputs.
            value = source.get(key, '')
            merged[key] = '' if value is None else value
    for key, default in zip(INVOICE_FIELDS, ('summary', 'common_mrp', '[]')):
        # Product details have the same access rules as the sales values.
        source = form if field_edit_access(user, 'values', key, existing=original is not None) else {}
        merged[key] = source.get(key, (original or {}).get(key, default))
    # Customer-only fields are not invoice ledger columns. They are accepted
    # solely from an invoice editor and used only for explicit customer saves.
    if field_edit_access(user, 'invoice', 'new_customer', existing=original is not None):
        for key in customer_details.PROFILE_FIELDS:
            merged[key] = form.get(key, '')
    return merged


def invoice_form_values(invoice):
    if not invoice:
        return {'entry_mode': 'summary', 'price_list': 'common_mrp', 'invoice_items': '[]', '_invoice_saved': False}
    return {'entry_mode': 'invoice', 'price_list': invoice['price_list'],
            'invoice_items': json.dumps(invoice['items'], default=str), '_invoice_saved': True}


def sheet_summary_only(cur, entry):
    if entry.get('source') == 'sheets_migration':
        return True
    cur.execute('select 1 from aux.sheet_sales_row where entry_id=%s', (entry['entry_id'],))
    return cur.fetchone() is not None


def esc(value):
    return escape("" if value is None else str(value), quote=True)


def icon(name):
    paths = {
        "plus": '<path d="M12 5v14M5 12h14"/>',
        "download": '<path d="M12 3v12m-4-4 4 4 4-4M5 15v5h14v-5"/>',
        "file": '<path d="M14 3H5v18h14V8zm0 0v5h5M8 12h8M8 16h6"/>',
        "money": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="12" cy="12" r="3"/><path d="M6 12h.01M18 12h.01"/>',
        "box": '<path d="m12 3 9 5v9l-9 5-9-5V8zm0 10v9M3 8l9 5 9-5M7 5.8l9 5V16"/>',
        "edit": '<path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-4-4L5 15z"/>',
        "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
    }
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths[name] + '</svg>'


def number(value, places=2):
    """Indian digit grouping without converting stored Decimal amounts to floats."""
    raw = f'{value or 0:.{places}f}'
    integer, dot, fraction = raw.partition('.')
    sign = '-' if integer.startswith('-') else ''
    integer = integer.lstrip('-')
    tail, head = integer[-3:], integer[:-3]
    groups = []
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return sign + ','.join(groups + [tail]) + dot + fraction


def register_url(params, **changes):
    selected = {key: params[key] for key in ('q', 'from', 'to') if params.get(key)}
    selected.update(changes)
    return '/entries' + ('?' + urlencode(selected) if selected else '')


class EntryError(ValueError):
    pass


class EntryAccessError(EntryError):
    pass


def entry_channels(user):
    """Channel limits also govern primary sales operations, independently of views."""
    if user is None or is_admin(user):
        return None
    if auth._user_value(user, 'dashboard_access_json') is None:
        return None  # Legacy internal callers without a saved data policy.
    return dp.policy_for(user)['scopes']['channel']


def channel_scope_clause(user, column='channel'):
    if column not in ('channel', 'code'):
        raise ValueError('Unsupported channel column.')
    allowed = entry_channels(user)
    if allowed is None:
        return '', []
    if not allowed:
        return 'false', []
    names = set(allowed)
    tokens = sorted({token for token, name in dp._CHANNEL_ALIASES.items() if name in names})
    return f"regexp_replace(lower({column}),'[^a-z0-9]','','g') = any(%s)", [tokens]


def assert_entry_channel(cur, channel_id, user):
    allowed = entry_channels(user)
    if allowed is None:
        return
    if not allowed:
        raise EntryAccessError('This sales entry is outside your assigned channels.')
    try:
        selected = int(channel_id)
        if not 0 < selected <= 2147483647:
            raise ValueError
    except (ValueError, TypeError):
        raise EntryAccessError('Choose one of your assigned channels.')
    cur.execute('select code from master.channel where channel_id=%s', (selected,))
    row = cur.fetchone()
    if not row or dp.canonical_channel(row[0]) not in allowed:
        raise EntryAccessError('This sales entry is outside your assigned channels.')


def csrf_token(request):
    if not request.session.get("csrf_token"):
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    return request.session["csrf_token"]


def parse_weight(value):
    raw = str(value if value is not None else '').strip()
    if not raw:
        return None
    if len(raw) > 64:
        raise EntryError('Weight (kg): enter a valid number.')
    try:
        weight = Decimal(raw)
    except InvalidOperation:
        raise EntryError('Weight (kg): enter a valid number.')
    if not weight.is_finite() or weight <= 0 or weight > Decimal('999999999.999'):
        raise EntryError('Weight (kg): enter a positive number up to 999999999.999, or leave it blank.')
    if weight != weight.quantize(Decimal('0.001')):
        raise EntryError('Weight (kg): use no more than three decimal places.')
    return weight


def parse_transport_service(value):
    service = str(value if value is not None else '').strip()
    if service and service not in TRANSPORT_SERVICES:
        raise EntryError('Choose Cargo, OnRoad or Airway for Transport service.')
    return service or None


def parse_entry(form):
    """Validate values independently of database constraints and browser controls."""
    out = {}
    for key, maximum in (("invoice_no", 100), ("pi_number", 100),
                         ("courier_docket_no", 120), ("remarks", 2000)):
        value = str(form.get(key, "")).strip()
        if len(value) > maximum:
            raise EntryError(f"{key.replace('_', ' ').title()} is too long (maximum {maximum} characters).")
        out[key] = value or None
    out['weight_kg'] = parse_weight(form.get('weight_kg'))
    out['transport_service'] = parse_transport_service(form.get('transport_service'))
    for key in ("invoice_date", "stock_supplied_on"):
        raw = str(form.get(key, "")).strip()
        try:
            out[key] = parse_date(raw)
        except ValueError:
            raise EntryError("Enter a valid " + key.replace("_", " ") + " in DD-MM-YYYY format.")
    if not out["invoice_date"]:
        raise EntryError("Invoice date is required.")
    for key in (*DIMENSIONS, "entry_id"):
        raw = str(form.get(key, "")).strip()
        try:
            value = int(raw) if raw else None
        except ValueError:
            raise EntryError("Choose a valid " + key.replace("_id", "").replace("_", " ") + ".")
        if value is not None and (value < 1 or value > 2147483647):
            raise EntryError("Invalid number for " + key.replace("_", " ") + ".")
        out[key] = value
    for key, label in AMOUNTS.items():
        raw = str(form.get(key, "")).strip() or "0"
        try:
            value = Decimal(raw)
        except InvalidOperation:
            raise EntryError(f"{label}: enter a valid number.")
        if not value.is_finite() or value < 0 or value > Decimal("999999999999.99"):
            raise EntryError(f"{label}: enter a non-negative number below 1 trillion.")
        if value != value.quantize(Decimal("0.01")):
            raise EntryError(f"{label}: use no more than two decimal places.")
        out[key] = value
    out["order_type"] = form.get("order_type", "Re-order")
    if out["order_type"] not in ("First", "Re-order"):
        raise EntryError("Choose First or Re-order.")
    if out["primary_with_tax"] < out["primary_taxable"]:
        raise EntryError("Sales including GST cannot be less than sales before GST.")
    out["new_customer"] = str(form.get("new_customer", "")).strip()
    if len(out["new_customer"]) > 200:
        raise EntryError("Customer name must be at most 200 characters.")
    if out["customer_id"] and out["new_customer"]:
        raise EntryError("Select an existing customer or enter a new customer name, using one field only.")
    for key in ("channel_id", "brand_id", "state_id"):
        if not out[key]:
            raise EntryError("Choose a " + key.replace("_id", "") + ".")
    if not out["customer_id"] and not out["new_customer"]:
        raise EntryError("Choose an existing customer or enter a new customer name.")
    if out['new_customer']:
        try:
            out['customer_details'] = customer_details.parse_fields(form)
        except customer_details.CustomerError as exc:
            raise EntryError(str(exc)) from None
    out["revision"] = str(form.get("revision", ""))
    out["submission_id"] = None
    if not out['entry_id']:
        try:
            out['submission_id'] = str(UUID(str(form.get('submission_id', ''))))
        except (ValueError, TypeError, AttributeError):
            raise EntryError("This form needs to be refreshed. Review your details below and save again.")
    return out


def save_limited_dispatch(cur, form, original, username, user):
    """Update only explicitly granted dispatch fields on an already-locked row."""
    revision = (original['updated_at'] or original['created_at']).isoformat()
    if str(form.get('revision', '')) != revision:
        raise EntryError('Another user changed this entry. Reload it before saving your changes.')
    changes = {}
    if auth.can(user, 'sales.courier.edit'):
        raw = str(form.get('courier_id', original['courier_id']) or '').strip()
        try:
            courier_id = int(raw) if raw else None
            if courier_id is not None and not 0 < courier_id <= 2147483647:
                raise ValueError
        except ValueError:
            raise EntryError('Choose a valid courier type.')
        docket = str(form.get('courier_docket_no', original['courier_docket_no']) or '').strip() or None
        if docket and len(docket) > 120:
            raise EntryError('Courier number must be at most 120 characters.')
        if courier_id:
            cur.execute('select code from master.courier where courier_id=%s and (is_active or %s)',
                        (courier_id, courier_id == original['courier_id']))
            row = cur.fetchone()
            if not row:
                raise EntryError('The selected courier is unavailable. Reload the form.')
            if row[0].casefold() not in COURIER_LABELS and courier_id != original['courier_id']:
                raise EntryError('Choose DTDC, Bluedart, Pionexxco or RK for a new dispatch.')
        elif docket:
            raise EntryError('Choose the courier type for this courier number.')
        changes.update(courier_id=courier_id, courier_docket_no=docket)
        changes['weight_kg'] = parse_weight(form.get('weight_kg', original.get('weight_kg')))
        changes['transport_service'] = parse_transport_service(form.get('transport_service', original.get('transport_service')))
    if auth.can(user, 'sales.stock_supplied.edit'):
        try:
            supplied = parse_date(form.get('stock_supplied_on', original['stock_supplied_on']))
        except ValueError:
            raise EntryError('Enter a valid stock supplied date in DD-MM-YYYY format.')
        if supplied and original['payment_received_on'] and supplied > original['payment_received_on']:
            raise EntryError('Stock supplied date cannot be after the recorded payment date.')
        changes['stock_supplied_on'] = supplied
    validate_required_dispatch({**original, **changes}, user, existing=True)
    setters = ','.join(f'{field}=%s' for field in changes)
    cur.execute(f'update txn.sales_entry set {setters},updated_by=%s,updated_at=now() '
                'where entry_id=%s returning entry_id', (*changes.values(), username, original['entry_id']))
    return cur.fetchone()[0], False


def save_entry(form, username, user=None):
    """Return (entry identifier, whether this new-form submission was replayed)."""
    raw_id = str(form.get('entry_id', '')).strip()
    try:
        entry_id = int(raw_id) if raw_id else None
        if entry_id is not None and not 0 < entry_id <= 2147483647:
            raise ValueError
    except ValueError:
        raise EntryError('Choose a valid entry to edit.')
    if user is not None and not (may_edit(user) if entry_id else may_create(user)):
        raise EntryError('You do not have access to edit sales entries.' if entry_id else CREATE_MESSAGE)
    with closing(pgdata.connect()) as conn, conn:
        with conn.cursor() as cur:
            cur.execute("select set_config('app.username', %s, true)", (username,))
            original = None
            existing_invoice = None
            if entry_id:
                cur.execute('select * from txn.sales_entry where entry_id=%s and not is_deleted for update', (entry_id,))
                record = cur.fetchone()
                if not record:
                    raise EntryError('This entry no longer exists.')
                original = dict(zip([d[0] for d in cur.description], record))
                assert_entry_channel(cur, original['channel_id'], user)
                if limited_dispatch_only(user):
                    return save_limited_dispatch(cur, form, original, username, user)
                existing_invoice = invoice_items.load_invoice(cur, entry_id)
                original.update(invoice_form_values(existing_invoice))
            merged = restricted_form(form, user, original)
            assert_entry_channel(cur, merged.get('channel_id'), user)
            # PI identity belongs to the server. New forms get the next number
            # after validation; historical PIs (including NULL) stay unchanged.
            merged['pi_number'] = (original.get('pi_number') or '') if original else ''
            edit_values = field_edit_access(user, 'values', 'invoice_items', existing=original is not None)
            product_mode = merged.get('entry_mode') == 'invoice'
            if edit_values and product_mode:
                # Browser totals are only a preview. The server derives these
                # after the duplicate-submission and revision checks below.
                merged.update(dict.fromkeys(PRIMARY_AMOUNTS, '0'))
            data = parse_entry(merged)
            if data['new_customer'] and not may_manage_customer(user):
                raise EntryAccessError('Customer creation requires Invoice & customer and Location edit access.')
            validate_required_dispatch(data, user, existing=original is not None)
            if not data['entry_id']:
                # Brand identifiers are positive, reserving lock namespace 0
                # for new-form tokens. Retries wait for the original commit.
                cur.execute('select pg_advisory_xact_lock(hashtext(%s), 0)', (data['submission_id'],))
                cur.execute('select entry_id,is_deleted,channel_id from txn.sales_entry where submission_id=%s', (data['submission_id'],))
                submitted = cur.fetchone()
                if submitted:
                    if entry_channels(user) is not None:
                        assert_entry_channel(cur, submitted[2], user)
                    if submitted[1]:
                        raise EntryError('The original entry was removed. Open a new form if you need to enter it again.')
                    return submitted[0], True
            old_courier = None
            old_dimensions = {}
            allocate_number = True
            if data["entry_id"]:
                old_dimensions = {key: original[key] for key in DIMENSIONS}
                old_courier = old_dimensions["courier_id"]
                data['line_no'] = original['line_no']
                allocate_number = not ((original.get('invoice_no') or '').casefold() == (data.get('invoice_no') or '').casefold() and old_dimensions['brand_id'] == data['brand_id'])
                if not data["revision"] or (original['updated_at'] or original['created_at']).isoformat() != data["revision"]:
                    raise EntryError("Another user changed this entry. Reload it before saving your changes.")
                if original['payment_received_on'] and data['stock_supplied_on'] and data['stock_supplied_on'] > original['payment_received_on']:
                    detail = f" ({format_date(original['payment_received_on'])})" if section_access(user, 'values') else ''
                    raise EntryError(f"Stock supplied date cannot be after the historical payment date{detail}. Check the dispatch date or ask your database administrator to review the historical record.")
            prepared_invoice = None
            if edit_values:
                if product_mode and original and not existing_invoice and sheet_summary_only(cur, original):
                    raise EntryError('Google Sheets entries use summary amounts. Create a new sales entry for product invoicing.')
                try:
                    prepared_invoice = invoice_items.prepare_invoice(cur, merged, existing=existing_invoice)
                except invoice_items.InvoiceError as exc:
                    raise EntryError(str(exc)) from exc
                if prepared_invoice['entry_mode'] == 'invoice':
                    data.update({key: prepared_invoice[key] for key in PRIMARY_AMOUNTS})
            for key, (table, _) in DIMENSIONS.items():
                if data[key] is None:
                    continue
                # Identifiers come only from the fixed DIMENSIONS mapping.
                active = " and (is_active or %s)" if table not in ("state", "district") else ""
                args = (data[key], data[key] == old_dimensions.get(key)) if active else (data[key],)
                cur.execute(f"select {key} from master.{table} where {key}=%s{active}", args)
                if not cur.fetchone():
                    raise EntryError("The selected " + table.replace("_", " ") + " is unavailable. Reload the form.")
            if data["district_id"]:
                cur.execute("select state_id from master.district where district_id=%s", (data["district_id"],))
                if cur.fetchone()[0] != data["state_id"]:
                    raise EntryError("The district must belong to the selected state.")
            if data["city_id"]:
                cur.execute("select state_id,district_id from master.city where city_id=%s", (data["city_id"],))
                city_state, city_district = cur.fetchone()
                if city_state != data["state_id"] or (data["district_id"] and city_district != data["district_id"]):
                    raise EntryError("The location must belong to the selected state and district. Complete its master mapping first if needed.")
                data["district_id"] = city_district
            if data["courier_id"]:
                cur.execute("select code from master.courier where courier_id=%s", (data["courier_id"],))
                if cur.fetchone()[0].casefold() not in COURIER_LABELS and data["courier_id"] != old_courier:
                    raise EntryError("Choose DTDC, Bluedart, Pionexxco or RK for a new dispatch.")
            elif data["courier_docket_no"]:
                raise EntryError("Choose the courier type for this courier number.")
            fy = data["invoice_date"].year - (data["invoice_date"].month < 4)
            cur.execute("select 1 from master.financial_year where fy=%s", (fy,))
            if not cur.fetchone():
                raise EntryError("This financial year has not been set up. Ask the administrator to add it first.")
            if data["new_customer"]:
                try:
                    data['customer_id'] = customer_details.create_customer(
                        cur, data['new_customer'], data['customer_details'],
                        {key: data[key] for key in ('state_id', 'district_id', 'city_id')},
                        data['sub_channel_id'], username)
                except customer_details.CustomerError as exc:
                    raise EntryError(str(exc)) from None
            if not data['entry_id']:
                try:
                    data['pi_number'] = pi_numbers.allocate_next(cur, fy)
                except pi_numbers.PIError as exc:
                    raise EntryError(str(exc)) from None
            if not data['invoice_no']:
                if not data['entry_id']:
                    data['line_no'] = 1
            elif allocate_number:
                # Serialize allocation per case-insensitive invoice + brand.
                # Include deleted history so its internal numbers are not reused.
                cur.execute('select pg_advisory_xact_lock(hashtext(lower(%s)), %s)', (data['invoice_no'], data['brand_id']))
                cur.execute('select coalesce(max(line_no),0)+1 from txn.sales_entry where lower(invoice_no)=lower(%s) and brand_id=%s', (data['invoice_no'], data['brand_id']))
                data['line_no'] = cur.fetchone()[0]
                if data['line_no'] > 32767:
                    raise EntryError('This invoice has reached its entry limit. Ask the administrator to review its records.')
            values = [data[key] for key in COLUMNS]
            if data["entry_id"]:
                setters = ",".join(f"{key}=%s" for key in COLUMNS)
                cur.execute(f"update txn.sales_entry set {setters},updated_by=%s,updated_at=now() where entry_id=%s returning entry_id", (*values, username, data["entry_id"]))
            else:
                placeholders = ",".join(["%s"] * len(COLUMNS))
                cur.execute(f"insert into txn.sales_entry({','.join(COLUMNS)},fy,created_by,source,submission_id) values ({placeholders},%s,%s,'web',%s) returning entry_id", (*values, fy, username, data['submission_id']))
            saved_id = cur.fetchone()[0]
            if prepared_invoice and prepared_invoice['entry_mode'] == 'invoice':
                try:
                    invoice_items.save_invoice(cur, saved_id, prepared_invoice)
                except invoice_items.InvoiceError as exc:
                    raise EntryError(str(exc)) from exc
            return saved_id, False


def delete_entry(entry_id, revision, username, user=None):
    """Soft deletion keeps invoice history and the existing database audit trail."""
    with closing(pgdata.connect()) as conn, conn, conn.cursor() as cur:
        cur.execute("select set_config('app.username', %s, true)", (username,))
        cur.execute('select coalesce(updated_at,created_at),channel_id from txn.sales_entry where entry_id=%s and not is_deleted for update', (entry_id,))
        row = cur.fetchone()
        if not row:
            raise EntryError('This entry no longer exists.')
        if entry_channels(user) is not None:
            assert_entry_channel(cur, row[1], user)
        if not revision or row[0].isoformat() != revision:
            raise EntryError('Another user changed this entry. Review it again before deleting.')
        cur.execute('update txn.sales_entry set is_deleted=true,updated_by=%s,updated_at=now() where entry_id=%s', (username, entry_id))


def load_options(cur, values=None, keys=None, user=None):
    options = {}
    for key, (table, label) in DIMENSIONS.items():
        if keys is not None and key not in keys:
            options[key] = []
            continue
        extra = ", state_id" if table in ("district", "city") else ""
        if table == "city":
            extra += ",district_id"
        active = f" where is_active or {key}=%s" if table not in ("state", "district") else ""
        try:
            current = int((values or {}).get(key) or 0)
        except (ValueError, TypeError):
            current = 0
        display = f"{label} || case when is_active then '' else ' (inactive)' end" if active else label
        scope, scope_args = channel_scope_clause(user, 'code') if table == 'channel' else ('', [])
        if scope:
            conditions = f' where (is_active or {key}=%s) and ({scope})'
            cur.execute(f"select {key},{display}{extra} from master.{table}{conditions} order by {label}", [current, *scope_args])
        else:
            cur.execute(f"select {key},{display}{extra} from master.{table}{active} order by {label}", (current,) if active else ())
        options[key] = cur.fetchall()
    return options


def select_field(key, label, options, values, required=False):
    selected = str(values.get(key) or "")
    mark = '<span class="entry-required">*</span>' if required else ''
    parts = [f'<div class="entry-field"><label for="{key}">{label}{mark}</label><select id="{key}" name="{key}"{" required" if required else ""}><option value="">Select…</option>']
    rows = options[key]
    if key == 'courier_id':
        ranks = {code.casefold(): i for i, code in enumerate(COURIER_CODES)}
        rows = sorted(rows, key=lambda row: (ranks.get(row[1].casefold(), len(ranks)), row[1]))
    for row in rows:
        attrs = f' data-state="{row[2]}"' if key in ("district_id", "city_id") else ""
        if key == "city_id":
            attrs += f' data-district="{row[3] or ""}"'
        if key == "courier_id" and row[1].casefold() not in COURIER_LABELS and str(row[0]) != selected:
            continue
        text = row[1]
        if key == 'courier_id':
            text = COURIER_LABELS.get(row[1].casefold(), row[1] + ' (historical)')
        parts.append(f'<option value="{row[0]}"{attrs}{" selected" if str(row[0]) == selected else ""}>{esc(text)}</option>')
    parts.append('</select></div>')
    return "".join(parts)


def input_field(key, label, values, kind="text", required=False, extra=""):
    value = values.get(key, "")
    hint = ''
    if kind == 'date':
        # Native date controls choose their visible format from browser locale.
        # Keep the requested day-month-year text; DATE_SCRIPT adds a calendar.
        kind, value = 'text', format_date(value)
        extra += ' class="entry-date" data-entry-date inputmode="numeric" placeholder="DD-MM-YYYY" pattern="[0-9]{2}-[0-9]{2}-[0-9]{4}" maxlength="10"'
    mark = '<span class="entry-required">*</span>' if required else ''
    return (f'<div class="entry-field"><label for="{key}">{label}{mark}</label>'
            f'<input id="{key}" name="{key}" type="{kind}" value="{esc(value)}"{ " required" if required else ""} {extra}>{hint}</div>')


def entry_form(options, values, token, user=None):
    edit = bool(values.get("entry_id"))
    writable = user is None or (may_edit(user) if edit else may_create(user))
    inp = lambda *a, **kw: input_field(*a, values=values, **kw)
    sel = lambda key, label, **kw: select_field(key, label, options, values, **kw)
    limited_edit = edit and dispatch_only_editor(user)
    edit_subject = 'dispatch'
    header = (f"Edit {edit_subject} details" if limited_edit else "Edit sales entry" if writable else "View sales entry") if edit else "New sales entry"
    stock_readonly = '' if field_edit_access(user, 'dispatch', 'stock_supplied_on', existing=edit) else ' disabled'
    remarks_readonly = '' if field_edit_access(user, 'dispatch', 'remarks', existing=edit) else ' disabled'
    courier_readonly = '' if field_edit_access(user, 'dispatch', 'courier_id', existing=edit) else ' disabled'
    required_dispatch = required_dispatch_fields(user, existing=edit)
    dispatch_controls = {
        'stock_supplied_on': inp('stock_supplied_on', 'Stock supplied date', kind='date',
                                required='stock_supplied_on' in required_dispatch, extra=stock_readonly),
        'weight_kg': inp('weight_kg', 'Weight (kg)', kind='number', required='weight_kg' in required_dispatch,
                        extra='min="0.001" max="999999999.999" step="0.001" placeholder="'
                        + ('Enter weight in kg' if 'weight_kg' in required_dispatch else 'Optional') + '"' + courier_readonly),
        'transport_service': select_field('transport_service', 'Transport service',
                       {'transport_service': [(service, service) for service in TRANSPORT_SERVICES]}, values,
                       required='transport_service' in required_dispatch
                       ).replace('<select ', '<select' + courier_readonly + ' ', 1),
        'courier_id': sel('courier_id', 'Courier type', required='courier_id' in required_dispatch
                         ).replace('<select ', '<select' + courier_readonly + ' ', 1),
        'courier_docket_no': inp('courier_docket_no', 'Courier number / AWB',
                                required='courier_docket_no' in required_dispatch,
                                extra='maxlength="120" placeholder="Enter tracking number"' + courier_readonly),
        'remarks': '<div class="entry-field entry-full"><label for="remarks">Remarks</label><textarea id="remarks" name="remarks" maxlength="2000"'
        + remarks_readonly + ' placeholder="Add any notes for this invoice or delivery">'
        + esc(values.get('remarks')) + '</textarea></div>',
    }
    dispatch_fields = ''.join(control for key, control in dispatch_controls.items()
                              if not limited_edit or field_edit_access(user, 'dispatch', key, existing=True))
    pi_hint = ('Saved PI number.' if edit else
               values.get('_pi_preview_error') or 'Next number · assigned on save.')
    pi_control = inp('pi_number', 'PI number', extra='maxlength="100" readonly aria-readonly="true" placeholder="'
                     + ('Not recorded' if edit else 'Generated when saved') + '"')
    pi_control = pi_control.replace('</div>', '<small id="pi-number-status" class="entry-note">'
                                    + esc(pi_hint) + '</small></div>', 1)
    sections = [
        ("Invoice & customer", pi_control + inp("invoice_no", "Invoice number", extra='maxlength="100" placeholder="Optional — enter when billed"') + inp("invoice_date", "Invoice date", kind="date", required=True) + sel("customer_id", "Existing customer")),
        ("Sales classification", sel("channel_id", "Channel", required=True) + sel("sub_channel_id", "Subchannel") + sel("person_id", "Salesperson") + sel("brand_id", "Brand", required=True) + '<div><label for="order_type">Order type</label><select id="order_type" name="order_type">' + ''.join(f'<option{" selected" if values.get("order_type","Re-order")==x else ""}>{x}</option>' for x in ("First", "Re-order")) + '</select></div>'),
        ("Customer / invoice location", sel("state_id", "State", required=True) + sel("district_id", "District") + sel("city_id", "City")),
        ("Sales values", invoice_ui.render(options.get('product_catalog', []), values,
                         editable=writable and field_edit_access(user, 'values', 'invoice_items', existing=edit),
                         colours=options.get('product_colours', ()))
         + '<div id="summary-sales-values" class="entry-grid entry-full"' + (' hidden' if not edit or values.get('_invoice_saved') else '') + '>'
         + ''.join(inp(key, AMOUNTS[key], kind="number", extra='min="0" max="999999999999.99" step="0.01"') for key in PRIMARY_AMOUNTS)
         + '</div>'),
        ("Dispatch details", dispatch_fields),
    ]
    submission_field = ''
    if not edit:
        try:
            submission_id = str(UUID(str(values.get('submission_id', ''))))
        except (ValueError, TypeError, AttributeError):
            submission_id = str(uuid4())
        submission_field = f'<input type="hidden" name="submission_id" value="{submission_id}">'
    html = f'<div class="entry-panel"><div class="entry-panel-heading entry-form-heading"><div><h2>{header}</h2></div><span class="entry-note"><span class="entry-required">*</span> Required fields</span></div><form method="post" action="/entries/save"><input type="hidden" name="csrf_token" value="{esc(token)}"><input type="hidden" name="entry_id" value="{esc(values.get("entry_id"))}"><input type="hidden" name="revision" value="{esc(values.get("revision"))}">{submission_field}<div class="entry-form-grid"><div class="entry-form-main">'
    specs = dict(zip(SECTION_FIELDS, sections))
    steps = {'invoice': 1, 'classification': 2, 'values': 3, 'dispatch': 4}

    def render_section(section):
        title, fields = specs[section]
        if not section_access(user, section) or (limited_edit and section != 'dispatch'):
            return ''
        readonly = not writable or not any(field_edit_access(user, section, key, existing=edit) for key in SECTION_FIELDS[section])
        step = f'<span class="step" aria-hidden="true">{steps[section]:02}</span>' if section in steps else ''
        css = ' customer-location-section' if section == 'location' else ''
        return f'<fieldset class="entry-section{css}" data-entry-section="{section}"{" disabled" if readonly else ""}><legend>{step}{title}</legend><div class="entry-grid">{fields}</div></fieldset>'

    profile = '' if limited_edit else customer_ui.render(
        values, can_edit_profile=may_manage_customer(user), can_view_profile=may_view_customer(user))
    if profile:
        profile_readonly = not writable or not field_edit_access(user, 'invoice', 'new_customer', existing=edit)
        html += ('<div class="entry-customer-section"><div class="customer-invoice-column">'
                 + render_section('invoice') + render_section('classification')
                 + '</div><div class="customer-profile-column">'
                 + '<fieldset class="customer-profile-fieldset" aria-label="Customer details"'
                 + (' disabled' if profile_readonly else '') + '>' + profile + '</fieldset>'
                 + render_section('location') + '</div></div>')
    else:
        html += ''.join(render_section(section) for section in ('invoice', 'location', 'classification'))
    html += render_section('values') + render_section('dispatch')
    html += '</div></div><div class="entry-form-actions"><div class="entry-form-buttons"><a class="entry-btn" href="/entries">Cancel</a><button class="entry-btn primary" type="submit">' + ('Save changes' if edit else 'Save sales entry') + '</button></div></div></form></div>'
    if not writable:
        html = html.replace('<button class="entry-btn primary" type="submit">Save changes</button>', '')
    if limited_edit:
        context = 'Entry ' + esc(values.get('entry_id'))
        if section_access(user, 'invoice'):
            document = values.get('invoice_no') or values.get('pi_number') or ('Entry ' + str(values.get('entry_id')))
            label = 'Invoice ' if values.get('invoice_no') else 'PI ' if values.get('pi_number') else ''
            context = label + esc(document) + ' · ' + esc(format_date(values.get('invoice_date')))
            customer = next((row[1] for row in options.get('customer_id', [])
                             if str(row[0]) == str(values.get('customer_id'))), '')
            if customer:
                context += ' · ' + esc(customer)
        # Which invoice is being completed stays visible; it is not help text.
        html = html.replace(f'<div><h2>{header}</h2></div>', f'<div><h2>{header}</h2><p>{context}</p></div>', 1)
        html = html.replace('>Save changes</button>', f'>Save {edit_subject} details</button>')
        html = html.replace('<span class="step" aria-hidden="true">04</span>', '')
    if user is not None and not auth.can(user, 'registers.view'):
        html = html.replace('href="/entries">Cancel', 'href="/entries?view=new">Cancel')
    html += """<script>
    (()=>{const state=document.getElementById('state_id'),district=document.getElementById('district_id'),city=document.getElementById('city_id');
    if(!state||!district||!city||state.closest('fieldset').disabled)return;
    function filter(){for(const o of district.options){if(!o.value)continue;o.hidden=o.dataset.state!==state.value;o.disabled=o.hidden;if(o.hidden&&o.selected)district.value='';}
    for(const o of city.options){if(!o.value)continue;o.hidden=o.dataset.state!==state.value||!!district.value&&o.dataset.district!==district.value;o.disabled=o.hidden;if(o.hidden&&o.selected)city.value='';}}
    state.addEventListener('change',()=>{district.value='';city.value='';filter()});district.addEventListener('change',()=>{city.value='';filter()});city.addEventListener('change',()=>{const o=city.selectedOptions[0];if(o&&o.value){district.value=o.dataset.district||'';filter()}});filter();})();
    </script>"""
    if not edit:
        html += """<script>
        (()=>{const pi=document.getElementById('pi_number'),date=document.getElementById('invoice_date'),status=document.getElementById('pi-number-status');
        if(!pi||!date||pi.matches(':disabled'))return;let request=0,controller=null;
        date.addEventListener('change',async()=>{const current=++request;controller?.abort();controller=new AbortController();
          pi.value='';if(status)status.textContent='Checking next PI number…';
          try{const response=await fetch('/entries/pi-next?date='+encodeURIComponent(date.value),{credentials:'same-origin',headers:{Accept:'application/json'},signal:controller.signal});
            const result=await response.json();if(current!==request)return;
            if(!response.ok)throw new Error(result.message||'The next PI preview is unavailable. It will be assigned when saved.');
            pi.value=result.pi_number||'';if(status)status.textContent='Next number · assigned on save.';
          }catch(error){if(error.name!=='AbortError'&&current===request&&status)status.textContent=error.message||'The next PI preview is unavailable. It will be assigned when saved.';}
        });})();
        </script>"""
    if may_view_customer(user) and not limited_edit:
        html += customer_ui.assets()
    return html


def ledger_query(params, user=None):
    clauses, args = [], []
    scope, scope_args = channel_scope_clause(user)
    if scope:
        clauses.append(scope)
        args.extend(scope_args)
    search = str(params.get("q", "")).strip()[:200]
    if search:
        searchable = (['pi_number', 'invoice_no', 'customer'] if section_access(user, 'invoice') else [])
        if section_access(user, 'dispatch'):
            searchable.append('courier_docket_no')
        if not searchable:
            raise EntryError('Search requires access to Invoice & customer or Dispatch details.')
        clauses.append('(' + ' or '.join(f'{key} ilike %s' for key in searchable) + ')')
        args.extend(["%" + search + "%"] * len(searchable))
    dates = {}
    for key, operator in (("from", ">="), ("to", "<=")):
        if str(params.get(key, '')).strip():
            if not section_access(user, 'invoice'):
                raise EntryError('Date filters require access to Invoice & customer.')
            try:
                dates[key] = parse_date(params[key])
            except ValueError:
                raise EntryError("Use valid dates in DD-MM-YYYY format in the report filters.")
            clauses.append(f"invoice_date {operator} %s")
            args.append(dates[key])
    if len(dates) == 2 and dates["from"] > dates["to"]:
        raise EntryError("The report start date must be before the end date.")
    return (" where " + " and ".join(clauses) if clauses else ""), args


def csv_cell(value):
    if isinstance(value, date):
        return format_date(value)
    # Neutralize spreadsheet formulas in free-text fields while keeping numbers numeric.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


EXPORT_FIELDS = {
    'invoice': ('invoice_date', 'pi_number', 'invoice_no', 'line_no', 'customer'),
    'classification': ('channel', 'sub_channel', 'sales_person', 'brand', 'order_type'),
    'location': ('state', 'district', 'city'),
    'values': (*AMOUNTS, 'credit_days', 'payment_received_on'),
    'dispatch': ('stock_supplied_on', *SHIPPING_FIELDS, 'courier', 'courier_docket_no', 'remarks'),
}
REGISTER_COLUMNS = (
    ('invoice', 'invoice_date', 'Invoice date'), ('invoice', 'pi_number', 'PI number'), ('invoice', 'invoice_no', 'Invoice / line'),
    ('invoice', 'customer', 'Customer'), ('classification', 'channel', 'Channel'),
    ('classification', 'sales_person', 'Salesperson'), ('classification', 'brand', 'Brand'),
    ('location', 'state', 'State'), ('location', 'district', 'District'), ('location', 'city', 'Location'),
    ('values', 'primary_taxable', 'Before GST (₹)'),
    ('dispatch', 'weight_kg', 'Weight (kg)'), ('dispatch', 'transport_service', 'Transport service'),
    ('dispatch', 'courier', 'Courier type'), ('dispatch', 'courier_docket_no', 'Courier number'),
)
REGISTER_WIDTHS = {
    'invoice_date': 10, 'pi_number': 12, 'invoice_no': 11, 'customer': 16, 'channel': 7,
    'sales_person': 10, 'brand': 8, 'state': 8, 'district': 8, 'city': 8,
    'primary_taxable': 12, 'courier': 8, 'courier_docket_no': 9, 'actions': 10,
    'weight_kg': 7, 'transport_service': 8,
}


def register(app, current_user, login_redirect, page, invalidate):
    def denied(user, message='You do not have access to this sales operation.'):
        return HTMLResponse(page('Access denied', '<p>' + esc(message) + '</p>', user), status_code=403,
                            headers={'Cache-Control': 'no-store'})

    def valid_csrf(request, form):
        token = str(form.get('csrf_token', ''))
        return bool(token) and secrets.compare_digest(token.encode('utf-8'), request.session.get('csrf_token', '').encode('utf-8'))

    def read_record(cur, entry_id, user=None):
        try:
            entry_id = int(entry_id)
            if not 0 < entry_id <= 2147483647:
                raise ValueError
        except (ValueError, TypeError):
            raise EntryError('Choose a valid sales entry.')
        cur.execute('select * from txn.sales_entry where entry_id=%s and not is_deleted', (entry_id,))
        record = cur.fetchone()
        if not record:
            raise EntryError('The requested entry was not found.')
        values = dict(zip([d[0] for d in cur.description], record))
        assert_entry_channel(cur, values['channel_id'], user)
        values['revision'] = (values['updated_at'] or values['created_at']).isoformat()
        return values

    def render(request, user, values=None, error=None, status=200):
        can_register = auth.can(user, 'registers.view')
        can_new = may_create(user)
        can_change = may_edit(user)
        visible = visible_sections(user)
        edit_id = (values or {}).get('entry_id') or request.query_params.get('edit')
        requested_new = request.query_params.get('view') == 'new' or bool(error and not edit_id)
        delete_id = request.query_params.get('delete')
        if delete_id and not auth.can(user, 'sales.delete'):
            return denied(user)
        if edit_id and not (can_change or (can_register and visible)):
            return denied(user)
        if requested_new and not can_new:
            return denied(user, CREATE_MESSAGE)
        if not edit_id and not delete_id and not requested_new and not can_register:
            if can_new:
                return RedirectResponse('/entries?view=new', status_code=303)
            return denied(user, CREATE_MESSAGE if auth.can(user, 'sales.create') else 'You do not have access to the sales register.')
        body = STYLE + '<main class="entries-page">'
        if not pgdata.enabled():
            return HTMLResponse(page('Sales entry', body + '<div class="err">Connect PostgreSQL to use sales entry.</div></main>', user), status_code=503)
        form_open = bool(requested_new or edit_id)
        columns = [(key, label) for section, key, label in REGISTER_COLUMNS if section in visible]
        selected = ['entry_id', *[key for key, _ in columns]]
        if 'invoice' in visible:
            selected.append('line_no')
        total, taxable, gross, quantity = 0, Decimal(0), Decimal(0), Decimal(0)
        rows, pages, page_no, offset = [], 1, 1, 0
        try:
            with closing(pgdata.connect()) as conn, conn.cursor() as cur:
                if delete_id:
                    record = read_record(cur, delete_id, user)
                    identification = ((f'Invoice {record["invoice_no"]}, line {record["line_no"]}' if record.get('invoice_no')
                                       else f'PI {record["pi_number"]}' if record.get('pi_number') else f'Entry {record["entry_id"]}')
                                      if 'invoice' in visible else f'Entry {record["entry_id"]}')
                    body += f'<div class="entry-panel entry-section"><h1>Delete sales entry</h1><p>{esc(identification)}</p><p>This removes the entry from registers, dashboards and target actuals. Its history remains in the database audit trail.</p><form method="post" action="/entries/delete"><input type="hidden" name="csrf_token" value="{esc(csrf_token(request))}"><input type="hidden" name="entry_id" value="{record["entry_id"]}"><input type="hidden" name="revision" value="{esc(record["revision"])}"><label><input style="width:auto;min-height:0" type="checkbox" name="confirm" value="yes" required> I confirm deletion of this sales entry</label><p><a class="entry-btn" href="/entries">Cancel</a> <button class="entry-btn primary" type="submit">Delete sales entry</button></p></form></div></main>'
                    return HTMLResponse(page('Delete sales entry', body, user), headers={'Cache-Control': 'no-store'})
                if edit_id:
                    original = read_record(cur, edit_id, user)
                    saved_invoice = None
                    if 'values' in visible and not dispatch_only_editor(user):
                        saved_invoice = invoice_items.load_invoice(cur, original['entry_id'])
                        original.update(invoice_form_values(saved_invoice))
                        original['_invoice_snapshots'] = (saved_invoice or {}).get('items', [])
                        original['_invoice_summary_only'] = not saved_invoice and sheet_summary_only(cur, original)
                    if values is not None:
                        values = {**original, **restricted_form(values, user, original)}
                    else:
                        values = original
                else:
                    values = {**(values or {'invoice_date': date.today()}), '_invoice_saved': False, '_invoice_summary_only': False}
                option_keys = {key for section in visible for key in SECTION_FIELDS[section] if key in DIMENSIONS}
                options = load_options(cur, values, option_keys, user) if can_new or form_open else {key: [] for key in DIMENSIONS}
                if can_new and not edit_id:
                    try:
                        preview_date = parse_date(values.get('invoice_date'))
                        values['pi_number'] = pi_numbers.preview_next(cur, pi_numbers.fiscal_year(preview_date))
                    except (ValueError, pi_numbers.PIError) as exc:
                        values['pi_number'] = ''
                        values['_pi_preview_error'] = str(exc) or 'Choose a valid invoice date to preview the next PI number.'
                if form_open and 'values' in visible and not (edit_id and dispatch_only_editor(user)):
                    inactive_ids = [item['product_id'] for item in (saved_invoice or {}).get('items', [])] if edit_id else []
                    options['product_catalog'] = invoice_items.catalog_options(cur, include_inactive_ids=inactive_ids)
                    saved_colours = [item.get('colour') for item in (saved_invoice or {}).get('items', [])] if edit_id else []
                    options['product_colours'] = invoice_items.colour_options(cur, include=saved_colours)
                if can_register:
                    where, args = ledger_query(request.query_params, user)
                    sums = ',coalesce(sum(primary_taxable),0),coalesce(sum(primary_with_tax),0),coalesce(sum(primary_qty),0)' if 'values' in visible else ',0,0,0'
                    cur.execute('select count(*)' + sums + ' from rpt.v_sales' + where, args)
                    total, taxable, gross, quantity = cur.fetchone()
                    pages = max(1, ceil(total / PAGE_SIZE))
                    try:
                        page_no = max(1, min(int(request.query_params.get('page', '1')), pages))
                    except ValueError:
                        page_no = 1
                    offset = (page_no - 1) * PAGE_SIZE
                    cur.execute('select ' + ','.join(selected) + ' from rpt.v_sales' + where + ' order by invoice_date desc,entry_id desc limit %s offset %s', [*args, PAGE_SIZE, offset])
                    rows = [dict(zip(selected, row)) for row in cur.fetchall()]
        except EntryAccessError as exc:
            return denied(user, str(exc))
        except EntryError as exc:
            return HTMLResponse(page('Sales entry', STYLE + f'<div class="err">{esc(exc)}</div><p><a href="/entries">Return to sales entry</a></p>', user), status_code=400)
        except Exception:
            return HTMLResponse(page('Sales entry', STYLE + '<div class="err">Sales data is unavailable. Check the PostgreSQL connection and migrations.</div>', user), status_code=503)

        query = urlencode({key: request.query_params[key] for key in ('q', 'from', 'to') if request.query_params.get(key)})
        filtered = bool(query)
        export_url = '/entries/export.csv' + ('?' + query if query else '')
        if auth.can(user, 'dashboard.view'):
            body += '<div class="entry-breadcrumb"><a href="/">Dashboard</a><span aria-hidden="true">/</span><span>Sales operations</span></div>'
        dispatch_edit = bool(edit_id and dispatch_only_editor(user))
        heading = 'Dispatch entry' if dispatch_edit else 'Sales entries'
        intro = 'Update delivery details for this entry.' if dispatch_edit else 'Manage invoices, sales records and courier details.'
        body += f'<header class="entry-heading"><div><h1>{heading}</h1><p>{intro}</p></div><div class="entry-heading-actions">'
        if can_register and auth.can(user, 'exports.access'):
            body += f'<a class="entry-btn" href="{esc(export_url)}">{icon("download")}Export CSV</a>'
        if form_open and can_register:
            body += f'<a class="entry-btn" href="/entries">{icon("file")}View register</a>'
        elif can_new:
            body += f'<a class="entry-btn primary" href="/entries?view=new">{icon("plus")}New sales entry</a>'
        body += '</div></header>'
        if error:
            body += f'<div class="err" role="alert">{esc(error)}</div>'
        saved = request.query_params.get('saved', '')
        if saved.isdigit():
            if request.query_params.get('replayed') == '1':
                next_step = f'<a href="/entries?edit={esc(saved)}">Edit the saved entry</a> to make changes, or choose New sales entry to add another.' if can_change else 'Choose New sales entry to add another.'
                body += f'<div class="ok" role="status">This form was already saved as entry {esc(saved)}. No additional entry was created. {next_step}</div>'
            else:
                body += f'<div class="ok" role="status">Entry {esc(saved)} saved successfully. Target reports reflect your changes immediately.</div>'
        if request.query_params.get('deleted', '').isdigit():
            body += '<div class="ok" role="status">Sales entry deleted. Dashboards and reports will reflect the change.</div>'
        if can_register:
            scope = 'Matching your filters' if filtered else 'All saved entries'
            stats = [('Invoice lines', number(total, 0), 'file')]
            if 'values' in visible:
                stats += [('Sales before GST', '₹ ' + number(taxable), 'money'), ('Sales including GST', '₹ ' + number(gross), 'money'), ('Primary quantity', number(quantity, 0 if quantity == int(quantity) else 2), 'box')]
            body += '<section class="entry-stats" aria-label="Sales totals"' + (' hidden' if form_open else '') + '>'
            for label, value, symbol in stats:
                body += f'<div class="entry-stat"><div class="entry-stat-label">{icon(symbol)}{label}</div><strong>{value}</strong><small>{scope}</small></div>'
            body += '</section>'
        body += '<nav class="entry-tabs" aria-label="Sales entry views">'
        if can_register:
            body += f'<a class="entry-tab{" active" if not form_open else ""}" href="{esc(register_url(request.query_params))}">Sales register<span class="entry-count">{number(total, 0)}</span></a>'
        if can_new or form_open:
            edit_label = ('Edit dispatch' if dispatch_edit else 'Edit entry' if can_change else 'View entry') if values.get('entry_id') else 'New entry'
            tab_url = '/entries?edit=' + str(values['entry_id']) if values.get('entry_id') else '/entries?view=new'
            body += f'<a class="entry-tab{" active" if form_open else ""}" href="{esc(tab_url)}">{edit_label}</a>'
        body += '<span class="entry-format">Dates: DD-MM-YYYY &nbsp; · &nbsp; Amounts in INR</span></nav>'
        if can_new or form_open:
            body += '<section id="entry-form"' + ('' if form_open else ' hidden') + '>' + entry_form(options, values, csrf_token(request), user) + '</section>'
        if can_register:
            body += '<section id="ledger" class="entry-panel"' + (' hidden' if form_open else '') + '><div class="entry-panel-heading"><div><h2>Sales register</h2><p>Only sections included in your access are displayed.</p></div><span class="entry-note">25 entries per page</span></div><form class="ledger-filter" method="get" action="/entries">'
            if visible & {'invoice', 'dispatch'}:
                search_hint = 'PI, invoice or customer' if 'invoice' in visible else 'Courier number'
                if {'invoice', 'dispatch'} <= visible:
                    search_hint = 'PI, invoice, customer or courier number'
                body += input_field('q', 'Search entries', request.query_params, extra=f'placeholder="{search_hint}" maxlength="200"')
            if 'invoice' in visible:
                for key, label in (('from', 'From date'), ('to', 'To date')):
                    body += input_field(key, label, request.query_params, 'date')
            body += '<div class="ledger-filter-actions"><button class="entry-btn primary" type="submit">Apply filters</button>'
            if filtered:
                body += '<a class="entry-clear" href="/entries">Clear</a>'
            body += '</div></form>'
            if filtered:
                body += f'<div class="entry-filter-note">{number(total, 0)} matching invoice lines.</div>'
            actions = bool(visible or auth.can(user, 'sales.delete'))
            visible_columns = columns + ([('actions', 'Action')] if actions else [])
            total_width = sum(REGISTER_WIDTHS[key] for key, _ in visible_columns)
            body += '<div class="entry-scroll" role="region" aria-label="Sales register table"><table class="entry-table" role="table"><colgroup>'
            for key, _ in visible_columns:
                body += f'<col style="width:{REGISTER_WIDTHS[key] / total_width * 100:.3f}%">'
            body += '</colgroup><thead><tr>'
            for key, label in columns:
                body += f'<th scope="col" class="entry-col-{key}{" entry-money" if key in ("primary_taxable", "weight_kg") else ""}">{label}</th>'
            body += ('<th scope="col" class="entry-actions">Action</th>' if actions else '')
            if not visible_columns:
                body += '<th scope="col">Sales details</th>'
            body += '</tr></thead><tbody>'
            empty = '<span class="entry-empty-value">—</span>'
            for row in rows if visible_columns else []:
                entry_id = row['entry_id']
                body += f'<tr{" class=saved-row" if str(entry_id) == saved else ""}>'
                for key, column_label in columns:
                    value = row.get(key)
                    if key == 'invoice_date':
                        cell = esc(format_date(value))
                    elif key == 'invoice_no':
                        cell = f'<span class="entry-invoice">{esc(value) if value else "—"}</span><span class="entry-line">Line {esc(row.get("line_no"))}</span>'
                    elif key == 'primary_taxable':
                        cell = number(value)
                    elif key == 'weight_kg':
                        cell = number(value, 3) if value is not None else empty
                    elif key == 'courier':
                        cell = esc(COURIER_LABELS.get(str(value).casefold(), value)) or empty
                    else:
                        cell = esc(value) or empty
                    body += f'<td class="entry-col-{key}{" entry-money" if key in ("primary_taxable", "weight_kg") else ""}" data-label="{esc(column_label)}">{cell}</td>'
                if actions:
                    body += '<td class="entry-actions" data-label="Action"><div class="entry-action-list">'
                    if visible:
                        label = 'Edit dispatch' if dispatch_only_editor(user) else 'Edit' if can_change else 'View'
                        body += f'<a class="entry-edit" href="{esc(register_url(request.query_params, edit=entry_id))}" aria-label="{label} entry {entry_id}">{icon("edit")}{label}</a> '
                    if auth.can(user, 'sales.delete'):
                        body += f'<a class="entry-edit" href="/entries?delete={entry_id}" aria-label="Delete entry {entry_id}">Delete</a>'
                    body += '</div></td>'
                body += '</tr>'
            if not visible_columns:
                body += '<tr><td class="entry-empty-state"><strong>No sales sections enabled</strong>Ask your administrator for View access to the sales details you need.</td></tr>'
            elif not rows:
                body += f'<tr><td class="entry-empty-state" colspan="{len(visible_columns)}"><strong>No entries found</strong>Try another filter.</td></tr>'
            body += '</tbody></table></div><footer class="entry-pagination">'
            body += f'<span>Showing <strong>{number(offset + 1 if total else 0, 0)}–{number(offset + len(rows), 0)}</strong> of <strong>{number(total, 0)}</strong> entries</span><div class="entry-page-controls">'
            for label, target, enabled in (('Previous', page_no - 1, page_no > 1), ('Next', page_no + 1, page_no < pages)):
                if label == 'Next':
                    body += f'<span>Page <strong>{page_no}</strong> of {pages}</span>'
                body += (f'<a class="entry-btn" href="{esc(register_url(request.query_params, page=target))}">{label}</a>' if enabled else f'<span class="entry-btn" aria-disabled="true">{label}</span>')
            body += '</div></footer></section><p class="entry-footer-note">One entry per invoice line. Access restrictions also apply to CSV exports.</p>'
        body += DATE_SCRIPT + '</main>'
        return HTMLResponse(page('Sales entry', body, user), status_code=status, headers={'Cache-Control': 'no-store'})

    @app.get('/entries', response_class=HTMLResponse)
    def entries(request: Request):
        user = current_user(request)
        return render(request, user) if user else login_redirect(request)

    @app.get('/entries/pi-next')
    def next_pi_preview(request: Request):
        user = current_user(request)
        headers = {'Cache-Control': 'no-store'}
        if not user:
            return JSONResponse({'message': 'Sign in to preview the next PI number.'}, status_code=401, headers=headers)
        if not may_create(user):
            return JSONResponse({'message': 'Create sales access is required to preview a new PI number.'}, status_code=403, headers=headers)
        try:
            selected_date = parse_date(request.query_params.get('date', ''))
            fy = pi_numbers.fiscal_year(selected_date)
            with closing(pgdata.connect()) as conn, conn.cursor() as cur:
                number = pi_numbers.preview_next(cur, fy)
        except ValueError as exc:
            return JSONResponse({'message': str(exc) or 'Choose a valid invoice date.'}, status_code=400, headers=headers)
        except Exception:
            return JSONResponse({'message': 'The next PI preview is unavailable. It will be assigned when saved.'}, status_code=503, headers=headers)
        return JSONResponse({'pi_number': number, 'financial_year': fy, 'reserved': False}, headers=headers)

    def customer_failure(message, status):
        return JSONResponse({'message': message}, status_code=status,
                            headers={'Cache-Control': 'no-store'})

    def read_customer_profile(customer_id, user):
        with closing(pgdata.connect()) as conn, conn.cursor() as cur:
            return customer_details.get_profile(cur, customer_id, channels=entry_channels(user))

    @app.get('/entries/customers/{customer_id}')
    def customer_profile(request: Request, customer_id: int):
        user = current_user(request)
        if not user:
            return customer_failure('Sign in to view customer details.', 401)
        if not may_view_customer(user):
            return customer_failure('You do not have access to view customer details.', 403)
        try:
            profile = read_customer_profile(customer_id, user)
        except customer_details.CustomerError as exc:
            return customer_failure(str(exc), 404)
        except Exception:
            return customer_failure('Customer details are unavailable. Please try again.', 503)
        return JSONResponse({'customer': profile}, headers={'Cache-Control': 'no-store'})

    def persist_customer_profile(customer_id, form, user):
        with closing(pgdata.connect()) as conn, conn:
            with conn.cursor() as cur:
                details = customer_details.parse_fields(form)
                # A page opened before the contact field was installed must not
                # clear a phone number it never displayed.
                if 'customer_contact_number' not in form:
                    details.pop('contact_number', None)
                return customer_details.update_customer(
                    cur, customer_id, details,
                    {key: form.get(key) for key in ('state_id', 'district_id', 'city_id')},
                    form.get('revision'), user['username'], channels=entry_channels(user))

    @app.post('/entries/customers/{customer_id}')
    async def update_customer_profile(request: Request, customer_id: int):
        user = current_user(request)
        if not user:
            return customer_failure('Sign in to update customer details.', 401)
        if not may_manage_customer(user):
            return customer_failure('Customer updates require Invoice & customer and Location edit access.', 403)
        if request.headers.get('content-length', '').isdigit() and int(request.headers['content-length']) > 16000:
            return customer_failure('The customer details are too long.', 400)
        form = await request.form()
        if not valid_csrf(request, form):
            return customer_failure('Reload the sales entry page and try again.', 403)
        try:
            profile = await run_in_threadpool(persist_customer_profile, customer_id, form, user)
        except customer_details.CustomerError as exc:
            return customer_failure(str(exc), getattr(exc, 'status_code', 400))
        except Exception:
            return customer_failure('The customer could not be updated. Please try again.', 503)
        return JSONResponse({'customer': profile}, headers={'Cache-Control': 'no-store'})

    @app.post('/entries/save')
    async def save(request: Request):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        form = await request.form()
        editing = bool(str(form.get('entry_id', '')).strip())
        if not (may_edit(user) if editing else may_create(user)):
            return denied(user, 'You do not have access to edit sales entries.' if editing else CREATE_MESSAGE)
        if not valid_csrf(request, form):
            return denied(user, 'Reload the sales entry page and try again.')
        if not pgdata.enabled():
            return await run_in_threadpool(render, request, user)
        try:
            entry_id, replayed = await run_in_threadpool(save_entry, form, user['username'], user)
        except EntryAccessError as exc:
            return denied(user, str(exc))
        except EntryError as exc:
            return await run_in_threadpool(render, request, user, dict(form), str(exc), 400)
        except Exception as exc:
            code = getattr(exc, 'pgcode', '')
            message = ('This invoice changed while you were saving. Review it before saving again.' if code == '23505'
                       else 'The entry could not be saved. Check the values and database configuration, then retry.')
            return await run_in_threadpool(render, request, user, dict(form), message, 409 if code == '23505' else 503)
        invalidate()
        destination = '/entries?' if auth.can(user, 'registers.view') else '/entries?view=new&'
        return RedirectResponse(f'{destination}saved={entry_id}{"&replayed=1" if replayed else ""}#ledger', status_code=303)

    @app.post('/entries/delete')
    async def remove(request: Request):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        if not auth.can(user, 'sales.delete'):
            return denied(user)
        form = await request.form()
        if not valid_csrf(request, form):
            return denied(user, 'Reload the delete confirmation page and try again.')
        if form.get('confirm') != 'yes':
            return HTMLResponse(page('Confirm deletion', '<p>Confirm deletion before continuing.</p>', user), status_code=400)
        try:
            entry_id = int(form.get('entry_id', ''))
            if not 0 < entry_id <= 2147483647:
                raise ValueError
            await run_in_threadpool(delete_entry, entry_id, str(form.get('revision', '')), user['username'], user)
        except EntryAccessError as exc:
            return denied(user, str(exc))
        except (ValueError, EntryError) as exc:
            return HTMLResponse(page('Delete sales entry', '<p>' + esc(str(exc) or 'Choose a valid entry.') + '</p>', user), status_code=400)
        except Exception:
            return HTMLResponse(page('Delete unavailable', '<p>The entry could not be deleted. Check the PostgreSQL connection and try again.</p>', user), status_code=503)
        invalidate()
        return RedirectResponse(f'/entries?deleted={entry_id}' if auth.can(user, 'registers.view') else '/', status_code=303)

    @app.get('/entries/export.csv')
    def export(request: Request):
        user = current_user(request)
        if not user:
            return login_redirect(request)
        if not auth.can(user, 'registers.view') or not auth.can(user, 'exports.access'):
            return denied(user, 'Sales register and Export access are required.')
        try:
            where, args = ledger_query(request.query_params, user)
            columns = [key for section, fields in EXPORT_FIELDS.items() if section_access(user, section) for key in fields]
            columns = columns or ['entry_id']
            with closing(pgdata.connect()) as conn, conn.cursor() as cur:
                cur.execute('select ' + ','.join(columns) + ' from rpt.v_sales' + where + ' order by invoice_date,entry_id', args)
                output = io.StringIO(newline='')
                writer = csv.writer(output)
                writer.writerow(columns)
                for row in cur:
                    writer.writerow([csv_cell(v) for v in row])
            return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="sales-register.csv"', 'Cache-Control': 'no-store'})
        except EntryError as exc:
            return HTMLResponse(page('Report filters', '<p>' + esc(exc) + '</p>', user), status_code=400)
        except Exception:
            return HTMLResponse(page('Export unavailable', '<p>Check the PostgreSQL connection and migrations, then try again.</p>', user), status_code=503)


