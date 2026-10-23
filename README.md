# Sales Dashboard

A Python/FastAPI sales dashboard with PostgreSQL reporting, product invoice entry,
dispatch updates, target planning, customer and state breakups, user access controls,
Google Sheets imports, and an Online Sales TV slideshow.

This public repository contains application code, database schema and migrations,
selected isolated tests, map geometry, branding assets, and clearly fictional
product and target examples. It contains no live sales, approved targets, private
product prices, accounts, database backups, connection files, or source workbook IDs.
The private deployment and its data are maintained separately.

## Getting started

Use Python 3.10 or later and PostgreSQL. From the repository directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
```

Follow [deployment setup](DEPLOY.md) and [database setup](pgdb/README.md).
The application requires separately provisioned databases and configuration;
cloning this repository does not provide a running dashboard or any business data.

## Features and operation

- Channel, executive, customer, retailer, monthly and geographic reporting.
- GST-inclusive product rates, quantity totals and line discounts before GST.
- Independent sales creation, dispatch editing and export permissions.
- Dashboard section grants and server-enforced data scopes.
- Optional one-way spreadsheet imports with conflict detection.
- Separate Online Sales storage and an automatic two-slide TV view.

Read [access controls](ACCESS_CONTROLS.md) and [Online Sales](ONLINE_SALES.md)
before configuring accounts or enabling imports.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The public suite uses synthetic values, temporary account files and fake database
connections. Node.js enables the JavaScript layout and invoice checks. These tests
do not connect to a deployed dashboard or modify production records.

## Data and configuration

Keep database connections, account files, session keys, spreadsheets, imports,
generated dashboards and backups outside version control. Example files are
marked `.example`; their values are fictional and must not be used as approved
business prices or targets. No public license has been specified for this project.
