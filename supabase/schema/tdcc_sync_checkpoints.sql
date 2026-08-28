begin;

create table if not exists public.tdcc_sync_checkpoints (
    data_date date not null,
    stock_code text not null,
    status text not null,
    record_count smallint not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint tdcc_sync_checkpoints_pkey
        primary key (data_date, stock_code),
    constraint tdcc_sync_checkpoints_stock_code_fkey
        foreign key (stock_code) references public.stocks(stock_code),
    constraint tdcc_sync_checkpoints_status_check
        check (status in ('completed', 'no_data')),
    constraint tdcc_sync_checkpoints_record_count_check
        check (
            (status = 'no_data' and record_count = 0)
            or
            (status = 'completed' and record_count between 1 and 15)
        )
);

create index if not exists idx_tdcc_sync_checkpoints_stock_date
    on public.tdcc_sync_checkpoints (stock_code, data_date);

-- Bootstrap checkpoints only for stock/date pairs that already contain all
-- 15 persisted levels. Incomplete older writes remain eligible for repair.
insert into public.tdcc_sync_checkpoints (
    data_date,
    stock_code,
    status,
    record_count
)
select
    data_date,
    stock_code,
    'completed',
    count(*)::smallint
from public.tdcc_distributions
where holding_level between 1 and 15
group by data_date, stock_code
having count(*) = 15
on conflict (data_date, stock_code) do nothing;

alter table public.tdcc_sync_checkpoints enable row level security;

revoke all on table public.tdcc_sync_checkpoints from anon, authenticated;
grant select, insert, update on table public.tdcc_sync_checkpoints
    to service_role;

comment on table public.tdcc_sync_checkpoints is
    'Completed TDCC historical stock/date queries used for resumable synchronization.';
comment on column public.tdcc_sync_checkpoints.status is
    'completed when distribution rows were stored; no_data when TDCC explicitly returned no result.';

commit;
