"""The sales-entry date convention must never swap a valid day and month."""

from datetime import date, datetime, timezone
import unittest

from entry_dates import format_date, parse_date


class EntryDateTests(unittest.TestCase):
    def test_day_and_month_are_unambiguous(self):
        self.assertEqual(parse_date("04-01-2026"), date(2026, 1, 4))
        self.assertEqual(parse_date("01-04-2026"), date(2026, 4, 1))
        self.assertEqual(format_date("2026-01-04"), "04-01-2026")
        self.assertEqual(format_date("2026-04-01"), "01-04-2026")

    def test_leap_year_and_calendar_validation(self):
        self.assertEqual(parse_date("29-02-2024"), date(2024, 2, 29))
        self.assertEqual(parse_date("2000-02-29"), date(2000, 2, 29))
        for value in ("29-02-2026", "29-02-1900", "31-04-2026", "00-01-2026",
                      "01-00-2026", "01-13-2026", "01-01-0000", "2026-02-30"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "DD-MM-YYYY"):
                parse_date(value)

    def test_iso_backwards_compatibility(self):
        for iso, display, expected in (
            ("2026-09-22", "22-09-2026", date(2026, 9, 22)),
            ("2024-02-29", "29-02-2024", date(2024, 2, 29)),
            ("0001-01-01", "01-01-0001", date(1, 1, 1)),
        ):
            with self.subTest(iso=iso):
                self.assertEqual(parse_date(iso), expected)
                self.assertEqual(parse_date(display), expected)
                self.assertEqual(format_date(iso), display)
                self.assertEqual(format_date(display), display)

    def test_requires_full_date_with_hyphens(self):
        for value in ("4-1-2026", "04-1-2026", "2026-1-04", "04/01/2026",
                      "01/04/2026", "04-01-26", "2026-01", "January 4, 2026",
                      "2026-01-04T12:30:00", "13-31-2026", 20260104, []):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "DD-MM-YYYY"):
                parse_date(value)

    def test_blank_input_and_surrounding_whitespace(self):
        for value in (None, "", " \t\n"):
            with self.subTest(value=value):
                self.assertIsNone(parse_date(value))
                self.assertEqual(format_date(value), "")
        self.assertEqual(parse_date(" 04-01-2026 "), date(2026, 1, 4))
        self.assertEqual(format_date(" 2026-01-04 "), "04-01-2026")

    def test_date_and_datetime_objects_keep_their_calendar_day(self):
        for value in (date(2026, 9, 22), datetime(2026, 9, 22, 23, 30),
                      datetime(2026, 9, 22, 23, 30, tzinfo=timezone.utc)):
            with self.subTest(value=value):
                self.assertEqual(parse_date(value), date(2026, 9, 22))
                self.assertIs(type(parse_date(value)), date)
                self.assertEqual(format_date(value), "22-09-2026")

    def test_invalid_form_text_is_preserved_exactly(self):
        for value in ("31-02-2026", " 04/01/2026 ", "not a date", '<invalid>'):
            with self.subTest(value=value):
                self.assertEqual(format_date(value), value)


if __name__ == "__main__":
    unittest.main()
