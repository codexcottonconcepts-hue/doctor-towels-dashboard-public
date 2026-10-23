-- Preserve supplementary dashboard datasets in PostgreSQL at the cutover.
-- JSONB keeps the exact legacy shapes (including dates and category labels).
-- Importing is explicit; an application refresh never writes these records.
begin;
create schema if not exists aux;
create table if not exists aux.dashboard_dataset (
    dataset_key text primary key check (dataset_key in
        ('hist', 'histLabels', 'lfo2', 'terr', 'cat', 'pipeline', 'mtd')),
    payload jsonb not null,
    source_file text not null,
    source_sha256 char(64) not null,
    source_recorded_date date,
    imported_at timestamptz not null default now()
);
comment on table aux.dashboard_dataset is
    'Supplementary data retained from the last local Google workbook snapshot; PostgreSQL is authoritative after cutover.';
grant usage on schema aux to dstt_app, dstt_etl, dstt_read;
grant select on aux.dashboard_dataset to dstt_app, dstt_etl, dstt_read;
grant insert on aux.dashboard_dataset to dstt_etl;
commit;
