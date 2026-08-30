begin;

create table if not exists public.margin_history (
    trade_date date not null,
    stock_code text not null,
    market text not null,
    margin_buy bigint not null,
    margin_sell bigint not null,
    margin_cash_redemption bigint not null,
    margin_previous_balance bigint not null,
    margin_balance bigint not null,
    short_buy bigint not null,
    short_sell bigint not null,
    short_stock_redemption bigint not null,
    short_previous_balance bigint not null,
    short_balance bigint not null,
    offsetting_volume bigint,
    margin_limit bigint,
    margin_utilization numeric(8, 4),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint margin_history_pkey
        primary key (trade_date, stock_code),
    constraint margin_history_stock_code_fkey
        foreign key (stock_code) references public.stocks(stock_code),
    constraint margin_history_market_check
        check (market in ('TWSE', 'TPEX')),
    constraint margin_history_stock_code_not_blank
        check (btrim(stock_code) <> ''),
    constraint margin_history_quantities_nonnegative
        check (
            margin_buy >= 0
            and margin_sell >= 0
            and margin_cash_redemption >= 0
            and margin_previous_balance >= 0
            and margin_balance >= 0
            and short_buy >= 0
            and short_sell >= 0
            and short_stock_redemption >= 0
            and short_previous_balance >= 0
            and short_balance >= 0
            and (offsetting_volume is null or offsetting_volume >= 0)
        ),
    constraint margin_history_limit_nonnegative
        check (margin_limit is null or margin_limit >= 0),
    constraint margin_history_utilization_range
        check (margin_utilization is null or margin_utilization between 0 and 100)
);

create index if not exists idx_margin_history_stock_date
    on public.margin_history (stock_code, trade_date desc);

create index if not exists idx_margin_history_trade_date
    on public.margin_history (trade_date desc);

alter table public.margin_history enable row level security;

-- Margin rows are written and read by the server-side desktop/mobile clients.
-- Do not expose financing balances through anonymous or authenticated Data API
-- roles because the service key is the intended access boundary.
revoke all on table public.margin_history from anon, authenticated;
grant select, insert, update, delete on table public.margin_history to service_role;

comment on table public.margin_history is
    'Official daily TWSE/TPEx margin-trading balances and financing utilization.';
comment on column public.margin_history.margin_limit is
    'TWSE next-business-day financing limit; TPEx approved financing limit, in 張.';
comment on column public.margin_history.margin_utilization is
    'Financing utilization percentage. TPEx is official; TWSE is margin_balance divided by margin_limit.';

commit;
