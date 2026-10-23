-- =====================================================================
-- 03_views.sql   Everything the application reads.
--
-- The app should query rpt.* only. Base tables can then be reshaped
-- without touching application code.
-- =====================================================================

begin;

-- ---------------------------------------------------------------------
-- The sales ledger, flattened, with names instead of ids.
-- This is what the 'Data Entry' grid and every export is built on.
-- ---------------------------------------------------------------------
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

comment on view rpt.v_sales is 'The sales ledger with every dimension resolved to its name. Soft-deleted rows are already excluded.';


-- ---------------------------------------------------------------------
-- Targets, with names and a readable description of what each one covers.
-- ---------------------------------------------------------------------
create or replace view rpt.v_target as
select
    t.target_id,
    t.plan_id,
    p.fy,
    p.name        as plan_name,
    p.status      as plan_status,
    t.period_month,
    to_char(t.period_month, 'Mon YY') as month_label,
    t.metric,
    t.target_value,
    t.is_rollup,

    t.channel_id,     ch.code      as channel,
    t.sub_channel_id, sc.code      as sub_channel,
    t.person_id,      sp.full_name as sales_person,
    t.state_id,       st.name      as state,
    t.district_id,    di.name      as district,
    t.city_id,        ci.name      as city,
    t.brand_id,       br.name      as brand,

    -- A stable identity for 'the same target across months', used to run
    -- cumulative totals down a financial year.
    concat_ws('|', coalesce(t.channel_id,0), coalesce(t.sub_channel_id,0),
                   coalesce(t.person_id,0),  coalesce(t.state_id,0),
                   coalesce(t.district_id,0),coalesce(t.city_id,0),
                   coalesce(t.brand_id,0),   t.metric) as scope_key,

    -- Which dimensions this target constrains, for grouping in the UI.
    coalesce(nullif(concat_ws(' + ',
        case when t.channel_id     is not null then 'Channel'     end,
        case when t.sub_channel_id is not null then 'Sub Channel' end,
        case when t.person_id      is not null then 'Person'      end,
        case when t.state_id       is not null then 'State'       end,
        case when t.district_id    is not null then 'District'    end,
        case when t.city_id        is not null then 'City'        end,
        case when t.brand_id       is not null then 'Brand'       end), ''),
        'Company') as scope_level,

    -- What the row is called on screen.
    coalesce(nullif(concat_ws(' / ', sp.full_name, ch.code, sc.code,
                                     ci.name, di.name, st.name, br.name), ''),
             'Company Total') as scope_label,
    t.notes
from tgt.target t
join      tgt.target_plan   p  on p.plan_id        = t.plan_id
left join master.channel    ch on ch.channel_id    = t.channel_id
left join master.sub_channel sc on sc.sub_channel_id = t.sub_channel_id
left join master.sales_person sp on sp.person_id   = t.person_id
left join master.state      st on st.state_id      = t.state_id
left join master.district   di on di.district_id   = t.district_id
left join master.city       ci on ci.city_id       = t.city_id
left join master.brand      br on br.brand_id      = t.brand_id;


-- ---------------------------------------------------------------------
-- THE TARGET ENGINE
--
-- For each target row, add up the sales that fall inside its scope. A NULL
-- dimension on the target means 'any value of that dimension', so one
-- predicate serves a company target, a channel target, a state target and a
-- person-in-a-city target alike.
--
-- Note the metric: achievement is measured on TAXABLE value (without tax)
-- by default, which is the rule the existing dashboard already uses.
-- ---------------------------------------------------------------------
create or replace view rpt.v_target_achievement as
select
    v.*,
    coalesce(a.achieved, 0)                          as achieved,
    coalesce(a.achieved, 0) - v.target_value         as variance,
    case when v.target_value > 0
         then round(coalesce(a.achieved,0) * 100.0 / v.target_value, 1)
         else null end                               as achieved_pct,
    coalesce(a.invoice_count, 0)                     as invoice_count,

    -- How far through the month we are, so a mid-month number can be read
    -- against a mid-month expectation instead of the full target.
    case
      when v.period_month > date_trunc('month', current_date)::date then 0
      when v.period_month < date_trunc('month', current_date)::date then 100
      else round(extract(day from current_date) * 100.0
                 / extract(day from (v.period_month + interval '1 month - 1 day')), 1)
    end                                              as month_elapsed_pct,

    case when v.target_value > 0 and v.period_month = date_trunc('month', current_date)::date
         then round(coalesce(a.achieved,0)
                    * extract(day from (v.period_month + interval '1 month - 1 day'))
                    / greatest(extract(day from current_date), 1), 0)
         end                                         as run_rate_projection,

    case
      when v.target_value = 0                                       then 'No target'
      when coalesce(a.achieved,0) >= v.target_value                 then 'Achieved'
      when coalesce(a.achieved,0) >= v.target_value * 0.90          then 'Near'
      when coalesce(a.achieved,0) >= v.target_value * 0.70          then 'Behind'
      else                                                               'Critical'
    end                                              as status
from rpt.v_target v
left join lateral (
    select sum(case v.metric
                 when 'primary_taxable'  then s.primary_taxable
                 when 'primary_with_tax' then s.primary_with_tax
                 when 'primary_qty'      then s.primary_qty
                 when 'secondary_value'  then s.secondary_value
                 when 'tertiary_value'   then s.tertiary_value
               end)        as achieved,
           count(*)        as invoice_count
    from txn.sales_entry s
    where not s.is_deleted
      and s.period_month = v.period_month
      and (v.channel_id     is null or s.channel_id     = v.channel_id)
      and (v.sub_channel_id is null or s.sub_channel_id = v.sub_channel_id)
      and (v.person_id      is null or s.person_id      = v.person_id)
      and (v.state_id       is null or s.state_id       = v.state_id)
      and (v.district_id    is null or s.district_id    = v.district_id)
      and (v.city_id        is null or s.city_id        = v.city_id)
      and (v.brand_id       is null or s.brand_id       = v.brand_id)
) a on true;

comment on view rpt.v_target_achievement is
'Target vs actual at whatever grain the target was set. NULL dimension = any value.';


-- ---------------------------------------------------------------------
-- The same thing cumulative down the financial year.
-- ---------------------------------------------------------------------
create or replace view rpt.v_target_achievement_ytd as
select
    a.*,
    sum(a.target_value) over w as target_ytd,
    sum(a.achieved)     over w as achieved_ytd,
    case when sum(a.target_value) over w > 0
         then round(sum(a.achieved) over w * 100.0 / sum(a.target_value) over w, 1)
         end as achieved_ytd_pct
from rpt.v_target_achievement a
window w as (partition by a.plan_id, a.scope_key
             order by a.period_month
             rows between unbounded preceding and current row);


-- ---------------------------------------------------------------------
-- Company roll-up: only the targets flagged is_rollup are summed, because
-- targets deliberately overlap (a state and a person's city can cover the
-- same invoices). Actuals are summed from the ledger directly, which is the
-- honest number and a useful cross-check on the flags.
-- ---------------------------------------------------------------------
create or replace view rpt.v_company_month as
select
    p.plan_id,
    p.fy,
    m.period_month,
    to_char(m.period_month, 'Mon YY')            as month_label,
    coalesce(t.target_value, 0)                  as target_value,
    coalesce(s.achieved, 0)                      as achieved,
    coalesce(s.achieved,0) - coalesce(t.target_value,0) as variance,
    case when coalesce(t.target_value,0) > 0
         then round(coalesce(s.achieved,0) * 100.0 / t.target_value, 1) end as achieved_pct
from tgt.target_plan p
cross join lateral (
    select generate_series(fyr.start_date, fyr.end_date, interval '1 month')::date as period_month
    from master.financial_year fyr where fyr.fy = p.fy
) m
left join lateral (
    select sum(t.target_value) as target_value
    from tgt.target t
    where t.plan_id = p.plan_id and t.period_month = m.period_month
      and t.is_rollup and t.metric = 'primary_taxable'
) t on true
left join lateral (
    select sum(e.primary_taxable) as achieved
    from txn.sales_entry e
    where not e.is_deleted and e.period_month = m.period_month
) s on true;


-- ---------------------------------------------------------------------
-- Plain actuals, sliced the four ways the business asks for. These back the
-- report tabs and need no target to exist.
-- ---------------------------------------------------------------------
create or replace view rpt.v_month_by_channel as
select period_month, month_label, fy, channel_id, channel,
       sum(primary_qty)      as qty,
       sum(primary_taxable)  as taxable,
       sum(primary_with_tax) as with_tax,
       count(*)              as invoices,
       count(distinct customer_id) as customers
from rpt.v_sales group by 1,2,3,4,5;

create or replace view rpt.v_month_by_sub_channel as
select period_month, month_label, fy, sub_channel_id, sub_channel, channel,
       sum(primary_qty) as qty, sum(primary_taxable) as taxable,
       sum(primary_with_tax) as with_tax, count(*) as invoices
from rpt.v_sales group by 1,2,3,4,5,6;

create or replace view rpt.v_month_by_person as
select period_month, month_label, fy, person_id, sales_person,
       sum(primary_qty) as qty, sum(primary_taxable) as taxable,
       sum(primary_with_tax) as with_tax, count(*) as invoices,
       count(distinct customer_id) as customers
from rpt.v_sales where is_real_person group by 1,2,3,4,5;

create or replace view rpt.v_month_by_geo as
select period_month, month_label, fy, zone, state_id, state,
       district_id, district, city_id, city,
       sum(primary_qty) as qty, sum(primary_taxable) as taxable,
       sum(primary_with_tax) as with_tax, count(*) as invoices
from rpt.v_sales group by 1,2,3,4,5,6,7,8,9,10;

create or replace view rpt.v_month_by_brand as
select period_month, month_label, fy, brand_id, brand,
       sum(primary_qty) as qty, sum(primary_taxable) as taxable,
       sum(primary_with_tax) as with_tax, count(*) as invoices
from rpt.v_sales group by 1,2,3,4,5;


-- ---------------------------------------------------------------------
-- New vs repeat, and the customers who have gone quiet. Both come free
-- from the ledger and neither exists in the sheet today.
-- ---------------------------------------------------------------------
create or replace view rpt.v_customer_activity as
select
    customer_id, customer, channel, sub_channel, city, state, sales_person,
    min(invoice_date)          as first_invoice,
    max(invoice_date)          as last_invoice,
    current_date - max(invoice_date) as days_since_last,
    count(*)                   as invoices,
    sum(primary_taxable)       as lifetime_taxable,
    case when current_date - max(invoice_date) > 90 then true else false end as is_dormant
from rpt.v_sales
group by 1,2,3,4,5,6,7;


-- ---------------------------------------------------------------------
-- Collections: what has shipped and not been paid for.
-- ---------------------------------------------------------------------
create or replace view rpt.v_receivables as
select entry_id, invoice_no, invoice_date, customer, channel, sales_person,
       state, city, primary_with_tax as amount, credit_days,
       stock_supplied_on, courier, courier_docket_no, tracking_url,
       days_outstanding, is_overdue,
       case when days_outstanding is null      then 'Not shipped'
            when days_outstanding <= 30        then '0-30'
            when days_outstanding <= 60        then '31-60'
            when days_outstanding <= 90        then '61-90'
            else                                    '90+' end as ageing_bucket
from rpt.v_sales
where payment_received_on is null;


-- ---------------------------------------------------------------------
-- Dispatch. What has gone out, with whom, and what has not gone out at all.
-- ---------------------------------------------------------------------
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

-- Volume and value per courier per month, for negotiating rates and for
-- noticing when one courier is quietly carrying everything.
create or replace view rpt.v_month_by_courier as
select period_month, month_label, fy, courier_id, courier,
       count(*)                          as consignments,
       count(distinct invoice_no)        as invoices,
       sum(primary_qty)                  as qty,
       sum(primary_with_tax)             as value,
       round(avg(days_since_dispatch), 1) as avg_days_since_dispatch
from rpt.v_sales
where stock_supplied_on is not null
group by 1,2,3,4,5;


-- ---------------------------------------------------------------------
-- The sales-person / territory map, in the shape the ROTN page wants.
-- 'ledger' is the name invoices carry and is what rows are matched on;
-- 'name' is what the page displays.
-- ---------------------------------------------------------------------
create or replace view rpt.v_person_territory as
select sp.person_id,
       coalesce(nullif(btrim(sp.display_name), ''), sp.full_name) as name,
       sp.full_name                                               as ledger,
       array_agg(pt.territory order by pt.sort_order, pt.territory) as territories
from master.sales_person sp
join master.person_territory pt on pt.person_id = sp.person_id
where sp.is_active and sp.is_real_person
group by sp.person_id, 2, 3;


-- ---------------------------------------------------------------------
-- Data quality. Run this before trusting a month's close.
-- ---------------------------------------------------------------------
create or replace view rpt.v_data_quality as
select 'Missing sales person'   as issue, entry_id, invoice_no, invoice_date, customer
from rpt.v_sales where person_id is null
union all
select 'Missing city',           entry_id, invoice_no, invoice_date, customer
from rpt.v_sales where city_id is null
union all
select 'Missing sub channel',    entry_id, invoice_no, invoice_date, customer
from rpt.v_sales where sub_channel_id is null
union all
select 'Zero value invoice',     entry_id, invoice_no, invoice_date, customer
from rpt.v_sales where primary_taxable = 0 and primary_qty = 0
union all
select 'Taxable exceeds with-tax', entry_id, invoice_no, invoice_date, customer
from rpt.v_sales where primary_taxable > primary_with_tax and primary_with_tax > 0
union all
select 'No invoice number',      entry_id, invoice_no, invoice_date, customer
from rpt.v_sales where invoice_no is null or btrim(invoice_no) = ''
union all
-- Dispatched, but nobody recorded who carried it.
select 'Dispatched, no courier', entry_id, invoice_no, invoice_date, customer
from rpt.v_sales where stock_supplied_on is not null and courier_id is null
union all
-- A courier consignment with no docket cannot be traced when it goes missing.
select 'Courier without docket', entry_id, invoice_no, invoice_date, customer
from rpt.v_sales where courier is not null and courier <> 'OWN'
                   and (courier_docket_no is null or btrim(courier_docket_no) = '')
union all
-- A docket recorded against an invoice that never shipped.
select 'Docket but no dispatch date', entry_id, invoice_no, invoice_date, customer
from rpt.v_sales where courier_docket_no is not null and stock_supplied_on is null
union all
-- Two lines of one invoice ship together under one docket. Different numbers
-- on the same bill means one of them belongs to a different invoice.
select 'Invoice lines disagree on docket', entry_id, invoice_no, invoice_date, customer
from rpt.v_sales s
where invoice_no is not null and courier_docket_no is not null
  and exists (select 1 from rpt.v_sales o
              where o.invoice_no = s.invoice_no and o.entry_id <> s.entry_id
                and o.courier_docket_no is not null
                and lower(o.courier_docket_no) <> lower(s.courier_docket_no))
union all
-- The same docket on two different customers' invoices is a typo.
select 'Docket reused across customers', entry_id, invoice_no, invoice_date, customer
from rpt.v_sales s
where courier_docket_no is not null
  and exists (select 1 from rpt.v_sales o
              where lower(o.courier_docket_no) = lower(s.courier_docket_no)
                and o.customer_id <> s.customer_id);

commit;
