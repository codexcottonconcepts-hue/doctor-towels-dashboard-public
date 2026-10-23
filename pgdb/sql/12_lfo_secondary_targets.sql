-- Secondary targets use their own distributor monthly amounts from the sheet.
-- Preserve missing targets as NULL rather than treating blanks as zero or
-- retaining a previous target after the source was cleared. No rows are deleted.
begin;
alter table lfo.distributor_weight alter column weight drop not null;
comment on column lfo.distributor_weight.weight is 'Absolute secondary distributor monthly target from LFO Distributed. NULL means unreported; zero is an explicit zero target. Split equally among named stores; independent of primary LFO targets.';
commit;
