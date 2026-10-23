"""Private PostgreSQL configuration loading; no database connection required."""

import os
from pathlib import Path
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

import pgdata


class DatabaseConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.config = self.root / "database.url"
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.module_path = patch.object(pgdata, "__file__", str(self.root / "pgdata.py"))
        self.module_path.start()
        self.addCleanup(self.module_path.stop)
        self.driver = Mock()
        self.driver_patch = patch.object(pgdata, "psycopg2", self.driver)
        self.driver_patch.start()
        self.addCleanup(self.driver_patch.stop)

    def test_missing_configuration_rejects_connection(self):
        self.assertEqual(pgdata.dsn(), "")
        self.assertFalse(pgdata.enabled())
        with self.assertRaises(pgdata.NotConfigured):
            pgdata.connect()
        self.driver.connect.assert_not_called()

    def test_adjacent_file_supports_bom_and_whitespace(self):
        expected = "postgresql://app:test-only@localhost/example"
        self.config.write_text("\n  " + expected + "  \n", encoding="utf-8-sig")
        self.assertTrue(pgdata.enabled())
        self.assertEqual(pgdata.dsn(), expected)
        pgdata.connect()
        self.driver.connect.assert_called_once_with(expected)

    def test_environment_takes_precedence_without_reading_file(self):
        os.environ["DATABASE_URL"] = "  postgresql://environment/example  "
        self.config.write_text("file-value", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=AssertionError("must not read file")):
            self.assertTrue(pgdata.enabled())
            self.assertEqual(pgdata.dsn(), "postgresql://environment/example")

    def test_blank_environment_uses_file_and_override(self):
        self.config.write_text("default-file", encoding="utf-8")
        alternate = self.root / "private.url"
        alternate.write_text("selected-file", encoding="utf-8")
        os.environ["DATABASE_URL"] = "  "
        os.environ["DATABASE_URL_FILE"] = str(alternate)
        self.assertEqual(pgdata.dsn(), "selected-file")
        self.assertTrue(pgdata.enabled())

    def test_existing_empty_file_rejects_connection(self):
        self.config.write_text(" \n", encoding="utf-8")
        self.assertTrue(pgdata.enabled())
        with self.assertRaisesRegex(pgdata.NotConfigured, "empty"):
            pgdata.connect()
        self.driver.connect.assert_not_called()

    def test_unreadable_file_rejects_connection_without_exposing_error(self):
        self.config.write_text("secret-connection-string", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=PermissionError("secret-error-detail")):
            self.assertTrue(pgdata.enabled())
            with self.assertRaises(pgdata.NotConfigured) as caught:
                pgdata.dsn()
        self.assertIn("permissions", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_unreadable_parent_does_not_hide_configuration(self):
        with patch.object(Path, "stat", side_effect=PermissionError("private-path")):
            self.assertTrue(pgdata.enabled())

    def test_invalid_encoding_fails_without_exposing_bytes(self):
        self.config.write_bytes(b"\xffsecret")
        self.assertTrue(pgdata.enabled())
        with self.assertRaises(pgdata.NotConfigured) as caught:
            pgdata.dsn()
        self.assertNotIn("secret", str(caught.exception))

    def test_driver_absent_disables_database_and_explains_installation(self):
        self.config.write_text("configured", encoding="utf-8")
        with patch.object(pgdata, "psycopg2", None):
            self.assertFalse(pgdata.enabled())
            with self.assertRaisesRegex(pgdata.NotConfigured, "psycopg2"):
                pgdata.connect()


class DashboardRoundingTests(unittest.TestCase):
    def test_half_units_match_spreadsheet_dashboard_without_changing_exact_ledger(self):
        cursor = Mock()
        cursor.fetchall.return_value = [('Jun-2026', 'LFO', 'Person', 'State', 'City', 'Brand', 'Sub', 'Week I', Decimal('2.50'), Decimal('840.50'), Decimal('881.50'), 2026, 'Supplier Name')]
        context = Mock()
        context.__enter__ = Mock(return_value=cursor)
        context.__exit__ = Mock(return_value=False)
        connection = Mock()
        connection.cursor.return_value = context
        row = pgdata.fetch_rows(connection)[0]
        self.assertEqual(row[8:11], [2,840,882])
        self.assertEqual(row[11:], [2026, 'Supplier Name'])
        self.assertNotIn('round(', pgdata.ROWS_SQL.lower())


if __name__ == "__main__":
    unittest.main()
