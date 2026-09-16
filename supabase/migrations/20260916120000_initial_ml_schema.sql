begin;

create extension if not exists pgcrypto;

create schema if not exists pulso;

create table pulso.stations (
    station_id text primary key,
    station_name text not null,
    corridor text not null,
    latitude double precision not null check (latitude between -90 and 90),
    longitude double precision not null check (longitude between -180 and 180),
    created_at timestamptz not null default now()
);

create table pulso.context (
    observed_at timestamptz primary key,
    rain_mm double precision not null check (rain_mm >= 0),
    rain_forecast double precision not null check (rain_forecast >= 0),
    temperature_c double precision not null,
    temperature_forecast double precision not null,
    event_intensity double precision not null check (event_intensity between 0 and 1),
    ingested_at timestamptz not null default now()
);

create table pulso.observations (
    observed_at timestamptz not null,
    station_id text not null references pulso.stations (station_id),
    demand integer not null check (demand >= 0),
    ingested_at timestamptz not null default now(),
    primary key (observed_at, station_id)
);

create table pulso.data_cutoffs (
    cutoff_id text primary key,
    cutoff_at timestamptz not null,
    train_start timestamptz not null,
    validation_start timestamptz not null,
    validation_end timestamptz not null,
    split_strategy text not null,
    created_at timestamptz not null default now(),
    check (train_start < validation_start),
    check (validation_start <= validation_end),
    check (validation_end <= cutoff_at)
);

create table pulso.feature_snapshots (
    feature_at timestamptz not null,
    station_id text not null references pulso.stations (station_id),
    feature_version text not null,
    lag_15m double precision,
    lag_1h double precision,
    lag_1d double precision,
    rolling_mean_1h double precision,
    rolling_std_1d double precision,
    hour smallint not null check (hour between 0 and 23),
    weekday smallint not null check (weekday between 0 and 6),
    is_weekend boolean not null,
    rain_mm double precision check (rain_mm >= 0),
    temperature_c double precision,
    target_demand integer check (target_demand >= 0),
    created_at timestamptz not null default now(),
    primary key (feature_at, station_id, feature_version)
);

create table pulso.model_versions (
    model_id text primary key,
    algorithm text not null,
    feature_version text not null,
    code_commit text not null,
    artifact_uri text,
    hyperparameters jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table pulso.training_runs (
    run_id text primary key,
    model_id text not null references pulso.model_versions (model_id),
    cutoff_id text not null references pulso.data_cutoffs (cutoff_id),
    status text not null check (status in ('started', 'succeeded', 'failed')),
    started_at timestamptz not null,
    finished_at timestamptz,
    train_rows integer not null default 0 check (train_rows >= 0),
    validation_rows integer not null default 0 check (validation_rows >= 0),
    wape double precision check (wape >= 0),
    accuracy double precision check (accuracy between 0 and 100),
    metrics jsonb not null default '{}'::jsonb,
    error_message text,
    created_at timestamptz not null default now(),
    check (finished_at is null or finished_at >= started_at),
    check ((status = 'failed' and error_message is not null) or status <> 'failed')
);

create table pulso.predictions (
    prediction_id uuid primary key default gen_random_uuid(),
    run_id text not null references pulso.training_runs (run_id),
    station_id text not null references pulso.stations (station_id),
    target_at timestamptz not null,
    horizon smallint not null check (horizon > 0),
    y_pred double precision not null check (y_pred >= 0),
    lower_bound double precision check (lower_bound >= 0),
    upper_bound double precision check (upper_bound >= y_pred),
    submitted_at timestamptz not null default now(),
    unique (run_id, station_id, target_at, horizon)
);

create table pulso.data_quality_checks (
    check_id uuid primary key default gen_random_uuid(),
    observed_at timestamptz not null default now(),
    check_name text not null,
    status text not null check (status in ('passed', 'failed', 'warning')),
    value double precision,
    details jsonb not null default '{}'::jsonb
);

create table pulso.drift_reports (
    report_id uuid primary key default gen_random_uuid(),
    run_id text not null references pulso.training_runs (run_id),
    feature_name text not null,
    psi double precision check (psi >= 0),
    ks_stat double precision check (ks_stat between 0 and 1),
    threshold double precision not null check (threshold >= 0),
    status text not null check (status in ('stable', 'warning', 'drift')),
    created_at timestamptz not null default now()
);

create index observations_station_time_idx
    on pulso.observations (station_id, observed_at desc);

create index context_time_idx
    on pulso.context (observed_at desc);

create index feature_snapshots_station_time_idx
    on pulso.feature_snapshots (station_id, feature_at desc);

create index predictions_station_target_idx
    on pulso.predictions (station_id, target_at desc);

create index training_runs_created_idx
    on pulso.training_runs (created_at desc);

create index data_quality_checks_observed_idx
    on pulso.data_quality_checks (observed_at desc);

create index drift_reports_created_idx
    on pulso.drift_reports (created_at desc);

create or replace view pulso.prediction_evaluation as
select
    p.prediction_id,
    p.run_id,
    p.station_id,
    p.target_at,
    p.horizon,
    p.y_pred,
    o.demand as actual_demand,
    abs(o.demand - p.y_pred) as absolute_error,
    case when o.demand is null then null else abs(o.demand - p.y_pred) / nullif(o.demand, 0) end as absolute_percentage_error
from pulso.predictions p
left join pulso.observations o
    on o.station_id = p.station_id
   and o.observed_at = p.target_at;

alter table pulso.stations enable row level security;
alter table pulso.context enable row level security;
alter table pulso.observations enable row level security;
alter table pulso.data_cutoffs enable row level security;
alter table pulso.feature_snapshots enable row level security;
alter table pulso.model_versions enable row level security;
alter table pulso.training_runs enable row level security;
alter table pulso.predictions enable row level security;
alter table pulso.data_quality_checks enable row level security;
alter table pulso.drift_reports enable row level security;

-- The pipeline should use the server-side service role. No public or anon access is granted.

commit;
