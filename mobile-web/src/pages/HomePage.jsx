import { Search, SlidersHorizontal, TrendingUp, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "../components/Feedback.jsx";
import StockCard from "../components/StockCard.jsx";
import { fetchJson } from "../lib/api.js";

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
  const [screenState, setScreenState] = useState({
    status: "idle",
    items: [],
    error: "",
  });
  const searchInput = useRef(null);

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
          `/api/stocks/search?q=${encodeURIComponent(value)}&limit=10`,
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
  }, [query]);

  const loadScreener = useCallback(async () => {
    setScreenState((current) => ({ ...current, status: "loading", error: "" }));
    try {
      const payload = await fetchJson(
        `/api/screeners/increasing?weeks=${weeks}&limit=100`,
      );
      setScreenState({ status: "success", items: payload.items, error: "" });
    } catch (error) {
      setScreenState({ status: "error", items: [], error: error.message });
    }
  }, [weeks]);

  useEffect(() => {
    if (activeView === "screen") loadScreener();
  }, [activeView, loadScreener]);

  function switchView(view) {
    setActiveView(view);
    if (view === "search") {
      window.setTimeout(() => searchInput.current?.focus(), 80);
    }
  }

  return (
    <div className="page home-page">
      <section className="hero-panel">
        <span className="eyebrow">每週籌碼觀察</span>
        <h1>看懂大戶與散戶<br />持股變化</h1>
        <p>直接查詢 TDCC 最新股權分散，找出大戶連續加碼的台股。</p>
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
      </div>

      {activeView === "search" ? (
        <section className="content-section" aria-labelledby="search-title">
          <div className="section-heading">
            <div>
              <span className="section-kicker">SEARCH</span>
              <h2 id="search-title">搜尋股票</h2>
            </div>
            <small>代碼或名稱</small>
          </div>

          <label className="search-field">
            <Search size={20} />
            <input
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
          </label>

          <SearchResults state={searchState} query={query.trim()} />
        </section>
      ) : (
        <section className="content-section" aria-labelledby="screen-title">
          <div className="section-heading">
            <div>
              <span className="section-kicker">SCREENER</span>
              <h2 id="screen-title">大戶連續增持</h2>
            </div>
            <SlidersHorizontal size={20} />
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

          <ScreenerResults state={screenState} weeks={weeks} onRetry={loadScreener} />
        </section>
      )}

      <footer className="definition-note">
        <strong>計算口徑</strong>
        <p>
          大戶採 TDCC 第 15 級距（1,000,001 股以上，約 1,000 張以上）；
          散戶為第 1～6 級距合計（30 張以下）。
        </p>
      </footer>
    </div>
  );
}

function SearchResults({ state, query }) {
  if (!query) {
    return (
      <div className="search-prompt">
        <div className="prompt-orbit"><Search size={24} /></div>
        <strong>輸入股票代碼或名稱</strong>
        <span>立即查看最新大戶與散戶持股比例、持股張數</span>
      </div>
    );
  }
  if (state.status === "loading") return <LoadingState label="正在搜尋股票…" />;
  if (state.status === "error") return <ErrorState message={state.error} />;
  if (state.status === "success" && !state.items.length) {
    return <EmptyState title="找不到相符股票" description="請確認代碼或改用較短的名稱搜尋。" />;
  }
  return (
    <div className="result-list">
      {state.items.map((stock) => <StockCard key={stock.stock_code} stock={stock} />)}
    </div>
  );
}

function ScreenerResults({ state, weeks, onRetry }) {
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
        <span>符合條件</span>
        <strong>{state.items.length} 檔</strong>
        <small>依增幅排序</small>
      </div>
      <div className="result-list">
        {state.items.map((stock) => (
          <StockCard key={stock.stock_code} stock={stock} screener />
        ))}
      </div>
    </>
  );
}
