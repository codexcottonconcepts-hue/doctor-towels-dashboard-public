-- Reusable customer billing details. Existing invoices and their geography are
-- unchanged: these columns belong to the customer profile, not to the ledger.
begin;
alter table master.customer add column if not exists address text;
alter table master.customer add column if not exists location text;
alter table master.customer add column if not exists state_id integer references master.state on delete restrict;
alter table master.customer add column if not exists district_id integer references master.district on delete set null;
alter table master.customer add column if not exists details_updated_at timestamptz not null default clock_timestamp();
alter table master.customer add column if not exists details_updated_by text;

-- A legacy customer may have only a city. Copy only its known master mapping;
-- do not infer an address or GSTIN from invoices or overwrite a saved profile.
update master.customer cu set state_id=ci.state_id,
    district_id=case when cu.district_id is null then ci.district_id else cu.district_id end
from master.city ci where ci.city_id=cu.city_id and cu.state_id is null;

create or replace function master.fn_customer_details_revision() returns trigger
language plpgsql as $$
begin
    if row(new.name,new.address,new.location,new.gstin,new.state_id,new.district_id,new.city_id,new.is_active)
       is distinct from row(old.name,old.address,old.location,old.gstin,old.state_id,old.district_id,old.city_id,old.is_active) then
        new.details_updated_at=clock_timestamp();
        new.details_updated_by=coalesce(nullif(current_setting('app.username',true),''),current_user);
    end if;
    return new;
end $$;
drop trigger if exists trg_customer_details_revision on master.customer;
create trigger trg_customer_details_revision before update on master.customer
for each row execute function master.fn_customer_details_revision();

-- The older generic audit function recognizes sales/target keys only. Keep its
-- behavior unchanged and give customer records their actual primary key here.
create or replace function sec.fn_customer_details_audit() returns trigger
language plpgsql as $$
declare actor text=coalesce(nullif(current_setting('app.username',true),''),current_user);
begin
    if tg_op='UPDATE' and to_jsonb(new)=to_jsonb(old) then return new; end if;
    insert into sec.audit_log(table_name,row_pk,action,changed_by,old_row,new_row)
    values ('master.customer',new.customer_id::text,tg_op,actor,
            case when tg_op='UPDATE' then to_jsonb(old) else null end,to_jsonb(new));
    return new;
end $$;
drop trigger if exists trg_customer_details_audit on master.customer;
create trigger trg_customer_details_audit after insert or update on master.customer
for each row execute function sec.fn_customer_details_audit();

-- Existing select/insert rights are retained. This migration adds only the
-- profile column update rights; it does not broaden account or ledger access.
grant update(address,location,gstin,state_id,district_id,city_id,details_updated_at,details_updated_by)
    on master.customer to dstt_app;
comment on column master.customer.address is 'Reusable customer billing/street address, entered explicitly.';
comment on column master.customer.location is 'Customer area/locality, distinct from the mapped city.';
comment on column master.customer.details_updated_at is 'Optimistic revision for explicit customer profile edits.';
commit;
