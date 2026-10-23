-- Optional product invoices beneath an existing, single-brand sales entry.
-- No legacy sales are changed; rpt.v_sales remains one row per sales entry.
-- Safe to re-run as owner. Import the approved catalog separately afterwards.
begin;

create table if not exists master.product_catalog (
    product_id serial primary key,
    catalog_key text not null unique check (length(catalog_key) between 1 and 100),
    product_name text not null check (length(product_name) between 1 and 1000),
    unit text check (length(unit) between 1 and 50),
    mrp numeric(14,2) check (mrp between 0 and 999999999999.99),
    common_mrp numeric(14,2) check (common_mrp between 0 and 999999999999.99),
    north_retail numeric(14,2) check (north_retail between 0 and 999999999999.99),
    gifting numeric(14,2) check (gifting between 0 and 999999999999.99),
    lulu_retail numeric(14,2) check (lulu_retail between 0 and 999999999999.99),
    is_active boolean not null default true,
    source_payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz
);

create table if not exists txn.sales_invoice (
    entry_id bigint primary key references txn.sales_entry(entry_id) on delete restrict,
    price_list text not null check (price_list in ('common_mrp','north_retail','gifting','lulu_retail')),
    gst_rate numeric(5,2) not null default 18.00 check (gst_rate = 18.00),
    created_at timestamptz not null default now(),
    updated_at timestamptz
);

create table if not exists txn.sales_invoice_item (
    item_id bigserial primary key,
    entry_id bigint not null references txn.sales_invoice(entry_id) on delete restrict,
    position smallint not null check (position between 1 and 100),
    product_id integer not null references master.product_catalog(product_id) on delete restrict,
    catalog_key text not null,
    product_name text not null check (length(product_name) between 1 and 1000),
    unit text check (length(unit) between 1 and 50),
    mrp numeric(14,2) check (mrp between 0 and 999999999999.99),
    price_list_rate numeric(14,2) check (price_list_rate between 0 and 999999999999.99),
    quantity numeric(14,2) not null check (quantity > 0 and quantity <= 999999999999.99),
    unit_rate numeric(14,2) not null check (unit_rate between 0 and 999999999999.99),
    discount_percent numeric(5,2) not null check (discount_percent between 0 and 100),
    gross_amount numeric(14,2) not null check (gross_amount between 0 and 999999999999.99),
    discount_amount numeric(14,2) not null check (discount_amount between 0 and 999999999999.99),
    taxable_amount numeric(14,2) not null check (taxable_amount between 0 and 999999999999.99),
    gst_amount numeric(14,2) not null check (gst_amount between 0 and 999999999999.99),
    total_amount numeric(14,2) not null check (total_amount between 0 and 999999999999.99),
    unique(entry_id,position),
    check (gross_amount = round(quantity * unit_rate, 2)),
    check (discount_amount = round(gross_amount * discount_percent / 100, 2)),
    check (total_amount = gross_amount - discount_amount),
    check (taxable_amount = round(total_amount / 1.18, 2)),
    check (gst_amount = total_amount - taxable_amount)
);

comment on table txn.sales_invoice is
    'One optional catalog-invoice header for a single-brand sales entry. Parent totals alone feed sales reports.';
comment on table txn.sales_invoice_item is
    'Saved product/rate snapshots. Rates include 18 percent GST; discounts apply before extracting GST. Never join directly into rpt.v_sales.';

-- A Sheets-linked entry remains a summary while that source owns its amounts.
-- A conversion with unchanged amounts would not count as a local value change
-- to the three-way synchronizer, so the next sheet edit could break the invoice.
create or replace function txn.fn_validate_product_invoice_owner() returns trigger
language plpgsql as $$
declare parent record;
begin
    select source,is_deleted into parent from txn.sales_entry where entry_id=new.entry_id for update;
    if not found or parent.is_deleted then
        raise exception 'Choose an active sales entry for the product invoice' using errcode='23514';
    end if;
    if parent.source='sheets_migration' or exists (
        select 1 from aux.sheet_sales_row where entry_id=new.entry_id
    ) then
        raise exception 'A Google Sheets sales entry must remain a summary entry' using errcode='23514';
    end if;
    return new;
end;
$$;
drop trigger if exists trg_product_invoice_owner on txn.sales_invoice;
create trigger trg_product_invoice_owner before insert or update of entry_id on txn.sales_invoice
    for each row execute function txn.fn_validate_product_invoice_owner();

-- All item edits use the same parent lock/revision and transaction as sales.
-- At transaction end, prevent independent writes from drifting the totals.
create or replace function txn.fn_check_product_invoice_totals() returns trigger
language plpgsql as $$
declare
    parent_id bigint;
    parent_ids bigint[];
    sale record;
    sums record;
begin
    parent_ids := case when tg_op = 'INSERT' then array[new.entry_id]
                       when tg_op = 'DELETE' then array[old.entry_id]
                       else array[old.entry_id,new.entry_id] end;
    foreach parent_id in array parent_ids loop
    if not exists (select 1 from txn.sales_invoice where entry_id=parent_id) then
        continue;
    end if;
    select primary_qty,primary_taxable,primary_with_tax into sale
      from txn.sales_entry where entry_id=parent_id;
    select count(*) as lines,sum(quantity) as quantity,sum(taxable_amount) as taxable,
           sum(total_amount) as total into sums
      from txn.sales_invoice_item where entry_id=parent_id;
    if sums.lines not between 1 and 100 or
       sale.primary_qty is distinct from sums.quantity or
       sale.primary_taxable is distinct from sums.taxable or
       sale.primary_with_tax is distinct from sums.total then
        raise exception 'Product invoice totals must match its sales entry'
            using errcode='23514';
    end if;
    end loop;
    return null;
end;
$$;

drop trigger if exists trg_sales_product_invoice_totals on txn.sales_entry;
create constraint trigger trg_sales_product_invoice_totals
    after insert or update on txn.sales_entry deferrable initially deferred
    for each row execute function txn.fn_check_product_invoice_totals();
drop trigger if exists trg_invoice_product_totals on txn.sales_invoice;
create constraint trigger trg_invoice_product_totals
    after insert or update on txn.sales_invoice deferrable initially deferred
    for each row execute function txn.fn_check_product_invoice_totals();
drop trigger if exists trg_invoice_item_totals on txn.sales_invoice_item;
create constraint trigger trg_invoice_item_totals
    after insert or update or delete on txn.sales_invoice_item deferrable initially deferred
    for each row execute function txn.fn_check_product_invoice_totals();

create or replace function sec.fn_product_invoice_audit() returns trigger
language plpgsql as $$
declare
    before_row jsonb;
    after_row jsonb;
    actor text := coalesce(nullif(current_setting('app.username',true),''),current_user);
begin
    if tg_op <> 'INSERT' then before_row := to_jsonb(old); end if;
    if tg_op <> 'DELETE' then after_row := to_jsonb(new); end if;
    insert into sec.audit_log(table_name,row_pk,action,changed_by,old_row,new_row)
      values(tg_table_schema||'.'||tg_table_name,
             coalesce(after_row->>tg_argv[0],before_row->>tg_argv[0]),
             tg_op,actor,before_row,after_row);
    if tg_op='DELETE' then return old; end if;
    return new;
end;
$$;

drop trigger if exists trg_product_catalog_audit on master.product_catalog;
create trigger trg_product_catalog_audit after insert or update or delete on master.product_catalog
    for each row execute function sec.fn_product_invoice_audit('product_id');
drop trigger if exists trg_sales_invoice_audit on txn.sales_invoice;
create trigger trg_sales_invoice_audit after insert or update or delete on txn.sales_invoice
    for each row execute function sec.fn_product_invoice_audit('entry_id');
drop trigger if exists trg_sales_invoice_item_audit on txn.sales_invoice_item;
create trigger trg_sales_invoice_item_audit after insert or update or delete on txn.sales_invoice_item
    for each row execute function sec.fn_product_invoice_audit('item_id');

grant select on master.product_catalog to dstt_app,dstt_etl;
grant insert,update on master.product_catalog to dstt_etl;
grant usage,select on sequence master.product_catalog_product_id_seq to dstt_etl;
grant select,insert,update on txn.sales_invoice to dstt_app;
grant select,insert,update,delete on txn.sales_invoice_item to dstt_app;
grant usage,select on sequence txn.sales_invoice_item_item_id_seq to dstt_app;
-- The existing append-only sec.audit_log grants and sales_entry grants remain.
-- ETL can inspect invoices so constraint checks also protect source sync edits.
grant select on txn.sales_invoice,txn.sales_invoice_item to dstt_etl;

commit;
