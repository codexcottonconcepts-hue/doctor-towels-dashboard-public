-- Optional reusable customer telephone/contact number. This belongs to the
-- customer profile and does not modify invoices, sales totals or PI payloads.
begin;
alter table master.customer add column if not exists contact_number text;

-- Phone-only corrections must invalidate stale customer edit forms too.
create or replace function master.fn_customer_details_revision() returns trigger
language plpgsql as $$
begin
    if row(new.name,new.address,new.location,new.gstin,new.contact_number,new.state_id,new.district_id,new.city_id,new.is_active)
       is distinct from row(old.name,old.address,old.location,old.gstin,old.contact_number,old.state_id,old.district_id,old.city_id,old.is_active) then
        new.details_updated_at=clock_timestamp();
        new.details_updated_by=coalesce(nullif(current_setting('app.username',true),''),current_user);
    end if;
    return new;
end $$;
grant update(contact_number) on master.customer to dstt_app;
comment on column master.customer.contact_number is 'Optional reusable customer contact number; international and landline formatting are preserved.';
commit;
