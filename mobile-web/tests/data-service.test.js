import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  clampInteger,
  createStockDataService,
  normalizeLargeHolderLevels,
} from "../server/data-service.js";

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
            latest_price_date: "2026-08-27",
            latest_close_price: "1160.00",
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
    assert.equal(rows[0].latest_price_date, "2026-08-27");
    assert.equal(rows[0].latest_close_price, 1160);
  });

  it("normalizes configurable large-holder level bounds", () => {
    assert.deepEqual(normalizeLargeHolderLevels(14, 15), {
      minLevel: 14,
      maxLevel: 15,
    });
    assert.deepEqual(normalizeLargeHolderLevels(15, 14), {
      minLevel: 14,
      maxLevel: 15,
    });
  });

  it("normalizes latest close prices for increasing-holder cards", async () => {
    const supabase = {
      rpc: async () => ({
        data: [{
          stock_code: "4931",
          stock_name: "新盛力",
          market: "TPEX",
          start_date: "2026-08-07",
          latest_date: "2026-08-21",
          latest_price_date: "2026-08-27",
          latest_close_price: "58.40",
          start_large_ratio: "13.48",
          latest_large_ratio: "24.71",
          increase_percentage_points: "11.23",
          streak_weeks: "3",
        }],
        error: null,
      }),
    };

    const rows = await createStockDataService(supabase).getIncreasingStocks(3, 50);

    assert.equal(rows[0].latest_price_date, "2026-08-27");
    assert.equal(rows[0].latest_close_price, 58.4);
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
            latest_price_date: "2026-08-27",
            latest_close_price: "1160.00",
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
    assert.equal(rows[0].latest_price_date, "2026-08-27");
    assert.equal(rows[0].latest_close_price, 1160);
  });

  it("normalizes daily price history values", async () => {
    let receivedArguments;
    const supabase = {
      rpc: async (name, args) => {
        receivedArguments = [name, args];
        return {
          data: [
            {
              trade_date: "2026-08-27",
              market: "TWSE",
              trade_volume: "1234000",
              trade_value: "1239876543",
              open_price: "100.50",
              high_price: "103.00",
              low_price: "99.80",
              close_price: "102.50",
              market_average_price: "101.234567",
            },
          ],
          error: null,
        };
      },
    };

    const rows = await createStockDataService(supabase).getPriceHistory("2330", 52);

    assert.deepEqual(receivedArguments, [
      "get_stock_price_history",
      { p_stock_code: "2330", p_weeks: 52 },
    ]);
    assert.equal(rows[0].trade_volume, 1234000);
    assert.equal(rows[0].open_price, 100.5);
    assert.equal(rows[0].close_price, 102.5);
    assert.equal(rows[0].market_average_price, 101.234567);
  });

  it("includes daily prices and insider transactions in stock detail", async () => {
    const rpcCalls = [];
    const supabase = {
      from: (table) => {
        if (table === "stocks") {
          return {
            select() {
              return this;
            },
            eq() {
              return this;
            },
            async maybeSingle() {
              return {
                data: { stock_code: "2330", stock_name: "台積電", market: "TWSE" },
                error: null,
              };
            },
          };
        }
        return {
          select() {
            return this;
          },
          eq() {
            return this;
          },
          order() {
            return this;
          },
          async limit() {
            return {
              data: [{
                report_date: "2026-08-20",
                stock_code: "2330",
                market: "TWSE",
                report_type: "planned_transfer",
                transaction_type: "transfer",
                insider_name: "王大明",
                insider_role: "董事",
                shares_changed: "250000",
                transfer_method: "鉅額交易",
                transferee: null,
                current_shares: "1000000",
                planned_shares: "250000",
                after_shares: "750000",
                effective_period: "2026/08/21~2026/09/20",
                reason: "財務規劃",
              }, {
                report_date: "2026-08-19",
                stock_code: "2330",
                market: "TWSE",
                report_type: "planned_transfer",
                transaction_type: "transfer",
                insider_name: "無張數申報",
                insider_role: "大股東",
                shares_changed: "0",
                planned_shares: "0",
              }, {
                report_date: "2026-08-18",
                stock_code: "2330",
                market: "TWSE",
                report_type: "untransferred",
                transaction_type: "untransferred",
                insider_name: "無張數未轉讓",
                insider_role: "大股東",
                shares_changed: null,
                planned_shares: null,
              }],
              error: null,
            };
          },
        };
      },
      rpc: async (name, args) => {
        rpcCalls.push([name, args]);
        return {
          data: name === "get_tdcc_stock_detail"
          ? [
            {
              data_date: "2026-08-21",
              large_holder_count: "10",
              large_share_count: "100000",
              large_ratio: "80",
              retail_holder_count: "20",
              retail_share_count: "20000",
              retail_ratio: "2",
            },
            {
              data_date: "2026-08-14",
              large_holder_count: "10",
              large_share_count: "90000",
              large_ratio: "72",
              retail_holder_count: "20",
              retail_share_count: "24000",
              retail_ratio: "2.4",
            },
            {
              data_date: "2026-01-02",
              large_holder_count: "10",
              large_share_count: "80000",
              large_ratio: "65",
              retail_holder_count: "20",
              retail_share_count: "18000",
              retail_ratio: "1.8",
            },
          ]
          : [{
              trade_date: "2026-08-27",
              market: "TWSE",
              trade_volume: "1000000",
              trade_value: "100000000",
              open_price: "100",
              high_price: "105",
              low_price: "99",
              close_price: "103",
              market_average_price: "100",
            }],
          error: null,
        };
      },
    };

    const detail = await createStockDataService(supabase).getStockDetail("2330", 2);

    assert.equal(detail.latest.large_ratio, 80);
    assert.equal(detail.history.length, 2);
    assert.equal(detail.annual_baselines["2026"].large_ratio, 65);
    assert.equal(detail.annual_baselines["2026"].retail_ratio, 1.8);
    assert.equal(detail.prices[0].close_price, 103);
    assert.equal(detail.prices[0].trade_volume, 1000000);
    assert.equal(detail.insider_transactions.length, 1);
    assert.equal(detail.insider_transactions[0].shares_changed, 250000);
    assert.equal(detail.insider_transactions[0].planned_shares, 250000);
    assert.equal(detail.insider_transactions[0].reason, "財務規劃");
    assert.deepEqual(rpcCalls[0], [
      "get_tdcc_stock_detail",
      {
        p_stock_code: "2330",
        p_weeks: 104,
        p_large_level_min: 15,
        p_large_level_max: 15,
      },
    ]);
  });
});
