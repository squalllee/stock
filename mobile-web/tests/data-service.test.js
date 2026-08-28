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

  it("normalizes holder direction turns", async () => {
    const supabase = {
      rpc: async () => ({
        data: [
          {
            turn_type: "sell_to_buy",
            stock_code: "2330",
            stock_name: "台積電",
            market: "TWSE",
            oldest_date: "2026-08-07",
            previous_date: "2026-08-14",
            latest_date: "2026-08-21",
            oldest_large_ratio: "84.20",
            previous_large_ratio: "83.90",
            latest_large_ratio: "84.71",
            previous_change_percentage_points: "-0.30",
            latest_change_percentage_points: "0.81",
            large_holder_count: "1479",
            large_share_count: "21968307511",
            retail_holder_count: "3051385",
            retail_share_count: "2085184706",
            retail_ratio: "8.0000",
          },
        ],
        error: null,
      }),
    };

    const rows = await createStockDataService(supabase).getHolderTurns(50);

    assert.equal(rows[0].turn_type, "sell_to_buy");
    assert.equal(rows[0].previous_change_percentage_points, -0.3);
    assert.equal(rows[0].latest_change_percentage_points, 0.81);
    assert.equal(rows[0].latest_large_ratio, 84.71);
  });
});
