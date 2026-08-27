import express from "express";

const STOCK_CODE_PATTERN = /^\d{4}$/;

export function createApp({ dataService, staticDirectory = null }) {
  const app = express();
  app.disable("x-powered-by");
  app.use(express.json({ limit: "32kb" }));

  app.get("/api/health", (_request, response) => {
    response.json({ status: "ok" });
  });

  app.get("/api/stocks/search", async (request, response, next) => {
    try {
      const query = String(request.query.q || "").trim();
      if (!query) return response.json({ items: [] });
      if (query.length > 30) {
        return response.status(400).json({ error: "搜尋文字不可超過 30 個字元。" });
      }
      const items = await dataService.searchStocks(query, request.query.limit);
      return response.json({ items });
    } catch (error) {
      return next(error);
    }
  });

  app.get("/api/stocks/:stockCode", async (request, response, next) => {
    try {
      const stockCode = request.params.stockCode;
      if (!STOCK_CODE_PATTERN.test(stockCode)) {
        return response.status(400).json({ error: "股票代碼必須是 4 位數字。" });
      }
      const result = await dataService.getStockDetail(
        stockCode,
        request.query.weeks,
      );
      if (!result) {
        return response.status(404).json({ error: "找不到這支股票。" });
      }
      return response.json(result);
    } catch (error) {
      return next(error);
    }
  });

  app.get("/api/screeners/increasing", async (request, response, next) => {
    try {
      const weeks = Number.parseInt(request.query.weeks, 10);
      if (!Number.isFinite(weeks) || weeks < 2 || weeks > 12) {
        return response
          .status(400)
          .json({ error: "連續週數必須介於 2 到 12 週。" });
      }
      const items = await dataService.getIncreasingStocks(
        weeks,
        request.query.limit,
      );
      return response.json({ weeks, count: items.length, items });
    } catch (error) {
      return next(error);
    }
  });

  if (staticDirectory) {
    app.use(express.static(staticDirectory, { maxAge: "1h" }));
    app.get("*", (_request, response) => {
      response.sendFile("index.html", { root: staticDirectory });
    });
  }

  app.use((error, _request, response, _next) => {
    console.error("Mobile web request failed", error);
    response.status(502).json({
      error: "目前無法讀取 Supabase 資料，請稍後再試。",
    });
  });

  return app;
}
