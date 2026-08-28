import {
  ArrowLeftRight,
  Search,
  SlidersHorizontal,
  TrendingUp,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "../components/Feedback.jsx";
import HolderTurnCard from "../components/HolderTurnCard.jsx";
import StockCard from "../components/StockCard.jsx";
import { fetchJson } from "../lib/api.js";
import {
  DEFAULT_LARGE_HOLDER_RANGE,
  getLargeHolderOption,
  LARGE_HOLDER_OPTIONS,
  largeHolderQuery,
} from "../lib/holderRanges.js";

const WEEK_OPTIONS = [2, 3, 4, 5, 6, 8, 12];

export default function HomePage() {
  const [activeView, setActiveView] = useState("search");
  const [query, setQuery] = useState("");
  const [searchState, setSearchState] = useState({
    status: "idle",
    items: [],
    error: "",
  });
  const [weeks, setWeeks] = useState(3);
  const [largeHolderRange, setLargeHolderRange] = useState(() => (
    window.localStorage.getItem("large-holder-range")
      || DEFAULT_LARGE_HOLDER_RANGE
  ));
  const [screenState, setScreenState] = useState({
    status: "idle",
    items: [],
    error: "",
  });
  const [turnType, setTurnType] = useState("sell_to_buy");
  const [turnState, setTurnState] = useState({
    status: "idle",
    items: [],
    error: "",
  });
  const searchInput = useRef(null);
  const largeHolderOption = getLargeHolderOption(largeHolderRange);
  const largeHolderParams = largeHolderQuery(largeHolderOption);

  useEffect(() => {
    window.localStorage.setItem("large-holder-range", largeHolderOption.value);
  }, [largeHolderOption.value]);

  useEffect(() => {
    const value = query.trim();
    if (!value) {
      setSearchState({ status: "idle", items: [], error: "" });
      return undefined;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setSearchState((current) => ({ ...current, status: "loading", error: "" }));
      try {
        const payload = await fetchJson(
          `/api/stocks/search?q=${encodeURIComponent(value)}&limit=10&${largeHolderParams}`,
          { signal: controller.signal },
        );
        setSearchState({ status: "success", items: payload.items, error: "" });
      } catch (error) {
        if (error.name !== "AbortError") {
          setSearchState({ status: "error", items: [], error: error.message });
        }
      }
    }, 260);

    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [query, largeHolderParams]);

  const loadScreener = useCallback(async () => {
    setScreenState((current) => ({ ...current, status: "loading", error: "" }));
    try {
      const payload = await fetchJson(
        `/api/screeners/increasing?weeks=${weeks}&limit=100&${largeHolderParams}`,
      );
      setScreenState({ status: "success", items: payload.items, error: "" });
    } catch (error) {
      setScreenState({ status: "error", items: [], error: error.message });
    }
  }, [weeks, largeHolderParams]);

  const loadHolderTurns = useCallback(async () => {
    setTurnState((current) => ({ ...current, status: "loading", error: "" }));
    try {
      const payload = await fetchJson(
        `/api/screeners/holder-turns?limit=100&${largeHolderParams}`,
      );
      setTurnState({ status: "success", items: payload.items, error: "" });
    } catch (error) {
      setTurnState({ status: "error", items: [], error: error.message });
    }
  }, [largeHolderParams]);

  useEffect(() => {
    if (activeView === "screen") loadScreener();
  }, [activeView, loadScreener]);

  useEffect(() => {
    if (activeView === "turns") loadHolderTurns();
  }, [activeView, loadHolderTurns]);

  function switchView(view) {
    setActiveView(view);
    if (view === "search") {
      window.setTimeout(() => searchInput.current?.focus(), 80);
    }
  }

  return (
    <div className="page home-page">
      <section className="hero-panel" aria-labelledby="home-title">
        <div className="hero-copy">
          <span className="eyebrow">TDCC / 每週資料</span>
          <h1 id="home-title">看懂大戶與散戶<br />持股變化</h1>
          <p>直接查詢 TDCC 最新股權分散，找出大戶加碼與買賣轉折的台股。</p>
        </div>
        <aside className="hero-brief" aria-label="資料口徑摘要">
          <div className="hero-brief-top">
            <span>本頁觀察</span>
            <strong>每週更新</strong>
          </div>
          <dl>
            <div><dt>大戶</dt><dd>{largeHolderOption.label}</dd></div>
            <div><dt>散戶</dt><dd>第 1～6 級距</dd></div>
            <div><dt>動向</dt><dd>最近三期轉折</dd></div>
            <div><dt>查詢內容</dt><dd>比例與持股張數</dd></div>
          </dl>
        </aside>
      </section>

      <div className="view-switcher" role="tablist" aria-label="功能切換">
        <button
          type="button"
          role="tab"
          aria-selected={activeView === "search"}
          className={activeView === "search" ? "active" : ""}
          onClick={() => switchView("search")}
        >
          <Search size={18} />
          個股查詢
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeView === "screen"}
          className={activeView === "screen" ? "active" : ""}
          onClick={() => switchView("screen")}
        >
          <TrendingUp size={18} />
          連續增持
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeView === "turns"}
          className={activeView === "turns" ? "active" : ""}
          onClick={() => switchView("turns")}
        >
          <ArrowLeftRight size={18} />
          大戶動向
        </button>
      </div>

      <section className="holder-range-panel" aria-labelledby="holder-range-title">
        <div>
          <strong id="holder-range-title">大戶統計範圍</strong>
          <span>依 TDCC 公布的持股級距加總</span>
        </div>
        <label>
          <span className="sr-only">選擇大戶持股張數範圍</span>
          <select
            value={largeHolderOption.value}
            onChange={(event) => setLargeHolderRange(event.target.value)}
          >
            {LARGE_HOLDER_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
      </section>

      {activeView === "search" ? (
        <section className="content-section" aria-labelledby="search-title">
          <div className="section-heading">
            <div>
              <h2 id="search-title">搜尋股票</h2>
              <p>輸入代碼或名稱，查看最新持股結構。</p>
            </div>
            <small className="section-meta">個股查詢</small>
          </div>

          <div className="search-form">
            <label className="field-label" htmlFor="stock-search">股票代碼或名稱</label>
            <div className="search-field">
              <Search size={20} aria-hidden="true" />
              <input
                id="stock-search"
                ref={searchInput}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="例如：2330 或 台積電"
                autoComplete="off"
                inputMode="search"
                aria-label="輸入股票代碼或股票名稱"
              />
              {query ? (
                <button type="button" onClick={() => setQuery("")} aria-label="清除搜尋">
                  <X size={18} />
                </button>
              ) : null}
            </div>
          </div>

          <SearchResults
            state={searchState}
            query={query.trim()}
            largeHolderOption={largeHolderOption}
          />
        </section>
      ) : activeView === "screen" ? (
        <section className="content-section" aria-labelledby="screen-title">
          <div className="section-heading">
            <div>
              <h2 id="screen-title">大戶連續增持</h2>
              <p>只保留大戶持股比例每週都高於前一週的股票。</p>
            </div>
            <SlidersHorizontal className="section-icon" size={20} aria-hidden="true" />
          </div>

          <div className="filter-panel">
            <div className="filter-copy">
              <strong>連續週數</strong>
              <span>大戶持股比例每週都高於前一週</span>
            </div>
            <div className="week-options" aria-label="選擇連續週數">
              {WEEK_OPTIONS.map((value) => (
                <button
                  type="button"
                  key={value}
                  className={weeks === value ? "active" : ""}
                  onClick={() => setWeeks(value)}
                >
                  {value} 週
                </button>
              ))}
            </div>
          </div>

          <ScreenerResults
            state={screenState}
            weeks={weeks}
            onRetry={loadScreener}
            largeHolderOption={largeHolderOption}
          />
        </section>
      ) : (
        <section className="content-section" aria-labelledby="turn-title">
          <div className="section-heading">
            <div>
              <h2 id="turn-title">大戶買賣轉折</h2>
              <p>比較最近三期大戶持股比例，找出由賣轉買或由買轉賣的股票。</p>
            </div>
            <ArrowLeftRight className="section-icon" size={20} aria-hidden="true" />
          </div>

          <div className="turn-type-switcher" role="tablist" aria-label="大戶動向方向">
            <button
              type="button"
              role="tab"
              aria-selected={turnType === "sell_to_buy"}
              className={turnType === "sell_to_buy" ? "active sell-to-buy" : "sell-to-buy"}
              onClick={() => setTurnType("sell_to_buy")}
            >
              <span>由賣轉買</span>
              <strong>{countTurns(turnState.items, "sell_to_buy")}</strong>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={turnType === "buy_to_sell"}
              className={turnType === "buy_to_sell" ? "active buy-to-sell" : "buy-to-sell"}
              onClick={() => setTurnType("buy_to_sell")}
            >
              <span>由買轉賣</span>
              <strong>{countTurns(turnState.items, "buy_to_sell")}</strong>
            </button>
          </div>

          <HolderTurnResults
            state={turnState}
            turnType={turnType}
            onRetry={loadHolderTurns}
            largeHolderOption={largeHolderOption}
          />
        </section>
      )}

      <footer className="definition-note">
        <strong>計算口徑</strong>
        <p>
          大戶目前採「{largeHolderOption.label}」級距；
          散戶為第 1～6 級距合計（30 張以下）。
        </p>
      </footer>
    </div>
  );
}

function SearchResults({ state, query, largeHolderOption }) {
  if (!query) {
    return (
      <div className="search-prompt">
        <div className="prompt-orbit"><Search size={24} /></div>
        <strong>從一檔股票開始</strong>
        <span>輸入代碼或名稱，立即查看大戶與散戶持股。</span>
      </div>
    );
  }
  if (state.status === "loading") return <LoadingState label="正在搜尋股票…" />;
  if (state.status === "error") return <ErrorState message={state.error} />;
  if (state.status === "success" && !state.items.length) {
    return <EmptyState title="找不到相符股票" description="請確認代碼或改用較短的名稱搜尋。" />;
  }
  return (
    <>
      <div className="result-summary">
        <div><span>搜尋結果</span><strong>{state.items.length}</strong><span>檔</span></div>
        <small>點選查看明細</small>
      </div>
      <div className="result-list">
        {state.items.map((stock) => (
          <StockCard
            key={stock.stock_code}
            stock={stock}
            largeHolderOption={largeHolderOption}
          />
        ))}
      </div>
    </>
  );
}

function ScreenerResults({ state, weeks, onRetry, largeHolderOption }) {
  if (state.status === "loading" || state.status === "idle") {
    return <LoadingState label={`正在篩選連續 ${weeks} 週增持股票…`} />;
  }
  if (state.status === "error") return <ErrorState message={state.error} onRetry={onRetry} />;
  if (!state.items.length) {
    return (
      <EmptyState
        title={`目前沒有連續 ${weeks} 週增持的股票`}
        description="可改用較短的連續週數再試一次。"
      />
    );
  }
  return (
    <>
      <div className="result-summary">
        <div><span>符合條件</span><strong>{state.items.length}</strong><span>檔</span></div>
        <small>依大戶增加幅度排序</small>
      </div>
      <div className="result-list">
        {state.items.map((stock) => (
          <StockCard
            key={stock.stock_code}
            stock={stock}
            screener
            largeHolderOption={largeHolderOption}
          />
        ))}
      </div>
    </>
  );
}

function countTurns(items, turnType) {
  return (items || []).filter((item) => item.turn_type === turnType).length;
}

function HolderTurnResults({ state, turnType, onRetry, largeHolderOption }) {
  if (state.status === "loading" || state.status === "idle") {
    return <LoadingState label="正在整理大戶買賣轉折…" />;
  }
  if (state.status === "error") return <ErrorState message={state.error} onRetry={onRetry} />;

  const items = state.items.filter((item) => item.turn_type === turnType);
  const title = turnType === "sell_to_buy" ? "由賣轉買" : "由買轉賣";
  const description = turnType === "sell_to_buy"
    ? "前一期大戶持股下降，最新一期回升。"
    : "前一期大戶持股上升，最新一期回落。";

  if (!items.length) {
    return (
      <EmptyState
        title={`目前沒有${title}的股票`}
        description="需要連續三期 TDCC 資料，且前後方向必須明確改變。"
      />
    );
  }

  return (
    <>
      <div className="result-summary">
        <div><span>{title}</span><strong>{items.length}</strong><span>檔</span></div>
        <small>{description}</small>
      </div>
      <div className="result-list turn-list">
        {items.map((stock) => (
          <HolderTurnCard
            key={stock.stock_code}
            stock={stock}
            largeHolderOption={largeHolderOption}
          />
        ))}
      </div>
    </>
  );
}
