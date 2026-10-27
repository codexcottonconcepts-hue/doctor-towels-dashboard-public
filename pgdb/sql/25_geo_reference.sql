-- Append-only LGD provenance and surrogate-key bindings. Master and sales
-- tables are deliberately not altered, renamed, reparented or backfilled.
begin;
create table if not exists aux.lgd_reference_snapshot (
    snapshot_id text primary key,
    source_date date not null,
    reference_sha256 text not null check (reference_sha256 ~ '^[0-9a-f]{64}$'),
    source_uri text not null,
    mirror_uri text not null,
    license_uri text not null,
    expected_counts jsonb not null,
    reference_json jsonb not null,
    imported_at timestamptz not null default clock_timestamp(),
    imported_by text not null
);
create table if not exists aux.lgd_state_binding (
    lgd_state_code integer primary key check (lgd_state_code > 0),
    state_id integer not null unique references master.state on delete restrict,
    source_name text not null,
    created_snapshot_id text not null references aux.lgd_reference_snapshot on delete restrict
);
create table if not exists aux.lgd_district_binding (
    lgd_district_code integer primary key check (lgd_district_code > 0),
    lgd_state_code integer not null references aux.lgd_state_binding on delete restrict,
    district_id integer not null unique references master.district on delete restrict,
    source_name text not null,
    created_snapshot_id text not null references aux.lgd_reference_snapshot on delete restrict,
    unique(lgd_district_code,lgd_state_code)
);
create table if not exists aux.lgd_city_binding (
    lgd_ulb_code integer not null check (lgd_ulb_code > 0),
    district_key integer not null check (district_key >= 0),
    lgd_district_code integer,
    lgd_state_code integer not null references aux.lgd_state_binding on delete restrict,
    city_id integer not null unique references master.city on delete restrict,
    source_name text not null,
    master_label text not null,
    local_body_type_code text not null,
    created_snapshot_id text not null references aux.lgd_reference_snapshot on delete restrict,
    primary key(lgd_ulb_code,district_key),
    check(district_key=coalesce(lgd_district_code,0)),
    foreign key(lgd_district_code,lgd_state_code)
        references aux.lgd_district_binding(lgd_district_code,lgd_state_code) on delete restrict
);
create table if not exists aux.geo_place_supplement_binding (
    source_key text primary key,
    lgd_state_code integer not null references aux.lgd_state_binding on delete restrict,
    lgd_district_code integer not null,
    city_id integer not null unique references master.city on delete restrict,
    source_name text not null,
    master_label text not null,
    source_uri text not null,
    created_snapshot_id text not null references aux.lgd_reference_snapshot on delete restrict,
    foreign key(lgd_district_code,lgd_state_code)
        references aux.lgd_district_binding(lgd_district_code,lgd_state_code) on delete restrict
);
grant select on aux.lgd_reference_snapshot,aux.lgd_state_binding,
    aux.lgd_district_binding,aux.lgd_city_binding,aux.geo_place_supplement_binding to dstt_app;
comment on table aux.lgd_city_binding is
    'Current LGD Urban Local Body/district variants mapped to preserved application city IDs; not a complete Census settlement directory.';
commit;
