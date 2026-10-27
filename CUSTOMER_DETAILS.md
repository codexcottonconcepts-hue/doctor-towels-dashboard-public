# Customer details and PI numbers

Customer profile details and invoice location stay hidden in the default
sales-entry form, which uses the full width without an empty side panel.
Add customer and Edit open the customer editor; Location opens a separate
invoice-location popup using the same State, District and City controls.
Done keeps invoice-location changes and Cancel restores them, without updating
the shared customer profile. Read-only users receive View details or View
location actions, and their existing permissions remain unchanged. Required
hidden name or State validation reopens the appropriate editor.

The customer editor shows Address, then optional Contact number and GSTIN
beside each other. The visible locality field has been removed; its stored
value remains in a hidden control and is preserved during updates. Done keeps
a compact new-customer draft name for identification and the sales entry;
Cancel restores the previous customer and invoice location. Updating an existing
customer explicitly saves its shared profile without creating an invoice or
changing historical sales, and restores the invoice's prior location.
This visibility update requires no database changes. Existing channel, CSRF
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
