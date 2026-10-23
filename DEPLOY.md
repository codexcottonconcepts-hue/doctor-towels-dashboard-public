# Deployment

This repository is a source distribution. Provision your own PostgreSQL database,
account storage, session key and approved business data before starting it.
Do not run deployment or import commands against an existing database without a
backup and a reviewed migration plan.

## Database

See [database setup](pgdb/README.md) for schema creation and explicit imports.
Configure the primary application connection through `DATABASE_URL`, or put it in
a private UTF-8 file and set `DATABASE_URL_FILE` to that file. The application also
recognizes an adjacent private `database.url` file. Never commit these files.

The dashboard reads PostgreSQL, including supplementary datasets in
`aux.dashboard_dataset`. An empty schema alone does not supply those datasets.
Import your approved inputs explicitly; absent configuration or unavailable data
does not fall back to a public spreadsheet or this repository's examples.

## Start the web application

Install `requirements.txt` in a virtual environment. Choose private persistent
paths for `DASH_DB` (SQLite accounts) and `DASH_SECRET` (the session-key file).
Create an administrator locally before first launch:

```powershell
python auth.py add owner --admin
python -m uvicorn app:app --host 127.0.0.1 --port 8090 --workers 1
```

Run both commands with the same account-file environment settings. Omitting the
password from the account command generates one and displays it once. Store it
privately. For automated first startup, configure `DASH_ADMIN_USER` and
`DASH_ADMIN_PASSWORD` in the host's secret environment settings; remove the
bootstrap password after creating the account.

Place the application behind an HTTPS reverse proxy for remote access and set
`HTTPS_ONLY=1`. Publish the application endpoint, not the project directory.
Use one process while accounts and session state use local persistent files.
The public health endpoint is `/healthz`; authenticated health details provide
more information. Windows launch and service helpers are included for adapting
to your own installation paths and service account.

## Optional spreadsheet imports

Imports are disabled when their private configuration is absent. Set
`SALES_SOURCE_ID` to your own authorized Direct Sales workbook ID before enabling
the Direct Sales worker. Its private `sheets-sales-sync.json` configuration names
the source, the `Direct Sales Report` tab and the import interval. Configure
secondary imports separately when your workbook supports those layouts.

Set `ONLINE_SALES_SOURCE_ID` for a different authorized online workbook and use a
private `online-sales-sync.json` file. See [Online Sales](ONLINE_SALES.md). Neither
source ID is included in this public repository. A readable export is required;
the application does not change spreadsheet sharing or write back to Sheets.

## Upgrades and recovery

Back up PostgreSQL, SQLite accounts, the session key and private configuration
before upgrades. Test restores in an isolated environment. For an existing schema,
review `python pgdb/deploy.py --upgrade --dry-run` before applying migrations.
Application-source copies do not back up database contents. Do not copy a live
account database or session key into a public repository or web directory.
