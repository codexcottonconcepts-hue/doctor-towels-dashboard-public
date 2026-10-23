-- LFO secondary sales are separate from primary invoice sales.
-- Run as database owner after 04_security.sql; safe to re-run.
begin;
create schema if not exists lfo;

create table if not exists lfo.distributor (
    distributor_id serial primary key,
    code text not null unique,
    name text not null,
    created_at timestamptz not null default now()
);
create table if not exists lfo.store (
    store_id serial primary key,
    distributor_id integer not null references lfo.distributor on delete restrict,
    name text not null check (btrim(name) <> ''),
    normalized_name text not null check (btrim(normalized_name) <> ''),
    is_aggregate boolean not null default false,
    created_at timestamptz not null default now(),
    unique(distributor_id, normalized_name)
);
create table if not exists lfo.import_run (
    import_id bigserial primary key,
    source_id text not null,
    sheet_name text not null,
    content_hash text not null,
    actor text not null,
    imported_at timestamptz not null default now(),
    warnings jsonb not null default '[]'::jsonb,
    counters jsonb not null default '{}'::jsonb
);
create table if not exists lfo.distributor_weight (
    distributor_id integer not null references lfo.distributor on delete restrict,
    period_month date not null check (extract(day from period_month)=1),
    weight numeric(18,2) check (weight >= 0 and weight < 1000000000000),
    source_id text not null,
    source_cell text not null,
    import_id bigint references lfo.import_run on delete restrict,
    updated_at timestamptz not null default now(),
    updated_by text not null,
    primary key(distributor_id, period_month)
);
create table if not exists lfo.monthly_sale (
    store_id integer not null references lfo.store on delete restrict,
    period_month date not null check (extract(day from period_month)=1),
    amount numeric(18,2) not null check (amount >= 0 and amount < 1000000000000),
    revision integer not null default 1 check (revision > 0),
    origin text not null check (origin in ('sheet','manual')),
    source_amount numeric(18,2) check (source_amount >= 0 and source_amount < 1000000000000),
    source_id text,
    source_cell text,
    import_id bigint references lfo.import_run on delete restrict,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    updated_by text not null,
    primary key(store_id, period_month)
);
create index if not exists lfo_monthly_sale_period_ix on lfo.monthly_sale(period_month);
create table if not exists lfo.audit_log (
    audit_id bigserial primary key,
    changed_at timestamptz not null default now(),
    actor text not null,
    table_name text not null,
    operation text not null,
    before_row jsonb,
    after_row jsonb
);
create or replace function lfo.audit_change() returns trigger language plpgsql as $$
begin
    insert into lfo.audit_log(actor,table_name,operation,before_row,after_row)
    values(coalesce(nullif(current_setting('app.lfo_actor',true),''),current_user),
           tg_table_name,tg_op,
           case when tg_op='INSERT' then null else to_jsonb(old) end,
           case when tg_op='DELETE' then null else to_jsonb(new) end);
    return new;
end $$;
drop trigger if exists lfo_sale_audit on lfo.monthly_sale;
create trigger lfo_sale_audit after insert or update on lfo.monthly_sale
for each row execute function lfo.audit_change();
drop trigger if exists lfo_weight_audit on lfo.distributor_weight;
create trigger lfo_weight_audit after insert or update on lfo.distributor_weight
for each row execute function lfo.audit_change();

grant usage on schema lfo to dstt_app,dstt_etl,dstt_read;
grant select on all tables in schema lfo to dstt_app,dstt_etl,dstt_read;
grant insert,update on lfo.distributor,lfo.store,lfo.monthly_sale,
    lfo.distributor_weight,lfo.import_run to dstt_app,dstt_etl;
grant insert on lfo.audit_log to dstt_app,dstt_etl;
grant usage,select on all sequences in schema lfo to dstt_app,dstt_etl;
revoke update,delete on lfo.audit_log from dstt_app,dstt_etl;
revoke delete on lfo.distributor,lfo.store,lfo.monthly_sale,
    lfo.distributor_weight,lfo.import_run from dstt_app,dstt_etl;
comment on schema lfo is 'LFO distributor and branch secondary sales; never part of the primary invoice ledger.';
comment on column lfo.store.is_aggregate is 'Others bucket retains sales but is not a named store and receives no target.';
comment on column lfo.distributor_weight.weight is 'Absolute secondary distributor monthly target from LFO Distributed. NULL means unreported; zero is an explicit zero target. Split equally among named stores; independent of primary LFO targets.';
comment on column lfo.monthly_sale.source_amount is 'Latest imported raw monthly amount. Manual amount overrides remain authoritative on later imports.';
commit;
