import { ArrowRight, TrendingDown, TrendingUp } from "lucide-react";

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

export default function HolderTurnCard({ stock, largeHolderOption }) {
  const isSellToBuy = stock.turn_type === "sell_to_buy";
  const DirectionIcon = isSellToBuy ? TrendingUp : TrendingDown;
  const directionLabel = isSellToBuy ? "由賣轉買" : "由買轉賣";
  const latestChange = stock.latest_change_percentage_points;
  const previousChange = stock.previous_change_percentage_points;

  return (
    <AppLink
      className={`stock-card turn-card ${isSellToBuy ? "sell-to-buy" : "buy-to-sell"}`}
      to={`/stocks/${stock.stock_code}?${largeHolderQuery(largeHolderOption)}`}
    >
      <div className="stock-card-head">
        <div className="stock-identity">
          <strong>{stock.stock_code}</strong>
          <div>
            <span>{stock.stock_name}</span>
            <div className="stock-meta">
              <span>{marketLabel(stock.market)}</span>
              <span>資料日期 {formatDate(stock.latest_date)}</span>
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

      <div className="turn-banner">
        <div>
          <DirectionIcon size={17} aria-hidden="true" />
          <span>{directionLabel}</span>
        </div>
        <strong>{formatSignedPoints(latestChange)}</strong>
      </div>

      <div className="turn-path" aria-label={`${directionLabel}比例變化`}>
        <div className="turn-phase">
          <span>前一段</span>
          <strong>{formatSignedPoints(previousChange)}</strong>
          <small>{formatDate(stock.oldest_date)} → {formatDate(stock.previous_date)}</small>
        </div>
        <ArrowRight className="turn-path-arrow" size={15} aria-hidden="true" />
        <div className="turn-phase">
          <span>最新一段</span>
          <strong>{formatSignedPoints(latestChange)}</strong>
          <small>{formatDate(stock.previous_date)} → {formatDate(stock.latest_date)}</small>
        </div>
      </div>

      <div className="holding-grid">
        <div className="holding-block holding-large">
          <span>最新大戶持股 · {largeHolderOption.label}</span>
          <strong>{formatRatio(stock.latest_large_ratio)}</strong>
          <small>{formatLots(stock.large_share_count)}</small>
        </div>
        <div className="holding-block holding-retail">
          <span>散戶持股</span>
          <strong>{formatRatio(stock.retail_ratio)}</strong>
          <small>{formatLots(stock.retail_share_count)} · {formatAccounts(stock.retail_holder_count)}</small>
        </div>
      </div>
    </AppLink>
  );
}
