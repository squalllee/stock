begin;

create table if not exists public.tdcc_distributions (
    data_date date not null,
    stock_code text not null,
    holding_level smallint not null,
    shareholder_count bigint not null,
    share_count bigint not null,
    holding_ratio numeric(8, 4) not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint tdcc_distributions_pkey
        primary key (data_date, stock_code, holding_level),
    constraint tdcc_distributions_stock_code_not_blank
        check (btrim(stock_code) <> ''),
    constraint tdcc_distributions_holding_level_range
        check (holding_level between 1 and 15),
    constraint tdcc_distributions_shareholder_count_nonnegative
        check (shareholder_count >= 0),
    constraint tdcc_distributions_share_count_nonnegative
        check (share_count >= 0),
    constraint tdcc_distributions_holding_ratio_range
        check (holding_ratio between 0 and 100)
);

create index if not exists idx_tdcc_distributions_stock_code
    on public.tdcc_distributions (stock_code);

create index if not exists idx_tdcc_distributions_data_date
    on public.tdcc_distributions (data_date);

create index if not exists idx_tdcc_distributions_stock_date
    on public.tdcc_distributions (stock_code, data_date);

alter table public.tdcc_distributions enable row level security;

revoke all on table public.tdcc_distributions from anon, authenticated;
grant select, insert, update, delete on table public.tdcc_distributions
    to service_role;

comment on table public.tdcc_distributions is
    'TDCC shareholding distribution data synchronized from the official TDCC OpenAPI.';
comment on column public.tdcc_distributions.holding_level is
    'TDCC numeric holding level, restricted to levels 1 through 15.';

commit;
