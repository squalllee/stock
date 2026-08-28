begin;

create table if not exists public.stocks (
    stock_code text not null,
    stock_name text not null,
    market text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint stocks_pkey primary key (stock_code),
    constraint stocks_stock_code_not_blank check (btrim(stock_code) <> ''),
    constraint stocks_stock_name_not_blank check (btrim(stock_name) <> ''),
    constraint stocks_market_check check (market in ('TWSE', 'TPEX'))
);

create index if not exists idx_stocks_market
    on public.stocks (market);

create table if not exists public.price_history (
    trade_date date not null,
    stock_code text not null,
    market text not null,
    trade_volume bigint not null,
    trade_value bigint not null,
    open_price numeric(14, 4),
    high_price numeric(14, 4),
    low_price numeric(14, 4),
    close_price numeric(14, 4),
    transaction_count bigint,
    market_average_price numeric(14, 6),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint price_history_pkey primary key (trade_date, stock_code),
    constraint price_history_stock_code_fkey
        foreign key (stock_code) references public.stocks(stock_code),
    constraint price_history_market_check check (market in ('TWSE', 'TPEX')),
    constraint price_history_trade_volume_nonnegative check (trade_volume >= 0),
    constraint price_history_trade_value_nonnegative check (trade_value >= 0),
    constraint price_history_transaction_count_nonnegative
        check (transaction_count is null or transaction_count >= 0)
);

create index if not exists idx_price_history_stock_code
    on public.price_history (stock_code);

create index if not exists idx_price_history_trade_date
    on public.price_history (trade_date);

create index if not exists idx_price_history_stock_date
    on public.price_history (stock_code, trade_date);

alter table public.stocks enable row level security;
alter table public.price_history enable row level security;

revoke all on table public.stocks from anon, authenticated;
revoke all on table public.price_history from anon, authenticated;
grant select, insert, update, delete on table public.stocks to service_role;
grant select, insert, update, delete on table public.price_history to service_role;

comment on table public.stocks is
    'TWSE and TPEx common-stock master synchronized from official sources.';
comment on table public.price_history is
    'Official daily trading facts synchronized from TWSE and TPEx.';
comment on column public.price_history.market_average_price is
    '成交金額除以成交股數的每日成交均價 proxy.';

commit;
