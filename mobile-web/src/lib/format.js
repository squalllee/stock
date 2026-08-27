const integerFormatter = new Intl.NumberFormat("zh-TW", {
  maximumFractionDigits: 0,
});
const ratioFormatter = new Intl.NumberFormat("zh-TW", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const dateFormatter = new Intl.DateTimeFormat("zh-TW", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});
const shortDateFormatter = new Intl.DateTimeFormat("zh-TW", {
  month: "numeric",
  day: "numeric",
});

export function formatRatio(value) {
  return `${ratioFormatter.format(Number(value || 0))}%`;
}

export function formatSignedRatio(value) {
  const number = Number(value || 0);
  return `${number > 0 ? "+" : ""}${ratioFormatter.format(number)}%`;
}

export function formatSignedPoints(value) {
  const number = Number(value || 0);
  return `${number > 0 ? "+" : ""}${ratioFormatter.format(number)} 百分點`;
}

export function formatLots(shares) {
  return `${integerFormatter.format(Number(shares || 0) / 1000)} 張`;
}

export function formatAccounts(value) {
  return `${integerFormatter.format(Number(value || 0))} 戶`;
}

export function formatDate(value) {
  if (!value) return "尚無資料";
  return dateFormatter.format(new Date(`${value}T00:00:00`));
}

export function formatShortDate(value) {
  if (!value) return "無日期";
  return shortDateFormatter.format(new Date(`${value}T00:00:00`));
}

export function marketLabel(market) {
  return market === "TPEX" ? "上櫃" : "上市";
}
