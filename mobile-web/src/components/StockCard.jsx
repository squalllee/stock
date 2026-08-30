import { ArrowRight, TrendingUp } from "lucide-react";

import { AppLink } from "../lib/router.jsx";
import {
  formatAccounts,
  formatDate,
  formatLots,
  formatPrice,
  formatRatio,
  formatSignedPoints,
  formatShortDate,
  marketLabel,
} from "../lib/format.js";
import { largeHolderQuery } from "../lib/holderRanges.js";

export default function StockCard({ stock, screener = false, largeHolderOption }) {
  const latestRatio = screener
    ? stock.latest_large_ratio
    : stock.large_ratio;
  const latestDate = screener ? stock.latest_date : stock.data_date;

  return (
    <AppLink
      className="stock-card"
      to={`/stocks/${stock.stock_code}?${largeHolderQuery(largeHolderOption)}`}
    >
      <div className="stock-card-head">
        <div className="stock-identity">
          <strong>{stock.stock_code}</strong>
          <div>
            <span>{stock.stock_name}</span>
            <div className="stock-meta">
              <span>{marketLabel(stock.market)}</span>
              <span>資料日期 {formatDate(latestDate)}</span>
              <span
                className="stock-close"
                title={stock.latest_price_date
                  ? `股價日期 ${formatDate(stock.latest_price_date)}`
                  : "尚無每日成交價"}
              >
                最新收盤 <strong>{formatPrice(stock.latest_close_price)}</strong>
                {stock.latest_close_price === null || stock.latest_close_price === undefined
                  ? null
                  : " 元"}
                {stock.latest_price_date
                  ? ` · ${formatShortDate(stock.latest_price_date)}`
                  : null}
              </span>
            </div>
          </div>
        </div>
        <span className="card-arrow" aria-hidden="true"><ArrowRight size={18} /></span>
      </div>

      {screener ? (
        <div className="streak-banner">
          <TrendingUp size={17} />
          <span>連續 {stock.streak_weeks} 週增加</span>
          <strong>{formatSignedPoints(stock.increase_percentage_points)}</strong>
        </div>
      ) : null}

      <div className="holding-grid">
        <div className="holding-block holding-large">
          <span className="holding-label">大戶持股 · {largeHolderOption.label}</span>
          <strong>{formatRatio(latestRatio)}</strong>
          <small>{formatLots(stock.large_share_count)}</small>
        </div>
        <div className="holding-block holding-retail">
          <span className="holding-label">散戶持股</span>
          <strong>{formatRatio(stock.retail_ratio)}</strong>
          <small>{formatLots(stock.retail_share_count)} · {formatAccounts(stock.retail_holder_count)}</small>
        </div>
      </div>
    </AppLink>
  );
}
