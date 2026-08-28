import { useMemo, useState } from "react";

import {
  formatDate,
  formatPrice,
  formatShortDate,
  formatVolume,
} from "../lib/format.js";

const CHART_WIDTH = 720;
const CHART_HEIGHT = 330;
const PLOT_LEFT = 48;
const PLOT_RIGHT = 12;
const PRICE_TOP = 18;
const PRICE_BOTTOM = 214;
const VOLUME_TOP = 238;
const VOLUME_BOTTOM = 294;

const PRICE_UP = "#bc4b4f";
const PRICE_DOWN = "#087f70";
const MOVING_AVERAGES = [
  { key: "ma5", label: "MA5", color: "#bd6907" },
  { key: "ma10", label: "MA10", color: "#7a58a4" },
  { key: "ma20", label: "MA20", color: "#3d6fa2" },
];

export default function PriceChart({ prices = [], weeks = 26 }) {
  const rows = useMemo(() => prepareRows(prices, weeks), [prices, weeks]);
  const [selectedDate, setSelectedDate] = useState("");

  if (!rows.length) {
    return (
      <div className="price-chart-empty">
        <strong>尚無每日股價資料</strong>
        <span>請先同步每日成交行情，再回到這裡查看 K 線。</span>
      </div>
    );
  }

  const selected = rows.find((row) => row.trade_date === selectedDate) || rows[rows.length - 1];
  const { minimum, maximum } = getPriceDomain(rows);
  const maximumVolume = Math.max(
    ...rows.map((row) => numberOrZero(row.trade_volume)),
    1,
  );
  const plotWidth = CHART_WIDTH - PLOT_LEFT - PLOT_RIGHT;
  const xFor = (index) =>
    PLOT_LEFT + (rows.length === 1 ? plotWidth / 2 : (index / (rows.length - 1)) * plotWidth);
  const yForPrice = (value) =>
    PRICE_BOTTOM - ((value - minimum) / (maximum - minimum)) * (PRICE_BOTTOM - PRICE_TOP);
  const yForVolume = (value) =>
    VOLUME_BOTTOM - (numberOrZero(value) / maximumVolume) * (VOLUME_BOTTOM - VOLUME_TOP);
  const candleWidth = Math.max(
    2.2,
    Math.min(10, (plotWidth / Math.max(rows.length, 1)) * 0.62),
  );
  const selectedClose = numberOrNull(selected.close_price);
  const selectedOpen = numberOrNull(selected.open_price) ?? selectedClose;
  const selectedTone = selectedClose !== null && selectedOpen !== null && selectedClose >= selectedOpen
    ? "up"
    : "down";
  const closeCount = rows.filter((row) => numberOrNull(row.close_price) !== null).length;
  const chartNote = closeCount < 20
    ? `目前有 ${closeCount} 個交易日，MA5／MA10／MA20 會在資料累積足夠後依序顯示。`
    : "K 線上漲為紅色、下跌為綠色；成交量以張顯示。";
  const priceTicks = Array.from({ length: 4 }, (_, index) =>
    maximum - ((maximum - minimum) / 3) * index,
  );
  const dateTickIndexes = getTickIndexes(rows.length, 6);

  return (
    <div className="price-chart" aria-label="每日股價 K 線、移動平均線與成交量">
      <div className="price-readout" aria-live="polite">
        <div className={`price-readout-heading ${selectedTone}`}>
          <span>{selectedDate ? "選取交易日" : "最新交易日"}</span>
          <strong>{formatDate(selected.trade_date)}</strong>
          <small>收盤 {formatPrice(selected.close_price)}</small>
        </div>
        <dl className="price-readout-values">
          <div><dt>開</dt><dd>{formatPrice(selected.open_price)}</dd></div>
          <div><dt>高</dt><dd>{formatPrice(selected.high_price)}</dd></div>
          <div><dt>低</dt><dd>{formatPrice(selected.low_price)}</dd></div>
          <div><dt>收</dt><dd>{formatPrice(selected.close_price)}</dd></div>
          <div><dt>成交量</dt><dd>{formatVolume(selected.trade_volume)}</dd></div>
        </dl>
      </div>

      <div className="price-legend" aria-label="股價圖例">
        <span><i className="candle-swatch up" aria-hidden="true" />上漲</span>
        <span><i className="candle-swatch down" aria-hidden="true" />下跌</span>
        {MOVING_AVERAGES.map((item) => (
          <span key={item.key}>
            <i className={`ma-swatch ${item.key}`} aria-hidden="true" />
            {item.label}
          </span>
        ))}
      </div>

      <svg
        className="price-chart-svg"
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        role="img"
        aria-label="每日股價 K 線圖，包含 5 日、10 日、20 日移動平均線與成交量"
      >
        <rect
          className="price-panel-fill"
          x={PLOT_LEFT}
          y={PRICE_TOP}
          width={plotWidth}
          height={PRICE_BOTTOM - PRICE_TOP}
          rx="8"
        />
        <rect
          className="volume-panel-fill"
          x={PLOT_LEFT}
          y={VOLUME_TOP}
          width={plotWidth}
          height={VOLUME_BOTTOM - VOLUME_TOP}
          rx="8"
        />

        {priceTicks.map((tick) => {
          const y = yForPrice(tick);
          return (
            <g key={tick}>
              <line className="price-grid-line" x1={PLOT_LEFT} x2={CHART_WIDTH - PLOT_RIGHT} y1={y} y2={y} />
              <text className="price-axis-label" x={PLOT_LEFT - 8} y={y + 4} textAnchor="end">
                {formatPrice(tick)}
              </text>
            </g>
          );
        })}
        <line className="volume-axis-line" x1={PLOT_LEFT} x2={CHART_WIDTH - PLOT_RIGHT} y1={VOLUME_BOTTOM} y2={VOLUME_BOTTOM} />
        <text className="volume-axis-label" x={PLOT_LEFT + 8} y={VOLUME_TOP - 7}>
          成交量（張）
        </text>
        <text className="volume-axis-label" x={CHART_WIDTH - PLOT_RIGHT - 6} y={VOLUME_TOP - 7} textAnchor="end">
          {formatVolume(maximumVolume)}
        </text>

        <line
          className="price-crosshair"
          x1={xFor(rows.indexOf(selected))}
          x2={xFor(rows.indexOf(selected))}
          y1={PRICE_TOP}
          y2={VOLUME_BOTTOM}
        />

        {rows.map((row, index) => {
          const x = xFor(index);
          const volume = numberOrZero(row.trade_volume);
          const close = numberOrNull(row.close_price);
          const open = numberOrNull(row.open_price) ?? close;
          const high = numberOrNull(row.high_price) ?? (open !== null && close !== null ? Math.max(open, close) : null);
          const low = numberOrNull(row.low_price) ?? (open !== null && close !== null ? Math.min(open, close) : null);
          const isUp = close !== null && open !== null && close >= open;
          const color = isUp ? PRICE_UP : PRICE_DOWN;
          const isSelected = row.trade_date === selected.trade_date;
          const bodyTop = open !== null && close !== null ? yForPrice(Math.max(open, close)) : null;
          const bodyHeight = open !== null && close !== null
            ? Math.max(1.6, Math.abs(yForPrice(open) - yForPrice(close)))
            : 0;

          return (
            <g
              key={`${row.trade_date}-${index}`}
              className={`price-candle${isSelected ? " selected" : ""}`}
              onMouseEnter={() => setSelectedDate(row.trade_date)}
              onClick={() => setSelectedDate(row.trade_date)}
            >
              <title>
                {`${formatDate(row.trade_date)} 開 ${formatPrice(row.open_price)} 高 ${formatPrice(row.high_price)} 低 ${formatPrice(row.low_price)} 收 ${formatPrice(row.close_price)}`}
              </title>
              <rect
                className="volume-bar"
                x={x - candleWidth / 2}
                y={yForVolume(volume)}
                width={candleWidth}
                height={Math.max(0.8, VOLUME_BOTTOM - yForVolume(volume))}
                fill={color}
              />
              {high !== null && low !== null ? (
                <line
                  className="candle-wick"
                  x1={x}
                  x2={x}
                  y1={yForPrice(high)}
                  y2={yForPrice(low)}
                  stroke={color}
                />
              ) : null}
              {bodyTop !== null ? (
                <rect
                  className="candle-body"
                  x={x - candleWidth / 2}
                  y={bodyTop}
                  width={candleWidth}
                  height={bodyHeight}
                  fill={color}
                />
              ) : null}
            </g>
          );
        })}

        {MOVING_AVERAGES.map((item) => {
          const path = buildLinePath(rows, item.key, xFor, yForPrice);
          return path ? (
            <path
              key={item.key}
              className="price-ma-line"
              d={path}
              stroke={item.color}
            />
          ) : null;
        })}

        {dateTickIndexes.map((index) => (
          <text
            key={index}
            className="price-date-label"
            x={xFor(index)}
            y={CHART_HEIGHT - 9}
            textAnchor={index === 0 ? "start" : index === rows.length - 1 ? "end" : "middle"}
          >
            {formatShortDate(rows[index].trade_date)}
          </text>
        ))}
      </svg>
      <p className="price-chart-note">{chartNote} 點選或將游標移到 K 線上可查看當日開高低收。</p>
    </div>
  );
}

function prepareRows(prices, weeks) {
  const ordered = [...prices]
    .filter((row) => row?.trade_date)
    .sort((left, right) => String(left.trade_date).localeCompare(String(right.trade_date)))
    .map((row, index, allRows) => ({
      ...row,
      ma5: movingAverage(allRows, index, 5),
      ma10: movingAverage(allRows, index, 10),
      ma20: movingAverage(allRows, index, 20),
    }));
  const visibleCount = Math.max(1, Number(weeks || 26) * 5);
  return ordered.slice(-visibleCount);
}

function movingAverage(rows, endIndex, period) {
  if (endIndex + 1 < period) return null;
  const window = rows.slice(endIndex - period + 1, endIndex + 1);
  if (window.some((row) => numberOrNull(row.close_price) === null)) return null;
  return window.reduce((sum, row) => sum + Number(row.close_price), 0) / period;
}

function getPriceDomain(rows) {
  const values = rows.flatMap((row) => [
    numberOrNull(row.open_price),
    numberOrNull(row.high_price),
    numberOrNull(row.low_price),
    numberOrNull(row.close_price),
  ]).filter((value) => value !== null);
  if (!values.length) return { minimum: 0, maximum: 1 };
  const lowest = Math.min(...values);
  const highest = Math.max(...values);
  const padding = Math.max((highest - lowest) * 0.08, Math.abs(highest) * 0.01, 0.5);
  return { minimum: Math.max(0, lowest - padding), maximum: highest + padding };
}

function buildLinePath(rows, key, xFor, yForPrice) {
  const segments = [];
  let current = [];
  rows.forEach((row, index) => {
    const value = numberOrNull(row[key]);
    if (value === null) {
      if (current.length) segments.push(current.join(" "));
      current = [];
      return;
    }
    current.push(`${current.length ? "L" : "M"}${xFor(index)},${yForPrice(value)}`);
  });
  if (current.length) segments.push(current.join(" "));
  return segments.join(" ");
}

function getTickIndexes(length, maximumTicks) {
  if (length <= maximumTicks) return Array.from({ length }, (_, index) => index);
  return Array.from({ length: maximumTicks }, (_, index) =>
    Math.round((index / (maximumTicks - 1)) * (length - 1)),
  ).filter((index, position, indexes) => indexes.indexOf(index) === position);
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function numberOrZero(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}
