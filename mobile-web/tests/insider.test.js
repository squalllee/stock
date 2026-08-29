import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { filterToLatestHoldingMonth } from "../src/lib/insider.js";

describe("insider display helpers", () => {
  it("keeps only the latest monthly holding snapshot", () => {
    const rows = filterToLatestHoldingMonth([
      { report_type: "after_report", report_date: "2026-06-30", insider_name: "甲" },
      { report_type: "after_report", report_date: "2026-07-31", insider_name: "甲" },
      { report_type: "after_report", report_date: "2026-07-31", insider_name: "乙" },
      { report_type: "planned_transfer", report_date: "2026-06-20", insider_name: "丙" },
    ]);

    assert.deepEqual(rows, [
      { report_type: "after_report", report_date: "2026-07-31", insider_name: "甲" },
      { report_type: "after_report", report_date: "2026-07-31", insider_name: "乙" },
      { report_type: "planned_transfer", report_date: "2026-06-20", insider_name: "丙" },
    ]);
  });

  it("leaves transfer-only data untouched when no monthly holdings exist", () => {
    const rows = [
      { report_type: "planned_transfer", report_date: "2026-08-20" },
      { report_type: "untransferred", report_date: "2026-08-21" },
    ];

    assert.equal(filterToLatestHoldingMonth(rows), rows);
  });
});
