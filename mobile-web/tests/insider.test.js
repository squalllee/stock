import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  filterPlannedTransfers,
  filterToLatestHoldingMonth,
} from "../src/lib/insider.js";

describe("insider display helpers", () => {
  it("keeps only latest-month MOPS rows with a buy or sell", () => {
    const rows = filterToLatestHoldingMonth([
      {
        report_type: "after_report",
        transaction_type: "buy",
        report_date: "2026-06-30",
        insider_name: "甲",
      },
      {
        report_type: "after_report",
        transaction_type: "other",
        report_date: "2026-07-31",
        insider_name: "甲",
      },
      {
        report_type: "after_report",
        transaction_type: "sell",
        report_date: "2026-07-31",
        insider_name: "乙",
      },
      { report_type: "planned_transfer", report_date: "2026-06-20", insider_name: "丙" },
    ]);

    assert.deepEqual(rows, [
      {
        report_type: "after_report",
        transaction_type: "sell",
        report_date: "2026-07-31",
        insider_name: "乙",
      },
    ]);
  });

  it("uses the latest month that has a trade when newer snapshots are unchanged", () => {
    const rows = filterToLatestHoldingMonth([
      { report_type: "after_report", transaction_type: "other", report_date: "2026-07-31" },
      { report_type: "after_report", transaction_type: "buy", report_date: "2026-06-30" },
    ]);

    assert.deepEqual(rows, [
      { report_type: "after_report", transaction_type: "buy", report_date: "2026-06-30" },
    ]);
  });

  it("returns no cards when there are no buy/sell rows", () => {
    const rows = [
      { report_type: "planned_transfer", report_date: "2026-08-20" },
      { report_type: "untransferred", report_date: "2026-08-21" },
      { report_type: "after_report", transaction_type: "other", report_date: "2026-08-31" },
    ];

    assert.deepEqual(filterToLatestHoldingMonth(rows), []);
  });

  it("keeps only positive planned-transfer disclosures for the planned card", () => {
    const planned = {
      report_type: "planned_transfer",
      transaction_type: "transfer",
      planned_shares: "16000000",
      insider_name: "華瑋投資股份有限公司",
    };
    assert.deepEqual(filterPlannedTransfers([
      planned,
      { ...planned, planned_shares: "0", insider_name: "無張數申報" },
      { report_type: "untransferred", planned_shares: "1000" },
      { report_type: "after_report", transaction_type: "sell", shares_changed: "1000" },
    ]), [planned]);
  });
});
