# Taiwan Stock Master

Taiwan Stock Master 會從臺灣證券交易所（TWSE）與證券櫃檯買賣中心（TPEx）的官方 OpenAPI，同步目前有效的上市／上櫃普通股票到 SQLite。

資料表只包含：

| 欄位 | 說明 |
| --- | --- |
| stock_code | 四位數股票代碼 |
| stock_name | 股票簡稱 |
| market | TWSE 或 TPEX |

ETF、ETN、權證、債券、基金、REIT、存託憑證、興櫃與戰略新板不會寫入 stocks。

## 安裝

需要 Python 3.11 以上。執行：

    python -m pip install -e ".[dev]"

執行環境沒有第三方 runtime dependency；HTTP、JSON、SQLite 與 CLI 都使用 Python 標準函式庫。

## 同步

    python -m stock_master sync

資料庫預設建立於 data/stocks.db。也可以指定資料庫：

    python -m stock_master sync --db /tmp/taiwan-stocks.db

同步會先完整取得兩個官方來源，再以單一 SQLite transaction 執行 UPSERT。任何一個來源 HTTP 失敗、回傳空陣列、schema 無法辨識，或資料量低於 sanity threshold，都會停止寫入；既有資料不會被刪除。V1 也不會因上游清單消失而自動刪除股票。

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

HTTP client 會設定 User-Agent、驗證 2xx status、解析 JSON，並對暫時性 HTTP／網路錯誤最多嘗試三次。

## 查詢

    sqlite3 data/stocks.db \
      "SELECT stock_code, stock_name, market FROM stocks WHERE stock_code IN ('2330', '3105');"

驗證排除的 ETF：

    sqlite3 data/stocks.db \
      "SELECT COUNT(*) FROM stocks WHERE stock_code IN ('0050', '0056', '00878', '00919');"

結果應為 0。

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

