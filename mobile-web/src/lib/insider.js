const TRADE_TYPES = new Set(["buy", "sell"]);

/**
 * Keep only MOPS monthly rows with an actual buy/sell change, using the latest
 * month that contains one.  The API still returns the full holding history so
 * the weekly detail cards can calculate historical snapshots independently.
 */
export function filterToLatestHoldingMonth(transactions) {
  const tradeRows = (transactions || [])
    .filter(isTradeRow);
  const availableMonths = tradeRows
    .map((row) => reportMonth(row?.report_date))
    .filter(Boolean)
    .sort();
  const latestMonth = availableMonths[availableMonths.length - 1];

  if (!latestMonth) return [];
  return tradeRows.filter((row) => reportMonth(row.report_date) === latestMonth);
}

function isTradeRow(row) {
  return (
    row?.report_type === "after_report"
    && TRADE_TYPES.has(String(row?.transaction_type || "").toLowerCase())
  );
}

function reportMonth(value) {
  const match = String(value || "").match(/^(\d{4}-\d{2})-\d{2}$/);
  return match ? match[1] : null;
}
