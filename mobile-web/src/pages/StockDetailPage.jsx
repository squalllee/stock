import { CalendarDays, Info, TrendingDown, TrendingUp, Users } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState, ErrorState, LoadingState } from "../components/Feedback.jsx";
import PriceChart from "../components/PriceChart.jsx";
import { fetchJson } from "../lib/api.js";
import {
  formatAccounts,
  formatDate,
  formatIndex,
  formatLots,
  formatRatio,
  formatShortDate,
  formatSignedPoints,
  formatSignedRatio,
  marketLabel,
} from "../lib/format.js";

const RANGE_OPTIONS = [12, 26, 52];

export default function StockDetailPage({ stockCode }) {
  const [weeks, setWeeks] = useState(26);
  const [state, setState] = useState({ status: "loading", data: null, error: "" });

  const loadDetail = useCallback(async () => {
    setState((current) => ({ ...current, status: "loading", error: "" }));
    try {
      const payload = await fetchJson(`/api/stocks/${stockCode}?weeks=${weeks}`);
      setState({ status: "success", data: payload, error: "" });
      document.title = `${payload.stock.stock_code} ${payload.stock.stock_name}｜籌碼週報`;
    } catch (error) {
      setState({ status: "error", data: null, error: error.message });
    }
  }, [stockCode, weeks]);

  useEffect(() => {
    loadDetail();
    return () => {
      document.title = "籌碼週報｜TDCC 股權分散";
    };
  }, [loadDetail]);

  if (state.status === "loading" && !state.data) {
    return <div className="page detail-page"><LoadingState label="正在讀取持股明細…" /></div>;
  }
  if (state.status === "error") {
    return <div className="page detail-page"><ErrorState message={state.error} onRetry={loadDetail} /></div>;
  }

  const {
    stock,
    latest,
    history,
    annual_baselines: annualBaselines = {},
    prices = [],
  } = state.data;
  return (
    <div className="page detail-page">
      <section className="detail-hero" aria-labelledby="detail-title">
        <div className="detail-stock-title">
          <span>{stock.stock_code}</span>
          <h1 id="detail-title">{stock.stock_name}</h1>
          <small>{marketLabel(stock.market)}</small>
        </div>
        <div className="as-of-date">
          <CalendarDays size={15} aria-hidden="true" />
          <span>最近資料</span>
          <strong>{formatDate(latest?.data_date)}</strong>
        </div>
      </section>

      {!latest ? (
        <EmptyState title="尚無 TDCC 資料" description="請先在同步工具更新這支股票的 TDCC 資料。" />
      ) : (
        <>
          <section className="latest-grid" aria-label="最新持股摘要">
            <HoldingHero
              variant="large"
              title="大戶持股"
              ratio={latest.large_ratio}
              shares={latest.large_share_count}
              holders={latest.large_holder_count}
            />
            <HoldingHero
              variant="retail"
              title="散戶持股"
              ratio={latest.retail_ratio}
              shares={latest.retail_share_count}
              holders={latest.retail_holder_count}
            />
          </section>

          <section className="detail-section price-section">
            <div className="section-heading compact">
              <div>
                <h2>每日股價</h2>
                <p>日 K 線搭配 5、10、20 日移動平均線，成交量列在下方。</p>
              </div>
            </div>
            <div className="range-toolbar">
              <span>股價與持股趨勢區間</span>
              <div className="range-options">
                {RANGE_OPTIONS.map((value) => (
                  <button
                    key={value}
                    type="button"
                    className={weeks === value ? "active" : ""}
                    onClick={() => setWeeks(value)}
                  >
                    {value}週
                  </button>
                ))}
              </div>
            </div>
            <PriceChart prices={prices} weeks={weeks} />
          </section>

          <section className="detail-section">
            <div className="section-heading compact">
              <div>
                <h2>持股比例增幅</h2>
                <p>每個年度的最低持股比例設為 1，只比較其後增加的幅度。</p>
              </div>
            </div>
            <TrendChart history={history} annualBaselines={annualBaselines} />
          </section>

          <section className="detail-section">
            <div className="section-heading compact">
              <div>
                <h2>每週明細</h2>
                <p>依資料日期由新到舊排列。</p>
              </div>
              <small className="section-meta">{history.length} 期</small>
            </div>
            <WeeklyHistory history={history} />
          </section>
        </>
      )}

      <footer className="definition-note detail-definition">
        <Info size={18} />
        <div>
          <strong>資料口徑</strong>
          <p>大戶為 TDCC 第 15 級距；散戶為第 1～6 級距加總。持股張數由股數除以 1,000 計算。</p>
        </div>
      </footer>
    </div>
  );
}

function HoldingHero({ variant, title, ratio, shares, holders }) {
  return (
    <article className={`holding-hero ${variant}`}>
      <div className="holding-hero-icon"><Users size={19} /></div>
      <span>{title}</span>
      <strong>{formatRatio(ratio)}</strong>
      <dl>
        <div><dt>持股數</dt><dd>{formatLots(shares)}</dd></div>
        <div><dt>戶數</dt><dd>{formatAccounts(holders)}</dd></div>
      </dl>
    </article>
  );
}

function TrendChart({ history, annualBaselines }) {
  const chartData = useMemo(
    () => {
      const baselines = resolveAnnualBaselines(history, annualBaselines);
      return [...history].reverse().map((item) => {
        const year = item.data_date.slice(0, 4);
        const baseline = baselines[year];
        const largeIncrease = Math.max(0, item.large_ratio - baseline.large_ratio);
        const retailIncrease = Math.max(0, item.retail_ratio - baseline.retail_ratio);
        return {
          ...item,
          label: formatShortDate(item.data_date),
          baseline_year: year,
          large_baseline: baseline.large_ratio,
          retail_baseline: baseline.retail_ratio,
          large_increase: largeIncrease,
          retail_increase: retailIncrease,
          large_index: roundTrendValue(largeIncrease + 1),
          retail_index: roundTrendValue(retailIncrease + 1),
        };
      });
    },
    [annualBaselines, history],
  );
  const latestYear = history[0]?.data_date.slice(0, 4);
  const latestBaseline = latestYear
    ? resolveAnnualBaselines(history, annualBaselines)[latestYear]
    : null;

  return (
    <>
      <div className="chart-wrap holding-bar-chart" aria-label="大戶與散戶持股增幅指數長條圖">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={chartData}
            margin={{ top: 12, right: 2, left: -18, bottom: 0 }}
            barCategoryGap="24%"
            barGap={2}
          >
            <CartesianGrid stroke="rgba(89, 119, 119, .16)" vertical={false} />
            <XAxis dataKey="label" stroke="#71858a" tickLine={false} axisLine={false} minTickGap={24} fontSize={11} />
            <YAxis stroke="#71858a" tickLine={false} axisLine={false} fontSize={11} domain={[0, "auto"]} tickFormatter={formatIndex} />
            <Tooltip cursor={{ fill: "rgba(8, 127, 112, .05)" }} content={<ChartTooltip />} />
            <Legend iconType="square" wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
            <Bar name="大戶增幅指數" dataKey="large_index" fill="#078b78" radius={[4, 4, 0, 0]} maxBarSize={18} />
            <Bar name="散戶增幅指數" dataKey="retail_index" fill="#bd6907" radius={[4, 4, 0, 0]} maxBarSize={18} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      {latestBaseline ? (
        <div className="trend-baseline">
          <div className="trend-baseline-values">
            <div className="large">
              <span>{latestYear} 大戶最低</span>
              <strong>{formatRatio(latestBaseline.large_ratio)}</strong>
              <small>基準 1</small>
            </div>
            <div className="retail">
              <span>{latestYear} 散戶最低</span>
              <strong>{formatRatio(latestBaseline.retail_ratio)}</strong>
              <small>基準 1</small>
            </div>
          </div>
          <p>柱高 = 當期持股比例 - 當年度最低持股比例 + 1</p>
        </div>
      ) : null}
    </>
  );
}

function ChartTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div className="chart-tooltip">
      <strong>{formatDate(row.data_date)}</strong>
      <span className="large-dot">
        大戶指數 {formatIndex(row.large_index)}
        <small>實際 {formatRatio(row.large_ratio)}，較年度最低 {formatSignedPoints(row.large_increase)}</small>
      </span>
      <span className="retail-dot">
        散戶指數 {formatIndex(row.retail_index)}
        <small>實際 {formatRatio(row.retail_ratio)}，較年度最低 {formatSignedPoints(row.retail_increase)}</small>
      </span>
    </div>
  );
}

function resolveAnnualBaselines(history, providedBaselines) {
  return history.reduce((baselines, item) => {
    const year = item.data_date.slice(0, 4);
    if (providedBaselines?.[year]) {
      baselines[year] = providedBaselines[year];
      return baselines;
    }
    const current = baselines[year];
    baselines[year] = {
      large_ratio: current
        ? Math.min(current.large_ratio, item.large_ratio)
        : item.large_ratio,
      retail_ratio: current
        ? Math.min(current.retail_ratio, item.retail_ratio)
        : item.retail_ratio,
    };
    return baselines;
  }, {});
}

function roundTrendValue(value) {
  return Math.round(value * 10000) / 10000;
}

function WeeklyHistory({ history }) {
  return (
    <div className="week-list">
      {history.map((item, index) => {
        const older = history[index + 1];
        const delta = older ? item.large_ratio - older.large_ratio : 0;
        const DeltaIcon = delta >= 0 ? TrendingUp : TrendingDown;
        return (
          <article className="week-row" key={item.data_date}>
            <div className="week-date">
              <strong>{formatShortDate(item.data_date)}</strong>
              <span>{item.data_date.slice(0, 4)}</span>
            </div>
            <div className="week-main">
              <div>
                <span>大戶</span>
                <strong>{formatRatio(item.large_ratio)}</strong>
                <small>{formatLots(item.large_share_count)}</small>
              </div>
              <div>
                <span>散戶</span>
                <strong>{formatRatio(item.retail_ratio)}</strong>
                <small>{formatLots(item.retail_share_count)}</small>
              </div>
            </div>
            <div className={`week-delta ${delta < 0 ? "down" : "up"}`} title="大戶比例週變化">
              <DeltaIcon size={14} />
              {older ? formatSignedRatio(delta) : "起始"}
            </div>
          </article>
        );
      })}
    </div>
  );
}
