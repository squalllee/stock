import assert from "node:assert/strict";
import { describe, it } from "node:test";

import request from "supertest";

import { createApp } from "../server/app.js";

function makeService(overrides = {}) {
  return {
    searchStocks: async () => [],
    getStockDetail: async () => null,
    getIncreasingStocks: async () => [],
    ...overrides,
  };
}

describe("mobile web API", () => {
  it("searches by stock code or name", async () => {
    let receivedArguments;
    const dataService = makeService({
      searchStocks: async (...args) => {
        receivedArguments = args;
        return [{ stock_code: "2330", stock_name: "台積電", large_ratio: 84.71 }];
      },
    });
    const response = await request(createApp({ dataService }))
      .get("/api/stocks/search")
      .query({ q: "台積", limit: 5 });

    assert.equal(response.status, 200);
    assert.equal(response.body.items[0].stock_code, "2330");
    assert.deepEqual(receivedArguments, ["台積", "5"]);
  });

  it("rejects an invalid stock code", async () => {
    const response = await request(createApp({ dataService: makeService() }))
      .get("/api/stocks/23A0");

    assert.equal(response.status, 400);
    assert.match(response.body.error, /4 位數字/);
  });

  it("returns a stock detail with TDCC history", async () => {
    let receivedArguments;
    const dataService = makeService({
      getStockDetail: async (...args) => {
        receivedArguments = args;
        return {
          stock: { stock_code: "2330", stock_name: "台積電", market: "TWSE" },
          latest: { data_date: "2026-08-21", large_ratio: 84.71 },
          history: [],
        };
      },
    });
    const response = await request(createApp({ dataService }))
      .get("/api/stocks/2330")
      .query({ weeks: 26 });

    assert.equal(response.status, 200);
    assert.equal(response.body.stock.stock_name, "台積電");
    assert.deepEqual(receivedArguments, ["2330", "26"]);
  });

  it("validates screener week bounds", async () => {
    const response = await request(createApp({ dataService: makeService() }))
      .get("/api/screeners/increasing")
      .query({ weeks: 1 });

    assert.equal(response.status, 400);
  });

  it("returns increasing-holder candidates", async () => {
    let receivedArguments;
    const dataService = makeService({
      getIncreasingStocks: async (...args) => {
        receivedArguments = args;
        return [{ stock_code: "2454", streak_weeks: 3 }];
      },
    });
    const response = await request(createApp({ dataService }))
      .get("/api/screeners/increasing")
      .query({ weeks: 3, limit: 50 });

    assert.equal(response.status, 200);
    assert.equal(response.body.count, 1);
    assert.deepEqual(receivedArguments, [3, "50"]);
  });
});
