begin;

create index if not exists idx_tdcc_distributions_date_level_stock
    on public.tdcc_distributions (data_date desc, holding_level, stock_code);

create or replace function public.search_tdcc_stocks(
    p_query text,
    p_limit integer default 8
)
returns table (
    stock_code text,
    stock_name text,
    market text,
    data_date date,
    large_holder_count numeric,
    large_share_count numeric,
    large_ratio numeric,
    retail_holder_count numeric,
    retail_share_count numeric,
    retail_ratio numeric
)
language sql
stable
security invoker
set search_path = public
as $$
    with matched as (
        select
            s.stock_code,
            s.stock_name,
            s.market,
            case
                when s.stock_code = btrim(p_query) then 0
                when s.stock_name = btrim(p_query) then 1
                when s.stock_code ilike btrim(p_query) || '%' then 2
                else 3
            end as match_rank
        from public.stocks s
        where nullif(btrim(p_query), '') is not null
          and (
              s.stock_code ilike '%' || btrim(p_query) || '%'
              or s.stock_name ilike '%' || btrim(p_query) || '%'
          )
        order by match_rank, s.stock_code
        limit least(greatest(coalesce(p_limit, 8), 1), 20)
    )
    select
        matched.stock_code,
        matched.stock_name,
        matched.market,
        latest.data_date,
        coalesce(latest.large_holder_count, 0),
        coalesce(latest.large_share_count, 0),
        coalesce(latest.large_ratio, 0),
        coalesce(latest.retail_holder_count, 0),
        coalesce(latest.retail_share_count, 0),
        coalesce(latest.retail_ratio, 0)
    from matched
    left join lateral (
        select
            d.data_date,
            sum(d.shareholder_count) filter (where d.holding_level = 15)
                as large_holder_count,
            sum(d.share_count) filter (where d.holding_level = 15)
                as large_share_count,
            sum(d.holding_ratio) filter (where d.holding_level = 15)
                as large_ratio,
            sum(d.shareholder_count) filter (where d.holding_level between 1 and 6)
                as retail_holder_count,
            sum(d.share_count) filter (where d.holding_level between 1 and 6)
                as retail_share_count,
            sum(d.holding_ratio) filter (where d.holding_level between 1 and 6)
                as retail_ratio
        from public.tdcc_distributions d
        where d.stock_code = matched.stock_code
          and (d.holding_level between 1 and 6 or d.holding_level = 15)
        group by d.data_date
        order by d.data_date desc
        limit 1
    ) latest on true
    order by matched.match_rank, matched.stock_code;
$$;

create or replace function public.get_tdcc_stock_detail(
    p_stock_code text,
    p_weeks integer default 26
)
returns table (
    data_date date,
    large_holder_count numeric,
    large_share_count numeric,
    large_ratio numeric,
    retail_holder_count numeric,
    retail_share_count numeric,
    retail_ratio numeric
)
language sql
stable
security invoker
set search_path = public
as $$
    select
        d.data_date,
        coalesce(
            sum(d.shareholder_count) filter (where d.holding_level = 15),
            0
        ) as large_holder_count,
        coalesce(
            sum(d.share_count) filter (where d.holding_level = 15),
            0
        ) as large_share_count,
        coalesce(
            sum(d.holding_ratio) filter (where d.holding_level = 15),
            0
        ) as large_ratio,
        coalesce(
            sum(d.shareholder_count)
                filter (where d.holding_level between 1 and 6),
            0
        ) as retail_holder_count,
        coalesce(
            sum(d.share_count) filter (where d.holding_level between 1 and 6),
            0
        ) as retail_share_count,
        coalesce(
            sum(d.holding_ratio) filter (where d.holding_level between 1 and 6),
            0
        ) as retail_ratio
    from public.tdcc_distributions d
    where d.stock_code = btrim(p_stock_code)
      and (d.holding_level between 1 and 6 or d.holding_level = 15)
    group by d.data_date
    order by d.data_date desc
    limit least(greatest(coalesce(p_weeks, 26), 2), 104);
$$;

create or replace function public.get_tdcc_increasing_stocks(
    p_weeks integer default 3,
    p_limit integer default 100
)
returns table (
    stock_code text,
    stock_name text,
    market text,
    start_date date,
    latest_date date,
    start_large_ratio numeric,
    latest_large_ratio numeric,
    increase_percentage_points numeric,
    large_holder_count numeric,
    large_share_count numeric,
    retail_holder_count numeric,
    retail_share_count numeric,
    retail_ratio numeric,
    streak_weeks integer
)
language sql
stable
security invoker
set search_path = public
as $$
    with parameters as (
        select
            least(greatest(coalesce(p_weeks, 3), 2), 12) as weeks,
            least(greatest(coalesce(p_limit, 100), 1), 200) as row_limit
    ),
    latest_dates as (
        select distinct d.data_date
        from public.tdcc_distributions d
        order by d.data_date desc
        limit (select weeks from parameters)
    ),
    weekly as (
        select
            d.stock_code,
            d.data_date,
            coalesce(
                sum(d.shareholder_count) filter (where d.holding_level = 15),
                0
            ) as large_holder_count,
            coalesce(
                sum(d.share_count) filter (where d.holding_level = 15),
                0
            ) as large_share_count,
            coalesce(
                sum(d.holding_ratio) filter (where d.holding_level = 15),
                0
            ) as large_ratio,
            coalesce(
                sum(d.shareholder_count)
                    filter (where d.holding_level between 1 and 6),
                0
            ) as retail_holder_count,
            coalesce(
                sum(d.share_count)
                    filter (where d.holding_level between 1 and 6),
                0
            ) as retail_share_count,
            coalesce(
                sum(d.holding_ratio)
                    filter (where d.holding_level between 1 and 6),
                0
            ) as retail_ratio
        from public.tdcc_distributions d
        inner join latest_dates on latest_dates.data_date = d.data_date
        where d.holding_level between 1 and 6 or d.holding_level = 15
        group by d.stock_code, d.data_date
    ),
    sequenced as (
        select
            weekly.*,
            lag(weekly.large_ratio) over (
                partition by weekly.stock_code
                order by weekly.data_date
            ) as previous_large_ratio
        from weekly
    ),
    qualified as (
        select sequenced.stock_code
        from sequenced
        group by sequenced.stock_code
        having count(*) = (select weeks from parameters)
           and bool_and(
               sequenced.previous_large_ratio is null
               or sequenced.large_ratio > sequenced.previous_large_ratio
           )
    ),
    summary as (
        select
            sequenced.stock_code,
            min(sequenced.data_date) as start_date,
            max(sequenced.data_date) as latest_date,
            (array_agg(sequenced.large_ratio order by sequenced.data_date))[1]
                as start_large_ratio,
            (array_agg(sequenced.large_ratio order by sequenced.data_date desc))[1]
                as latest_large_ratio,
            (array_agg(sequenced.large_holder_count order by sequenced.data_date desc))[1]
                as large_holder_count,
            (array_agg(sequenced.large_share_count order by sequenced.data_date desc))[1]
                as large_share_count,
            (array_agg(sequenced.retail_holder_count order by sequenced.data_date desc))[1]
                as retail_holder_count,
            (array_agg(sequenced.retail_share_count order by sequenced.data_date desc))[1]
                as retail_share_count,
            (array_agg(sequenced.retail_ratio order by sequenced.data_date desc))[1]
                as retail_ratio,
            count(*)::integer as streak_weeks
        from sequenced
        inner join qualified on qualified.stock_code = sequenced.stock_code
        group by sequenced.stock_code
    )
    select
        stocks.stock_code,
        stocks.stock_name,
        stocks.market,
        summary.start_date,
        summary.latest_date,
        summary.start_large_ratio,
        summary.latest_large_ratio,
        summary.latest_large_ratio - summary.start_large_ratio
            as increase_percentage_points,
        summary.large_holder_count,
        summary.large_share_count,
        summary.retail_holder_count,
        summary.retail_share_count,
        summary.retail_ratio,
        summary.streak_weeks
    from summary
    inner join public.stocks on stocks.stock_code = summary.stock_code
    order by
        increase_percentage_points desc,
        summary.latest_large_ratio desc,
        stocks.stock_code
    limit (select row_limit from parameters);
$$;

create or replace function public.get_tdcc_holder_turns(
    p_limit integer default 100
)
returns table (
    turn_type text,
    stock_code text,
    stock_name text,
    market text,
    oldest_date date,
    previous_date date,
    latest_date date,
    oldest_large_ratio numeric,
    previous_large_ratio numeric,
    latest_large_ratio numeric,
    previous_change_percentage_points numeric,
    latest_change_percentage_points numeric,
    large_holder_count numeric,
    large_share_count numeric,
    retail_holder_count numeric,
    retail_share_count numeric,
    retail_ratio numeric
)
language sql
stable
security invoker
set search_path = public
as $$
    with parameters as (
        select least(greatest(coalesce(p_limit, 100), 1), 200) as row_limit
    ),
    latest_dates as (
        select distinct d.data_date
        from public.tdcc_distributions d
        order by d.data_date desc
        limit 3
    ),
    weekly as (
        select
            d.stock_code,
            d.data_date,
            coalesce(
                sum(d.shareholder_count) filter (where d.holding_level = 15),
                0
            ) as large_holder_count,
            coalesce(
                sum(d.share_count) filter (where d.holding_level = 15),
                0
            ) as large_share_count,
            coalesce(
                sum(d.holding_ratio) filter (where d.holding_level = 15),
                0
            ) as large_ratio,
            coalesce(
                sum(d.shareholder_count)
                    filter (where d.holding_level between 1 and 6),
                0
            ) as retail_holder_count,
            coalesce(
                sum(d.share_count)
                    filter (where d.holding_level between 1 and 6),
                0
            ) as retail_share_count,
            coalesce(
                sum(d.holding_ratio)
                    filter (where d.holding_level between 1 and 6),
                0
            ) as retail_ratio
        from public.tdcc_distributions d
        inner join latest_dates on latest_dates.data_date = d.data_date
        where d.holding_level between 1 and 6 or d.holding_level = 15
        group by d.stock_code, d.data_date
    ),
    sequenced as (
        select
            weekly.*,
            lag(weekly.data_date) over (
                partition by weekly.stock_code
                order by weekly.data_date
            ) as previous_date,
            lag(weekly.data_date, 2) over (
                partition by weekly.stock_code
                order by weekly.data_date
            ) as oldest_date,
            lag(weekly.large_ratio) over (
                partition by weekly.stock_code
                order by weekly.data_date
            ) as previous_large_ratio,
            lag(weekly.large_ratio, 2) over (
                partition by weekly.stock_code
                order by weekly.data_date
            ) as oldest_large_ratio
        from weekly
    ),
    turns as (
        select
            case
                when sequenced.oldest_large_ratio > sequenced.previous_large_ratio
                     and sequenced.large_ratio > sequenced.previous_large_ratio
                    then 'sell_to_buy'
                when sequenced.oldest_large_ratio < sequenced.previous_large_ratio
                     and sequenced.large_ratio < sequenced.previous_large_ratio
                    then 'buy_to_sell'
            end as turn_type,
            sequenced.stock_code,
            sequenced.data_date as latest_date,
            sequenced.oldest_date,
            sequenced.previous_date,
            sequenced.oldest_large_ratio,
            sequenced.previous_large_ratio,
            sequenced.large_ratio as latest_large_ratio,
            sequenced.previous_large_ratio - sequenced.oldest_large_ratio
                as previous_change_percentage_points,
            sequenced.large_ratio - sequenced.previous_large_ratio
                as latest_change_percentage_points,
            sequenced.large_holder_count,
            sequenced.large_share_count,
            sequenced.retail_holder_count,
            sequenced.retail_share_count,
            sequenced.retail_ratio
        from sequenced
        where sequenced.data_date = (select max(data_date) from latest_dates)
          and sequenced.oldest_large_ratio is not null
          and sequenced.previous_large_ratio is not null
          and (
              (
                  sequenced.oldest_large_ratio > sequenced.previous_large_ratio
                  and sequenced.large_ratio > sequenced.previous_large_ratio
              )
              or (
                  sequenced.oldest_large_ratio < sequenced.previous_large_ratio
                  and sequenced.large_ratio < sequenced.previous_large_ratio
              )
          )
    ),
    ranked as (
        select
            turns.*,
            row_number() over (
                partition by turns.turn_type
                order by
                    abs(turns.latest_change_percentage_points) desc,
                    turns.latest_large_ratio desc,
                    turns.stock_code
            ) as turn_rank
        from turns
    )
    select
        ranked.turn_type,
        stocks.stock_code,
        stocks.stock_name,
        stocks.market,
        ranked.oldest_date,
        ranked.previous_date,
        ranked.latest_date,
        ranked.oldest_large_ratio,
        ranked.previous_large_ratio,
        ranked.latest_large_ratio,
        ranked.previous_change_percentage_points,
        ranked.latest_change_percentage_points,
        ranked.large_holder_count,
        ranked.large_share_count,
        ranked.retail_holder_count,
        ranked.retail_share_count,
        ranked.retail_ratio
    from ranked
    inner join public.stocks on stocks.stock_code = ranked.stock_code
    where ranked.turn_rank <= (select row_limit from parameters)
    order by
        ranked.turn_type,
        abs(ranked.latest_change_percentage_points) desc,
        stocks.stock_code;
$$;

revoke all on function public.search_tdcc_stocks(text, integer)
    from public, anon, authenticated;
revoke all on function public.get_tdcc_stock_detail(text, integer)
    from public, anon, authenticated;
revoke all on function public.get_tdcc_increasing_stocks(integer, integer)
    from public, anon, authenticated;
revoke all on function public.get_tdcc_holder_turns(integer)
    from public, anon, authenticated;

grant execute on function public.search_tdcc_stocks(text, integer)
    to service_role;
grant execute on function public.get_tdcc_stock_detail(text, integer)
    to service_role;
grant execute on function public.get_tdcc_increasing_stocks(integer, integer)
    to service_role;
grant execute on function public.get_tdcc_holder_turns(integer)
    to service_role;

comment on function public.search_tdcc_stocks(text, integer) is
    'Mobile web stock search with the latest TDCC level 15 and level 1-6 summaries.';
comment on function public.get_tdcc_stock_detail(text, integer) is
    'Weekly TDCC large-holder and retail-holder history for one stock.';
comment on function public.get_tdcc_increasing_stocks(integer, integer) is
    'Stocks whose TDCC level 15 holding ratio strictly increased in every requested recent week.';
comment on function public.get_tdcc_holder_turns(integer) is
    'Stocks whose TDCC level 15 holding direction changed between the latest three weekly observations.';

commit;
