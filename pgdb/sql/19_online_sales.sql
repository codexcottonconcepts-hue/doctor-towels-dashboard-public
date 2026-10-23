-- Dedicated Online Sales source; no primary invoice or target rows are changed.
-- Run as database owner after existing dashboard migrations. Safe to rerun.
begin;
create schema if not exists online_sales;
create table if not exists online_sales.source_snapshot (
    source_id text primary key,
    source_fingerprint char(64) not null,
    imported_at timestamptz not null default now(),
    metadata jsonb not null check(jsonb_typeof(metadata)='object')
);
create table if not exists online_sales.daily_sale (
    source_id text not null references online_sales.source_snapshot(source_id) on delete restrict,
    period date not null,
    platform text not null check(length(platform) between 1 and 100),
    amount numeric(18,2) not null,
    primary key(source_id,period,platform)
);
create table if not exists online_sales.monthly_sale (
    source_id text not null references online_sales.source_snapshot(source_id) on delete restrict,
    period date not null check(extract(day from period)=1),
    platform text not null check(length(platform) between 1 and 100),
    amount numeric(18,2) not null,
    primary key(source_id,period,platform)
);
comment on schema online_sales is 'Separate online platform sales actuals from Daily Sales and Monthly Sales; no targets or primary invoice ledger linkage.';
comment on table online_sales.daily_sale is 'Only nonblank daily platform input cells. Sheet weekly and overall totals are ignored.';
comment on table online_sales.monthly_sale is 'Only nonblank Monthly Sales platform input cells. Blank cells do not become zero; daily fallback is derived during reads.';
grant usage on schema online_sales to dstt_app,dstt_etl;
grant select,insert,update,delete on all tables in schema online_sales to dstt_app,dstt_etl;
commit;
