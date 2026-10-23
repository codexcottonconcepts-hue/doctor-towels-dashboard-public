-- Executive display names and territory assignment structure.
-- Run after 01_schema.sql and 04_security.sql. Safe to repeat.
-- Staff identities and territory assignments are private configuration;
-- this public migration intentionally carries no assignment data.

begin;

alter table master.sales_person
    add column if not exists display_name text;

create table if not exists master.person_territory (
    person_id  int  not null references master.sales_person on delete cascade,
    territory  text not null,
    sort_order smallint not null default 100,
    primary key (person_id, territory)
);
create index if not exists person_territory_person_ix
    on master.person_territory (person_id);

grant select on master.person_territory to dstt_app, dstt_read, dstt_etl;
grant insert, update, delete on master.person_territory to dstt_app, dstt_etl;

commit;
