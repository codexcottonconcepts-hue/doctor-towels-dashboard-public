# Customer details and PI numbers

Add customer and Edit customer open a large customer editor containing the
address, optional contact number, locality, GSTIN, state, district and city.
Done returns the draft to the sales-entry form; Cancel restores the previous
selection and invoice location. Updating an existing customer saves its shared
profile explicitly, without creating an invoice or changing historical sales.
GSTIN and contact number remain optional. Existing permission, channel, CSRF
and stale-edit protections still apply.

PI number appears first and is read-only. A preview is informational and never
reserves a number. Saving a new sales entry assigns the next `DST-PIyy-nnn`
number on the server, using one transactional counter per financial year across
both brands. Concurrent saves receive distinct numbers; repeating the same form
returns its original saved entry and PI. Existing PI strings, product/brand
splits, letter suffixes and deleted history are preserved and considered when
finding the next numeric stem. An existing entry's PI is never backfilled or
renumbered during editing.

Invoice number is optional and is never generated. Enter the real billing
invoice number when available. The same invoice can still have separate rows
for Doctor Towels and Thirsty Towels. The date determines the April–March
financial year used for a new PI.

Apply `pgdb/sql/22_customer_details.sql`, `pgdb/sql/23_customer_contact.sql`, then
`pgdb/sql/24_sales_pi_numbers.sql` through the database-owner workflow, or use
`pgdb/apply_update.py`. These source-copy migrations do not require Warehouse,
sales-unit or GST migrations.

Run `python -m unittest discover -s tests -p test_customer_details.py` and
`python -m unittest discover -s tests -p test_pi_numbers.py` for validation and
route tests. PostgreSQL suites require explicitly prepared localhost `*_test`
databases in `CUSTOMER_DETAILS_TEST_DATABASE_URL` and `PI_TEST_DATABASE_URL`.
Never point tests at a live database. The Warehouse-specific optional test is
skipped when that integration is not part of the source copy.
