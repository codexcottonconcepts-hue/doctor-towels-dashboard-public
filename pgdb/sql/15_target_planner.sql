-- Target planner: future-year targets and secondary sales for every channel.
-- Run as database owner after 14_llfo_secondary.sql; safe to re-run.
-- Nothing is deleted or rewritten: one check constraint is widened and the
-- web application may add a financial year when a planner opens it.
begin;

-- Saloon, D&R-S and D&R-N get sub-channels / groups and branches / retailers
-- beside LFO and L-LFO. Existing rows keep their channel.
alter table lfo.distributor drop constraint if exists distributor_channel_check;
alter table lfo.distributor
    add constraint distributor_channel_check
    check (channel in ('LFO', 'L-LFO', 'D&R-S', 'D&R-N', 'Saloon'));

-- The planner lists the current and following financial years and adds a
-- year row the first time one is planned. Years are never changed or removed.
grant insert on master.financial_year to dstt_app;

comment on column lfo.distributor.channel is
    'Sales channel: LFO and L-LFO (imported from their Distributed tabs), or D&R-S, D&R-N and Saloon (set up in the Target planner). Stores, sales and targets inherit it through distributor_id.';
comment on schema lfo is
    'Secondary sales for every channel: sub-channels / groups (lfo.distributor), their branches / retailers (lfo.store), monthly sales and targets. Never part of the primary invoice ledger.';
comment on column lfo.branch_target_plan.source_id is
    'Google spreadsheet ID for imported plans, or dashboard for plans saved in the Target planner. A sheet import never changes a group and year the planner has saved.';
comment on column lfo.distributor_weight.source_id is
    'Google spreadsheet ID for imported targets, or dashboard for targets saved in the Target planner. A sheet import never changes a group and year the planner has saved.';

commit;
