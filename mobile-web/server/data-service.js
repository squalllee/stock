const MAX_SEARCH_LIMIT = 20;
const MAX_DETAIL_WEEKS = 104;
const MAX_PRICE_WEEKS = 104;
const MAX_SCREENER_LIMIT = 200;
const MAX_TURN_LIMIT = 200;
const MAX_INSIDER_LIMIT = 100;

export class SupabaseQueryError extends Error {
  constructor(operation, cause) {
    super(`${operation}失敗：${cause?.message || "Supabase 無法回應"}`);
    this.name = "SupabaseQueryError";
    this.cause = cause;
  }
}

export function createStockDataService(supabase) {
  async function getPriceHistory(stockCode, requestedWeeks = 26) {
    const weeks = clampInteger(requestedWeeks, 2, MAX_PRICE_WEEKS, 26);
    const { data, error } = await supabase.rpc("get_stock_price_history", {
      p_stock_code: stockCode,
      p_weeks: weeks,
    });
    if (error) throw new SupabaseQueryError("股價歷史查詢", error);
    return (data || []).map(normalizePriceRow);
  }

  async function getInsiderTransactions(stockCode, requestedLimit = 60) {
    const limit = clampInteger(requestedLimit, 1, MAX_INSIDER_LIMIT, 60);
    const { data, error } = await supabase
      .from("insider_transactions")
      .select(
        "report_date,stock_code,market,report_type,transaction_type,insider_name,insider_role,shares_changed,transfer_method,transferee,current_shares,planned_shares,after_shares,effective_period,reason",
      )
      .eq("stock_code", stockCode)
      .order("report_date", { ascending: false })
      .order("id", { ascending: false })
      .limit(limit);
    if (error) throw new SupabaseQueryError("內部人申報查詢", error);
    return (data || []).map(normalizeInsiderRow);
  }

  return {
    async searchStocks(
      query,
      requestedLimit = 8,
      requestedLargeLevelMin = 15,
      requestedLargeLevelMax = 15,
    ) {
      const limit = clampInteger(requestedLimit, 1, MAX_SEARCH_LIMIT, 8);
      const { minLevel, maxLevel } = normalizeLargeHolderLevels(
        requestedLargeLevelMin,
        requestedLargeLevelMax,
      );
      const { data, error } = await supabase.rpc("search_tdcc_stocks", {
        p_query: query.trim(),
        p_limit: limit,
        p_large_level_min: minLevel,
        p_large_level_max: maxLevel,
      });
      if (error) throw new SupabaseQueryError("股票搜尋", error);
      return (data || []).map(normalizeSummary);
    },

    async getStockDetail(
      stockCode,
      requestedWeeks = 26,
      requestedLargeLevelMin = 15,
      requestedLargeLevelMax = 15,
    ) {
      const weeks = clampInteger(requestedWeeks, 2, MAX_DETAIL_WEEKS, 26);
      const { minLevel, maxLevel } = normalizeLargeHolderLevels(
        requestedLargeLevelMin,
        requestedLargeLevelMax,
      );
      const [stockResult, historyResult, priceRows, insiderRows] = await Promise.all([
        supabase
          .from("stocks")
          .select("stock_code,stock_name,market")
          .eq("stock_code", stockCode)
          .maybeSingle(),
        supabase.rpc("get_tdcc_stock_detail", {
          p_stock_code: stockCode,
          p_weeks: MAX_DETAIL_WEEKS,
          p_large_level_min: minLevel,
          p_large_level_max: maxLevel,
        }),
        getPriceHistory(stockCode, weeks),
        getInsiderTransactions(stockCode),
      ]);

      if (stockResult.error) {
        throw new SupabaseQueryError("股票主檔查詢", stockResult.error);
      }
      if (!stockResult.data) return null;
      if (historyResult.error) {
        throw new SupabaseQueryError("TDCC 明細查詢", historyResult.error);
      }

      const fullHistory = (historyResult.data || []).map(normalizeHoldingRow);
      const history = fullHistory.slice(0, weeks);
      return {
        stock: stockResult.data,
        latest: fullHistory[0] || null,
        history,
        annual_baselines: buildAnnualBaselines(fullHistory),
        prices: priceRows,
        insider_transactions: insiderRows,
      };
    },

    getPriceHistory,
    getInsiderTransactions,

    async getIncreasingStocks(
      requestedWeeks = 3,
      requestedLimit = 100,
      requestedLargeLevelMin = 15,
      requestedLargeLevelMax = 15,
    ) {
      const weeks = clampInteger(requestedWeeks, 2, 12, 3);
      const limit = clampInteger(
        requestedLimit,
        1,
        MAX_SCREENER_LIMIT,
        100,
      );
      const { minLevel, maxLevel } = normalizeLargeHolderLevels(
        requestedLargeLevelMin,
        requestedLargeLevelMax,
      );
      const { data, error } = await supabase.rpc(
        "get_tdcc_increasing_stocks",
        {
          p_weeks: weeks,
          p_limit: limit,
          p_large_level_min: minLevel,
          p_large_level_max: maxLevel,
        },
      );
      if (error) throw new SupabaseQueryError("連續增持篩選", error);
      return (data || []).map(normalizeScreenerRow);
    },

    async getHolderTurns(
      requestedLimit = 100,
      requestedLargeLevelMin = 15,
      requestedLargeLevelMax = 15,
    ) {
      const limit = clampInteger(requestedLimit, 1, MAX_TURN_LIMIT, 100);
      const { minLevel, maxLevel } = normalizeLargeHolderLevels(
        requestedLargeLevelMin,
        requestedLargeLevelMax,
      );
      const { data, error } = await supabase.rpc("get_tdcc_holder_turns", {
        p_limit: limit,
        p_large_level_min: minLevel,
        p_large_level_max: maxLevel,
      });
      if (error) throw new SupabaseQueryError("大戶動向篩選", error);
      return (data || []).map(normalizeTurnRow);
    },
  };
}

export function clampInteger(value, minimum, maximum, fallback) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(Math.max(parsed, minimum), maximum);
}

function normalizeSummary(row) {
  return {
    stock_code: row.stock_code,
    stock_name: row.stock_name,
    market: row.market,
    ...normalizeLatestPrice(row),
    ...normalizeHoldingRow(row),
  };
}

export function normalizeLargeHolderLevels(minimum, maximum) {
  const minLevel = clampInteger(minimum, 7, 15, 15);
  const maxLevel = clampInteger(maximum, 7, 15, 15);
  return minLevel <= maxLevel
    ? { minLevel, maxLevel }
    : { minLevel: maxLevel, maxLevel: minLevel };
}

function normalizeHoldingRow(row) {
  return {
    data_date: row.data_date,
    large_holder_count: numberOrZero(row.large_holder_count),
    large_share_count: numberOrZero(row.large_share_count),
    large_ratio: numberOrZero(row.large_ratio),
    retail_holder_count: numberOrZero(row.retail_holder_count),
    retail_share_count: numberOrZero(row.retail_share_count),
    retail_ratio: numberOrZero(row.retail_ratio),
  };
}

function normalizeScreenerRow(row) {
  return {
    stock_code: row.stock_code,
    stock_name: row.stock_name,
    market: row.market,
    start_date: row.start_date,
    latest_date: row.latest_date,
    ...normalizeLatestPrice(row),
    start_large_ratio: numberOrZero(row.start_large_ratio),
    latest_large_ratio: numberOrZero(row.latest_large_ratio),
    increase_percentage_points: numberOrZero(row.increase_percentage_points),
    large_holder_count: numberOrZero(row.large_holder_count),
    large_share_count: numberOrZero(row.large_share_count),
    retail_holder_count: numberOrZero(row.retail_holder_count),
    retail_share_count: numberOrZero(row.retail_share_count),
    retail_ratio: numberOrZero(row.retail_ratio),
    streak_weeks: Number(row.streak_weeks || 0),
  };
}

function normalizeTurnRow(row) {
  return {
    turn_type: row.turn_type,
    stock_code: row.stock_code,
    stock_name: row.stock_name,
    market: row.market,
    oldest_date: row.oldest_date,
    previous_date: row.previous_date,
    latest_date: row.latest_date,
    ...normalizeLatestPrice(row),
    oldest_large_ratio: numberOrZero(row.oldest_large_ratio),
    previous_large_ratio: numberOrZero(row.previous_large_ratio),
    latest_large_ratio: numberOrZero(row.latest_large_ratio),
    previous_change_percentage_points: numberOrZero(
      row.previous_change_percentage_points,
    ),
    latest_change_percentage_points: numberOrZero(
      row.latest_change_percentage_points,
    ),
    large_holder_count: numberOrZero(row.large_holder_count),
    large_share_count: numberOrZero(row.large_share_count),
    retail_holder_count: numberOrZero(row.retail_holder_count),
    retail_share_count: numberOrZero(row.retail_share_count),
    retail_ratio: numberOrZero(row.retail_ratio),
  };
}

function normalizeLatestPrice(row) {
  return {
    latest_price_date: row.latest_price_date || null,
    latest_close_price: numberOrNull(row.latest_close_price),
  };
}

function normalizePriceRow(row) {
  return {
    trade_date: row.trade_date,
    market: row.market,
    trade_volume: numberOrZero(row.trade_volume),
    trade_value: numberOrZero(row.trade_value),
    open_price: numberOrNull(row.open_price),
    high_price: numberOrNull(row.high_price),
    low_price: numberOrNull(row.low_price),
    close_price: numberOrNull(row.close_price),
    market_average_price: numberOrNull(row.market_average_price),
  };
}

function normalizeInsiderRow(row) {
  return {
    report_date: row.report_date,
    stock_code: row.stock_code,
    market: row.market,
    report_type: row.report_type,
    transaction_type: row.transaction_type,
    insider_name: row.insider_name,
    insider_role: row.insider_role,
    shares_changed: numberOrZero(row.shares_changed),
    transfer_method: row.transfer_method || null,
    transferee: row.transferee || null,
    current_shares: numberOrNull(row.current_shares),
    planned_shares: numberOrNull(row.planned_shares),
    after_shares: numberOrNull(row.after_shares),
    effective_period: row.effective_period || null,
    reason: row.reason || null,
  };
}

function buildAnnualBaselines(history) {
  return history.reduce((baselines, row) => {
    const year = String(row.data_date || "").slice(0, 4);
    if (!/^\d{4}$/.test(year)) return baselines;
    const current = baselines[year];
    baselines[year] = {
      large_ratio: current
        ? Math.min(current.large_ratio, row.large_ratio)
        : row.large_ratio,
      retail_ratio: current
        ? Math.min(current.retail_ratio, row.retail_ratio)
        : row.retail_ratio,
    };
    return baselines;
  }, {});
}

function numberOrZero(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
