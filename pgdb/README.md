# Database setup

The SQL files define PostgreSQL schemas for master dimensions, invoice and
dispatch records, target plans, audit history, supplementary dashboard datasets,
secondary store sales, product invoices and online sales.

## Provisioning

Use a new empty PostgreSQL database and an authorized migration connection in
`DATABASE_URL`. Install the project requirements, then preview the schema work:

```powershell
python pgdb/deploy.py --schema-only --dry-run
python pgdb/deploy.py --schema-only
```

Fresh provisioning refuses an existing schema. For an existing installation,
back up first, review `--upgrade --dry-run`, then use `--upgrade` for repeatable
migrations. Configure a limited application role separately; migration rights
must not be granted to ordinary dashboard users.

Reference seeds in this public distribution are placeholders. Private territory
assignments and business targets are not included. Supply your own approved
masters and import data explicitly; schema creation alone does not populate a
working dashboard's supplementary datasets.

## Imports

- `etl/load_sales.py`: explicit workbook import for initial invoice loading.
- `etl/load_targets.py`: explicit CSV import of target scopes and values.
- `etl/import_supplementary.py`: explicit migration of supporting dashboard data.
- `etl/import_lfo_secondary.py`: supported secondary-sales workbook layouts.
- `import_product_catalog.py`: validates and imports a supplied product catalog.

Review each command's `--help` and dry-run output before writing to a database.
`etl/targets.example.csv` and `../catalog/products.example.json` are fictional
examples only. They are not approved prices, financial targets or actual sales.
Imports must use the same master names and approved measurement definitions as
the destination installation.

Targets can combine channel, person, state, city, retailer and brand scopes.
A blank dimension means all values of that dimension. Overlapping assignments
are useful for drilldowns; mark only a non-overlapping set for company rollups.
Never sum all scoped targets to obtain a company budget.

## Independent storage

Primary business data is in the configured application database. Online sales
uses a separate `dstt_online` database and migration `sql/19_online_sales.sql`.
Account credentials and dashboard policies use private SQLite storage outside
the PostgreSQL business tables. Preserve all necessary stores and configuration
in restricted backups, and test recovery before a production move.

The public unit tests require no live database. Integration tools and database
self-tests should be run only against an explicitly selected isolated test copy.
