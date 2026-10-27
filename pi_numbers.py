"""Transactional web PI numbering (fictional example: DST-PI26-001).

One counter is shared across brands for each financial year. Existing PI
strings, including brand/product splits and letter suffixes, are never edited.
The caller owns the SQL transaction; a failed sales entry rolls allocation back.
"""
from datetime import date


MAX_NUMBER = 9223372036854775806


class PIError(ValueError):
    """Safe PI validation or allocation failure."""


def fiscal_year(value):
    if not isinstance(value, date):
        raise PIError('Choose a valid invoice date before generating a PI number.')
    return value.year - (value.month < 4)


def _year(value):
    try:
        result = int(str(value))
    except (ValueError, TypeError):
        raise PIError('Choose a valid financial year for this PI.') from None
    if not 2000 <= result <= 2099:
        raise PIError('Choose a valid financial year for this PI.')
    return result


def _prefix(fy):
    return f'DST-PI{fy % 100:02d}-'


def _historical_max(cur, fy):
    prefix = _prefix(fy)
    # Scan by the PI's own prefix, not the invoice date/FY. Old PIs can be
    # invoiced later; deleted entries and A/B product variants still reserve
    # their numeric stem. Parameters never contain caller-selected regex text.
    cur.execute("select coalesce(max((regexp_match(upper(btrim(pi_number)),%s))[1]::numeric),0) "
                "from txn.sales_entry where pi_number is not null and upper(btrim(pi_number)) like %s",
                ('^' + prefix + '([0-9]+)', prefix + '%'))
    highest = int(cur.fetchone()[0])
    if highest >= MAX_NUMBER:
        raise PIError('This PI series has reached its numbering limit. Ask the administrator to review it.')
    return highest


def _formatted_next(cur, fy, last_number):
    number = max(int(last_number or 0), _historical_max(cur, fy)) + 1
    if number > MAX_NUMBER:
        raise PIError('This PI series has reached its numbering limit. Ask the administrator to review it.')
    return number, _prefix(fy) + f'{number:03d}'


def preview_next(cur, fy):
    """Read the next number without reserving or changing a counter."""
    fy = _year(fy)
    cur.execute('select 1 from master.financial_year where fy=%s', (fy,))
    if not cur.fetchone():
        raise PIError('This financial year has not been set up. Ask the administrator to add it first.')
    cur.execute('select last_number from txn.sales_pi_counter where fy=%s', (fy,))
    saved = cur.fetchone()
    return _formatted_next(cur, fy, saved[0] if saved else 0)[1]


def allocate_next(cur, fy):
    """Reserve the next number within the caller's sales-entry transaction.

    The caller must check an existing submission UUID before calling this.
    Row locking serializes simultaneous new forms; rollback returns the number
    to the series. No existing PI number is rewritten or made globally unique.
    """
    fy = _year(fy)
    cur.execute('insert into txn.sales_pi_counter(fy,last_number) values(%s,0) on conflict(fy) do nothing', (fy,))
    cur.execute('select last_number from txn.sales_pi_counter where fy=%s for update', (fy,))
    saved = cur.fetchone()
    if not saved:
        raise PIError('The PI numbering series is unavailable. Reload the sales entry form.')
    number, label = _formatted_next(cur, fy, saved[0])
    cur.execute('update txn.sales_pi_counter set last_number=%s,updated_at=clock_timestamp() where fy=%s', (number, fy))
    return label
