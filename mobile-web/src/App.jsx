import { Activity, ChevronLeft } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AppLink, NavigationProvider } from "./lib/router.jsx";
import HomePage from "./pages/HomePage.jsx";
import StockDetailPage from "./pages/StockDetailPage.jsx";

export default function App() {
  const [path, setPath] = useState(window.location.pathname);
  const navigate = useCallback((nextPath) => {
    if (nextPath === window.location.pathname) return;
    window.history.pushState({}, "", nextPath);
    setPath(nextPath);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  useEffect(() => {
    const handlePopState = () => setPath(window.location.pathname);
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const route = useMemo(() => {
    const detailMatch = path.match(/^\/stocks\/(\d{4})\/?$/);
    if (detailMatch) {
      return { name: "detail", stockCode: detailMatch[1] };
    }
    return { name: "home" };
  }, [path]);

  return (
    <NavigationProvider navigate={navigate}>
      <div className="app-shell">
        <AppHeader isDetail={route.name === "detail"} />
        <main className="app-main">
          {route.name === "detail" ? (
            <StockDetailPage stockCode={route.stockCode} />
          ) : (
            <HomePage />
          )}
        </main>
      </div>
    </NavigationProvider>
  );
}

function AppHeader({ isDetail }) {
  return (
    <header className="app-header">
      <div className="header-inner">
        {isDetail ? (
          <AppLink className="icon-button" to="/" aria-label="返回首頁">
            <ChevronLeft size={22} />
          </AppLink>
        ) : (
          <div className="brand-mark" aria-hidden="true">
            <Activity size={20} />
          </div>
        )}
        <AppLink className="brand-copy" to="/">
          <span>籌碼週報</span>
          <small>TDCC 股權分散</small>
        </AppLink>
        <div className="live-pill" aria-label="Supabase 資料來源">
          <span className="status-dot" aria-hidden="true" />
          Supabase
        </div>
      </div>
    </header>
  );
}
