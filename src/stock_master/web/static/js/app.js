(function () {
  "use strict";

  const numberFormat = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 2 });
  const integerFormat = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 });
  const charts = Object.create(null);

  document.addEventListener("DOMContentLoaded", function () {
    initSearch();
    initHomeSync();
    initDetailPage();
  });

  function initSearch() {
    const input = document.querySelector("[data-stock-search]");
    const suggestions = document.querySelector("[data-search-suggestions]");
    if (!input || !suggestions) return;
    let timer;
    input.addEventListener("input", function () {
      window.clearTimeout(timer);
      const query = input.value.trim();
      if (!query) {
        suggestions.hidden = true;
        suggestions.innerHTML = "";
        return;
      }
      timer = window.setTimeout(async function () {
        try {
          const data = await fetchJSON("/api/v1/stocks/search?q=" + encodeURIComponent(query) + "&limit=6");
          suggestions.innerHTML = data.items.length
            ? data.items.map(function (stock) {
                return '<a class="suggestion" href="/stocks/' + encodeURIComponent(stock.stock_code) + '"><span><strong>' + escapeHtml(stock.stock_code) + '</strong>　' + escapeHtml(stock.stock_name) + '</span><small>' + escapeHtml(stock.market) + '　↗</small></a>';
              }).join("")
            : '<div class="suggestion"><span class="muted">找不到相符股票</span></div>';
          suggestions.hidden = false;
        } catch (_error) {
          suggestions.hidden = true;
        }
      }, 220);
    });
    document.addEventListener("click", function (event) {
      if (!event.target.closest(".search-box")) suggestions.hidden = true;
    });
  }

  function initHomeSync() {
    const button = document.querySelector("[data-sync-all]");
    const status = document.querySelector("[data-sync-status]");
    if (!button || !status) return;

    button.addEventListener("click", async function () {
      button.disabled = true;
      button.textContent = "同步工作啟動中…";
      try {
        const job = await fetchJSON("/api/v1/sync/all", { method: "POST" });
        renderSyncStatus(job);
        pollSyncJob(job.job_id, button, status);
      } catch (error) {
        renderSyncFailure(status, "無法啟動同步", error);
        button.disabled = false;
        button.textContent = "↻　同步所有資料";
      }
    });
  }

  async function pollSyncJob(jobId, button, status) {
    let delay = 700;
    while (true) {
      await wait(delay);
      try {
        const job = await fetchJSON("/api/v1/sync/all/" + encodeURIComponent(jobId));
        renderSyncStatus(job);
        if (job.status === "completed" || job.status === "failed") {
          button.disabled = false;
          button.textContent = "↻　同步所有資料";
          if (job.status === "completed") {
            window.setTimeout(function () { window.location.reload(); }, 800);
          }
          return;
        }
      } catch (error) {
        renderSyncFailure(status, "同步狀態讀取失敗", error);
        button.disabled = false;
        button.textContent = "↻　同步所有資料";
        return;
      }
      delay = Math.min(Math.round(delay * 1.35), 5000);
    }
  }

  function renderSyncStatus(data) {
    const status = document.querySelector("[data-sync-status]");
    if (!status) return;
    const message = status.querySelector("[data-sync-message]");
    const detail = status.querySelector("[data-sync-detail]");
    const steps = status.querySelector("[data-sync-steps]");
    status.hidden = false;
    status.dataset.state = data.status || "queued";
    if (message) message.textContent = data.message || "同步工作進行中";
    if (detail) {
      const result = data.result || {};
      detail.textContent = data.status === "completed"
        ? "同步日期：" + (result.sync_date || result.end_date || "—")
        : data.error || "只同步各資料源最新資料。";
    }
    if (steps) {
      steps.innerHTML = (data.steps || []).map(function (step) {
        const state = step.status || "pending";
        const mark = state === "completed" ? "✓" : state === "failed" ? "!" : state === "running" ? "…" : state === "skipped" ? "—" : "○";
        return '<li class="sync-step ' + escapeHtml(state) + '"><span class="sync-step-mark">' + mark + '</span><span>' + escapeHtml(step.label) + '</span></li>';
      }).join("");
    }
  }

  function renderSyncFailure(status, message, error) {
    status.hidden = false;
    status.dataset.state = "failed";
    const messageElement = status.querySelector("[data-sync-message]");
    const detail = status.querySelector("[data-sync-detail]");
    if (messageElement) messageElement.textContent = message;
    if (detail) detail.textContent = messageFrom(error);
  }

  function wait(milliseconds) {
    return new Promise(function (resolve) { window.setTimeout(resolve, milliseconds); });
  }

  async function initDetailPage() {
    const page = document.querySelector("[data-stock-code]");
    if (!page) return;
    const code = page.dataset.stockCode;
    const base = "/api/v1/stocks/" + encodeURIComponent(code);
    const overviewResult = await Promise.allSettled([fetchJSON(base + "/overview")]);
    const overview = fulfilled(overviewResult[0]) ? overviewResult[0].value : null;
    if (overview) renderOverview(overview);
    else renderOverviewError(overviewResult[0].reason);

    async function loadHistory(days) {
      const query = historyQuery(days);
      const tdccQuery = days === "default" ? "?limit=1000" : query;
      const results = await Promise.allSettled([
        fetchJSON(base + "/prices" + query),
        fetchJSON(base + "/margin" + query),
        fetchJSON(base + "/margin-estimates" + query),
        fetchJSON(base + "/tdcc" + tdccQuery)
      ]);
      fulfilled(results[0]) ? renderPrices(results[0].value) : renderSectionError("prices", results[0].reason);
      fulfilled(results[1]) ? renderMargin(results[1].value) : renderSectionError("margin", results[1].reason);
      fulfilled(results[2]) ? renderEstimates(results[2].value) : renderSectionError("estimates", results[2].reason);
      fulfilled(results[3]) ? renderTdcc(results[3].value, fulfilled(results[0]) ? results[0].value : null) : renderSectionError("tdcc", results[3].reason);
    }

    await loadHistory("default");
    document.querySelectorAll("[data-range-days]").forEach(function (button) {
      button.addEventListener("click", async function () {
        document.querySelectorAll("[data-range-days]").forEach(function (item) { item.classList.remove("active"); });
        button.classList.add("active");
        await loadHistory(button.dataset.rangeDays === "all" ? "all" : Number(button.dataset.rangeDays));
      });
    });
  }

  function historyQuery(days) {
    if (days === "default") return "?limit=90";
    if (days === "all") return "?limit=1000";
    const end = new Date();
    const start = new Date(end.getTime());
    start.setDate(start.getDate() - Number(days));
    return "?from=" + localISODate(start) + "&to=" + localISODate(end) + "&limit=1000";
  }

  function localISODate(value) {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, "0");
    const day = String(value.getDate()).padStart(2, "0");
    return year + "-" + month + "-" + day;
  }

  function renderOverview(data) {
    const stock = data.stock || {};
    const price = data.price || {};
    const margin = data.margin || {};
    const estimate = data.margin_estimate || {};
    const tdcc = data.tdcc || {};
    const container = document.querySelector("[data-overview-metrics]");
    if (!container) return;
    container.innerHTML = [
      metric("最新收盤價", price.close, price.trade_date, "price"),
      metric("市場成交均價", price.market_average_price, price.trade_date, "price"),
      metric("融資餘額", margin.margin_balance, margin.trade_date, "volume"),
      metric("維持率估算", estimate.estimated_maintenance_ratio, estimate.trade_date, "ratio", true),
      metric("TDCC 最新日期", tdcc.latest_date, tdcc.levels ? tdcc.levels + " 個持股級距" : "", "text")
    ].join("");
    const date = price.trade_date || margin.trade_date || estimate.trade_date;
    const dateElement = document.querySelector("[data-overview-date]");
    if (dateElement) dateElement.textContent = date ? "資料截至 " + date : "尚無最新資料";
    document.title = (stock.stock_code || "股票") + " " + (stock.stock_name || "") + "｜Taiwan Stock Data";
  }

  function renderOverviewError(error) {
    const container = document.querySelector("[data-overview-metrics]");
    if (container) container.innerHTML = emptyHtml(messageFrom(error));
  }

  function renderPrices(data) {
    const items = data.items || [];
    if (!items.length) {
      renderEmpty("[data-price-table]", "尚無每日成交資料");
      setText("[data-price-summary]", "目前沒有可繪製的行情");
      return;
    }
    const latest = items[items.length - 1];
    setText("[data-price-summary]", "最新收盤 " + displayNumber(latest.close, "price") + " · " + latest.trade_date);
    makeLineChart("price-chart", items.map(function (item) { return item.trade_date; }), [
      { label: "收盤價", data: items.map(function (item) { return item.close; }), borderColor: "#087f78", backgroundColor: "rgba(8,127,120,.12)" },
      { label: "市場成交均價", data: items.map(function (item) { return item.market_average_price; }), borderColor: "#e36e56", backgroundColor: "rgba(227,110,86,.05)" }
    ]);
    document.querySelector("[data-price-facts]").innerHTML = [
      fact("日期", latest.trade_date, "text"),
      fact("開盤", latest.open, "price"),
      fact("最高", latest.high, "price"),
      fact("最低", latest.low, "price"),
      fact("收盤", latest.close, "price"),
      fact("成交股數", latest.trade_volume, "volume"),
      fact("成交金額", latest.trade_value, "volume"),
      fact("市場成交均價", latest.market_average_price, "price")
    ].join("");
    const rows = items.slice(-8).reverse().map(function (item) { return "<tr><td>" + escapeHtml(item.trade_date) + "</td><td>" + displayNumber(item.close, "price") + "</td><td>" + displayNumber(item.market_average_price, "price") + "</td><td>" + displayNumber(item.trade_volume, "volume") + "</td></tr>"; }).join("");
    document.querySelector("[data-price-table]").innerHTML = tableHtml(["日期", "收盤價", "市場成交均價", "成交股數"], rows);
  }

  function renderMargin(data) {
    const items = data.items || [];
    if (!items.length) {
      renderEmpty("[data-margin-table]", "尚無融資融券資料");
      setText("[data-margin-summary]", "目前沒有可繪製的融資資料");
      return;
    }
    const latest = items[items.length - 1];
    const utilizationSummary = latest.margin_utilization === null || latest.margin_utilization === undefined
      ? ""
      : " · 使用率 " + displayNumber(latest.margin_utilization, "ratio");
    setText("[data-margin-summary]", "融資 " + displayNumber(latest.margin_balance, "volume") + " · 融券 " + displayNumber(latest.short_balance, "volume") + utilizationSummary + " · " + latest.trade_date);
    document.querySelector("[data-margin-facts]").innerHTML = [
      fact("前日融資餘額", latest.margin_previous_balance, "volume"),
      fact("融資買進", latest.margin_buy, "volume"),
      fact("融資賣出", latest.margin_sell, "volume"),
      fact("現金償還", latest.margin_cash_redemption, "volume"),
      fact("融資餘額", latest.margin_balance, "volume"),
      fact("融資限額", latest.margin_limit, "volume"),
      fact("融資使用率", latest.margin_utilization, "ratio"),
      fact("融券餘額", latest.short_balance, "volume"),
      fact("資券相抵", latest.offsetting_volume, "volume")
    ].join("");
    makeLineChart("margin-chart", items.map(function (item) { return item.trade_date; }), [
      { label: "融資餘額", data: items.map(function (item) { return item.margin_balance; }), borderColor: "#e36e56", backgroundColor: "rgba(227,110,86,.10)" },
      { label: "融券餘額", data: items.map(function (item) { return item.short_balance; }), borderColor: "#087f78", backgroundColor: "rgba(8,127,120,.08)" }
    ]);
    const rows = items.slice(-8).reverse().map(function (item) { return "<tr><td>" + escapeHtml(item.trade_date) + "</td><td>" + displayNumber(item.margin_balance, "volume") + "</td><td>" + displayNumber(item.short_balance, "volume") + "</td><td>" + displayNumber(item.offsetting_volume, "volume") + "</td></tr>"; }).join("");
    document.querySelector("[data-margin-table]").innerHTML = tableHtml(["日期", "融資餘額", "融券餘額", "資券相抵"], rows);
  }

  function renderEstimates(data) {
    const items = data.items || [];
    const cards = document.querySelector("[data-estimate-cards]");
    if (!cards || !items.length) {
      if (cards) cards.innerHTML = emptyHtml("尚無融資維持率估算資料");
      return;
    }
    const latest = items[items.length - 1];
    cards.innerHTML = [
      estimateCard("估算融資平均成本", displayNumber(latest.estimated_margin_avg_cost, "price"), latest.trade_date, ""),
      estimateCard("融資成數", displayNumber(Number(latest.margin_financing_ratio) * 100, "ratio"), latest.trade_date, ""),
      estimateCard("估算每股融資金額", displayNumber(latest.estimated_financing_per_share, "price"), latest.trade_date, ""),
      estimateCard("估算維持率", displayNumber(latest.estimated_maintenance_ratio, "ratio"), latest.trade_date, riskClass(latest.estimated_maintenance_ratio)),
      estimateCard("估算 130% 壓力價", displayNumber(latest.estimated_130_price, "price"), latest.trade_date, ""),
      estimateCard("模型版本", latest.model_version, latest.trade_date, "", "text")
    ].join("");
  }

  function renderTdcc(data, priceData) {
    const items = data.items || [];
    if (!items.length) {
      renderEmpty("[data-tdcc-table]", "尚無 TDCC 股權分散資料");
      setText("[data-tdcc-date]", "尚無資料");
      return;
    }
    const dates = Array.from(new Set(items.map(function (item) { return item.data_date; }))).sort();
    const summaries = dates.map(function (dataDate) {
      return items.filter(function (item) { return item.data_date === dataDate; }).reduce(function (summary, item) {
        const level = Number.parseInt(item.holding_level, 10);
        const ratio = Number(item.holding_ratio) || 0;
        if (level >= 14) summary.high += ratio;
        if (level <= 6) summary.low += ratio;
        return summary;
      }, { high: 0, low: 0 });
    });
    const closeByDate = new Map(((priceData && priceData.items) || []).map(function (item) { return [item.trade_date, item.close]; }));
    const closePrices = dates.map(function (dataDate) { return closeByDate.has(dataDate) ? closeByDate.get(dataDate) : null; });
    const makeCloseDataset = function () { return { type: "line", label: "當日收盤價", unit: "price", data: closePrices, borderColor: "#172b3a", backgroundColor: "rgba(23,43,58,.08)", borderWidth: 3, pointRadius: 4, pointHoverRadius: 6, pointBackgroundColor: "#ffffff", pointBorderColor: "#172b3a", pointBorderWidth: 2, tension: .24, fill: false, yAxisID: "price", order: 1 }; };
    makeMixedBarLineChart("tdcc-high-chart", dates, { label: "14 級距以上持股比例總和", unit: "ratio", data: summaries.map(function (item) { return item.high; }), backgroundColor: "rgba(227,110,86,.52)", borderColor: "#e36e56", borderWidth: 1, borderRadius: 5, order: 2 }, makeCloseDataset());
    makeMixedBarLineChart("tdcc-low-chart", dates, { label: "6 級距以下持股比例總和", unit: "ratio", data: summaries.map(function (item) { return item.low; }), backgroundColor: "rgba(8,127,120,.52)", borderColor: "#087f78", borderWidth: 1, borderRadius: 5, order: 2 }, makeCloseDataset());
    const latestDate = dates[dates.length - 1];
    const latest = items.filter(function (item) { return item.data_date === latestDate; }).sort(function (left, right) { return Number.parseInt(left.holding_level, 10) - Number.parseInt(right.holding_level, 10); });
    setText("[data-tdcc-date]", latestDate + " · " + latest.length + " 個級距");
    const rows = latest.map(function (item) { return "<tr><td>" + escapeHtml(item.holding_level) + "</td><td>" + displayNumber(item.shareholder_count, "volume") + "</td><td>" + displayNumber(item.share_count, "volume") + "</td><td>" + displayNumber(item.holding_ratio, "ratio") + "</td></tr>"; }).join("");
    document.querySelector("[data-tdcc-table]").innerHTML = tableHtml(["持股級距", "股東人數", "持股數", "持股比例"], rows);
  }

  function renderSectionError(section, error) {
    const target = document.querySelector('[data-section="' + section + '"]');
    if (!target) return;
    const content = target.querySelector(".chart-card, [data-estimate-cards], [data-tdcc-charts]");
    if (content) content.outerHTML = emptyHtml(messageFrom(error));
  }

  function metric(label, value, note, type, estimate) {
    const extra = estimate ? '<span class="estimate-label">估算</span>' : "";
    return '<article class="metric-card"><span class="metric-label">' + escapeHtml(label) + extra + '</span><strong>' + displayNumber(value, type) + '</strong><span class="metric-note">' + escapeHtml(note || "尚無資料") + '</span></article>';
  }

  function estimateCard(label, value, date, risk, type) {
    return '<article class="estimate-card ' + risk + '"><span class="estimate-kicker">估算 · ' + escapeHtml(label) + '</span><strong class="' + (type === "text" ? "estimate-version" : "") + '">' + (type === "text" ? escapeHtml(value) : value) + '</strong><small>截至 ' + escapeHtml(date || "—") + '</small></article>';
  }

  function fact(label, value, type) {
    return '<div class="fact-item"><span>' + escapeHtml(label) + '</span><strong>' + displayNumber(value, type) + '</strong></div>';
  }

  function makeLineChart(id, labels, datasets) {
    if (!window.Chart) return;
    const canvas = document.getElementById(id);
    if (!canvas) return;
    destroyChart(id);
    charts[id] = new window.Chart(canvas, { type: "line", data: { labels: labels, datasets: datasets.map(function (dataset) { return Object.assign({ fill: true, tension: .28, pointRadius: 0, borderWidth: 2 }, dataset); }) }, options: chartOptions() });
  }

  function makeBarChart(id, labels, datasets) {
    if (!window.Chart) return;
    const canvas = document.getElementById(id);
    if (!canvas) return;
    destroyChart(id);
    charts[id] = new window.Chart(canvas, { type: "bar", data: { labels: labels, datasets: datasets }, options: chartOptions() });
  }

  function makeMixedBarLineChart(id, labels, barDataset, lineDataset) {
    if (!window.Chart) return;
    const canvas = document.getElementById(id);
    if (!canvas) return;
    destroyChart(id);
    charts[id] = new window.Chart(canvas, {
      type: "bar",
      data: { labels: labels, datasets: [Object.assign({ type: "bar", order: 2 }, barDataset), Object.assign({ type: "line", order: 1 }, lineDataset)] },
      options: mixedChartOptions()
    });
  }

  function destroyChart(id) {
    if (charts[id]) {
      charts[id].destroy();
      delete charts[id];
    }
  }

  function chartOptions() {
    return { responsive: true, maintainAspectRatio: false, interaction: { intersect: false, mode: "index" }, plugins: { legend: { display: true, labels: { usePointStyle: true, boxWidth: 7, color: "#6f7f87", font: { size: 11 } } }, tooltip: { callbacks: { label: function (context) { return " " + context.dataset.label + ": " + displayNumber(context.raw, context.dataset.unit || "price"); } } } }, scales: { x: { grid: { display: false }, ticks: { color: "#8b999d", maxTicksLimit: 7, font: { size: 10 } } }, y: { grid: { color: "#edf1f1" }, ticks: { color: "#8b999d", font: { size: 10 } } } } };
  }

  function mixedChartOptions() {
    const options = chartOptions();
    options.scales.y.title = { display: true, text: "持股比例 (%)", color: "#6f7f87", font: { size: 10 } };
    options.scales.price = { position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "收盤價", color: "#6f7f87", font: { size: 10 } }, ticks: { color: "#172b3a", font: { size: 10 } } };
    return options;
  }

  function tableHtml(headers, rows) { return '<table class="data-table"><thead><tr>' + headers.map(function (header) { return "<th>" + header + "</th>"; }).join("") + "</tr></thead><tbody>" + rows + "</tbody></table>"; }
  function renderEmpty(selector, message) { const target = document.querySelector(selector); if (target) target.innerHTML = emptyHtml(message); }
  function emptyHtml(message) { return '<div class="empty-state">' + escapeHtml(message) + '</div>'; }
  function setText(selector, value) { const element = document.querySelector(selector); if (element) element.textContent = value; }
  function fulfilled(result) { return result && result.status === "fulfilled"; }
  function riskClass(value) { return value >= 166 ? "risk-normal" : value >= 130 ? "risk-caution" : "risk-high"; }
  function displayNumber(value, type) { if (value === null || value === undefined || value === "") return "—"; if (type === "text") return escapeHtml(String(value)); if (type === "ratio") return numberFormat.format(Number(value)) + "%"; if (type === "volume") return integerFormat.format(Number(value)); return numberFormat.format(Number(value)); }
  function messageFrom(error) { return error && error.message ? error.message : "資料暫時無法取得"; }
  async function fetchJSON(url, options) { const response = await fetch(url, Object.assign({ headers: { Accept: "application/json" } }, options || {})); const data = await response.json(); if (!response.ok) throw data.error || { message: "資料暫時無法取得" }; return data; }
  function escapeHtml(value) { return String(value).replace(/[&<>"']/g, function (character) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[character]; }); }
})();
