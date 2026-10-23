-- =====================================================================
-- 04_security.sql   Database roles and grants.
--
-- Three database roles, not one. The web app connects as dstt_app and
-- physically cannot drop a table; migrations run as the owner. This is the
-- difference between a bad deploy losing a column and losing the database.
--
-- New roles cannot log in until an administrator enables LOGIN and sets
-- credentials using the provider console or psql's \password command.
-- Existing roles and their credentials are never reset by this migration.
-- =====================================================================

begin;

-- ---------------------------------------------------------------- roles
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'dstt_app') then
        create role dstt_app nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'dstt_read') then
        create role dstt_read nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'dstt_etl') then
        create role dstt_etl nologin;
    end if;
end
$$;

comment on role dstt_app  is 'The web application. Reads views, writes the ledger and targets. No DDL.';
comment on role dstt_read is 'Analysts, Power BI, Excel. Views only, nothing else.';
comment on role dstt_etl  is 'The bulk loaders. Writes master data as well as the ledger.';


-- --------------------------------------------------------------- grants
grant usage on schema master, txn, tgt, rpt to dstt_app, dstt_etl;
grant usage on schema rpt                   to dstt_read;
grant usage on schema sec                   to dstt_app;
grant usage on schema sec                   to dstt_etl;

-- The app reads through rpt.* and writes through the base tables.
grant select on all tables in schema rpt    to dstt_app, dstt_read, dstt_etl;
grant select on all tables in schema master to dstt_app, dstt_etl;
grant select, insert, update on txn.sales_entry to dstt_app, dstt_etl;
grant select, insert, update, delete on tgt.target, tgt.target_plan to dstt_app;
grant select, insert, update on all tables in schema sec to dstt_app;

-- Adding a customer or a city has to be possible from the entry form, or
-- people will pick the wrong existing one rather than stop and ask.
grant insert, update on master.customer, master.city, master.district,
                        master.sales_person, master.sub_channel to dstt_app;
-- Couriers are managed from the admin screen, not created mid-entry: the
-- list is short and a new one is a decision, not a typo recovery.
do $$
begin
    -- Very old databases acquire this table in 05_add_courier.sql, which
    -- grants the same access after creating it.
    if to_regclass('master.courier') is not null then
        grant insert, update on master.courier to dstt_app;
    end if;
end
$$;

-- The loaders may create any master row they meet.
grant insert, update on all tables in schema master to dstt_etl;
grant insert on tgt.target, tgt.target_plan to dstt_etl;
grant select, insert, update, delete on tgt.target to dstt_etl;
grant select, update on tgt.target_plan to dstt_etl;
-- Audit triggers execute with the caller's privileges. ETL needs only
-- append access, never access to user accounts or existing audit rows.
grant insert on sec.audit_log to dstt_etl;
grant usage, select on sequence sec.audit_log_audit_id_seq to dstt_etl;

grant usage, select on all sequences in schema master to dstt_app, dstt_etl;
grant usage, select on all sequences in schema txn    to dstt_app, dstt_etl;
grant usage, select on all sequences in schema tgt    to dstt_app, dstt_etl;
grant usage, select on all sequences in schema sec    to dstt_app;

-- Nobody edits history. Corrections are a soft delete plus a fresh row, so
-- the audit trail keeps both.
revoke delete on txn.sales_entry from dstt_app, dstt_etl;
-- The audit log is append-only even for the application.
revoke update, delete on sec.audit_log from dstt_app;

-- Anything created later inherits the same shape.
alter default privileges in schema rpt
    grant select on tables to dstt_app, dstt_read, dstt_etl;
alter default privileges in schema master
    grant select on tables to dstt_app, dstt_read, dstt_etl;

commit;


-- =====================================================================
-- Optional: row level security, so an executive sees only their own rows.
--
-- Left commented because it only works once the app sets
--     set local app.person_id = '<the logged-in person>'
-- on every connection it hands out. Turn it on when that is wired up;
-- until then the application enforces the same rule in its queries.
-- =====================================================================

-- alter table txn.sales_entry enable row level security;
--
-- create policy sales_entry_all_access on txn.sales_entry
--     for all to dstt_app
--     using (current_setting('app.role_code', true) in ('admin','manager','entry')
--            or person_id = nullif(current_setting('app.person_id', true), '')::int);
--
-- create policy sales_entry_etl on txn.sales_entry for all to dstt_etl using (true);
