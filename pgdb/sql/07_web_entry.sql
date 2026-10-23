-- Web entry integrity and audit attribution. Safe to re-run as the owner.
-- Run 04_security.sql first. Existing rows are preserved; these triggers
-- validate new and edited records, not a bulk repair of historical data.
begin;

-- The requested RK carrier is distinct from legacy RKB. Never relabel old
-- consignments. The web form offers only active DTDC, OWN and RK records.
insert into master.courier (code, name, sort_order) values
    ('RK', 'RK', 30)
on conflict do nothing;

create or replace function master.fn_validate_city_geography() returns trigger
language plpgsql as $fn$
begin
    if new.district_id is not null and not exists (
        select 1 from master.district d
        where d.district_id = new.district_id and d.state_id = new.state_id
    ) then
        raise exception 'District must belong to the selected state'
            using errcode = '23514';
    end if;
    return new;
end;
$fn$;
drop trigger if exists trg_city_geography on master.city;
create trigger trg_city_geography before insert or update on master.city
    for each row execute function master.fn_validate_city_geography();

create or replace function txn.fn_fill_geography() returns trigger
language plpgsql as $fn$
declare
    c record;
    actor text := coalesce(nullif(current_setting('app.username', true), ''), current_user);
begin
    if new.city_id is not null then
        select state_id, district_id into c from master.city where city_id = new.city_id;
        if not found then
            raise exception 'city_id % does not exist', new.city_id;
        end if;
        new.state_id := c.state_id;
        new.district_id := c.district_id;
    end if;
    if new.district_id is not null and not exists (
        select 1 from master.district d
        where d.district_id = new.district_id and d.state_id = new.state_id
    ) then
        raise exception 'District must belong to the selected state'
            using errcode = '23514';
    end if;
    new.fy := case when extract(month from new.invoice_date) >= 4
                   then extract(year from new.invoice_date)::smallint
                   else (extract(year from new.invoice_date) - 1)::smallint end;
    if tg_op = 'UPDATE' then
        new.updated_by := actor;
        new.updated_at := now();
    else
        new.created_by := actor;
    end if;
    return new;
end;
$fn$;

create or replace function tgt.fn_validate_target() returns trigger
language plpgsql as $fn$
declare
    yr record;
    c record;
    actor text := coalesce(nullif(current_setting('app.username', true), ''), current_user);
begin
    select f.start_date, f.end_date into yr
    from tgt.target_plan p join master.financial_year f using (fy)
    where p.plan_id = new.plan_id;
    if not found then
        raise exception 'Target plan does not exist' using errcode = '23503';
    end if;
    if new.period_month < yr.start_date or new.period_month > yr.end_date then
        raise exception 'Target month must fall within the plan financial year'
            using errcode = '23514';
    end if;
    if new.district_id is not null and new.state_id is not null and not exists (
        select 1 from master.district d
        where d.district_id = new.district_id and d.state_id = new.state_id
    ) then
        raise exception 'Target district must belong to the selected state'
            using errcode = '23514';
    end if;
    if new.city_id is not null then
        select state_id, district_id into c from master.city where city_id = new.city_id;
        if not found then
            raise exception 'Target city does not exist' using errcode = '23503';
        end if;
        if new.state_id is not null and new.state_id <> c.state_id then
            raise exception 'Target city must belong to the selected state'
                using errcode = '23514';
        end if;
        if new.district_id is not null and new.district_id is distinct from c.district_id then
            raise exception 'Target city must belong to the selected district'
                using errcode = '23514';
        end if;
    end if;
    -- Sub-channel's usual channel is a default, not a required relationship.
    if tg_op = 'UPDATE' then
        new.updated_by := actor;
        new.updated_at := now();
    else
        new.created_by := actor;
    end if;
    return new;
end;
$fn$;
drop trigger if exists trg_target_integrity on tgt.target;
create trigger trg_target_integrity before insert or update on tgt.target
    for each row execute function tgt.fn_validate_target();

create or replace function tgt.fn_validate_plan_year() returns trigger
language plpgsql as $fn$
begin
    if new.fy is distinct from old.fy and exists (
        select 1 from tgt.target t join master.financial_year f on f.fy = new.fy
        where t.plan_id = new.plan_id
          and (t.period_month < f.start_date or t.period_month > f.end_date)
    ) then
        raise exception 'Existing target months do not fit the new financial year'
            using errcode = '23514';
    end if;
    return new;
end;
$fn$;
drop trigger if exists trg_target_plan_year on tgt.target_plan;
create trigger trg_target_plan_year before update of fy on tgt.target_plan
    for each row execute function tgt.fn_validate_plan_year();

create or replace function sec.fn_audit() returns trigger
language plpgsql as $fn$
declare
    j_old jsonb;
    j_new jsonb;
    pkcol text;
    actor text := coalesce(nullif(current_setting('app.username', true), ''), current_user);
begin
    pkcol := case tg_table_name when 'sales_entry' then 'entry_id'
                               when 'target' then 'target_id' else null end;
    if tg_op <> 'INSERT' then j_old := to_jsonb(old); end if;
    if tg_op <> 'DELETE' then j_new := to_jsonb(new); end if;
    insert into sec.audit_log (table_name, row_pk, action, changed_by, old_row, new_row)
    values (tg_table_schema || '.' || tg_table_name,
            coalesce(j_new ->> pkcol, j_old ->> pkcol, '?'),
            tg_op, actor, j_old, j_new);
    if tg_op = 'DELETE' then return old; end if;
    return new;
end;
$fn$;

-- Repeat the narrow ETL grants so applying this upgrade also repairs the
-- previous missing trigger/target-plan permissions. No account-table access.
grant usage on schema sec to dstt_etl;
grant insert on sec.audit_log to dstt_etl;
grant usage, select on sequence sec.audit_log_audit_id_seq to dstt_etl;
grant select, insert, update on tgt.target_plan to dstt_etl;

commit;
