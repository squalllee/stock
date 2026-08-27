import { CalendarDays, Info, TrendingDown, TrendingUp, Users } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState, ErrorState, LoadingState } from "../components/Feedback.jsx";
import { fetchJson } from "../lib/api.js";
import {
  formatAccounts,
  formatDate,
  formatLots,
  formatRatio,
  formatShortDate,
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

  const { stock, latest, history } = state.data;
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

          <section className="detail-section">
            <div className="section-heading compact">
              <div>
                <h2>持股比例趨勢</h2>
                <p>比較大戶與散戶在不同週期的比例變化。</p>
              </div>
            </div>
            <div className="range-toolbar">
              <span>顯示區間</span>
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
            <TrendChart history={history} />
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

function TrendChart({ history }) {
  const chartData = useMemo(
    () => [...history].reverse().map((item) => ({
      ...item,
      label: formatShortDate(item.data_date),
    })),
    [history],
  );

  return (
    <div className="chart-wrap" aria-label="大戶與散戶持股比例折線圖">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 12, right: 2, left: -18, bottom: 0 }}>
          <CartesianGrid stroke="rgba(89, 119, 119, .16)" vertical={false} />
          <XAxis dataKey="label" stroke="#71858a" tickLine={false} axisLine={false} minTickGap={24} fontSize={11} />
          <YAxis stroke="#71858a" tickLine={false} axisLine={false} fontSize={11} tickFormatter={(value) => `${value}%`} />
          <Tooltip content={<ChartTooltip />} />
          <Legend iconType="circle" wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
          <Line type="monotone" name="大戶" dataKey="large_ratio" stroke="#078b78" strokeWidth={2.5} dot={false} activeDot={{ r: 4 }} />
          <Line type="monotone" name="散戶" dataKey="retail_ratio" stroke="#bd6907" strokeWidth={2.5} dot={false} activeDot={{ r: 4 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function ChartTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div className="chart-tooltip">
      <strong>{formatDate(row.data_date)}</strong>
      <span className="large-dot">大戶 {formatRatio(row.large_ratio)}</span>
      <span className="retail-dot">散戶 {formatRatio(row.retail_ratio)}</span>
    </div>
  );
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
