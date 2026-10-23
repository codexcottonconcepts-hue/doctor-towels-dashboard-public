"""Permission persistence and migration checks using isolated account files."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import auth


class PermissionsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = str(Path(self.folder.name) / "users.db")
        self.db_patch = patch.object(auth, "DB_PATH", self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        auth.init()

    def create(self, name="operator", **kwargs):
        auth.create_user(name, "TestPassword123", **kwargs)
        return auth.get_user(name)

    def test_new_account_defaults_to_viewer_without_export_or_writes(self):
        user = self.create()
        self.assertEqual(auth.permissions_for(user), auth.DEFAULT_PERMISSIONS)
        self.assertTrue(auth.can(user, "dashboard.view"))
        self.assertFalse(auth.can(user, "exports.access"))
        self.assertFalse(auth.can(user, "sales.create"))
        self.assertFalse(auth.can(user, "users.manage"))

    def test_explicit_empty_grants_persist_through_startup(self):
        self.create(permissions=[])
        auth.init()
        self.assertEqual(auth.permissions_for(auth.get_user("operator")), set())

    def test_lfo_store_permissions_are_explicit_and_independent(self):
        self.assertNotIn("lfo.sales.view", auth.DEFAULT_PERMISSIONS)
        self.assertNotIn("lfo.stores.create", auth.DEFAULT_PERMISSIONS)
        self.assertEqual(auth.normalize_permissions(["lfo.stores.create"]),
                         {"lfo.stores.create", "lfo.sales.view"})
        for action in ("create", "edit"):
            self.assertEqual(auth.normalize_permissions([f"lfo.sales.{action}"]),
                             {f"lfo.sales.{action}", "lfo.sales.view"})

    def test_section_edit_adds_own_visibility_without_other_sections(self):
        user = self.create(permissions=["sales.edit", "sales.dispatch.edit"])
        self.assertEqual(auth.permissions_for(user), {
            "sales.edit", "sales.dispatch.edit", "sales.dispatch.view",
        })
        self.assertFalse(auth.can(user, "sales.values.view"))
        self.assertFalse(auth.can(user, "sales.invoice.edit"))

    def test_create_does_not_grant_all_sales_sections(self):
        user = self.create(permissions=["sales.create", "sales.dispatch.edit"])
        self.assertFalse(auth.can(user, "sales.invoice.edit"))
        self.assertTrue(auth.can(user, "sales.create"))

    def test_courier_edit_grants_only_required_visibility_and_can_be_revoked(self):
        user = self.create(permissions=["sales.courier.edit"])
        self.assertEqual(auth.permissions_for(user), {
            "sales.courier.edit", "sales.dispatch.view", "registers.view",
        })
        self.assertFalse(auth.can(user, "sales.edit"))
        self.assertFalse(auth.can(user, "sales.dispatch.edit"))
        self.assertFalse(auth.can(user, "sales.create"))
        self.assertFalse(auth.can(user, "sales.delete"))
        self.assertFalse(auth.can(user, "sales.invoice.view"))
        self.assertFalse(auth.can(user, "sales.stock_supplied.edit"))
        auth.set_permissions("operator", ["sales.dispatch.view", "registers.view"])
        auth.init()
        self.assertFalse(auth.can(auth.get_user("operator"), "sales.courier.edit"))

    def test_stock_supplied_edit_grants_only_required_visibility(self):
        user = self.create(permissions=["sales.stock_supplied.edit"])
        self.assertEqual(auth.permissions_for(user), {
            "sales.stock_supplied.edit", "sales.dispatch.view", "registers.view",
        })
        for key in ("sales.edit", "sales.dispatch.edit", "sales.courier.edit",
                    "sales.create", "sales.delete", "exports.access", "sales.invoice.view"):
            with self.subTest(key=key):
                self.assertFalse(auth.can(user, key))
        auth.init()
        self.assertTrue(auth.can(auth.get_user("operator"), "sales.stock_supplied.edit"))

    def test_stock_supplied_and_courier_grants_can_be_revoked_independently(self):
        self.create(permissions=["sales.courier.edit", "sales.stock_supplied.edit"])
        for retained, revoked in (("sales.courier.edit", "sales.stock_supplied.edit"),
                                  ("sales.stock_supplied.edit", "sales.courier.edit")):
            with self.subTest(retained=retained):
                auth.set_permissions("operator", [retained])
                auth.init()
                user = auth.get_user("operator")
                self.assertEqual(auth.permissions_for(user), {
                    retained, "sales.dispatch.view", "registers.view",
                })
                self.assertFalse(auth.can(user, revoked))

    def test_permissions_can_be_replaced_without_changing_password(self):
        original = self.create()
        auth.set_permissions(" OPERATOR ", ["targets.edit", "exports.access"])
        updated = auth.get_user("operator")
        self.assertEqual(updated["salt"], original["salt"])
        self.assertEqual(updated["pw_hash"], original["pw_hash"])
        self.assertEqual(auth.permissions_for(updated), {
            "targets.edit", "targets.view", "exports.access",
        })
        self.assertFalse(auth.can(updated, "dashboard.view"))
        auth.set_permissions("operator", [])
        self.assertEqual(auth.permissions_for(auth.get_user("operator")), set())

    def test_admin_gets_known_permissions_only_and_disabled_admin_gets_none(self):
        user = self.create(is_admin=True, permissions=[])
        self.assertEqual(auth.permissions_for(user), auth.PERMISSION_KEYS)
        self.assertFalse(auth.can(user, "unknown.access"))
        auth.set_disabled("operator", True)
        self.assertEqual(auth.permissions_for(auth.get_user("operator")), set())

    def test_missing_and_corrupt_grants_fail_closed(self):
        self.assertFalse(auth.can(None, "dashboard.view"))
        self.assertEqual(auth.permissions_for({"is_admin": False}), set())
        for stored in (None, "not json", '{"dashboard.view": true}',
                       '["dashboard.view", "unknown.permission"]', '[7]'):
            with self.subTest(stored=stored):
                self.assertEqual(auth.permissions_for({"permissions_json": stored}), set())

    def test_invalid_permissions_rejected_before_account_changes(self):
        user = self.create(permissions=["dashboard.view"])
        for invalid in (["unknown.permission"], "dashboard.view", None, [7], [["dashboard.view"]]):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    auth.set_permissions("operator", invalid)
                self.assertEqual(auth.permissions_for(auth.get_user("operator")),
                                 auth.permissions_for(user))
        with self.assertRaises(ValueError):
            self.create(name="invalid", permissions=["unknown.permission"])
        self.assertIsNone(auth.get_user("invalid"))
        with self.assertRaises(ValueError):
            auth.set_permissions("missing", [])

    def test_listing_exposes_grants_without_password_material(self):
        self.create(permissions=["users.manage"])
        row = auth.all_users()[0]
        self.assertEqual(auth.permissions_for(row), {"users.manage"})
        self.assertNotIn("salt", row.keys())
        self.assertNotIn("pw_hash", row.keys())


class LegacyPermissionMigrationTests(unittest.TestCase):
    def test_existing_accounts_keep_read_access_and_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            db = str(Path(folder) / "legacy.db")
            with sqlite3.connect(db) as conn:
                conn.executescript("""
                    CREATE TABLE users (
                        username TEXT PRIMARY KEY, salt TEXT NOT NULL,
                        pw_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
                        disabled INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
                        last_login INTEGER
                    );
                    INSERT INTO users VALUES ('viewer', 'salt', 'hash', 0, 0, 42, 43);
                    INSERT INTO users VALUES ('administrator', 'salt2', 'hash2', 1, 0, 40, 41);
                """)
            conn.close()
            with patch.object(auth, "DB_PATH", db):
                auth.init()
                viewer = auth.get_user("viewer")
                self.assertEqual(auth.permissions_for(viewer), auth.LEGACY_PERMISSIONS)
                self.assertTrue(auth.can(viewer, "exports.access"))
                self.assertFalse(auth.can(viewer, "sales.edit"))
                self.assertEqual(viewer["salt"], "salt")
                self.assertEqual(viewer["pw_hash"], "hash")
                self.assertEqual(viewer["last_login"], 43)
                self.assertEqual(auth.permissions_for(auth.get_user("administrator")),
                                 auth.PERMISSION_KEYS)
                auth.set_permissions("viewer", ["sales.dispatch.edit"])
                auth.init()
                self.assertEqual(json.loads(auth.get_user("viewer")["permissions_json"]),
                                 ["sales.dispatch.edit", "sales.dispatch.view"])


if __name__ == "__main__":
    unittest.main()
