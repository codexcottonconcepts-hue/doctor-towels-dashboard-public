"""Conflict-safe, one-way Direct Sales worksheet -> PostgreSQL synchronization.

The caller downloads a complete workbook and supplies an application-role
connection. This module never reads credentials, downloads data, or deletes rows.
sync_workbook owns its transaction. A dry run performs no INSERT/UPDATE operations.
S.NO must remain unique and permanent even when the worksheet is sorted.
"""
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json

from psycopg2.extras import Json, RealDictCursor

from pgdb.etl.load_sales import (STATE_FIX, CITY_FIX, CITY_STATE_FIX, CITY_DISTRICT,
    CHANNEL_FIX, SUB_CHANNEL_FIX, ORDER_FIX, COURIER_FIX, DEFAULT_ZONE, NOT_PEOPLE)

SHEET = 'Direct Sales Report'
HEADERS = {
    'source_key': ('S.NO',), 'invoice_date': ('Tax Invoice Date', 'Invoice Date'),
    'pi_number': ('PI Number',), 'invoice_no': ('Tax Invoice No.', 'Tax Invoice No'),
    'customer': ('Supplier Name', 'Customer Name'), 'channel': ('Channels', 'Region'),
    'person': ('Execuitve', 'Executive'), 'city': ('City',), 'state': ('State',),
    'sub_channel': ('SUB Channels', 'Sub Channels', 'Direct /Distribution /Group'),
    'brand': ('DT / TT', 'Company'), 'order_type': ('Order',),
    'primary_qty': ('DT/TT Primary Order qty',),
    'primary_taxable': ('DT/TT Primary Taxable Value ( Without Tax)',),
    'primary_with_tax': ('DT/TT Primary Sale Value (With Tax)',),
    'secondary_qty': ('D2R Secondary Order Qty',),
    'secondary_value': ('D2R Secondary Sale Value',),
    'tertiary_nos': ('R2C Tertiary Sale Nos',),
    'tertiary_value': ('R2C Tertiary Sale Value',),
    'credit_days': ('Credit Period',), 'stock_supplied_on': ('Stock Supplied Date',),
    'payment_received_on': ('Payment Recieved Date', 'Payment Received Date'),
    'courier': ('Courier Type', 'Courier', 'Couries Type'),
    'courier_docket_no': ('Courier Number', 'Docket No', 'Docket Number', 'AWB', 'AWB No', 'Couries Number'),
    'remarks': ('Remarks',),
}
OPTIONAL_HEADERS = {'courier', 'courier_docket_no', 'remarks'}
NUMERIC_FIELDS = ('primary_qty', 'primary_taxable', 'primary_with_tax', 'secondary_qty',
                  'secondary_value', 'tertiary_nos', 'tertiary_value')
DATE_FIELDS = ('invoice_date', 'stock_supplied_on', 'payment_received_on')
GEO_FIELDS = {'city_id', 'state_id', 'district_id'}
INVOICE_FIELDS = {'invoice_no', 'brand_id'}


class SyncValidationError(ValueError):
    """Contains safe, non-PII issues suitable for operational status displays."""
    def __init__(self, issues, *, diagnostics=None):
        self.issues = issues
        self.diagnostics = {**(diagnostics or {})}
        if 'ignored_rows' in self.diagnostics:
            self.diagnostics['ignored_rows'] = [dict(row) for row in self.diagnostics['ignored_rows']]
        self.public_message = 'Direct Sales validation failed: ' + '; '.join(
            f"S.NO {x.get('source_key', '?')}: {x['code']}"
            + (f" (worksheet rows {', '.join(map(str, x['sheet_rows']))})" if x.get('sheet_rows')
               else f" (worksheet row {x['sheet_row']})" if x.get('sheet_row') else '')
            for x in issues[:10])
        if len(issues) > 10:
            self.public_message += f"; {len(issues) - 10} more issue(s)"
        super().__init__(self.public_message)


def clean(value):
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip() or None


def number(value):
    if value is None or value == '':
        return Decimal('0.00')
    if isinstance(value, bool):
        raise ValueError('invalid_number')
    try:
        result = Decimal(str(value).replace(',', '').replace('\u20b9', '').strip())
        if not result.is_finite() or result < 0 or result >= Decimal('1000000000000'):
            raise ValueError('invalid_number')
        return result.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError('invalid_number') from None


def parse_date(value, required=False):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if clean(value) is None:
        if required:
            raise ValueError('missing_invoice_date')
        return None
    for fmt in ('%d-%m-%Y', '%d/%m/%Y', '%Y-%m-%d', '%d-%b-%Y', '%d.%m.%Y'):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError('invalid_date')


def parse_workbook(workbook, *, diagnostics=None):
    """Validate the whole source before any database writes or master creation."""
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.update(source_rows=0, invoice_rows=0, ignored_blank_rows=0, ignored_rows=[])
    if SHEET not in workbook.sheetnames:
        raise SyncValidationError([{'code': 'missing_direct_sales_sheet'}], diagnostics=diagnostics)
    rows = iter(workbook[SHEET].iter_rows(values_only=True))
    header = next(rows, ())
    indices = defaultdict(list)
    for i, value in enumerate(header):
        if clean(value):
            indices[' '.join(clean(value).split()).casefold()].append(i)
    columns, issues = {}, []
    for field, aliases in HEADERS.items():
        matches = {i for alias in aliases for i in indices.get(' '.join(alias.split()).casefold(), [])}
        if len(matches) > 1:
            issues.append({'code': 'duplicate_column', 'field': field})
        elif matches:
            columns[field] = matches.pop()
        elif field not in OPTIONAL_HEADERS:
            issues.append({'code': 'missing_column', 'field': field})
    if issues:
        raise SyncValidationError(issues, diagnostics=diagnostics)
    parsed, seen = [], {}
    for sheet_row, row in enumerate(rows, start=2):
        values = {field: row[i] if i < len(row) else None for field, i in columns.items()}
        if not any(clean(value) is not None for value in values.values()):
            continue
        diagnostics['source_rows'] += 1
        diagnostics['invoice_rows'] += int(clean(values.get('invoice_no')) is not None)
        key = clean(values.get('source_key'))
        # Positive numeric serial numbers are deliberately required. Arbitrary
        # malformed text is not echoed back in error messages or logs.
        try:
            serial = Decimal(key or '')
            if not serial.is_finite() or serial != serial.to_integral() or not 0 < serial < 10**15:
                raise InvalidOperation
            key = str(int(serial))
        except (InvalidOperation, ValueError):
            issues.append({'code': 'missing_or_invalid_source_key', 'sheet_row': sheet_row})
            continue
        # The sheet pre-numbers future rows. Only a valid serial with every
        # other worksheet cell empty is a placeholder, including unknown columns.
        # Do not reserve its serial: a real invoice with that key must still be
        # validated, and duplicate real rows must still reject the whole source.
        if not any(clean(value) is not None for i, value in enumerate(row)
                   if i != columns['source_key']):
            diagnostics['ignored_blank_rows'] += 1
            diagnostics['ignored_rows'].append({'source_key': key, 'sheet_row': sheet_row})
            continue
        if key in seen:
            issues.append({'source_key': key, 'code': 'duplicate_source_key',
                           'sheet_row': sheet_row, 'sheet_rows': [seen[key], sheet_row]})
            continue
        seen[key] = sheet_row
        try:
            values = {k: clean(v) for k, v in values.items()}
            values['source_key'] = key
            for field in ('invoice_no', 'customer', 'channel', 'brand', 'state'):
                if not values[field]:
                    raise ValueError('missing_' + field)
            for field in DATE_FIELDS:
                values[field] = parse_date(row[columns[field]], field == 'invoice_date')
            for field in NUMERIC_FIELDS:
                values[field] = number(row[columns[field]])
            credit = values['credit_days']
            if credit is not None:
                credit_decimal = Decimal(credit)
                if not credit_decimal.is_finite() or credit_decimal != credit_decimal.to_integral() or not 0 <= credit_decimal <= 32767:
                    raise ValueError('invalid_credit_days')
                values['credit_days'] = int(credit_decimal) or None
            if values['stock_supplied_on'] and values['payment_received_on'] and values['payment_received_on'] < values['stock_supplied_on']:
                raise ValueError('payment_before_dispatch')
            order = (values['order_type'] or 'Re-order').casefold()
            if order not in ORDER_FIX:
                raise ValueError('invalid_order_type')
            values['order_type'] = ORDER_FIX[order]
            for field, fixes in (('state', STATE_FIX), ('city', CITY_FIX), ('channel', CHANNEL_FIX),
                                 ('sub_channel', SUB_CHANNEL_FIX), ('courier', COURIER_FIX)):
                if values.get(field):
                    values[field] = fixes.get(values[field].casefold(), values[field])
            if values['city'] and values['city'].casefold() in CITY_STATE_FIX:
                values['state'] = CITY_STATE_FIX[values['city'].casefold()]
            year = values['invoice_date'].year - (values['invoice_date'].month < 4)
            if not 2000 <= year <= 2099:
                raise ValueError('invalid_financial_year')
            parsed.append(values)
        except (ValueError, InvalidOperation) as exc:
            code = str(exc) if isinstance(exc, ValueError) else 'invalid_credit_days'
            issues.append({'source_key': key, 'code': code, 'sheet_row': sheet_row})
    if not parsed and not issues:
        issues.append({'code': 'empty_source'})
    if issues:
        raise SyncValidationError(issues, diagnostics=diagnostics)
    return parsed


def canonical(payload):
    result = {}
    for k, v in payload.items():
        if k in NUMERIC_FIELDS and v is not None:
            result[k] = str(Decimal(str(v)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
        else:
            result[k] = v.isoformat() if isinstance(v, date) else str(v) if isinstance(v, Decimal) else v
    return result


def fingerprint(payload):
    return hashlib.sha256(json.dumps(canonical(payload), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def reconcile_source_keys(resolved, links):
    """Plan one-to-one S.NO moves against accepted source data, never live edits.

    Sheets may renumber S.NO after deleting blank rows. Reserve unchanged links
    first, then match complete accepted payloads. Amount and executive changes
    are allowed only when all other fields agree and that identity is unique
    in both complete sets. Ambiguous invoice lines remain for review.
    """
    sources = {key: canonical(payload) for key, payload in resolved}
    baselines = {key: canonical(link['source_payload']) for key, link in links.items()}

    def nonfinancial_identity(payload):
        if not all(payload.get(field) for field in
                   ('invoice_no', 'brand_id', 'invoice_date', 'customer_id', 'channel_id')):
            return None
        return fingerprint({field: value for field, value in payload.items()
                            if field not in (*NUMERIC_FIELDS, 'person_id')})

    assigned, remaining = {}, set(links)
    for key, source in sources.items():
        if key in baselines and source == baselines[key]:
            assigned[key] = key
            remaining.remove(key)
    # Retain a stable S.NO with ordinary amount edits before looking at new
    # serials; an additional SKU must not steal an existing line's identity.
    for key, source in sources.items():
        if key not in assigned and key in remaining:
            signature = nonfinancial_identity(source)
            if signature is not None and signature == nonfinancial_identity(baselines[key]):
                assigned[key] = key
                remaining.remove(key)
    by_payload, source_payloads = defaultdict(list), defaultdict(list)
    for old in remaining:
        by_payload[fingerprint(baselines[old])].append(old)
    for key, source in sources.items():
        if key not in assigned:
            source_payloads[fingerprint(source)].append(key)
    errors = []
    for signature, keys in source_payloads.items():
        candidates = by_payload.get(signature, [])
        if not candidates:
            continue
        if len(keys) != 1 or len(candidates) != 1:
            errors.extend({'source_key': key, 'code': 'ambiguous_source_identity'} for key in keys)
            continue
        assigned[keys[0]] = candidates[0]
        remaining.remove(candidates[0])

    by_identity, source_identities = defaultdict(list), defaultdict(list)
    for old, baseline in baselines.items():
        signature = nonfinancial_identity(baseline)
        if signature is not None:
            by_identity[signature].append(old)
    for key, source in sources.items():
        signature = nonfinancial_identity(source)
        if signature is not None:
            source_identities[signature].append(key)
    for signature, keys in source_identities.items():
        candidates = by_identity.get(signature, [])
        if len(keys) == len(candidates) == 1:
            key, old = keys[0], candidates[0]
            if key not in assigned and old in remaining:
                assigned[key] = old
                remaining.remove(old)
        elif any(old in remaining for old in candidates):
            # Existing same-key financial edits still use the normal merge.
            # A new serial cannot guess which repeated invoice line it is.
            errors.extend({'source_key': key, 'code': 'ambiguous_source_identity'}
                          for key in keys if key not in assigned and key not in links)
    moves = [(old, key, links[old]['entry_id']) for key, old in assigned.items() if old != key]
    moved_keys = {old for old, _, _ in moves}
    for old, new, _ in moves:
        if new in links and new not in moved_keys:
            errors.append({'source_key': new, 'code': 'source_key_collision'})
    if errors:
        raise SyncValidationError(errors)
    remapped = {key: dict(link) for key, link in links.items() if key not in moved_keys}
    for old, new, _ in moves:
        remapped[new] = {**links[old], 'source_key': new}
    return remapped, moves


def merge_payload(baseline, source, current):
    """Merge independent field edits, keeping same-field conflicts for review."""
    baseline, source, current = map(canonical, (baseline, source, current))
    changed = {field for field in source if field not in baseline or source[field] != baseline[field]}
    conflicts = {field for field in changed if field in baseline and
                 current.get(field) != baseline[field] and current.get(field) != source[field]}
    conflicts |= {field for field in changed if field not in baseline and
                  current.get(field) is not None and current.get(field) != source[field]}
    # Geography is coupled by a trigger, so merging independent geo edits would
    # otherwise silently undo the dashboard's city/state change.
    if changed & GEO_FIELDS and any(current.get(f) != baseline.get(f) for f in GEO_FIELDS):
        if any(current.get(f) != source.get(f) for f in GEO_FIELDS):
            conflicts |= changed & GEO_FIELDS
    return {field: source[field] for field in changed if current.get(field) != source[field]}, sorted(conflicts)


class Masters:
    """Get or create without updating existing reference rows; dry run is read-only."""
    def __init__(self, cur, dry_run):
        self.cur, self.dry_run, self.maps, self.synthetic = cur, dry_run, {}, -1

    def get(self, table, id_column, fields, keys):
        signature = (table, tuple((k, str(fields[k]).casefold()) for k in keys))
        if signature in self.maps:
            return self.maps[signature]
        where, params = [], []
        for key in keys:
            if isinstance(fields[key], str):
                where.append(f'lower({key})=lower(%s)')
            else:
                where.append(f'{key}=%s')
            params.append(fields[key])
        query = f"select {id_column} from master.{table} where {' and '.join(where)}"
        self.cur.execute(query, params)
        found = self.cur.fetchone()
        if found:
            identity = found[0]
        elif self.dry_run:
            identity = self.synthetic
            self.synthetic -= 1
        else:
            self.cur.execute(f"insert into master.{table}({','.join(fields)}) values ({','.join(['%s'] * len(fields))}) on conflict do nothing returning {id_column}", list(fields.values()))
            found = self.cur.fetchone()
            if not found:
                self.cur.execute(query, params)
                found = self.cur.fetchone()
            if not found:
                raise SyncValidationError([{'code': 'conflicting_master_value', 'field': table}])
            identity = found[0]
        self.maps[signature] = identity
        return identity

    def payload(self, row):
        get = self.get
        state = get('state', 'state_id', {'name': row['state'], 'zone': DEFAULT_ZONE.get(row['state'], 'Unassigned')}, ['name'])
        city, district = None, None
        if row['city']:
            district_name = CITY_DISTRICT.get(row['city'].casefold())
            if district_name:
                district = get('district', 'district_id', {'state_id': state, 'name': district_name}, ['state_id', 'name'])
            city = get('city', 'city_id', {'state_id': state, 'name': row['city'], 'district_id': district}, ['state_id', 'name'])
            if city > 0:
                self.cur.execute('select district_id from master.city where city_id=%s', (city,))
                district = self.cur.fetchone()[0]
        channel = get('channel', 'channel_id', {'code': row['channel'], 'name': row['channel']}, ['code'])
        sub = get('sub_channel', 'sub_channel_id', {'code': row['sub_channel'], 'name': row['sub_channel'], 'channel_id': channel}, ['code']) if row['sub_channel'] else None
        person = get('sales_person', 'person_id', {'full_name': row['person'], 'channel_id': channel, 'is_real_person': row['person'].casefold() not in NOT_PEOPLE}, ['full_name']) if row['person'] else None
        brand = get('brand', 'brand_id', {'name': row['brand'], 'code': 'GS_' + hashlib.sha256(row['brand'].casefold().encode()).hexdigest()[:12]}, ['name'])
        customer = get('customer', 'customer_id', {'name': row['customer'], 'city_id': city, 'sub_channel_id': sub}, ['name'])
        year = row['invoice_date'].year - (row['invoice_date'].month < 4)
        get('financial_year', 'fy', {'fy': year, 'label': f'FY {year}-{str(year + 1)[-2:]}',
            'start_date': date(year, 4, 1), 'end_date': date(year + 1, 3, 31)}, ['fy'])
        payload = {field: row[field] for field in (*DATE_FIELDS, *NUMERIC_FIELDS, 'invoice_no', 'pi_number', 'order_type', 'credit_days')}
        payload.update(customer_id=customer, channel_id=channel, sub_channel_id=sub, person_id=person,
                       brand_id=brand, city_id=city, state_id=state, district_id=district)
        if 'courier' in row:
            payload['courier_id'] = get('courier', 'courier_id', {'code': row['courier'], 'name': row['courier']}, ['code']) if row['courier'] else None
        for field in ('courier_docket_no', 'remarks'):
            if field in row:
                payload[field] = row[field]
        return canonical(payload)


def sync_workbook(workbook, source_id, conn, dry_run=False):
    """Validate, attach legacy rows, and atomically apply conflict-free sheet edits.

    Invalid/ambiguous sources roll back the transaction. Concurrent dashboard
    edits are protected by row locks; same-field conflicts leave that row intact.
    A missing sheet row never deletes its database counterpart.
    """
    if not source_id or len(source_id) > 300:
        raise ValueError('A stable source_id is required')
    diagnostics = {}
    rows = parse_workbook(workbook, diagnostics=diagnostics)
    result = dict(source_rows=len(rows), attached=0, bootstrap_preserved=0, inserted=0,
                  updated=0, unchanged=0, preserved_local=0, conflicts=0, missing_source=0, relinked=0,
                  source_total_rows=diagnostics['source_rows'], invoice_rows=diagnostics['invoice_rows'],
                  ignored_blank_rows=diagnostics['ignored_blank_rows'],
                  dry_run=bool(dry_run), issues=[{'code': 'serial_only_placeholder', **row}
                                               for row in diagnostics['ignored_rows']], busy=False)
    with conn:
        with conn.cursor() as cur:
            cur.execute("select pg_try_advisory_xact_lock(hashtext('direct-sales-sheet-sync'))")
            if not cur.fetchone()[0]:
                result['busy'] = True
                return result
            cur.execute("select set_config('app.username','google_sheet_sync',true)")
            cur.execute("set local lock_timeout='5s'")
            masters = Masters(cur, dry_run)
            resolved = [(row['source_key'], masters.payload(row)) for row in rows]
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('select * from aux.sheet_sales_row' + ('' if dry_run else ' for update'))
            all_links = list(cur.fetchall())
            links = {r['source_key']: r for r in all_links if r['source_id'] == source_id}
            try:
                links, key_moves = reconcile_source_keys(resolved, links)
            except SyncValidationError as exc:
                raise SyncValidationError(exc.issues, diagnostics=diagnostics) from None
            result['relinked'] = len(key_moves)
            result['issues'].extend({'source_key': new, 'previous_source_key': old,
                                     'code': 'source_key_relinked'} for old, new, _ in key_moves)
            claimed = {r['entry_id'] for r in all_links}
            cur.execute('select * from txn.sales_entry order by entry_id' + ('' if dry_run else ' for update'))
            existing = {r['entry_id']: dict(r) for r in cur.fetchall()}
            available = {i: r for i, r in existing.items() if i not in claimed and r['source'] == 'sheets_migration' and not r['is_deleted']}
            history = defaultdict(list)
            if available:
                cur.execute("select row_pk,old_row from sec.audit_log where table_name='txn.sales_entry' and action='UPDATE' and old_row is not null")
                for audit in cur.fetchall():
                    if audit['row_pk'].isdigit() and int(audit['row_pk']) in available:
                        history[int(audit['row_pk'])].append(canonical(audit['old_row']))
            initial = not links
            resolved_keys = {key for key, _ in resolved}
            blank_rows = defaultdict(list)
            for row in diagnostics['ignored_rows']:
                blank_rows[row['source_key']].append(row['sheet_row'])
            # A linked invoice reduced to a serial-only placeholder remains
            # present in the source. Retain its ledger row and accepted baseline
            # and report the cleared source separately from an absent sheet row.
            result['missing_source'] = len(set(links) - resolved_keys - set(blank_rows))
            blank_linked_keys = (set(links) & set(blank_rows)) - resolved_keys
            for key in sorted(blank_linked_keys, key=int):
                result['conflicts'] += 1
                result['issues'].append({'source_key': key, 'code': 'linked_source_row_blank',
                                         'sheet_rows': blank_rows[key]})
            plan, errors = [], []
            for key, source in resolved:
                link = links.get(key)
                if link:
                    current = existing[link['entry_id']]
                    if current['is_deleted']:
                        result['conflicts'] += 1
                        result['issues'].append({'source_key': key, 'code': 'database_row_deleted'})
                        continue
                    changes, conflicts = merge_payload(link['source_payload'], source, current)
                    if conflicts:
                        result['conflicts'] += 1
                        result['issues'].append({'source_key': key, 'code': 'concurrent_edit', 'fields': conflicts})
                        continue
                    if changes:
                        plan.append(('update', key, source, current['entry_id'], changes))
                        result['updated'] += 1
                    else:
                        plan.append(('accept', key, source, current['entry_id'], {}))
                        result['unchanged'] += 1
                        if any(canonical(current).get(k) != v for k, v in source.items()):
                            result['preserved_local'] += 1
                    continue
                candidates = [r for r in available.values() if r['invoice_no'] and r['invoice_no'].casefold() == source['invoice_no'].casefold() and r['brand_id'] == source['brand_id']]
                exact = [r for r in candidates if all(canonical(r).get(k) == v for k, v in source.items())]
                historical = [r for r in candidates if any(all(old.get(k) == v for k, v in source.items()) for old in history[r['entry_id']])]
                identity = [r for r in candidates if canonical(r)['invoice_date'] == source['invoice_date'] and r['customer_id'] == source['customer_id']]
                match = exact[0] if len(exact) == 1 else historical[0] if len(historical) == 1 else identity[0] if len(identity) == 1 else None
                if match:
                    del available[match['entry_id']]
                    result['attached'] += 1
                    result['bootstrap_preserved'] += int(not exact)
                    plan.append(('attach', key, source, match['entry_id'], {}))
                elif candidates:
                    errors.append({'source_key': key, 'code': 'ambiguous_legacy_match'})
                elif any(r['invoice_no'] and r['invoice_no'].casefold() == source['invoice_no'].casefold() and r['brand_id'] == source['brand_id'] and
                         (r['source'] != 'sheets_migration' or r['is_deleted'] or result['missing_source']
                          or blank_linked_keys) for r in existing.values()):
                    # An unrecognized S.NO using an already-linked invoice might
                    # be a renumbered existing line; never silently duplicate it.
                    errors.append({'source_key': key, 'code': 'existing_invoice_requires_review'})
                else:
                    result['inserted'] += 1
                    plan.append(('insert', key, source, None, {}))
            # On first activation every historical sheet row must be accounted
            # for. This catches truncated exports and edited invoice identifiers.
            if initial and available:
                errors.append({'code': 'unmatched_legacy_rows', 'count': len(available)})
            if errors:
                raise SyncValidationError(errors, diagnostics=diagnostics)
            if dry_run:
                return result
            # Stage moves before final keys so swaps and longer renumbering
            # cycles respect the unique source key constraint atomically.
            temporary = []
            for old, new, entry_id in key_moves:
                temp = '__sync_rekey__' + str(entry_id)
                if temp in links:
                    raise SyncValidationError([{'source_key': new, 'code': 'source_key_collision'}])
                cur.execute('update aux.sheet_sales_row set source_key=%s where source_id=%s and source_key=%s and entry_id=%s',
                            (temp, source_id, old, entry_id))
                if cur.rowcount != 1:
                    raise SyncValidationError([{'source_key': new, 'code': 'source_key_collision'}])
                temporary.append((temp, new, entry_id))
            for temp, new, entry_id in temporary:
                cur.execute('update aux.sheet_sales_row set source_key=%s where source_id=%s and source_key=%s and entry_id=%s',
                            (new, source_id, temp, entry_id))
                if cur.rowcount != 1:
                    raise SyncValidationError([{'source_key': new, 'code': 'source_key_collision'}])
            for action, key, source, entry_id, changes in plan:
                if action == 'insert':
                    cur.execute('select pg_advisory_xact_lock(hashtext(lower(%s)), %s)', (source['invoice_no'], source['brand_id']))
                    cur.execute('select coalesce(max(line_no),0)+1 as n from txn.sales_entry where lower(invoice_no)=lower(%s) and brand_id=%s', (source['invoice_no'], source['brand_id']))
                    line = cur.fetchone()['n']
                    fields = {**source, 'line_no': line, 'source': 'sheets_migration'}
                    cur.execute(f"insert into txn.sales_entry({','.join(fields)}) values ({','.join(['%s'] * len(fields))}) returning entry_id", list(fields.values()))
                    entry_id = cur.fetchone()['entry_id']
                elif action == 'update':
                    if changes.keys() & INVOICE_FIELDS:
                        # Independent dashboard changes may retain the current
                        # invoice or brand. Allocate against the merged result,
                        # using the same lock namespace as dashboard saves.
                        invoice = changes.get('invoice_no', existing[entry_id]['invoice_no'])
                        brand = changes.get('brand_id', existing[entry_id]['brand_id'])
                        cur.execute('select pg_advisory_xact_lock(hashtext(lower(%s)), %s)', (invoice, brand))
                        cur.execute('select coalesce(max(line_no),0)+1 as n from txn.sales_entry where lower(invoice_no)=lower(%s) and brand_id=%s and entry_id<>%s', (invoice, brand, entry_id))
                        changes['line_no'] = cur.fetchone()['n']
                    cur.execute(f"update txn.sales_entry set {','.join(f'{f}=%s' for f in changes)} where entry_id=%s", [*changes.values(), entry_id])
                cur.execute('insert into aux.sheet_sales_row(source_id,source_key,entry_id,source_payload,source_fingerprint) values (%s,%s,%s,%s,%s) on conflict (source_id,source_key) do update set source_payload=excluded.source_payload,source_fingerprint=excluded.source_fingerprint,checked_at=now()',
                            (source_id, key, entry_id, Json(source), fingerprint(source)))
    if key_moves:
        # Only committed, non-financial identifiers are recorded in the normal
        # server log; failed transactions must never claim a completed repair.
        print(json.dumps({'event': 'direct_sales_source_keys_relinked',
                          'moves': [{'previous_source_key': old, 'source_key': new,
                                     'entry_id': entry_id} for old, new, entry_id in key_moves]}), flush=True)
    return result
