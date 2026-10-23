-- Optional colour on each product-invoice line, chosen from one shared list.
-- Additive only: existing invoices keep a null colour. Safe to re-run as owner.
begin;

create table if not exists master.product_colour (
    colour_id serial primary key,
    name text not null unique check (length(name) between 1 and 60),
    hex text check (hex ~ '^#[0-9a-f]{6}$'),
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

comment on table master.product_colour is
    'Colours offered on product-invoice lines. Deactivate instead of deleting; saved lines keep their colour name.';

-- A null hex is drawn as a mixed swatch (Assorted / Multicolour).
insert into master.product_colour(name,hex) values
    ('Aqua','#5fd3d0'),('Assorted',null),('Baby Pink','#f9d1dc'),('Beige','#e8dcc4'),
    ('Black','#111111'),('Blue','#1e63c4'),('Brown','#6d4c41'),('Charcoal','#3a3a3a'),
    ('Coffee','#6f4e37'),('Coral','#f2786b'),('Cream','#fdf5e6'),('Green','#2e7d32'),
    ('Grey','#9e9e9e'),('Ivory','#fffff0'),('Lavender','#c3b1e1'),('Light Grey','#cfcfcf'),
    ('Lilac','#c8a2c8'),('Maroon','#7b1e2b'),('Mint Green','#a8e6cf'),('Multicolour',null),
    ('Mustard','#d4a017'),('Natural','#e6d8bf'),('Navy Blue','#1f2a52'),('Off White','#f8f6f0'),
    ('Olive','#708238'),('Orange','#ef7d32'),('Peach','#f8c8a8'),('Pink','#f4a6c0'),
    ('Purple','#6a3d9a'),('Red','#c62828'),('Rose','#e0708b'),('Royal Blue','#2a4fbf'),
    ('Sage Green','#9caf88'),('Sand','#d8c4a0'),('Sky Blue','#87ceeb'),('Teal','#138a8a'),
    ('Turquoise','#30c5c0'),('White','#ffffff'),('Wine','#722f37'),('Yellow','#f5d33f')
on conflict (name) do nothing;

-- The name is a snapshot like product_name; renames cascade to saved lines.
alter table txn.sales_invoice_item add column if not exists colour text;
do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'sales_invoice_item_colour_fkey') then
        alter table txn.sales_invoice_item add constraint sales_invoice_item_colour_fkey
            foreign key (colour) references master.product_colour(name) on update cascade on delete restrict;
    end if;
end $$;

grant select on master.product_colour to dstt_app,dstt_etl;
grant insert,update on master.product_colour to dstt_etl;
grant usage,select on sequence master.product_colour_colour_id_seq to dstt_etl;

commit;
