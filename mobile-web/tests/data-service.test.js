import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { clampInteger, createStockDataService } from "../server/data-service.js";

describe("stock data service", () => {
  it("clamps query limits", () => {
    assert.equal(clampInteger("0", 1, 20, 8), 1);
    assert.equal(clampInteger("999", 1, 20, 8), 20);
    assert.equal(clampInteger("bad", 1, 20, 8), 8);
  });

  it("converts Supabase numeric values to JSON numbers", async () => {
    const supabase = {
      rpc: async () => ({
        data: [
          {
            stock_code: "2330",
            stock_name: "台積電",
            market: "TWSE",
            data_date: "2026-08-21",
            large_holder_count: "1479",
            large_share_count: "21968307511",
            large_ratio: "84.7100",
            retail_holder_count: "3051385",
            retail_share_count: "2085184706",
            retail_ratio: "8.0000",
          },
        ],
        error: null,
      }),
    };

    const rows = await createStockDataService(supabase).searchStocks("2330");

    assert.equal(rows[0].large_ratio, 84.71);
    assert.equal(rows[0].large_share_count, 21968307511);
    assert.equal(rows[0].retail_holder_count, 3051385);
  });
});
