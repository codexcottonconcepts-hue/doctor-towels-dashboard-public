# Customer details in sales entry

Select an existing customer to view its saved address, locality, GSTIN and mapped
state, district and city. Add customer creates a distinct customer when the sales
entry is successfully saved. Duplicate customer names are rejected rather than
merged. GSTIN is optional for an unregistered customer.

Edit customer details and Update customer explicitly save the shared customer
profile without creating or changing an invoice. The customer revision prevents
one editor from overwriting another editor's more recent changes. Invoice
locations remain invoice data; changing the shared profile does not rewrite
historical invoices.

Customer profile viewing requires Invoice & customer and Location view access.
Updating requires edit access to both sections and sales creation or sales edit
access. Existing channel restrictions still apply. Dispatch-only access does not
grant access to customer profiles. Updates require the authenticated form's CSRF
token and are recorded in the database audit log.

Before starting this version, apply `pgdb/sql/22_customer_details.sql` using the
database owner account, or run the existing `pgdb/apply_update.py` owner workflow.
The migration adds customer profile columns, revisions and auditing. It does not
require Warehouse, sales-unit or GST migrations and does not change invoice
values. Back up the database before applying schema changes.

Run `python -m unittest discover -s tests -p test_customer_details.py` for local
validation and route tests. Database tests are opt-in and require an explicitly
prepared localhost test database through `CUSTOMER_DETAILS_TEST_DATABASE_URL`.
Never point that variable at a live database.
