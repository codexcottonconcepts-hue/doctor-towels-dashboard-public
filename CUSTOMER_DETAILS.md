# Customer details in sales entry

The portrait customer panel beside the invoice fields contains address, optional
contact number, locality, GSTIN and mapped state, district and city. It stacks
below the invoice fields on smaller screens. GSTIN is optional for an
unregistered customer; contact numbers preserve international/landline formatting.

Add customer creates a distinct customer only when the sales entry is saved.
Duplicate names are rejected. Select an existing customer, click Edit, then
Update customer to save its shared profile without creating or changing an
invoice. Existing invoice locations and amounts remain unchanged. Concurrent
profile edits are protected by the customer revision and recorded in the audit
log. Older open forms cannot erase a contact number they never displayed.

Viewing requires Invoice & customer and Location view access. Updating requires
edit access to both sections plus sales creation or sales edit access. Channel
restrictions and CSRF validation still apply; dispatch-only access is insufficient.

Back up the database, then apply `pgdb/sql/22_customer_details.sql` followed by
`pgdb/sql/23_customer_contact.sql` using the database owner, or run the existing
`pgdb/apply_update.py` owner workflow. These migrations do not alter invoice
values or require Warehouse, sales-unit or GST migrations.

Run `python -m unittest discover -s tests -p test_customer_details.py` for local
validation and route tests. Database tests require an explicitly prepared
localhost test database in `CUSTOMER_DETAILS_TEST_DATABASE_URL`; never use a live
database for those tests.
