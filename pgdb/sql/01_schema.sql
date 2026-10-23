-- =====================================================================
-- DST & TT  --  Sales / Target / Reporting database
-- 01_schema.sql   structure only; run once on an empty database.
--
-- Run order: 01_schema -> 02_seed -> 03_views -> 04_security
-- =====================================================================

begin;

create schema if not exists master;   -- reference data that changes rarely
create schema if not exists txn;      -- the sales ledger (the fact table)
create schema if not exists tgt;      -- targets, at whatever grain they are set
create schema if not exists rpt;      -- views the dashboard reads
create schema if not exists sec;      -- users, roles, audit trail

comment on schema master is 'Dimension / reference data: channels, people, geography, customers.';
comment on schema txn    is 'Transactional sales ledger. One row per invoice line.';
comment on schema tgt    is 'Targets. Deliberately generic so any mix of dimensions can carry a number.';
comment on schema rpt    is 'Read-only views. The application queries these, never the base tables.';
comment on schema sec    is 'Authentication, authorisation and the audit trail.';


-- =====================================================================
-- MASTER
-- =====================================================================

-- Financial year runs April -> March.  fy = 2026 means FY 2026-27.
create table master.financial_year (
    fy          smallint primary key check (fy between 2000 and 2099),
    label       text     not null unique,
    start_date  date     not null,
    end_date    date     not null,
    is_current  boolean  not null default false,
    check (end_date > start_date)
);
-- Exactly one year may be flagged current, so the dashboard never has to guess.
create unique index financial_year_one_current on master.financial_year ((is_current)) where is_current;


create table master.channel (
    channel_id  serial primary key,
    code        text    not null,          -- LFO, D&R-S, L-LFO, D&R-N, Saloon, Hospital
    name        text    not null,
    sort_order  smallint not null default 100,
    is_active   boolean not null default true
);
create unique index channel_code_uq on master.channel (lower(code));


-- Sub-channel is an independent dimension, not a child of channel. The data
-- allows one retailer to bill under multiple channels. channel_id here is
-- only the usual channel, used to prefill the entry
-- form -- it is never enforced against what is actually billed.
create table master.sub_channel (
    sub_channel_id serial primary key,
    channel_id     int  references master.channel on delete set null,
    code           text not null,          -- a retailer or distributor code
    name           text not null,
    is_active      boolean not null default true
);
create unique index sub_channel_code_uq on master.sub_channel (lower(code));
create index sub_channel_channel_ix on master.sub_channel (channel_id);


-- 'DT / TT' in the sheet.  Kept as a table, not a text column, because every
-- report splits on it and a typo would quietly create a third product line.
create table master.brand (
    brand_id   serial primary key,
    code       text not null,              -- DT, TT
    name       text not null,              -- Doctor Towel, Thirsty Towel
    is_active  boolean not null default true
);
create unique index brand_code_uq on master.brand (lower(code));
create unique index brand_name_uq on master.brand (lower(name));


-- Courier type. A table rather than a text column for the same reason brand
-- is: 'DTDC', 'dtdc' and 'DTDC ' typed on three different days would split
-- one courier into three in every dispatch report.
create table master.courier (
    courier_id serial primary key,
    code       text not null,            -- DTDC, Bluedart, Pionexxco, RK; historical OWN/RKB retained
    name       text not null,
    -- Lets the dashboard turn a docket number into a link instead of dead
    -- text. {docket} is replaced with the number. NULL for own delivery.
    tracking_url_template text,
    sort_order smallint not null default 100,
    is_active  boolean not null default true
);
create unique index courier_code_uq on master.courier (lower(code));


create table master.state (
    state_id  serial primary key,
    name      text not null,
    code      text,                        -- TN, KL, MH ...
    zone      text not null default 'Unassigned'
              check (zone in ('South','North','West','East','Central','North East','Unassigned'))
);
create unique index state_name_uq on master.state (lower(name));


create table master.district (
    district_id serial primary key,
    state_id    int  not null references master.state on delete restrict,
    name        text not null
);
create unique index district_name_uq on master.district (state_id, lower(name));
create index district_state_ix on master.district (state_id);


create table master.city (
    city_id     serial primary key,
    name        text not null,
    state_id    int  not null references master.state on delete restrict,
    district_id int  references master.district on delete set null,
    is_active   boolean not null default true
);
create unique index city_name_uq on master.city (state_id, lower(name));
create index city_state_ix    on master.city (state_id);
create index city_district_ix on master.city (district_id);

-- 'TVM' and 'Trivandrum' are one place. Aliases let entry stay loose while
-- reporting stays exact.
create table master.city_alias (
    alias    text primary key,
    city_id  int not null references master.city on delete cascade
);
create index city_alias_city_ix on master.city_alias (city_id);


create table master.sales_person (
    person_id   serial primary key,
    emp_code    text,
    -- full_name is the name as it appears on invoices, and is what sales rows
    -- are matched on. display_name is what the dashboard shows when the two
    -- differ, for example a full name and a short invoice alias.
    full_name   text not null,
    display_name text,
    channel_id  int  references master.channel on delete set null,
    reports_to  int  references master.sales_person on delete set null,
    -- False for the placeholder rows inherited from the sheet ('LFO',
    -- 'North India', 'Saloon'), which name a channel rather than a person.
    -- They stay so historical rows keep resolving; they are excluded from
    -- every person-wise report.
    is_real_person boolean not null default true,
    is_active   boolean not null default true,
    joined_on   date,
    left_on     date
);
create unique index sales_person_name_uq on master.sales_person (lower(full_name));
create unique index sales_person_code_uq on master.sales_person (lower(emp_code)) where emp_code is not null;


-- Which territories sit under which executive. Nothing else records this --
-- it is an assignment, not something the sales rows imply. A territory is
-- usually a city, but it can also be a complete channel assignment.
-- Kept as text for that reason rather than pointed at master.city.
create table master.person_territory (
    person_id  int  not null references master.sales_person on delete cascade,
    territory  text not null,
    sort_order smallint not null default 100,
    primary key (person_id, territory)
);
create index person_territory_person_ix on master.person_territory (person_id);


create table master.customer (
    customer_id    serial primary key,
    name           text not null,
    sub_channel_id int  references master.sub_channel on delete set null,
    city_id        int  references master.city on delete set null,
    gstin          text,
    credit_days    smallint check (credit_days >= 0),
    is_active      boolean not null default true
);
create unique index customer_name_uq on master.customer (lower(name));
create index customer_city_ix on master.customer (city_id);
create index customer_sub_channel_ix on master.customer (sub_channel_id);


-- =====================================================================
-- TXN  --  the sales ledger
-- =====================================================================

create table txn.sales_entry (
    entry_id        bigserial primary key,

    fy              smallint not null references master.financial_year on delete restrict,
    invoice_date    date     not null,
    -- Derived, never typed. The sheet carried Month and Week as their own
    -- columns and they drifted out of step with the invoice date.
    -- The ::timestamp cast is required: without it the call resolves to the
    -- timestamptz overload of date_trunc, which is only STABLE, and a
    -- generated column will not accept it.
    period_month    date     generated always as (date_trunc('month', invoice_date::timestamp)::date) stored,
    week_no         smallint generated always as
                      (least(5, ((extract(day from invoice_date)::int - 1) / 7) + 1)::smallint) stored,

    pi_number       text,
    invoice_no      text,
    -- One invoice regularly carries more than one line: the same bill for the
    -- same brand appears twice with different quantities because two SKUs
    -- shipped together. 28 such invoices are in the sheet today. Without a
    -- line number the duplicate guard below would reject the second line and
    -- quietly lose the money on it.
    line_no         smallint not null default 1 check (line_no > 0),

    customer_id     int not null references master.customer     on delete restrict,
    channel_id      int not null references master.channel      on delete restrict,
    sub_channel_id  int          references master.sub_channel  on delete restrict,
    person_id       int          references master.sales_person on delete restrict,
    brand_id        int not null references master.brand        on delete restrict,

    -- Geography. city_id is what gets entered; state/district are filled from
    -- it by trigger so the three can never disagree.
    city_id         int          references master.city     on delete restrict,
    state_id        int not null references master.state    on delete restrict,
    district_id     int          references master.district on delete restrict,

    order_type      text not null default 'Re-order' check (order_type in ('First','Re-order')),

    primary_qty       numeric(14,2) not null default 0 check (primary_qty      >= 0),
    primary_taxable   numeric(14,2) not null default 0 check (primary_taxable  >= 0),
    primary_with_tax  numeric(14,2) not null default 0 check (primary_with_tax >= 0),
    secondary_qty     numeric(14,2) not null default 0 check (secondary_qty    >= 0),
    secondary_value   numeric(14,2) not null default 0 check (secondary_value  >= 0),
    tertiary_nos      numeric(14,2) not null default 0 check (tertiary_nos     >= 0),
    tertiary_value    numeric(14,2) not null default 0 check (tertiary_value   >= 0),

    credit_days         smallint check (credit_days >= 0),
    stock_supplied_on   date,

    -- Dispatch. Own delivery has no docket, so the number stays nullable and
    -- the pairing is checked in rpt.v_data_quality rather than refused here --
    -- a docket that arrives an hour after the invoice is normal, and blocking
    -- the entry until it does would push people back into the spreadsheet.
    courier_id          int  references master.courier on delete restrict,
    courier_docket_no   text,
    weight_kg           numeric(12,3) constraint sales_entry_weight_kg_check
                          check (weight_kg > 0 and weight_kg <= 999999999.999),
    transport_service   text constraint sales_entry_transport_service_check
                          check (transport_service in ('Cargo', 'OnRoad', 'Airway')),

    payment_received_on date,
    remarks             text,

    source      text not null default 'web' check (source in ('web','excel_import','sheets_migration','api')),
    is_deleted  boolean not null default false,   -- soft delete; nothing is ever really removed
    created_by  text not null default current_user,
    created_at  timestamptz not null default now(),
    updated_by  text,
    updated_at  timestamptz,

    check (payment_received_on is null or stock_supplied_on is null
           or payment_received_on >= stock_supplied_on)
);

-- The single most valuable constraint in this database: the same invoice line
-- cannot be entered twice. This is the error a shared spreadsheet cannot
-- prevent and the one that quietly inflates a month. line_no is what keeps a
-- genuine second line on the same bill from being mistaken for a duplicate.
create unique index sales_entry_invoice_uq
    on txn.sales_entry (lower(invoice_no), brand_id, line_no)
    where invoice_no is not null and not is_deleted;

create index sales_entry_month_ix    on txn.sales_entry (period_month) where not is_deleted;
create index sales_entry_fy_ix       on txn.sales_entry (fy)           where not is_deleted;
create index sales_entry_person_ix   on txn.sales_entry (person_id, period_month)      where not is_deleted;
create index sales_entry_channel_ix  on txn.sales_entry (channel_id, period_month)     where not is_deleted;
create index sales_entry_sub_ix      on txn.sales_entry (sub_channel_id, period_month) where not is_deleted;
create index sales_entry_state_ix    on txn.sales_entry (state_id, period_month)       where not is_deleted;
create index sales_entry_city_ix     on txn.sales_entry (city_id, period_month)        where not is_deleted;
create index sales_entry_customer_ix on txn.sales_entry (customer_id, period_month)    where not is_deleted;
create index sales_entry_courier_ix  on txn.sales_entry (courier_id, stock_supplied_on) where not is_deleted;
-- 'Where is docket 123456?' has to be instant, and people type it in any case.
create index sales_entry_docket_ix   on txn.sales_entry (lower(courier_docket_no))
    where courier_docket_no is not null and not is_deleted;
-- Covers the matching predicate used by the target engine.
create index sales_entry_scope_ix    on txn.sales_entry
    (period_month, channel_id, sub_channel_id, person_id, state_id, city_id, brand_id)
    where not is_deleted;


-- =====================================================================
-- TGT  --  targets at any grain
-- =====================================================================

-- A plan is one complete set of targets for a year. Keeping plans separate
-- means last year's numbers, a revised mid-year plan and a what-if all live
-- side by side instead of overwriting each other.
create table tgt.target_plan (
    plan_id    serial primary key,
    fy         smallint not null references master.financial_year on delete restrict,
    name       text not null,
    status     text not null default 'draft' check (status in ('draft','active','archived')),
    notes      text,
    created_by text not null default current_user,
    created_at timestamptz not null default now()
);
create unique index target_plan_name_uq on tgt.target_plan (fy, lower(name));
-- One active plan per year: reports must have exactly one answer to
-- 'what is the target'.
create unique index target_plan_one_active on tgt.target_plan (fy) where status = 'active';


-- The heart of the design.
--
-- Every dimension column is nullable and NULL means 'any'. One row can
-- therefore express a target for a channel, for a person, for a person in a
-- city, for a state, for a sub-channel, or for the company as a whole --
-- without a separate table per shape. That is what the old workbook needed
-- nine tabs to say.
create table tgt.target (
    target_id      bigserial primary key,
    plan_id        int  not null references tgt.target_plan on delete cascade,
    period_month   date not null,
    metric         text not null default 'primary_taxable'
                   check (metric in ('primary_taxable','primary_with_tax','primary_qty',
                                     'secondary_value','tertiary_value')),

    channel_id     int references master.channel      on delete cascade,
    sub_channel_id int references master.sub_channel  on delete cascade,
    person_id      int references master.sales_person on delete cascade,
    state_id       int references master.state        on delete cascade,
    district_id    int references master.district     on delete cascade,
    city_id        int references master.city         on delete cascade,
    brand_id       int references master.brand        on delete cascade,

    target_value   numeric(14,2) not null check (target_value >= 0),

    -- Targets overlap on purpose: a state plan and a person's city plan can
    -- cover the same invoices. Only rows flagged is_rollup are added up to
    -- reach a company total, which is how the old 'ALL TOTAL' column picked
    -- its non-overlapping set. Everything else is a drill-down.
    is_rollup      boolean not null default false,

    notes          text,
    created_by     text not null default current_user,
    created_at     timestamptz not null default now(),
    updated_by     text,
    updated_at     timestamptz,

    check (extract(day from period_month) = 1)   -- targets are always monthly
);

-- One number per scope per month per plan. coalesce(...,0) is used because a
-- plain UNIQUE treats two NULLs as different, which would let the same
-- channel-level target be entered twice.
create unique index target_scope_uq on tgt.target (
    plan_id, period_month, metric,
    coalesce(channel_id,0), coalesce(sub_channel_id,0), coalesce(person_id,0),
    coalesce(state_id,0), coalesce(district_id,0), coalesce(city_id,0), coalesce(brand_id,0)
);
create index target_plan_month_ix on tgt.target (plan_id, period_month);
create index target_rollup_ix     on tgt.target (plan_id, period_month) where is_rollup;


-- =====================================================================
-- SEC  --  users, roles, audit
-- =====================================================================

create table sec.app_user (
    user_id      serial primary key,
    username     text not null,
    full_name    text not null,
    email        text,
    pw_hash      text not null,
    -- Links a login to a row in master.sales_person so an executive can be
    -- shown only their own numbers.
    person_id    int references master.sales_person on delete set null,
    is_active    boolean not null default true,
    must_change_pw boolean not null default true,
    created_at   timestamptz not null default now(),
    last_login   timestamptz
);
create unique index app_user_username_uq on sec.app_user (lower(username));

create table sec.role (
    role_code   text primary key,
    name        text not null,
    description text
);

create table sec.user_role (
    user_id   int  not null references sec.app_user on delete cascade,
    role_code text not null references sec.role     on delete cascade,
    primary key (user_id, role_code)
);

-- Replacing a spreadsheet means answering 'who changed this number, and to
-- what' on demand. Without this table that question has no answer.
create table sec.audit_log (
    audit_id   bigserial primary key,
    table_name text not null,
    row_pk     text not null,
    action     text not null check (action in ('INSERT','UPDATE','DELETE')),
    changed_by text not null,
    changed_at timestamptz not null default now(),
    old_row    jsonb,
    new_row    jsonb
);
create index audit_log_table_ix on sec.audit_log (table_name, row_pk);
create index audit_log_when_ix  on sec.audit_log (changed_at desc);


-- =====================================================================
-- TRIGGERS
-- =====================================================================

-- Geography is derived from the city, never typed alongside it. This is what
-- stops 'Aurangabad' -- a city -- from being filed as a state, as it is in
-- the current sheet.
create or replace function txn.fn_fill_geography() returns trigger
language plpgsql as $fn$
declare
    c record;
begin
    if new.city_id is not null then
        select state_id, district_id into c from master.city where city_id = new.city_id;
        if not found then
            raise exception 'city_id % does not exist', new.city_id;
        end if;
        new.state_id    := c.state_id;
        new.district_id := c.district_id;
    end if;

    -- The financial year is April to March, so anything before April belongs
    -- to the year that started the previous April.
    new.fy := case when extract(month from new.invoice_date) >= 4
                   then extract(year from new.invoice_date)::smallint
                   else (extract(year from new.invoice_date) - 1)::smallint end;

    if tg_op = 'UPDATE' then
        new.updated_by := current_user;
        new.updated_at := now();
    end if;
    return new;
end;
$fn$;

create trigger trg_sales_entry_geo
    before insert or update on txn.sales_entry
    for each row execute function txn.fn_fill_geography();


-- The primary key is pulled out of the row's jsonb rather than by field name.
-- PL/pgSQL resolves record fields for every branch of a CASE expression, not
-- just the branch that runs, so naming new.target_id here would break every
-- insert into sales_entry -- a table that has no such column.
create or replace function sec.fn_audit() returns trigger
language plpgsql as $fn$
declare
    j_old jsonb;
    j_new jsonb;
    pkcol text;
begin
    pkcol := case tg_table_name
               when 'sales_entry' then 'entry_id'
               when 'target'      then 'target_id'
               else null end;

    if tg_op <> 'INSERT' then j_old := to_jsonb(old); end if;
    if tg_op <> 'DELETE' then j_new := to_jsonb(new); end if;

    insert into sec.audit_log (table_name, row_pk, action, changed_by, old_row, new_row)
    values (tg_table_schema || '.' || tg_table_name,
            coalesce(j_new ->> pkcol, j_old ->> pkcol, '?'),
            tg_op, current_user, j_old, j_new);

    if tg_op = 'DELETE' then
        return old;
    end if;
    return new;
end;
$fn$;

create trigger trg_sales_entry_audit
    after insert or update or delete on txn.sales_entry
    for each row execute function sec.fn_audit();

create trigger trg_target_audit
    after insert or update or delete on tgt.target
    for each row execute function sec.fn_audit();

commit;
