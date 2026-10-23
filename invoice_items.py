"""Optional catalog invoices, saved with their existing sales-entry transaction.

Rates include 18% GST. Only the parent sales_entry contributes to sales reports;
these snapshots retain the products and calculations behind its three totals.
HTTP callers remain responsible for section permissions, CSRF and parent revision.
"""
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path


PRICE_LISTS = ('common_mrp', 'north_retail', 'gifting', 'lulu_retail')
GST_RATE = Decimal('18.00')
CENT = Decimal('0.01')
MAX_VALUE = Decimal('999999999999.99')
MAX_ITEMS = 100
MAX_JSON_LENGTH = 100000
CATALOG_COLUMNS = ('product_id', 'catalog_key', 'product_name', 'unit', 'mrp',
                   *PRICE_LISTS, 'is_active')
ITEM_COLUMNS = ('product_id', 'catalog_key', 'product_name', 'unit', 'mrp',
                'price_list_rate', 'quantity', 'unit_rate', 'discount_percent',
                'gross_amount', 'discount_amount', 'taxable_amount', 'gst_amount',
                'total_amount', 'colour')


class InvoiceError(ValueError):
    """An invoice can be corrected and resubmitted without losing form values."""


def _number(value, label, *, positive=False, maximum=MAX_VALUE):
    if isinstance(value, bool) or value is None:
        raise InvoiceError(f'{label}: enter a valid number.')
    raw = str(value).strip()
    if not raw or len(raw) > 64:
        raise InvoiceError(f'{label}: enter a valid number.')
    try:
        result = Decimal(raw)
        if not result.is_finite() or result < 0 or result > maximum or (positive and result == 0):
            raise InvoiceError(f'{label}: enter a {"positive" if positive else "non-negative"} number up to {maximum}.')
        if result != result.quantize(CENT):
            raise InvoiceError(f'{label}: use no more than two decimal places.')
        return result.quantize(CENT)
    except InvalidOperation:
        raise InvoiceError(f'{label}: enter a valid number.') from None


def _product_id(value):
    if isinstance(value, bool):
        raise InvoiceError('Choose a valid product.')
    raw = str(value).strip()
    if not raw.isascii() or not raw.isdigit() or len(raw) > 10:
        raise InvoiceError('Choose a valid product.')
    result = int(raw)
    if not 0 < result <= 2147483647:
        raise InvoiceError('Choose a valid product.')
    return result


def _colour(value, position):
    """An optional colour name; blank means no colour was chosen."""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 60:
        raise InvoiceError(f'Line {position}: choose a colour from the list.')
    return value.strip() or None


def _money_string(value):
    return None if value is None else format(value, '.2f')


def _catalog_rows(cur, product_ids=None, include_inactive_ids=()):
    columns = ','.join(CATALOG_COLUMNS)
    if product_ids is not None:
        cur.execute(f'select {columns} from master.product_catalog where product_id=any(%s::integer[])',
                    (list(product_ids),))
    else:
        ids = [_product_id(value) for value in include_inactive_ids]
        cur.execute(f'select {columns} from master.product_catalog where is_active or product_id=any(%s::integer[]) '
                    'order by product_name,product_id', (ids,))
    return [dict(zip(CATALOG_COLUMNS, row)) for row in cur.fetchall()]


def catalog_options(cur, include_inactive_ids=()):
    """JSON-ready choices. The caller must require sales.values.view/edit."""
    return [{**{key: row[key] for key in ('product_id', 'catalog_key', 'product_name', 'unit', 'is_active')},
             'mrp': _money_string(row['mrp']),
             'prices': {key: _money_string(row[key]) for key in PRICE_LISTS}}
            for row in _catalog_rows(cur, include_inactive_ids=include_inactive_ids)]


def colour_options(cur, include=()):
    """Active colours, plus any inactive ones already saved on this invoice."""
    cur.execute('select name,hex from master.product_colour where is_active or name=any(%s) order by name',
                (sorted({name for name in include if name}),))
    return [{'name': name, 'hex': hex_code} for name, hex_code in cur.fetchall()]


def load_invoice(cur, entry_id):
    """Return the persisted snapshot, or None for a legacy/manual sales entry."""
    cur.execute('select price_list from txn.sales_invoice where entry_id=%s', (entry_id,))
    header = cur.fetchone()
    if header is None:
        return None
    cur.execute('select ' + ','.join(ITEM_COLUMNS) + ' from txn.sales_invoice_item '
                'where entry_id=%s order by position', (entry_id,))
    return {'entry_mode': 'invoice', 'price_list': header[0],
            'items': [dict(zip(ITEM_COLUMNS, row)) for row in cur.fetchall()]}


def prepare_invoice(cur, form, existing=None):
    """Validate untrusted item inputs; calculate all authoritative amounts.

    Existing is load_invoice's result, read after locking the sales entry. A
    missing payload never implicitly deletes saved items. Rates may be entered
    manually, including zero; missing catalog prices are never guessed.
    """
    mode = str(form.get('entry_mode', 'invoice' if existing else 'summary')).strip()
    if mode not in ('summary', 'invoice'):
        raise InvoiceError('Choose Summary entry or Product invoice.')
    if mode == 'summary':
        if existing:
            raise InvoiceError('A saved product invoice cannot be changed to a summary entry.')
        return {'entry_mode': 'summary', 'price_list': None, 'items': []}
    price_list = str(form.get('price_list', (existing or {}).get('price_list', 'common_mrp'))).strip()
    if price_list not in PRICE_LISTS:
        raise InvoiceError('Choose a valid price list.')
    raw = form.get('invoice_items')
    if isinstance(raw, str):
        if len(raw) > MAX_JSON_LENGTH:
            raise InvoiceError('The product invoice is too large.')
        try:
            raw = json.loads(raw, parse_float=Decimal)
        except (ValueError, TypeError, RecursionError):
            raise InvoiceError('Review the product lines and try again.') from None
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ITEMS:
        raise InvoiceError(f'Add between 1 and {MAX_ITEMS} product lines.')
    inputs = []
    for position, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise InvoiceError(f'Product line {position} is invalid.')
        prefix = f'Line {position} '
        inputs.append({'product_id': _product_id(item.get('product_id')),
                       'quantity': _number(item.get('quantity'), prefix + 'quantity', positive=True),
                       'unit_rate': _number(item.get('unit_rate'), prefix + 'rate'),
                       'discount_percent': _number(item.get('discount_percent', '0'), prefix + 'discount %', maximum=Decimal('100')),
                       'colour': _colour(item.get('colour'), position)})
    products = {row['product_id']: row for row in _catalog_rows(cur, sorted({item['product_id'] for item in inputs}))}
    old_by_product = {}
    for item in (existing or {}).get('items', []):
        old_by_product.setdefault(item['product_id'], item)
    chosen = {item['colour'] for item in inputs if item['colour']}
    if chosen:
        # A colour retired from the list stays valid on the invoice that saved it.
        saved = {item.get('colour') for item in (existing or {}).get('items', [])}
        allowed = {option['name'] for option in colour_options(cur, include=saved & chosen)}
        for position, values in enumerate(inputs, 1):
            if values['colour'] and values['colour'] not in allowed:
                raise InvoiceError(f'Line {position}: choose a colour from the list.')
    items = []
    with localcontext() as context:
        context.prec = 42
        for position, values in enumerate(inputs, 1):
            product = products.get(values['product_id'])
            old = old_by_product.get(values['product_id'])
            if not product or not product['is_active'] and old is None:
                raise InvoiceError(f'Line {position}: this product is unavailable. Choose an active product.')
            snapshot = old or product
            item = {**values, **{key: snapshot[key] for key in ('catalog_key', 'product_name', 'unit', 'mrp')}}
            # Price-list defaults are historical context, not a hidden override
            # of the entered rate. Preserve them on unchanged saved products.
            item['price_list_rate'] = (old.get('price_list_rate') if old and price_list == existing['price_list']
                                       else product[price_list])
            item['gross_amount'] = (values['quantity'] * values['unit_rate']).quantize(CENT, rounding=ROUND_HALF_UP)
            item['discount_amount'] = (item['gross_amount'] * values['discount_percent'] / 100).quantize(CENT, rounding=ROUND_HALF_UP)
            item['total_amount'] = item['gross_amount'] - item['discount_amount']
            item['taxable_amount'] = (item['total_amount'] / Decimal('1.18')).quantize(CENT, rounding=ROUND_HALF_UP)
            item['gst_amount'] = item['total_amount'] - item['taxable_amount']
            if item['gross_amount'] > MAX_VALUE:
                raise InvoiceError(f'Line {position}: the amount is too large.')
            items.append(item)
        totals = {'primary_qty': sum((item['quantity'] for item in items), Decimal('0.00')),
                  'primary_taxable': sum((item['taxable_amount'] for item in items), Decimal('0.00')),
                  'primary_with_tax': sum((item['total_amount'] for item in items), Decimal('0.00'))}
        if any(value > MAX_VALUE for value in totals.values()):
            raise InvoiceError('The invoice total or quantity is too large.')
    return {'entry_mode': 'invoice', 'price_list': price_list, 'items': items, **totals}


def save_invoice(cur, entry_id, prepared):
    """Persist prepared snapshots without committing the caller's transaction.

    The caller must write the parent totals first and must have checked its
    revision. Deferred database checks reconcile the parent at commit.
    """
    if prepared['entry_mode'] != 'invoice':
        cur.execute('select 1 from txn.sales_invoice where entry_id=%s', (entry_id,))
        if cur.fetchone():
            raise InvoiceError('A saved product invoice cannot be changed to a summary entry.')
        return
    cur.execute('insert into txn.sales_invoice(entry_id,price_list) values(%s,%s) '
                'on conflict(entry_id) do update set price_list=excluded.price_list,updated_at=now()',
                (entry_id, prepared['price_list']))
    cur.execute('delete from txn.sales_invoice_item where entry_id=%s', (entry_id,))
    columns = ('entry_id', 'position', *ITEM_COLUMNS)
    sql = 'insert into txn.sales_invoice_item(' + ','.join(columns) + ') values(' + ','.join(['%s'] * len(columns)) + ')'
    for position, item in enumerate(prepared['items'], 1):
        cur.execute(sql, (entry_id, position, *(item[key] for key in ITEM_COLUMNS)))


def import_catalog(cur, asset, *, dry_run=False):
    """Validate then upsert the approved asset; never touches sales or invoices.

    Use an owner/ETL cursor in a transaction. A replay updates only changed
    catalog values; old invoices retain their saved name, price and MRP.
    """
    if isinstance(asset, (str, Path)):
        asset = json.loads(Path(asset).read_text(encoding='utf-8'))
    if not isinstance(asset, dict) or asset.get('schema_version') != 1 or asset.get('tax_basis') != 'inclusive' or str(asset.get('gst_rate')) != '18.00':
        raise InvoiceError('Unsupported catalog format or GST basis.')
    products = asset.get('products')
    if not isinstance(products, list) or not products or len(products) > 10000:
        raise InvoiceError('The catalog must contain between 1 and 10000 products.')
    validated, seen = [], set()
    for product in products:
        if not isinstance(product, dict):
            raise InvoiceError('Invalid catalog product.')
        key, name = str(product.get('catalog_key', '')).strip(), str(product.get('product_name', '')).strip()
        if not key or len(key) > 100 or key in seen or not name or len(name) > 1000:
            raise InvoiceError('Catalog keys must be unique and product names must be present.')
        seen.add(key)
        prices = product.get('prices')
        if not isinstance(prices, dict) or set(prices) != set(PRICE_LISTS):
            raise InvoiceError(f'{key}: invalid price-list columns.')
        unit = product.get('unit')
        if unit is not None and (not isinstance(unit, str) or not unit.strip() or len(unit) > 50):
            raise InvoiceError(f'{key}: invalid unit.')
        price_values = [None if prices[key] is None else _number(prices[key], 'Catalog price') for key in PRICE_LISTS]
        mrp = None if product.get('mrp') is None else _number(product['mrp'], 'Catalog MRP')
        payload = json.dumps({'source_filename': asset.get('source_filename'), 'source_sha256': asset.get('source_sha256'),
                              'product': product}, ensure_ascii=False, allow_nan=False)
        validated.append((key, name, unit, mrp, *price_values, payload))
    columns = ('catalog_key', 'product_name', 'unit', 'mrp', *PRICE_LISTS, 'source_payload')
    setters = ','.join(f'{column}=excluded.{column}' for column in columns[1:])
    old = ','.join('product_catalog.' + column for column in columns[1:])
    new = ','.join('excluded.' + column for column in columns[1:])
    sql = 'insert into master.product_catalog(' + ','.join(columns) + ') values(' + ','.join(['%s'] * len(columns)) + ') ' + \
          'on conflict(catalog_key) do update set ' + setters + ',updated_at=now() where (' + old + ') is distinct from (' + new + ')'
    if not dry_run:
        for values in validated:
            cur.execute(sql, values)
    return len(validated)
