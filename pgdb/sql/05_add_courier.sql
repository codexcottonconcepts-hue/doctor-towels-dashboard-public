-- =====================================================================
-- 05_add_courier.sql   Courier type and docket number on the sales ledger.
--
-- ONLY needed if you already created the database before this change.
-- A fresh install picks it up from 01_schema.sql and 02_seed.sql, and
-- running this afterwards is harmless either way.
--
--     psql -h <host> -U <user> -d dstt -v ON_ERROR_STOP=1 -f sql/05_add_courier.sql
--     psql -h <host> -U <user> -d dstt -v ON_ERROR_STOP=1 -f sql/03_views.sql
--
-- Re-run 03_views.sql afterwards: the views are replaced, not altered, so
-- they need to be rebuilt to see the new columns.
-- =====================================================================

begin;

create table if not exists master.courier (
    courier_id serial primary key,
    code       text not null,
    name       text not null,
    tracking_url_template text,
    sort_order smallint not null default 100,
    is_active  boolean not null default true
);
create unique index if not exists courier_code_uq on master.courier (lower(code));

insert into master.courier (code, name, tracking_url_template, sort_order) values
    ('DTDC', 'DTDC Express', 'https://www.dtdc.in/tracking/shipment-tracking?awb={docket}', 10),
    ('RKB',  'RKB Logistics', null, 20),
    ('OWN',  'Own Delivery',  null, 30)
on conflict do nothing;

alter table txn.sales_entry
    add column if not exists courier_id        int references master.courier on delete restrict,
    add column if not exists courier_docket_no text;

create index if not exists sales_entry_courier_ix
    on txn.sales_entry (courier_id, stock_supplied_on) where not is_deleted;
create index if not exists sales_entry_docket_ix
    on txn.sales_entry (lower(courier_docket_no))
    where courier_docket_no is not null and not is_deleted;

grant select on master.courier to dstt_app, dstt_read, dstt_etl;
grant insert, update on master.courier to dstt_app, dstt_etl;
grant usage, select on sequence master.courier_courier_id_seq to dstt_app, dstt_etl;

commit;
