# Online Sales

Online sales is independent of primary invoice targets. It reads an authorized
workbook with `Daily Sales` and `Monthly Sales` tabs and imports facts into a
separate PostgreSQL database. It has no sales targets.

## Configuration

Set `ONLINE_SALES_SOURCE_ID` to your own workbook ID. Create a private
`online-sales-sync.json` with `enabled`, `source_id` and `interval_seconds` fields.
Its source must match the configured environment value. Keep this file out of Git.
The application must be able to read the workbook export; no production workbook
or private sharing configuration is distributed here.

The worker uses `online_sales_store.py` and the dedicated `dstt_online` database.
Provision that database and apply `pgdb/sql/19_online_sales.sql` separately with
the appropriate roles. The primary database connection configuration supplies the
host and credentials; the online connector selects its dedicated database name.
Do not merge online and primary invoice data or assume that deploying source
files migrates either database.

## Measures

Blank cells mean unreported data; numeric zero is an explicit report. Weekly
figures sum daily facts from Monday through Sunday. A reported monthly platform
amount takes priority; otherwise the application uses its available daily
rollup. Monthly and daily values are not added together. Valid imports retain
earlier reported periods as the source moves forward.

Online Sales appears as a Dashboard tab with its own platform, month and period
controls. Monthly platform columns and the legend rank sales from highest to
lowest. Daily charts show actual reporting dates and calendar weeks. Missing
daily periods are not manufactured from monthly-only figures.

## TV view

The Salesviewer display rotates Company logo → Dashboard → Online Sales every ten
seconds with a full-screen right-to-left push over 1,000 milliseconds, with smooth easing and no fade. The company logo is centered on a black
background with comfortable margins and no report header, date or menus. Its ordinary authenticated refresh updates
data without resetting the active slide. Reduced-motion preferences disable the
animation. Temporary outgoing pictures are inert and removed on completion, fresh renders, slideshow stop or access failure. Online access requires its page and section grants; identity-scoped
primary permissions do not imply access to aggregate platform sales.

Administrators can review import status at `/admin/online-sales-sync`. Invalid
inputs or an unavailable source preserve the last committed data rather than
turning missing reports into zero.
