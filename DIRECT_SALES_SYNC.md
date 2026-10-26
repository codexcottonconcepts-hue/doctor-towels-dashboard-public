# Direct Sales: Google Sheets to PostgreSQL

The Direct Sales Report worksheet is imported while the dashboard application
runs. Dashboards continue to read PostgreSQL. Configure your own readable source
workbook in the local sync configuration; do not commit configuration or credentials.

Give every billed row its own unique positive whole-number S.NO. Keep existing
values stable when sorting. Doctor and Thirsty brand rows on the same invoice
remain separate rows with separate serials; identity matching includes the brand.
Completely empty rows are ignored. A pre-numbered row is ignored only when all
other worksheet cells are empty. Partially entered invoices require correction.

If serials are renumbered, synchronization first reserves unchanged rows and
matches complete accepted values one to one. A financial edit can move only when
every nonfinancial identity field agrees and that identity is unique across both
source and accepted data. Ambiguous matches and occupied serials stop the import
for review. Matching never guesses between repeated invoice lines.

Reconnection keeps the original database entry and accepted source baseline.
Independent source and dashboard edits can merge. Conflicting edits to the same
field remain unchanged for review. Local sales and dispatch corrections are
preserved when the source value stays unchanged. Missing sheet rows never delete
database entries, and deleted entries are not automatically restored.

Administrators can use Sales sync to inspect status or request an immediate
import. The page and application log report accepted serial changes without
printing financial values. The normal worker retries on its configured interval.
This is a one-way import; it does not edit the source Google Sheet.

Back up the source linkage table together with PostgreSQL. Runtime connection
files, source configuration, account databases, sessions, live invoices and
rendered dashboards belong outside a public source repository.
