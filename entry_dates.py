"""Calendar dates for sales entries, filters, and exports.

Display dates as DD-MM-YYYY. Accept the previous ISO YYYY-MM-DD input format
as well, so existing links and submissions continue to identify the same day.
"""

from datetime import date, datetime
import re


DATE_MESSAGE = "Enter a valid date in DD-MM-YYYY format (for example, 31-01-2026)."
_DISPLAY_DATE = re.compile(r"([0-9]{2})-([0-9]{2})-([0-9]{4})")
_ISO_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")


def parse_date(value):
    """Return a date, or None for blank input; reject other date spellings.

    DD-MM-YYYY always means day, month, year. YYYY-MM-DD remains accepted for
    backward compatibility. Surrounding whitespace is ignored. Date and
    datetime objects are accepted without shifting their calendar date.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(DATE_MESSAGE)
    value = value.strip()
    if not value:
        return None

    display = _DISPLAY_DATE.fullmatch(value)
    iso = _ISO_DATE.fullmatch(value)
    if display:
        day, month, year = map(int, display.groups())
    elif iso:
        year, month, day = map(int, iso.groups())
    else:
        raise ValueError(DATE_MESSAGE)
    try:
        return date(year, month, day)
    except ValueError:
        raise ValueError(DATE_MESSAGE) from None


def format_date(value):
    """Display a valid date as DD-MM-YYYY, retaining invalid form text.

    Blank values become an empty string. Invalid input is returned as text so
    the form can show what needs correcting; callers must still HTML-escape it.
    """
    try:
        parsed = parse_date(value)
    except ValueError:
        return str(value)
    if parsed is None:
        return ""
    return f"{parsed.day:02d}-{parsed.month:02d}-{parsed.year:04d}"
