-- Optional shipment details for sales entries. Run as the database owner.
-- Requires the existing sales/courier schema and reporting dependencies (01-15).
-- The transaction also refreshes only the two affected views. No separate view
-- refresh is needed for apply_update.py, and existing column positions/grants
-- are retained. Safe to re-run; existing sales and historical OWN/RKB stay intact.
begin;

alter table txn.sales_entry
    add column if not exists weight_kg numeric(12,3),
    add column if not exists transport_service text;

do $$
begin
    if not exists (select 1 from pg_constraint
                   where conrelid = 'txn.sales_entry'::regclass
                     and conname = 'sales_entry_weight_kg_check') then
        alter table txn.sales_entry add constraint sales_entry_weight_kg_check
            check (weight_kg > 0 and weight_kg <= 999999999.999);
    end if;
    if not exists (select 1 from pg_constraint
                   where conrelid = 'txn.sales_entry'::regclass
                     and conname = 'sales_entry_transport_service_check') then
        alter table txn.sales_entry add constraint sales_entry_transport_service_check
            check (transport_service in ('Cargo', 'OnRoad', 'Airway'));
    end if;
end
$$;

-- The existing case-insensitive courier index prevents duplicate codes. On a
-- matching row, retain its ID, code spelling, name and tracking URL; only make
-- the requested carrier available again. Never rewrite another carrier's code.
insert into master.courier (code, name, tracking_url_template, sort_order, is_active) values
    ('DTDC', 'DTDC Express', 'https://www.dtdc.in/tracking/shipment-tracking?awb={docket}', 10, true),
    ('Bluedart', 'Bluedart', null, 20, true),
    ('Pionexxco', 'Pionexxco', null, 30, true),
    ('RK', 'RK', null, 40, true)
on conflict (lower(code)) do update set is_active = true;

comment on column txn.sales_entry.weight_kg is
    'Optional shipment weight in kilograms, greater than zero with up to three decimal places.';
comment on column txn.sales_entry.transport_service is
    'Optional shipment service: Cargo, OnRoad or Airway. Independent of courier.';

-- Keep these definitions aligned with 03_views.sql. New columns are appended.
create or replace view rpt.v_sales as
select
    e.entry_id,
    e.fy,
    fyr.label                as fy_label,
    e.invoice_date,
    e.period_month,
    to_char(e.period_month, 'Mon')   as month_short,
    to_char(e.period_month, 'Mon YY') as month_label,
    e.week_no,
    case e.week_no when 1 then 'Week I'  when 2 then 'Week II' when 3 then 'Week III'
                   when 4 then 'Week IV' when 5 then 'Week V'  end as week_label,
    e.pi_number,
    e.invoice_no,
    e.line_no,

    cu.customer_id,  cu.name              as customer,
    ch.channel_id,   ch.code              as channel,
    sc.sub_channel_id, sc.code            as sub_channel,
    sp.person_id,    sp.full_name         as sales_person,
    sp.is_real_person,
    br.brand_id,     br.name              as brand,

    ci.city_id,      ci.name              as city,
    di.district_id,  di.name              as district,
    st.state_id,     st.name              as state,
    st.zone,

    e.order_type,
    e.primary_qty,
    e.primary_taxable,
    e.primary_with_tax,
    e.secondary_qty,
    e.secondary_value,
    e.tertiary_nos,
    e.tertiary_value,
    e.credit_days,
    e.stock_supplied_on,

    co.courier_id,
    co.code                  as courier,
    co.name                  as courier_name,
    e.courier_docket_no,
    -- The docket as a clickable link where the courier publishes tracking.
    case when co.tracking_url_template is not null and e.courier_docket_no is not null
         then replace(co.tracking_url_template, '{docket}', e.courier_docket_no)
    end                      as tracking_url,
    case when e.stock_supplied_on is not null
         then (current_date - e.stock_supplied_on) end as days_since_dispatch,

    e.payment_received_on,
    -- Outstanding days: only meaningful once stock has gone out and money
    -- has not come back.
    case when e.payment_received_on is null and e.stock_supplied_on is not null
         then (current_date - e.stock_supplied_on) end as days_outstanding,
    case when e.payment_received_on is null and e.stock_supplied_on is not null
              and e.credit_days is not null
              and (current_date - e.stock_supplied_on) > e.credit_days
         then true else false end as is_overdue,
    e.remarks,
    e.source,
    e.created_by, e.created_at, e.updated_by, e.updated_at,
    -- Append shipping details so existing view column positions remain stable.
    e.weight_kg,
    e.transport_service
from txn.sales_entry e
join      master.financial_year fyr on fyr.fy  = e.fy
join      master.customer  cu on cu.customer_id = e.customer_id
join      master.channel   ch on ch.channel_id  = e.channel_id
left join master.sub_channel sc on sc.sub_channel_id = e.sub_channel_id
left join master.sales_person sp on sp.person_id = e.person_id
join      master.brand     br on br.brand_id    = e.brand_id
left join master.city      ci on ci.city_id     = e.city_id
left join master.district  di on di.district_id = e.district_id
join      master.state     st on st.state_id    = e.state_id
left join master.courier   co on co.courier_id  = e.courier_id
where not e.is_deleted;

create or replace view rpt.v_dispatch as
select entry_id, invoice_no, line_no, invoice_date, customer, city, state,
       channel, sales_person, brand, primary_qty as qty,
       primary_with_tax as amount,
       courier, courier_docket_no, tracking_url,
       stock_supplied_on, days_since_dispatch, payment_received_on,
       case when stock_supplied_on is null                     then 'Not dispatched'
            when courier_id is null                            then 'Courier not recorded'
            when courier <> 'OWN' and courier_docket_no is null then 'Docket missing'
            else                                                    'Dispatched' end as dispatch_status,
       weight_kg, transport_service
from rpt.v_sales;

commit;
