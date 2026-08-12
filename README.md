# Taiwan Stock Master

Taiwan Stock Master 會從臺灣證券交易所（TWSE）與證券櫃檯買賣中心（TPEx）的官方 OpenAPI，同步目前有效的上市／上櫃普通股票到 SQLite。

資料表只包含：

| 欄位 | 說明 |
| --- | --- |
| stock_code | 四位數股票代碼 |
| stock_name | 股票簡稱 |
| market | TWSE 或 TPEX |

ETF、ETN、權證、債券、基金、REIT、存託憑證、興櫃與戰略新板不會寫入 stocks。

TDCC 股權分散資料會以 `stocks` table 作為唯一股票 universe；TDCC 回傳的
ETF、其他有價證券與「合計」列都不會寫入 `tdcc_distributions`。

## 安裝

需要 Python 3.11 以上。執行：

    python -m pip install -e ".[dev]"

同步核心使用 Python 標準函式庫；Web 查詢平台額外使用 FastAPI、Uvicorn 與
Jinja2，測試工具使用 pytest 與 httpx。

## 同步

    python -m stock_master sync

資料庫預設建立於 data/stocks.db。也可以指定資料庫：

    python -m stock_master sync --db /tmp/taiwan-stocks.db

同步會先完整取得兩個官方來源，再以單一 SQLite transaction 執行 UPSERT。任何一個來源 HTTP 失敗、回傳空陣列、schema 無法辨識，或資料量低於 sanity threshold，都會停止寫入；既有資料不會被刪除。V1 也不會因上游清單消失而自動刪除股票。

在 stock master 建立後同步 TDCC 集保戶股權分散表：

    python -m stock_master tdcc-sync

也可以指定資料庫與 TDCC endpoint：

    python -m stock_master tdcc-sync \
      --db /tmp/taiwan-stocks.db \
      --tdcc-url https://openapi.tdcc.com.tw/v1/opendata/1-5

TDCC 資料會保留歷史日期，唯一鍵為 `data_date + stock_code + holding_level`，
並以單一 transaction 執行 UPSERT。TDCC API 失敗、回傳空陣列或 schema 改變時會
停止同步，不會刪除既有歷史資料。

同步最近 30 個日曆日內可取得的每週 TDCC 歷史資料：

    python -m stock_master tdcc-month-sync

`tdcc-history-sync` 是同一功能的別名。歷史頁只能逐檔查詢，因此預設使用 2 個
獨立 session 並在每次查詢間等待 0.2 秒；可依網路或 TDCC 限流狀況調整：

    python -m stock_master tdcc-month-sync \
      --db /tmp/taiwan-stocks.db \
      --days 30 \
      --workers 2 \
      --request-delay 0.2

此功能會依 TDCC 歷史頁實際提供的每週日期選項決定資料日期，不會自行猜測週末或
假日。查詢頁回傳的「差異數調整」與「合計」列不會寫入資料庫；既有資料不會被刪除。

## 融資融券歷史資料

在 stock master 建立後同步最新交易日的 TWSE／TPEx 融資融券 raw data：

    python -m stock_master margin-sync

同步指定交易日：

    python -m stock_master margin-sync \
      --db /tmp/taiwan-stocks.db \
      --date 2026-08-07

同步日期區間（日期包含起訖日）：

    python -m stock_master margin-history-sync \
      --db /tmp/taiwan-stocks.db \
      --start-date 2026-01-01 \
      --end-date 2026-08-11

未提供日期區間時，`margin-history-sync` 預設同步截至今天的最近 30 個日曆日。
系統會逐日查詢官方資料；週末或假日的明確 no-data 回應會略過，HTTP 失敗或
schema 改變則停止，之前已成功寫入的日期會保留。每個交易日各自使用一個 SQLite
transaction，重新執行只會 UPSERT，不會產生重複資料。

所有數量欄位統一使用交易單位（張），且只保留 `stocks` table 中的普通股票；ETF、
債券等官方回傳但不在股票主檔的代碼會被忽略。`margin_history` 只保存官方 raw
data，不包含融資成本、維持率或斷頭價等估算欄位。

查詢某股票最近的融資融券資料：

    sqlite3 data/stocks.db \
      "SELECT trade_date, market, margin_balance, short_balance FROM margin_history WHERE stock_code = '2330' ORDER BY trade_date DESC LIMIT 30;"

驗證融資融券資料沒有孤兒股票代碼：

    sqlite3 data/stocks.db \
      "SELECT COUNT(*) FROM margin_history m LEFT JOIN stocks s ON m.stock_code = s.stock_code WHERE s.stock_code IS NULL;"

結果應為 0。

## 每日成交價與融資估算

在 stock master 建立後，同步最新交易日的 TWSE／TPEx 每日成交資料：

    python -m stock_master price-sync

同步指定交易日：

    python -m stock_master price-sync \
      --db /tmp/taiwan-stocks.db \
      --date 2026-08-07

同步最近 30 個日曆日內可取得的每日成交資料：

    python -m stock_master price-history-sync \
      --db /tmp/taiwan-stocks.db

也可以指定完整日期區間：

    python -m stock_master price-history-sync \
      --db /tmp/taiwan-stocks.db \
      --start-date 2026-01-01 \
      --end-date 2026-08-11

`price_history` 只保存官方 raw facts：成交股數、成交金額、開高低收與成交筆數。
TWSE 的歷史介面是全市場查詢；TPEx 歷史介面是逐股票、逐月份查詢，provider 會以
股票主檔作為代碼 universe，並快取同一股票／月份，避免日期回補時重複請求。TPEx
來源的「成交張數」會乘以 1,000 成為股數，「成交仟元」會乘以 1,000 成為新台幣。

市場成交均價是可重現的精確衍生值，不另存一個容易失真的欄位：

    market_average_price = trade_value / trade_volume

它代表全市場成交均價，只是「新增融資成本」的 proxy，不是券商或使用者帳戶的
實際融資買進均價。成交股數為 0 時，`market_average_price` 為 NULL。

融資 raw data 與每日成交資料都同步後，可以估算指定股票最近 30 日的融資成本、
融資維持率與 130% 價格：

    python -m stock_master margin-estimate \
      --db /tmp/taiwan-stocks.db \
      --stock-code 2330

估算全部有融資歷史的股票：

    python -m stock_master margin-estimate \
      --db /tmp/taiwan-stocks.db \
      --all \
      --start-date 2026-01-01 \
      --end-date 2026-08-11 \
      --margin-ratio 0.60

`margin-estimate` 會將結果寫入獨立的 `margin_estimates` table，並用
`model_version` 支援日後改成 FIFO、VWAP 或其他模型。V1 使用加權移動平均：
第一筆仍有融資餘額時以市場成交均價 bootstrap；新增融資以當日市場成交均價
加入；賣出與現金償還只減少官方餘額，不調整剩餘平均成本；官方
`margin_balance` 永遠是數量 source of truth。若數量銜接或交易推導餘額與官方值
不一致，系統只記錄 warning，不會改寫官方 raw data。

若官方成交資料的收盤價是空值（常見於當日沒有成交的股票），該股票／日期的
維持率估算會記錄 warning 並略過，不會用市場成交均價代替收盤價；其他股票與日期
仍會繼續估算。若需要嚴格模式，可在程式 API 使用 `skip_missing_close=False`。

若某日完全沒有 `price_history`，但當日沒有新增融資且仍有前日融資餘額，系統會
沿用前一日 WMA 以維持後續成本計算；該日估算仍會略過，因為缺少收盤價。若缺少
價格的日期有新增融資，該列及其成本仍無法可靠建立的後續列會略過，避免捏造
新增成本。

維持率是獨立的 `MarginMaintenanceEstimator` 模型，使用收盤價而非市場成交均價：

    estimated_financing_per_share = estimated_margin_avg_cost * margin_financing_ratio
    estimated_maintenance_ratio = close_price / estimated_financing_per_share * 100
    estimated_130_price = estimated_financing_per_share * 1.30

這是研究用估算，不包含券商手續費、利息、完整帳戶擔保品、個別券商維持率規則、
追繳或斷頭決策。

官方來源：

* TWSE 每日收盤歷史資料：<https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX>
* TPEx 個股日成交資訊頁：<https://www.tpex.org.tw/zh-tw/mainboard/trading/info/stock-pricing.html>
* TPEx OpenAPI 文件：<https://www.tpex.org.tw/openapi/>

## Web 查詢平台

先完成至少一次 stock master 同步，再啟動唯讀 Web 服務：

    python -m stock_master web

預設監聽 `127.0.0.1:8000`，瀏覽 <http://127.0.0.1:8000>。可指定主機、埠號與資料庫：

    python -m stock_master web \
      --host 127.0.0.1 \
      --port 8000 \
      --db /tmp/taiwan-stocks.db

Web 層只透過短生命週期的 SQLite read-only connection 查詢既有資料，不會呼叫
TWSE、TPEx 或 TDCC endpoint，也不會在瀏覽頁面時執行同步或寫入資料庫。API 以
`/api/v1` 為前綴，主要端點如下：

* `GET /api/v1/health`
* `GET /api/v1/stocks?q=2330&limit=20&offset=0`
* `GET /api/v1/stocks/2330`
* `GET /api/v1/stocks/2330/overview`
* `GET /api/v1/stocks/2330/prices?from=2026-01-01&to=2026-08-11`
* `GET /api/v1/stocks/2330/margin`
* `GET /api/v1/stocks/2330/margin-estimates`
* `GET /api/v1/stocks/2330/margin-estimates/latest`
* `GET /api/v1/stocks/2330/tdcc`
* `GET /api/v1/stocks/2330/tdcc/latest`

歷史端點支援 `from`、`to`、`limit` 與 `offset`；日期為 ISO `YYYY-MM-DD`，
`limit` 最大為 1,000。錯誤統一回傳 `{"error": {"code": "...", "message": "..."}}`。
行情頁的「市場成交均價」明確使用 `成交金額 ÷ 成交股數`，不是融資買進均價。
維持率區塊的所有數值都標示為「估算」，僅供研究與風險提示，不代表券商實際維持率、
追繳線或個人帳戶數值。

## 設定

CLI 可覆寫：

    python -m stock_master sync \
      --timeout 30 \
      --max-attempts 3 \
      --min-twse 500 \
      --min-tpex 100

官方端點集中在 src/stock_master/config.py：

* TWSE: https://openapi.twse.com.tw/v1/opendata/t187ap03_L
* TPEx: https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O
* TDCC: https://openapi.tdcc.com.tw/v1/opendata/1-5
* TDCC historical query: https://www.tdcc.com.tw/portal/zh/smWeb/qryStock
* TWSE margin history: https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN
* TPEx margin history: https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php
* TWSE price history: https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX
* TPEx price history: https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock

HTTP client 會設定 User-Agent、驗證 2xx status、解析 JSON，並對暫時性 HTTP／網路錯誤最多嘗試三次。

## 查詢

    sqlite3 data/stocks.db \
      "SELECT stock_code, stock_name, market FROM stocks WHERE stock_code IN ('2330', '3105');"

驗證排除的 ETF：

    sqlite3 data/stocks.db \
      "SELECT COUNT(*) FROM stocks WHERE stock_code IN ('0050', '0056', '00878', '00919');"

結果應為 0。

查詢某股票最新的股權分散資料：

    sqlite3 data/stocks.db \
      "SELECT data_date, holding_level, shareholder_count, share_count, holding_ratio FROM tdcc_distributions WHERE stock_code = '2330' ORDER BY data_date DESC, holding_level;"

查詢某股票最近一個月的資料日期：

    sqlite3 data/stocks.db \
      "SELECT DISTINCT data_date FROM tdcc_distributions WHERE stock_code = '2330' AND data_date >= date('now', '-30 day') ORDER BY data_date;"

驗證 TDCC 資料只屬於 stock master 且沒有「合計」列：

    sqlite3 data/stocks.db \
      "SELECT COUNT(*) FROM tdcc_distributions d LEFT JOIN stocks s ON d.stock_code = s.stock_code WHERE s.stock_code IS NULL;"

    sqlite3 data/stocks.db \
      "SELECT COUNT(*) FROM tdcc_distributions WHERE holding_level = '合計';"

兩個結果都應為 0。

## 測試

    python -m pytest

測試使用本地 fixture 與 fake HTTP client，不依賴當日官方 API，因此不會因交易所網路或單一股票的市場狀態變動而失效。真正同步時則使用官方即時清單。

## 專案結構

    src/stock_master/
    ├── config.py
    ├── exceptions.py
    ├── main.py
    ├── models/
    ├── providers/
    ├── repositories/
    └── services/
