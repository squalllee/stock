/**
 * Keep the latest available MOPS holding month for the disclosure cards.
 *
 * Planned-transfer and untransferred rows are daily disclosures rather than
 * monthly holding snapshots, so they remain visible alongside the latest
 * after-report month.
 */
export function filterToLatestHoldingMonth(transactions) {
  const availableMonths = (transactions || [])
    .filter((row) => row?.report_type === "after_report")
    .map((row) => reportMonth(row?.report_date))
    .filter(Boolean)
    .sort();
  const latestMonth = availableMonths[availableMonths.length - 1];

  if (!latestMonth) return transactions || [];
  return (transactions || []).filter(
    (row) =>
      row?.report_type !== "after_report"
      || reportMonth(row.report_date) === latestMonth,
  );
}

function reportMonth(value) {
  const match = String(value || "").match(/^(\d{4}-\d{2})-\d{2}$/);
  return match ? match[1] : null;
}
