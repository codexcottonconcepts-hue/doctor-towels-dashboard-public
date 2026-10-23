-- New web entries receive an internal invoice line number automatically.
-- A submission UUID keeps a repeated request from creating another line.
-- Historical rows remain unchanged and do not need submission UUIDs.

begin;

alter table txn.sales_entry
    add column if not exists submission_id uuid;

-- Include deleted entries: replaying an old submission must not recreate a
-- sale that was deliberately removed. NULL remains valid for historical/ETL rows.
create unique index if not exists sales_entry_submission_uq
    on txn.sales_entry (submission_id)
    where submission_id is not null;

comment on column txn.sales_entry.submission_id is
    'Stable UUID for a new web submission; repeated requests identify the same entry. Historical and imported entries may be NULL.';

-- Existing table-level SELECT/INSERT/UPDATE grants cover the new column.
-- The invoice/brand/line uniqueness constraint is retained unchanged.

commit;
