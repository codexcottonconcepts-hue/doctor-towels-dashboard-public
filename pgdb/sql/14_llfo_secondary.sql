-- L-LFO secondary sales share the lfo schema with LFO, separated by channel.
-- Existing distributors are LFO. Nothing is deleted or rewritten; the only
-- data change is the new column's default on the existing three rows.
-- Run as database owner after 13_lfo_branch_targets.sql; safe to re-run.
begin;

alter table lfo.distributor add column if not exists channel text not null default 'LFO';

do $$
begin
    if not exists (select 1 from pg_constraint
                   where conname = 'distributor_channel_check'
                     and conrelid = 'lfo.distributor'::regclass) then
        alter table lfo.distributor
            add constraint distributor_channel_check check (channel in ('LFO', 'L-LFO'));
    end if;
end $$;

-- A distributor code is unique within its channel. Build the new index before
-- dropping the old constraint so there is no moment with no uniqueness at all.
create unique index if not exists lfo_distributor_channel_code_uq on lfo.distributor(channel, code);
alter table lfo.distributor drop constraint if exists distributor_code_key;
create index if not exists lfo_import_run_sheet_ix on lfo.import_run(sheet_name, import_id desc);

comment on column lfo.distributor.channel is
    'Sales channel this distributor belongs to: LFO (LFO Distributed) or L-LFO (L-LFO Distributed). Stores, sales and targets inherit it through distributor_id.';
comment on schema lfo is
    'LFO and L-LFO distributor and branch secondary sales, separated by lfo.distributor.channel; never part of the primary invoice ledger.';

commit;
