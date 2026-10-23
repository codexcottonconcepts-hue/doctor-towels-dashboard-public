-- One-way Direct Sales synchronization. Source identity is the permanent S.NO,
-- never the physical worksheet row. Only explicitly mapped sales are updated.
begin;
create schema if not exists aux;
create table if not exists aux.sheet_sales_row (
    source_id text not null,
    source_key text not null,
    entry_id bigint not null unique references txn.sales_entry(entry_id) on delete restrict,
    source_payload jsonb not null check (jsonb_typeof(source_payload) = 'object'),
    source_fingerprint char(64) not null,
    linked_at timestamptz not null default now(),
    checked_at timestamptz not null default now(),
    primary key (source_id, source_key)
);
comment on table aux.sheet_sales_row is
    'Stable Google sheet S.NO linkage and last accepted source values for conflict-safe three-way updates. Missing source rows are never deleted.';
grant usage on schema aux to dstt_app, dstt_etl;
grant select, insert, update on aux.sheet_sales_row to dstt_app, dstt_etl;
-- The source may introduce reference values. Existing reference records remain
-- untouched: runtime synchronization only gets or creates these classifications.
grant insert on master.state, master.channel, master.brand, master.financial_year to dstt_app;
commit;
