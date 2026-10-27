# India geography options

The pinned reference dated **27 October 2026** covers all **36 current Indian
states/UTs, 784 LGD districts and 5,050 Urban Local Bodies**. The 26 bodies with
verified coverage in several districts have separate district-qualified options;
12 bodies without district coverage retain a NULL district. This produces
5,087 ULB options. Kavaratti is one separately sourced government capital/locality
option for Lakshadweep, which has no ULB in this snapshot.

Urban Local Bodies are municipal entities. This reference does **not** claim to
list every Census city, town, village, postal locality or settlement. Existing
application places remain available, including historical names and mappings.
Source names can retain legacy spellings; the snapshot is not proof of the date
of every legal notification.

## Sources, rights and verification

Controlling authority: Ministry of Panchayati Raj, Government of India,
[Local Government Directory](https://lgdirectory.gov.in/), snapshot 27/10/2026.
The official public state report matched all 36 source code/name pairs, and the
official live totals matched 36 states/UTs, 784 districts and 5,050 ULBs.

Structured CSVs were obtained from the already-public
[ramSeraph/opendata mirror](https://ramseraph.github.io/opendata/lgd/), pinned to
[metadata generation d807d9e91b87f9bb4391e603](https://ramseraph.github.io/opendata/lgd/data/metadata.pages.d807d9e91b87f9bb4391e603.json),
published 27 October 2026 at 11:17:25 UTC. Both archive and extracted CSV SHA-256
values were checked against its manifests. The JSON metadata retains their URLs
and hashes. No mirror collector or CAPTCHA automation was executed; protected
official exports were not accessed.

Government data attribution follows [GODL-India](https://data.gov.in/Godl) and
the [official license notification](https://www.meity.gov.in/static/uploads/2024/02/Gazette-Notification_OpenDataLicense_13.02.2017.pdf).
Attribute the Ministry of Panchayati Raj, Government of India, Local Government
Directory, snapshot 27/10/2026, https://lgdirectory.gov.in/; public mirror
ramSeraph/opendata. Do not imply government endorsement. The mirror author's
code license does not replace the government data provider's rights.

Kavaratti is supported separately by the current
[Lakshadweep administration](https://lakshadweep.gov.in/about-lakshadweep/) and
[Kavaratti page](https://lakshadweep.gov.in/islands/kavaratti/). Its source key is
`government-lakshadweep-kavaratti`; no LGD ULB code is invented.

`india_lgd_reference.json` contains explicit same-state spelling aliases with
official evidence links. No fuzzy name matching is used. Known aliases include
Kanyakumari/Kanniyakumari, Tirupur/Tiruppur, East Singhbhum/East Singhbum,
Kamrup Metropolitan/Kamrup Metro and ampersand variants of the combined UT.
An identically named district in another state is never reused.

## Applying the pinned snapshot

Back up the database first. Run migration `pgdb/sql/25_geo_reference.sql` as the
database owner. It creates auxiliary provenance and source-code bindings only;
it does not change existing geography, customer or sales rows. The dashboard
application role receives SELECT access to these auxiliary tables.

Run the owner helper on the database host:

```powershell
py pgdb/import_geo_reference.py
py pgdb/import_geo_reference.py --apply --actor geography_reference_import
```

The default is a read-only dry run. The helper reads the owner DSN from
`.local-postgres/admin.url` without printing it. Production access is restricted
to BACKUPSERVER, loopback, database `dstt`, port 5433. Explicit loopback databases
ending in `_test` are permitted for isolated tests. `--apply` also checks ownership
of all three geography masters; it commits one transaction only after preservation
and repeatability checks pass. Migration 25 is a separate transaction.

For a geography-only exported inventory, a dry run needs no database connection:

```powershell
py pgdb/import_geo_reference.py --inventory-file private/geography.json
```

The import allocates normal database surrogate IDs. LGD codes are stored in
auxiliary bindings, never used as application IDs. All existing master rows,
IDs, parents, labels and active flags remain unchanged. Missing places are
appended; collisions receive qualified labels. Repeating the same snapshot adds
zero rows. A changed source hash, incomplete previous snapshot, ambiguous alias
or changed binding aborts the operation for review.

Pinned snapshot: `lgd-20261027-c1a2013490f3`.
JSON file SHA-256:
`1d851ac27ed0ff1f7d0f16ac22da66b3afa44a9c6397d7e70f8e8bebe061dff6`.
Canonical body SHA-256:
`a94a859556d5e769183936054c4c7016c68553f23e96280d788f1d963f0da2d7`.
Both are checked before the importer touches the database.

Tests: `python -m unittest discover -s tests -p test_geo_reference.py`.
Fixtures contain fictional places only. Private inventory and deployment
receipts belong outside public source copies.
