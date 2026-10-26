"""Regression checks for accepting a unique, unchanged invoice after S.NO moves.

These checks use accepted source baselines, never the mutable sales ledger, as
proof of identity. A unique strong invoice identity can retain ordinary source
edits; ambiguous invoice lines must never be guessed or overwrite local edits.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import unittest
from unittest.mock import patch

from sheet_sales_sync import (
    SyncValidationError, canonical, merge_payload, reconcile_source_keys,
    sync_workbook,
)


def payload(invoice="REKEY-1", **changes):
    """A complete normalized Direct Sales payload without business data."""
    fields = dict(
        invoice_no=invoice, invoice_date="2026-10-23", pi_number=None,
        stock_supplied_on=None, payment_received_on=None,
        order_type="Re-order", credit_days=None,
        primary_qty="10.00", primary_taxable="1234.50",
        primary_with_tax="1456.71", secondary_qty="0.00",
        secondary_value="0.00", tertiary_nos="0.00", tertiary_value="0.00",
        customer_id=10, channel_id=20, sub_channel_id=30, person_id=40,
        brand_id=50, city_id=60, state_id=70, district_id=80,
    )
    fields.update(changes)
    return canonical(fields)


def link(key, entry_id, accepted):
    return dict(
        source_id="regression-source", source_key=str(key), entry_id=entry_id,
        source_payload=deepcopy(accepted), source_fingerprint="test-fingerprint",
    )


class SourceKeyReconciliationTests(unittest.TestCase):
    def assertEntryMap(self, actual, expected):
        self.assertEqual(
            {key: value["entry_id"] for key, value in actual.items()}, expected,
        )

    def test_unchanged_keys_stay_attached(self):
        accepted = payload()
        links = {"1": link("1", 101, accepted)}
        remapped, moves = reconcile_source_keys([("1", accepted)], links)
        self.assertEntryMap(remapped, {"1": 101})
        self.assertEqual(moves, [])

    def test_single_unchanged_invoice_can_move_to_unused_serial(self):
        accepted = payload()
        links = {"1001": link("1001", 101, accepted)}
        remapped, moves = reconcile_source_keys([("987", accepted)], links)
        self.assertEntryMap(remapped, {"987": 101})
        self.assertEqual(remapped["987"]["source_key"], "987")
        self.assertEqual(remapped["987"]["source_payload"], accepted)
        self.assertEqual(moves, [("1001", "987", 101)])

    def test_rows_987_through_994_keep_the_same_eight_ledger_entries(self):
        accepted = [payload(f"REKEY-{n}", primary_qty=n + 1)
                    for n in range(8)]
        links = {str(1040 + n): link(1040 + n, 200 + n, row)
                 for n, row in enumerate(accepted)}
        original = deepcopy(links)
        resolved = [(str(987 + n), row) for n, row in enumerate(accepted)]
        remapped, moves = reconcile_source_keys(resolved, links)
        self.assertEntryMap(remapped, {str(987 + n): 200 + n for n in range(8)})
        self.assertEqual(len(moves), 8)
        self.assertEqual(links, original, "Planning must not mutate accepted links")
        self.assertEqual(
            sorted(row["source_payload"]["primary_taxable"]
                   for row in remapped.values()),
            sorted(row["source_payload"]["primary_taxable"]
                   for row in original.values()),
        )

    def test_second_sync_is_idempotent_after_accepting_new_keys(self):
        accepted = payload()
        resolved = [("987", accepted)]
        original = {"1040": link("1040", 101, accepted)}
        first, first_moves = reconcile_source_keys(resolved, original)
        self.assertEqual(len(first_moves), 1)
        second, second_moves = reconcile_source_keys(resolved, first)
        self.assertEntryMap(second, {"987": 101})
        self.assertEqual(second_moves, [])

    def test_serial_swap_is_one_to_one(self):
        first, second = payload("REKEY-A"), payload("REKEY-B")
        links = {"1": link("1", 101, first), "2": link("2", 102, second)}
        remapped, moves = reconcile_source_keys([("1", second), ("2", first)], links)
        self.assertEntryMap(remapped, {"1": 102, "2": 101})
        self.assertEqual(len(moves), 2)

    def test_three_way_cycle_is_one_to_one(self):
        rows = [payload(f"REKEY-{name}") for name in "ABC"]
        links = {str(n + 1): link(n + 1, 101 + n, row)
                 for n, row in enumerate(rows)}
        remapped, moves = reconcile_source_keys(
            [("1", rows[2]), ("2", rows[0]), ("3", rows[1])], links,
        )
        self.assertEntryMap(remapped, {"1": 103, "2": 101, "3": 102})
        self.assertEqual(len(moves), 3)

    def test_same_key_edits_remain_for_three_way_merge(self):
        accepted = payload()
        incoming = payload(primary_taxable="1300.00")
        links = {"1": link("1", 101, accepted)}
        remapped, moves = reconcile_source_keys([("1", incoming)], links)
        self.assertEntryMap(remapped, {"1": 101})
        self.assertEqual(moves, [])
        changes, conflicts = merge_payload(
            remapped["1"]["source_payload"], incoming, accepted,
        )
        self.assertEqual(changes, {"primary_taxable": "1300.00"})
        self.assertEqual(conflicts, [])

    def test_same_key_financial_edit_does_not_block_a_new_sku_on_same_invoice(self):
        accepted = payload("REKEY-SHARED-INVOICE", primary_qty="10.00",
                           primary_taxable="100.00")
        edited = {**accepted, "primary_taxable": "101.00"}
        new_line = payload("REKEY-SHARED-INVOICE", primary_qty="20.00",
                           primary_taxable="200.00")
        links = {"1": link("1", 101, accepted)}
        remapped, moves = reconcile_source_keys(
            [("1", edited), ("2", new_line)], links,
        )
        self.assertEntryMap(remapped, {"1": 101})
        self.assertNotIn("2", remapped)
        self.assertEqual(moves, [])
        changes, conflicts = merge_payload(
            remapped["1"]["source_payload"], edited, accepted,
        )
        self.assertEqual(changes, {"primary_taxable": "101.00"})
        self.assertEqual(conflicts, [])

    def test_new_line_identical_to_old_baseline_cannot_steal_an_edited_same_key(self):
        accepted = payload("REKEY-SHARED-INVOICE", primary_taxable="100.00")
        edited = {**accepted, "primary_taxable": "101.00"}
        links = {"1": link("1", 101, accepted)}
        remapped, moves = reconcile_source_keys(
            [("2", accepted), ("1", edited)], links,
        )
        self.assertEntryMap(remapped, {"1": 101})
        self.assertEqual(moves, [])

    def test_unique_strong_identity_allows_a_rekey_and_source_financial_edit(self):
        accepted = payload()
        incoming = payload(primary_taxable="1300.00")
        original = {"1": link("1", 101, accepted)}
        remapped, moves = reconcile_source_keys([("987", incoming)], original)
        self.assertEntryMap(remapped, {"987": 101})
        self.assertEqual(moves, [("1", "987", 101)])
        # Reassociation keeps the prior accepted baseline, so merge applies the
        # authentic source edit only after comparing it with the current ledger.
        changes, conflicts = merge_payload(
            remapped["987"]["source_payload"], incoming, accepted,
        )
        self.assertEqual(changes, {"primary_taxable": "1300.00"})
        self.assertEqual(conflicts, [])
        self.assertEqual(original["1"]["source_payload"], accepted)

    def test_identity_change_cannot_prove_new_serial_identity(self):
        accepted = payload(pi_number="REKEY-PI")
        for changed in (
            {"customer_id": 11}, {"invoice_date": "2026-10-24"},
            {"brand_id": 51}, {"channel_id": 21},
            {"pi_number": "OTHER-PI"}, {"invoice_no": "OTHER-INVOICE"},
            {"courier_docket_no": "OTHER-DOCKET"},
        ):
            with self.subTest(changed=changed):
                incoming = {**accepted, **changed}
                remapped, moves = reconcile_source_keys(
                    [("987", incoming)], {"1": link("1", 101, accepted)},
                )
                self.assertEntryMap(remapped, {"1": 101})
                self.assertEqual(moves, [])

    def test_ambiguous_multiline_identity_never_uses_financial_fallback(self):
        first = payload(primary_qty="10.00", primary_taxable="100.00")
        second = payload(primary_qty="20.00", primary_taxable="200.00")
        incoming = {**first, "primary_taxable": "101.00"}
        links = {"1": link("1", 101, first), "2": link("2", 102, second)}
        original = deepcopy(links)
        with self.assertRaises(SyncValidationError):
            reconcile_source_keys([("987", incoming)], links)
        self.assertEqual(links, original)

    def test_same_invoice_doctor_and_thirsty_brands_keep_separate_entries(self):
        doctor = payload("REKEY-SHARED-INVOICE", brand_id=50,
                         primary_qty="10.00", primary_taxable="100.00")
        thirsty = payload("REKEY-SHARED-INVOICE", brand_id=51,
                          primary_qty="20.00", primary_taxable="200.00")
        links = {"1040": link("1040", 101, doctor),
                 "1041": link("1041", 102, thirsty)}
        remapped, moves = reconcile_source_keys(
            [("988", thirsty), ("987", doctor)], links,
        )
        self.assertEntryMap(remapped, {"987": 101, "988": 102})
        self.assertEqual(remapped["987"]["source_payload"]["brand_id"], 50)
        self.assertEqual(remapped["988"]["source_payload"]["brand_id"], 51)
        self.assertEqual(set(moves), {("1040", "987", 101),
                                     ("1041", "988", 102)})

    def test_same_invoice_same_brand_lines_cannot_be_guessed_after_quantity_edit(self):
        first = payload("REKEY-SHARED-INVOICE", brand_id=50,
                        primary_qty="10.00", primary_taxable="100.00")
        second = payload("REKEY-SHARED-INVOICE", brand_id=50,
                         primary_qty="20.00", primary_taxable="200.00")
        changed_first = {**first, "primary_qty": "11.00"}
        changed_second = {**second, "primary_qty": "21.00"}
        links = {"1040": link("1040", 101, first),
                 "1041": link("1041", 102, second)}
        original = deepcopy(links)
        with self.assertRaises(SyncValidationError):
            reconcile_source_keys(
                [("987", changed_first), ("988", changed_second)], links,
            )
        self.assertEqual(links, original)

    def test_identical_identity_elsewhere_in_current_source_blocks_fallback(self):
        accepted = payload(primary_taxable="100.00")
        changed = payload(primary_taxable="101.00")
        additional_line = payload(primary_qty="20.00", primary_taxable="200.00")
        with self.assertRaises(SyncValidationError):
            reconcile_source_keys(
                [("987", changed), ("988", additional_line)],
                {"1": link("1", 101, accepted)},
            )

    def test_same_identity_at_reserved_unchanged_key_still_blocks_fallback(self):
        first = payload(primary_taxable="100.00")
        second = payload(primary_taxable="200.00")
        changed = {**second, "primary_taxable": "201.00"}
        with self.assertRaises(SyncValidationError):
            reconcile_source_keys(
                [("1", first), ("987", changed)],
                {"1": link("1", 101, first), "2": link("2", 102, second)},
            )

    def test_unique_canonical_payload_matches_decimal_and_date_values(self):
        accepted = payload()
        incoming = {**accepted, "invoice_date": date(2026, 10, 23),
                    "primary_qty": Decimal("10"),
                    "primary_taxable": Decimal("1234.50")}
        remapped, moves = reconcile_source_keys(
            [("987", incoming)], {"1": link("1", 101, accepted)},
        )
        self.assertEntryMap(remapped, {"987": 101})
        self.assertEqual(len(moves), 1)

    def test_unique_rekey_preserves_unrelated_missing_link(self):
        first, missing = payload("REKEY-A"), payload("REKEY-MISSING")
        remapped, moves = reconcile_source_keys(
            [("987", first)],
            {"1": link("1", 101, first), "2": link("2", 102, missing)},
        )
        self.assertEntryMap(remapped, {"987": 101, "2": 102})
        self.assertEqual(len(moves), 1)

    def test_unchanged_identical_rows_are_reserved_before_move_matching(self):
        accepted = payload()
        links = {"1": link("1", 101, accepted),
                 "2": link("2", 102, accepted)}
        remapped, moves = reconcile_source_keys(
            [("987", accepted), ("1", accepted)], links,
        )
        self.assertEntryMap(remapped, {"1": 101, "987": 102})
        self.assertEqual(len(moves), 1)

    def test_both_identical_same_key_rows_are_unchanged(self):
        accepted = payload()
        links = {"1": link("1", 101, accepted),
                 "2": link("2", 102, accepted)}
        remapped, moves = reconcile_source_keys(
            [("2", accepted), ("1", accepted)], links,
        )
        self.assertEntryMap(remapped, {"1": 101, "2": 102})
        self.assertEqual(moves, [])

    def test_identical_new_rows_cannot_compete_for_one_existing_entry(self):
        accepted = payload()
        links = {"1": link("1", 101, accepted)}
        original = deepcopy(links)
        with self.assertRaises(SyncValidationError):
            reconcile_source_keys([("987", accepted), ("988", accepted)], links)
        self.assertEqual(links, original)

    def test_identical_baselines_with_no_reserved_key_are_ambiguous(self):
        accepted = payload()
        links = {"1": link("1", 101, accepted),
                 "2": link("2", 102, accepted)}
        with self.assertRaises(SyncValidationError):
            reconcile_source_keys([("987", accepted)], links)

    def test_destination_cannot_replace_an_unmatched_existing_link(self):
        first, second = payload("REKEY-A"), payload("REKEY-B")
        links = {"1": link("1", 101, first), "2": link("2", 102, second)}
        original = deepcopy(links)
        with self.assertRaises(SyncValidationError):
            reconcile_source_keys([("2", first)], links)
        self.assertEqual(links, original)

    def test_local_sales_and_dispatch_edits_survive_rekey(self):
        accepted = payload(courier_id=None, courier_docket_no=None)
        current = {**accepted, "primary_qty": "12.00", "courier_id": 5,
                   "courier_docket_no": "001234"}
        original_current = deepcopy(current)
        remapped, moves = reconcile_source_keys(
            [("987", accepted)], {"1": link("1", 101, accepted)},
        )
        changes, conflicts = merge_payload(
            remapped["987"]["source_payload"], accepted, current,
        )
        self.assertEqual(changes, {})
        self.assertEqual(conflicts, [])
        self.assertEqual(current, original_current)
        self.assertEqual(len(moves), 1)

    def test_rekey_with_source_and_local_financial_edits_keeps_merge_conflict(self):
        accepted = payload(primary_taxable="100.00")
        incoming = payload(primary_taxable="101.00")
        current = payload(primary_taxable="102.00")
        remapped, moves = reconcile_source_keys(
            [("987", incoming)], {"1": link("1", 101, accepted)},
        )
        self.assertEntryMap(remapped, {"987": 101})
        changes, conflicts = merge_payload(
            remapped["987"]["source_payload"], incoming, current,
        )
        self.assertEqual(changes, {"primary_taxable": "101.00"})
        self.assertEqual(conflicts, ["primary_taxable"])
        self.assertEqual(moves, [("1", "987", 101)])


class MemoryConnection:
    """Small transactional ledger that enforces unique source keys in tests."""
    def __init__(self, links, entries):
        self.links = deepcopy(links)
        self.entries = deepcopy(entries)
        self.ledger_writes = []
        self.statements = []

    def __enter__(self):
        self.before = deepcopy((self.links, self.entries, self.ledger_writes))
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type:
            self.links, self.entries, self.ledger_writes = self.before
        return False

    def cursor(self, **kwargs):
        return MemoryCursor(self)


class MemoryCursor:
    def __init__(self, connection):
        self.conn = connection
        self.rows = []
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def fetchone(self):
        return self.rows.pop(0)

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        self.conn.statements.append((normalized, params))
        self.rowcount = -1
        if normalized.startswith("select pg_try_advisory_xact_lock"):
            self.rows = [(True,)]
        elif normalized.startswith(("select set_config", "set local")):
            self.rows = []
        elif normalized.startswith("select * from aux.sheet_sales_row"):
            self.rows = deepcopy(list(self.conn.links.values()))
        elif normalized.startswith("select * from txn.sales_entry"):
            self.rows = deepcopy(list(self.conn.entries.values()))
        elif normalized.startswith("update aux.sheet_sales_row set source_key="):
            new, source_id, old, entry_id = params
            row = self.conn.links.get(old)
            if getattr(self.conn, "skip_next_rekey", False):
                self.conn.skip_next_rekey = False
                self.rowcount = 0
                self.rows = []
                return
            if not row or (row["source_id"], row["entry_id"]) != (source_id, entry_id):
                self.rowcount = 0
                self.rows = []
                return
            if new in self.conn.links:
                raise AssertionError("A source key uniqueness constraint would fail")
            self.conn.links[new] = {**self.conn.links.pop(old), "source_key": new}
            self.rows = []
            self.rowcount = 1
        elif normalized.startswith("insert into aux.sheet_sales_row("):
            source_id, key, entry_id, adapted, signature = params
            existing = self.conn.links.get(key)
            if existing and existing["entry_id"] != entry_id:
                raise AssertionError("An accepted baseline cannot switch ledger entries")
            self.conn.links[key] = dict(
                source_id=source_id, source_key=key, entry_id=entry_id,
                source_payload=deepcopy(adapted.adapted),
                source_fingerprint=signature,
            )
            self.rows = []
            self.rowcount = 1
        elif normalized.startswith("update txn.sales_entry set "):
            fields = normalized.split(" set ", 1)[1].split(" where ", 1)[0]
            names = [part.split("=", 1)[0] for part in fields.split(",")]
            entry_id = params[-1]
            changes = dict(zip(names, params[:-1]))
            self.conn.ledger_writes.append((entry_id, deepcopy(changes)))
            self.conn.entries[entry_id].update(changes)
            self.rows = []
            self.rowcount = 1
        else:
            raise AssertionError(f"Unexpected SQL in rekey regression: {normalized}")


class WorkbookRekeyIntegrationTests(unittest.TestCase):
    """Exercise the real sync plan and writes without opening any database."""
    def connection(self, accepted, *, current=None, key="1040", deleted=False):
        links = {key: link(key, 101, accepted)}
        entry = dict(current or accepted, entry_id=101, source="sheets_migration",
                     is_deleted=deleted, line_no=1)
        return MemoryConnection(links, {101: entry})

    def sync(self, conn, resolved, *, ignored=(), dry_run=False):
        def parse(unused_workbook, *, diagnostics):
            diagnostics.update(
                source_rows=len(resolved) + len(ignored), invoice_rows=len(resolved),
                ignored_blank_rows=len(ignored), ignored_rows=list(ignored),
            )
            return [dict(source_key=key, payload=source) for key, source in resolved]

        class ResolvedMasters:
            def __init__(self, cursor, dry_run):
                pass

            def payload(self, row):
                return row["payload"]

        with patch("sheet_sales_sync.parse_workbook", side_effect=parse), \
                patch("sheet_sales_sync.Masters", ResolvedMasters):
            return sync_workbook(object(), "regression-source", conn, dry_run=dry_run)

    def test_rekey_and_cleared_old_serial_do_not_duplicate_or_change_sales(self):
        accepted = payload()
        conn = self.connection(accepted)
        before = deepcopy(conn.entries)
        result = self.sync(
            conn, [("987", accepted)],
            ignored=[{"source_key": "1040", "sheet_row": 1042}],
        )
        self.assertEqual(result["relinked"], 1)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["conflicts"], 0)
        self.assertEqual(result["missing_source"], 0)
        self.assertEqual(set(conn.links), {"987"})
        self.assertEqual(conn.entries, before)
        self.assertEqual(conn.ledger_writes, [])
        repeated = self.sync(conn, [("987", accepted)])
        self.assertEqual(repeated["relinked"], 0)
        self.assertEqual(repeated["inserted"], 0)
        self.assertEqual(conn.entries, before)

    def test_rekey_swap_respects_unique_keys_without_touching_invoices(self):
        first, second = payload("REKEY-A"), payload("REKEY-B")
        conn = self.connection(first, key="1")
        conn.links["2"] = link("2", 102, second)
        conn.entries[102] = dict(second, entry_id=102, source="sheets_migration",
                                 is_deleted=False, line_no=1)
        before = deepcopy(conn.entries)
        result = self.sync(conn, [("1", second), ("2", first)])
        self.assertEqual(result["relinked"], 2)
        self.assertEqual({key: row["entry_id"] for key, row in conn.links.items()},
                         {"1": 102, "2": 101})
        self.assertEqual(conn.entries, before)
        self.assertEqual(conn.ledger_writes, [])

    def test_same_invoice_two_brands_remain_two_unchanged_ledger_entries(self):
        doctor = payload("REKEY-SHARED-INVOICE", brand_id=50,
                         primary_qty="10.00", primary_taxable="100.00")
        thirsty = payload("REKEY-SHARED-INVOICE", brand_id=51,
                          primary_qty="20.00", primary_taxable="200.00")
        conn = self.connection(doctor, key="1040")
        conn.links["1041"] = link("1041", 102, thirsty)
        conn.entries[102] = dict(thirsty, entry_id=102, source="sheets_migration",
                                 is_deleted=False, line_no=1)
        before = deepcopy(conn.entries)
        result = self.sync(conn, [("988", thirsty), ("987", doctor)])
        self.assertEqual(result["relinked"], 2)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["conflicts"], 0)
        self.assertEqual({key: row["entry_id"] for key, row in conn.links.items()},
                         {"987": 101, "988": 102})
        self.assertEqual(conn.entries, before)
        self.assertEqual(conn.ledger_writes, [])

    def test_financial_rekey_updates_existing_entry_once_and_then_is_idempotent(self):
        accepted = payload(primary_with_tax="100.00")
        incoming = payload(primary_with_tax="110.00")
        conn = self.connection(accepted)
        result = self.sync(conn, [("987", incoming)])
        self.assertEqual(result["relinked"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(conn.ledger_writes, [(101, {"primary_with_tax": "110.00"})])
        self.assertEqual(len(conn.entries), 1)
        repeated = self.sync(conn, [("987", incoming)])
        self.assertEqual(repeated["updated"], 0)
        self.assertEqual(repeated["relinked"], 0)
        self.assertEqual(len(conn.ledger_writes), 1)

    def test_same_field_local_conflict_cannot_write_financial_values(self):
        accepted = payload(primary_with_tax="100.00")
        incoming = payload(primary_with_tax="110.00")
        current = payload(primary_with_tax="120.00")
        conn = self.connection(accepted, current=current)
        before = deepcopy(conn.entries)
        result = self.sync(conn, [("987", incoming)])
        self.assertEqual(result["conflicts"], 1)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(conn.links["987"]["source_payload"], accepted)
        self.assertEqual(conn.entries, before)
        self.assertEqual(conn.ledger_writes, [])
        self.assertIn("concurrent_edit", [issue["code"] for issue in result["issues"]])

    def test_local_sales_and_dispatch_values_survive_the_real_sync_path(self):
        accepted = payload(courier_id=None, courier_docket_no=None)
        current = {**accepted, "primary_qty": "12.00", "courier_id": 5,
                   "courier_docket_no": "001234"}
        conn = self.connection(accepted, current=current)
        before = deepcopy(conn.entries)
        result = self.sync(conn, [("987", accepted)])
        self.assertEqual(result["preserved_local"], 1)
        self.assertEqual(result["relinked"], 1)
        self.assertEqual(conn.entries, before)
        self.assertEqual(conn.ledger_writes, [])

    def test_dry_run_does_not_write_rekeys_or_sales(self):
        accepted = payload()
        conn = self.connection(accepted)
        before = deepcopy((conn.links, conn.entries))
        result = self.sync(conn, [("987", accepted)], dry_run=True)
        self.assertEqual(result["relinked"], 1)
        self.assertEqual((conn.links, conn.entries), before)
        self.assertEqual(conn.ledger_writes, [])
        self.assertFalse(any(query.startswith(("update ", "insert ", "delete "))
                             for query, params in conn.statements))

    def test_soft_deleted_invoice_cannot_be_recreated_by_renumbering(self):
        accepted = payload()
        conn = self.connection(accepted, deleted=True)
        before = deepcopy(conn.entries)
        result = self.sync(conn, [("987", accepted)])
        self.assertEqual(result["conflicts"], 1)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(conn.entries, before)
        self.assertEqual(conn.ledger_writes, [])
        self.assertIn("database_row_deleted", [issue["code"] for issue in result["issues"]])

    def test_ambiguous_rekey_fails_before_any_link_or_ledger_writes(self):
        accepted = payload()
        conn = self.connection(accepted)
        before = deepcopy((conn.links, conn.entries))
        with self.assertRaises(SyncValidationError):
            self.sync(conn, [("987", accepted), ("988", accepted)])
        self.assertEqual((conn.links, conn.entries), before)
        self.assertEqual(conn.ledger_writes, [])

    def test_rekey_that_updates_no_link_aborts_without_financial_changes(self):
        accepted = payload()
        conn = self.connection(accepted)
        conn.skip_next_rekey = True
        before = deepcopy((conn.links, conn.entries))
        with self.assertRaises(SyncValidationError):
            self.sync(conn, [("987", accepted)])
        self.assertEqual((conn.links, conn.entries), before)
        self.assertEqual(conn.ledger_writes, [])


if __name__ == "__main__":
    unittest.main()
