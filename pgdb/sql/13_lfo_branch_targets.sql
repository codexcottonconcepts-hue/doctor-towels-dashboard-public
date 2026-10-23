-- Branch plans retain the exact Month and Year amounts from LFO Distributed.
-- Unmatched names remain plans; they never create stores or sales.
begin;
create table if not exists lfo.branch_target_plan (
    plan_id bigserial primary key,
    distributor_id integer not null references lfo.distributor on delete restrict,
    fy integer not null check (fy between 2000 and 2099),
    source_name text not null check (btrim(source_name)<>''),
    normalized_name text not null check (btrim(normalized_name)<>''),
    monthly_target numeric(18,2) check (monthly_target>=0 and monthly_target<1000000000000),
    yearly_target numeric(18,2) check (yearly_target>=0 and yearly_target<1000000000000),
    store_id integer references lfo.store on delete restrict,
    is_active boolean not null default true,
    source_id text not null,
    sheet_name text not null,
    source_month_cell text not null,
    source_year_cell text not null,
    import_id bigint not null references lfo.import_run on delete restrict,
    updated_at timestamptz not null default now(),
    updated_by text not null,
    unique(distributor_id,fy,normalized_name)
);
create unique index if not exists lfo_branch_target_active_store_uq
    on lfo.branch_target_plan(distributor_id,fy,store_id)
    where is_active and store_id is not null;
create or replace function lfo.validate_branch_target_store() returns trigger language plpgsql as $$
begin
    if new.store_id is not null and not exists (
        select 1 from lfo.store where store_id=new.store_id
        and distributor_id=new.distributor_id and not is_aggregate
    ) then
        raise exception 'Branch target store must be a named store within its distributor';
    end if;
    return new;
end $$;
drop trigger if exists lfo_branch_target_store_check on lfo.branch_target_plan;
create trigger lfo_branch_target_store_check before insert or update on lfo.branch_target_plan
for each row execute function lfo.validate_branch_target_store();
drop trigger if exists lfo_branch_target_audit on lfo.branch_target_plan;
create trigger lfo_branch_target_audit after insert or update on lfo.branch_target_plan
for each row execute function lfo.audit_change();
grant select on lfo.branch_target_plan to dstt_app,dstt_etl,dstt_read;
grant insert,update on lfo.branch_target_plan to dstt_app,dstt_etl;
grant usage,select on sequence lfo.branch_target_plan_plan_id_seq to dstt_app,dstt_etl;
revoke delete on lfo.branch_target_plan from dstt_app,dstt_etl;
comment on table lfo.branch_target_plan is 'Exact source branch Monthly and Year targets; no scaling or equal allocation. Unmapped source names remain visible plans. Missing plans in a complete FY import are deactivated, never deleted.';
commit;
