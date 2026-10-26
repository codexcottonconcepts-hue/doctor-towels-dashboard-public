"""The administrator sync action requires both admin access and a session CSRF token."""

import re
import unittest
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

import sheet_sync_web


class SheetSyncAccessTests(unittest.TestCase):
    def setUp(self):
        self.user = {"username": "admin", "is_admin": True}
        self.status = {"state": "success", "enabled": True, "interval_seconds": 120,
                       "last_success": "2026-09-23T08:30:00+00:00",
                       "last_attempt": "2026-09-23T08:30:00+00:00",
                       "counters": {"source_rows": 767, "updated": 2},
                       "error": None, "issues": []}
        self.manager = MagicMock()
        self.manager.snapshot.side_effect = lambda: self.status
        self.invalidate = MagicMock()
        self.app = FastAPI()
        self.app.add_middleware(SessionMiddleware, secret_key="isolated-sales-sync-web-test")
        sheet_sync_web.register(self.app, lambda request: self.user,
                                lambda request: RedirectResponse("/login", status_code=303),
                                lambda title, body, user: f"<html><title>{title}</title><body>{body}</body></html>",
                                self.manager, self.invalidate)
        self.client = TestClient(self.app, follow_redirects=False)
        self.addCleanup(self.client.close)

    def token(self, client=None):
        response = (client or self.client).get("/admin/sales-sync")
        self.assertEqual(response.status_code, 200)
        return re.search(r'name="csrf" value="([^"]+)"', response.text).group(1)

    def test_anonymous_get_and_post_redirect_without_exposing_status(self):
        self.user = None
        for method in (self.client.get, self.client.post):
            response = method("/admin/sales-sync")
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/login")
        self.manager.snapshot.assert_not_called()
        self.manager.sync_once.assert_not_called()

    def test_delegated_user_manager_cannot_trigger_business_sync(self):
        self.user = {"username": "manager", "is_admin": False,
                     "permissions_json": '["users.manage","dashboard.view"]'}
        for method in (self.client.get, self.client.post):
            response = method("/admin/sales-sync")
            self.assertEqual(response.status_code, 403)
        self.manager.snapshot.assert_not_called()
        self.manager.sync_once.assert_not_called()

    def test_get_is_read_only_and_disables_cache(self):
        response = self.client.get("/admin/sales-sync")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("Direct Sales Report", response.text)
        self.assertIn("120 seconds", response.text)
        self.assertIn("767", response.text)
        self.manager.sync_once.assert_not_called()

    def test_missing_and_forged_tokens_are_rejected(self):
        self.token()
        for form in ({}, {"csrf": "wrong"}, {"csrf": ""}):
            response = self.client.post("/admin/sales-sync", data=form)
            self.assertEqual(response.status_code, 403)
            self.assertIn("session expired", response.text)
        self.manager.sync_once.assert_not_called()

    def test_valid_token_runs_one_import_with_dashboard_invalidation_callback(self):
        token = self.token()
        response = self.client.post("/admin/sales-sync", data={"csrf": token})
        self.assertEqual(response.status_code, 200)
        self.manager.sync_once.assert_called_once_with(self.invalidate)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_another_session_cannot_reuse_a_form_token(self):
        token = self.token()
        with TestClient(self.app, follow_redirects=False) as another:
            response = another.post("/admin/sales-sync", data={"csrf": token})
        self.assertEqual(response.status_code, 403)
        self.manager.sync_once.assert_not_called()

    def test_admin_revocation_applies_to_an_already_rendered_form(self):
        token = self.token()
        self.user = {"username": "admin", "is_admin": False}
        response = self.client.post("/admin/sales-sync", data={"csrf": token})
        self.assertEqual(response.status_code, 403)
        self.manager.sync_once.assert_not_called()

    def test_failed_attempt_counts_and_row_issues_are_separate_from_completed_counters(self):
        self.status.update(state="error", error="Direct Sales validation failed.",
                           attempt_diagnostics={"source_rows": 1010, "invoice_rows": 1000, "ignored_blank_rows": 10,
                                                "ignored_rows": [{"source_key": "987", "sheet_row": 994}]},
                           issues=[{"code": "duplicate_source_key", "source_key": "999", "sheet_rows": [1006, 1011],
                                    "invoice_no": "PRIVATE INVOICE", "customer": "PRIVATE CUSTOMER"}])
        response = self.client.get("/admin/sales-sync")
        self.assertEqual(response.status_code, 200)
        failed, completed = response.text.split('<h2>Last completed sync</h2>', 1)
        completed = completed.split('</div>', 1)[0]
        self.assertIn("Latest failed Direct Sales attempt", failed)
        self.assertIn("Nonempty source rows checked</th><td>1010", failed)
        self.assertIn("Rows with an invoice number</th><td>1000", failed)
        self.assertIn("Empty pre-numbered rows ignored</th><td>10", failed)
        self.assertIn("Worksheet rows 1006, 1011 · S.NO 999: Duplicate S.NO", failed)
        self.assertIn("Worksheet row 994 · S.NO 987: Empty pre-numbered row ignored", failed)
        self.assertIn("Valid billed rows</th><td>767", completed)
        self.assertNotIn("Duplicate S.NO", completed)
        self.assertNotIn("PRIVATE", response.text)
        self.assertNotIn("&#x27;source_key&#x27;", response.text)

    def test_placeholder_notes_do_not_claim_conflicts_need_review(self):
        self.status["counters"].update(source_rows=767, source_total_rows=777, invoice_rows=767,
                                       ignored_blank_rows=10, conflicts=0)
        self.status["issues"] = [{"code": "serial_only_placeholder", "source_key": "987", "sheet_row": 994}]
        response = self.client.get("/admin/sales-sync")
        self.assertIn("Direct Sales sync: success", response.text)
        self.assertIn("Nonempty source rows</th><td>777", response.text)
        self.assertIn("Empty pre-numbered rows ignored</th><td>10", response.text)
        self.assertIn("Notes from this import", response.text)
        self.assertNotIn("Issues to review", response.text)
        self.assertNotIn("conflicts need review", sheet_sync_web.footer(self.status))
        self.assertIn("every other worksheet cell is empty", response.text)
        self.assertIn("including having an invoice number", response.text)

    def test_linked_blank_invoice_is_described_as_preserved_for_review(self):
        self.status.update(state="warning", issues=[{"code": "linked_source_row_blank", "source_key": "999", "sheet_rows": [1006]}])
        self.status["counters"]["conflicts"] = 1
        response = self.client.get("/admin/sales-sync")
        self.assertIn("Worksheet row 1006 · S.NO 999", response.text)
        self.assertIn("saved sales and link were preserved", response.text)
        self.assertIn("Issues to review", response.text)
        self.assertIn("conflicts need review", sheet_sync_web.footer(self.status))

    def test_refresh_warning_does_not_claim_data_conflicts(self):
        self.status.update(state="warning", error="Sales were imported, but the dashboard could not be refreshed.")
        self.status["counters"]["conflicts"] = 0
        self.assertIn("needs attention", sheet_sync_web.footer(self.status))
        self.assertNotIn("conflicts need review", sheet_sync_web.footer(self.status))

    def test_error_and_issue_text_is_html_escaped(self):
        self.status.update(state="error", error='<img src=x onerror="evil()">',
                           issues=['<script>evil()</script>'])
        self.status["lfo"] = {"state": "success", "warnings": ['<script>sheet()</script>']}
        response = self.client.get("/admin/sales-sync")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('<img src=x', response.text)
        self.assertNotIn('<script>evil()', response.text)
        self.assertIn('&lt;script&gt;evil()', response.text)
        self.assertNotIn('sheet()', response.text)
        self.assertNotIn("About the sheet data", response.text)
        self.assertIn("Secondary targets come from each distributor’s monthly targets in LFO Distributed", response.text)
        self.assertIn("changes to Primary targets do not change Secondary targets", response.text)
        self.assertNotIn("using distributor proportions", response.text)


if __name__ == "__main__":
    unittest.main()
