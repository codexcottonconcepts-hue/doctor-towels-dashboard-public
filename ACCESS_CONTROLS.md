# Users and access

Administrators manage accounts in **Users & access**. Standard accounts can receive
independent dashboard, register, export, target and entry-operation permissions.
New grants do not make a standard account an administrator.

## Dashboard

Choose permitted pages, views, cards, charts, filters and drilldowns. Data scopes
can restrict channels, executives, customers, brands, distributions, states and
months. Restricted data is projected on the server before serialization; hiding a
filter is separate from restricting its underlying data.

A channel scope also limits primary sales registers, new sales entries and
dispatch edits. Those operations still require their own permissions. Other
dashboard data scopes do not automatically restrict entry operations.

## Sales entry and dispatch

- **Create** requires the entry-creation grant and Edit access to the required
  invoice, classification, location and sales-value sections.
- General **Sales Edit** and section Edit grants control updates to existing
  core invoice fields. Sections with only View remain read-only.
- Courier and stock-supplied editing can be granted independently without general
  Sales Edit. A dispatch operator's permitted fields are enforced when saving.
- Register visibility and exports are independent grants. Exports contain only
  sections the account may view.
- Deletion is a separate operation; deleted sales are excluded from reporting
  while the database audit history is retained.

Do not add general Sales Edit when an operator should only create new invoices
and update dispatch details on existing invoices. Combine creation-section grants
with the narrow dispatch grants instead.

## Administration

Target management and secondary store-sales operations have separate grants.
Delegated user managers cannot grant more access than they have, create
administrators or take over higher-access accounts. Keep administrator access
limited to trusted operators. Permission changes apply on subsequent requests.

Account credentials and policies are stored in private SQLite account storage;
business records and their audit history are in PostgreSQL. Preserve both stores
and the session key during recovery. The public tests exercise permission
boundaries with temporary files and fake database connections.
