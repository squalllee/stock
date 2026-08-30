# Taiwan Stock Master

Taiwan Stock Master 會從臺灣證券交易所（TWSE）與證券櫃檯買賣中心（TPEx）的官方
OpenAPI 取得資料。Windows 本機桌面同步介面會直接將股票主檔、每日成交行情、
TDCC 與公司內部人申報寫入 Supabase BillDB；原有 SQLite 命令列流程保留給既有
本機查詢功能。

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

官方資料與 SQLite 同步核心使用 Python 標準函式庫；Supabase 匯出使用固定版本的
Supabase Python client。Web 查詢平台額外使用 FastAPI、Uvicorn 與 Jinja2，測試
工具使用 pytest 與 httpx。

如果 macOS 將 repository 放在外接或特殊掛載磁碟（例如 `/Volumes/D`），而 pip
出現 `UnicodeDecodeError` 且 traceback 指向 `importlib.metadata`，通常是磁碟產生
的 `._*.dist-info` AppleDouble 側錄檔被 pip 誤認成套件 metadata。請先啟用專案
環境並清除這些側錄檔，再安裝：

    source .venv/bin/activate
    find "$VIRTUAL_ENV/lib/python3.12/site-packages" -name '._*' -exec rm -rf -- {} +
    PIP_DISABLE_PIP_VERSION_CHECK=1 python -m pip install --upgrade setuptools wheel
    PIP_DISABLE_PIP_VERSION_CHECK=1 python -m pip install -e ".[dev]" --no-build-isolation

之後執行專案時也請使用已啟用的 `.venv`，或直接使用
`.venv/bin/python`，避免誤用系統或 Conda 的 `python`。

## Windows 本機同步介面

如果要在 Windows 直接操作同步，不需要啟動 Web 介面。請使用包含 Tkinter 的
Python 安裝程式（python.org 的 Windows installer 預設包含），安裝專案後執行：

    python -m pip install -e ".[dev]"

在專案根目錄建立 `.env`（可複製 [`.env.example`](.env.example)），填入 BillDB 的
後端 Secret key。`.env` 已加入 `.gitignore`，不會提交到 Git：

    SUPABASE_SECRET_KEY=sb_secret_...
    python -m stock_master desktop

如果使用舊版 Supabase service-role key，`.env` 可以改成：

    SUPABASE_SERVICE_ROLE_KEY=eyJ...

需要切換 Supabase project 時：

    python -m stock_master desktop --supabase-url "https://你的專案.supabase.co"

視窗提供七個操作：股票主檔、每日成交行情（可選日期區間，預設最近一日）、最新融資使用率、
TDCC 最新一期、指定年份的 TDCC 歷史資料、TWSE／TPEx 公司內部人申報，以及指定年度的
MOPS 內部人持股。
所有資料直接寫入
Supabase BillDB 的 `stocks`、`price_history`、`tdcc_distributions` 與
`margin_history`、`insider_transactions`，不會寫入 SQLite；請先按「股票主檔」，再按每日成交行情、
融資使用率、TDCC 或內部人申報。視窗啟動時也會顯示 TDCC、每日行情與內部人申報 API 的最新資料日期。
年度 TDCC 與內部人持股同步可能需要較長時間，視窗會顯示完成筆數或錯誤原因。
MOPS 單一月份若在 HTTP 重試後仍逾時，年度同步會記錄該股票／月份並繼續處理；
完成摘要會標示「部分完成」，之後重跑相同年度即可用 upsert 補齊失敗月份。

內部人申報同步依 Supabase `stocks` 的股票代號篩選官方全市場 OpenAPI，保存「預定轉讓」
與「未轉讓」兩種申報，並保留原始 JSON。按下「內部人持股年度」後，程式會依 Supabase
股票主檔逐支查詢 MOPS 當年度每月「內部人持股異動事後申報表」，將上月底與本月底持股
餘額及本月淨變動以 `after_report` 類型寫入同一張表。這些資料是公司申報的持股餘額，
不代表交易所逐筆成交紀錄。

TDCC 每週更新一次；「TDCC 最新一期」會先比較 Supabase 中已存在的最大
`data_date`，若官方資料日期沒有更新就略過，不重複同步同一週資料。年度同步則依
TDCC 官方頁面提供的每週資料日期查詢，從最新週開始，每 50 支股票完成後立即批次
寫入 Supabase。`tdcc_sync_checkpoints` 會記錄成功與官方明確回覆查無資料的
`data_date + stock_code`；中斷後重新執行只補未完成項目。若 TDCC 在雙 worker
模式下發生連線失敗，該批會改用單 worker 與較長間隔重試，來源穩定後再恢復。
若個別結果頁缺少顯示日期，程式會先核對表單選定日期；遇到疑似 Session／Token
失效的不完整頁面時，會更新 Session 後只重試該股票與該週。

同步執行期間，視窗的「同步狀態」會即時顯示目前來源、資料日期、TDCC 股票進度與
Supabase 批次寫入進度；同步完成或失敗後會保留最後結果。

對應的 Supabase schema 位於
[`supabase/schema/market_data.sql`](supabase/schema/market_data.sql)、
[`supabase/schema/tdcc_distributions.sql`](supabase/schema/tdcc_distributions.sql)、
[`supabase/schema/tdcc_sync_checkpoints.sql`](supabase/schema/tdcc_sync_checkpoints.sql) 與
[`supabase/schema/insider_transactions.sql`](supabase/schema/insider_transactions.sql)、
[`supabase/schema/margin_history.sql`](supabase/schema/margin_history.sql)。

## Node.js 手機版籌碼網站

[`mobile-web`](mobile-web) 是獨立的 React＋Node.js 手機版網站，可依股票代碼或
名稱查詢大戶（TDCC 第 15 級）與散戶（第 1～6 級）持股比例、張數及戶數，也可
篩選最近 2～12 週大戶持股比例每週持續增加的股票，再點進個股查看趨勢、明細、
收盤價、最新融資使用率與公司內部人申報。

網站沿用專案根目錄 `.env` 的 `SUPABASE_SECRET_KEY`，金鑰只存在 Node.js 後端，
不會送到瀏覽器。第一次使用先安裝套件並啟動：

    cd mobile-web
    npm install
    npm run dev

瀏覽器開啟 `http://localhost:3000`。完整執行與正式模式說明請見
[`mobile-web/README.md`](mobile-web/README.md)。資料庫查詢函式定義於
[`supabase/schema/tdcc_mobile_web.sql`](supabase/schema/tdcc_mobile_web.sql)，內部人明細
資料表定義於 [`supabase/schema/insider_transactions.sql`](supabase/schema/insider_transactions.sql)。

安裝完成後也可以直接執行 `stock-master-desktop` 開啟同一個介面。

## 舊版 SQLite 命令列同步（相容保留）

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

### 同步今年 TDCC 資料到 Supabase BillDB

BillDB 的 `public.tdcc_distributions` schema 定義在
[`supabase/schema/tdcc_distributions.sql`](supabase/schema/tdcc_distributions.sql)。
table 使用 `data_date + stock_code + holding_level` 複合主鍵、限制級距為 1～15，
並啟用 RLS；`anon` 與 `authenticated` 沒有權限，只有後端 `service_role` 可同步。

先從 Supabase BillDB 的 API Keys 頁取得後端 Secret key，放在環境變數中。請勿將
key 寫入程式、命令列參數或提交到 Git：

    export SUPABASE_SECRET_KEY='sb_secret_...'

舊版 BillDB key 也可使用：

    export SUPABASE_SERVICE_ROLE_KEY='eyJ...'

先驗證今年 SQLite 的資料筆數與內容，不連線 Supabase：

    python -m stock_master tdcc-supabase-sync --dry-run

確認後同步今年資料到 BillDB：

    python -m stock_master tdcc-supabase-sync

也可以指定年度與批次大小：

    python -m stock_master tdcc-supabase-sync \
      --year 2026 \
      --batch-size 500

程式預設使用 BillDB 專案 URL；若要改用其他 Supabase project，可設定
`SUPABASE_URL` 或傳入 `--supabase-url`。同步來源是本機 SQLite
`tdcc_distributions`，只 upsert 指定年度且級距為 1～15 的資料；重複執行不會建立
重複列。

### 直接從 TDCC 官方資料同步到 Supabase

如果不想先同步 SQLite，可直接執行專案根目錄的
[`sync_tdcc_to_supabase.py`](sync_tdcc_to_supabase.py)。這個程式完全獨立於
`stock_master` 的內部服務。未指定年度時，會抓取 TDCC 官方 OpenAPI 最新回應；
指定 `--year` 時，會先取得官方歷史頁今年實際提供的每週日期，再逐檔查詢全年
資料。兩種模式都只保留 1～15 級距，並以 `data_date + stock_code + holding_level`
批次 upsert 到 BillDB 的 `public.tdcc_distributions`。預設會讀取
`data/stocks.db` 的 `stocks` table，只同步上市／上櫃普通股票；因此 ETF、權證、
債券等 TDCC 其他證券不會送到 Supabase。若尚未建立股票主檔，先執行：

    .venv/bin/python -m stock_master sync

先做唯讀驗證：

    .venv/bin/python sync_tdcc_to_supabase.py --dry-run --year 2026

確認筆數後執行實際同步：

    export SUPABASE_SECRET_KEY='sb_secret_...'
    .venv/bin/python sync_tdcc_to_supabase.py --year 2026

`--year 2026` 會同步 2026 年所有 TDCC 官方歷史頁提供的週資料，不是只同步
最新的 8/21。年度全市場資料量很大，程式會分批處理；可以用較保守的參數：

    .venv/bin/python sync_tdcc_to_supabase.py \
      --year 2026 \
      --workers 2 \
      --request-delay 0.2 \
      --chunk-size 100

正式跑全市場前，也可以先測試一檔股票：

    .venv/bin/python sync_tdcc_to_supabase.py \
      --year 2026 \
      --stock-code 2330 \
      --workers 1 \
      --request-delay 0 \
      --dry-run

只執行最新官方資料、不查歷史頁時，不要指定年度：

    .venv/bin/python sync_tdcc_to_supabase.py

若要刻意包含所有 TDCC 證券，才使用 `--all-securities`；一般股票同步不需要這個
參數。SQLite 不在預設位置時，可用 `--db /path/to/stocks.db` 指定。

請只使用 Supabase 後端 Secret key（或舊版 `SUPABASE_SERVICE_ROLE_KEY`），不要把
key 寫在程式、命令列或 Git。程式會直接連線 Supabase，不會讀取 SQLite，也不會
因為直接執行而觸發 `stock_master` 的相對匯入。

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
債券等官方回傳但不在股票主檔的代碼會被忽略。`margin_history` 保存官方 raw data、
融資限額，以及融資使用率；TWSE 使用率由今日餘額 ÷ 次一營業日限額計算，TPEx 則採官方百分比。

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

先完成至少一次 stock master 同步，再啟動 Web 服務：

    python -m stock_master web

預設監聽 `127.0.0.1:8000`，瀏覽 <http://127.0.0.1:8000>。可指定主機、埠號與資料庫：

    python -m stock_master web \
      --host 127.0.0.1 \
      --port 8000 \
      --db /tmp/taiwan-stocks.db

一般 Web 查詢只透過短生命週期的 SQLite read-only connection 讀取既有資料，不會
呼叫 TWSE、TPEx 或 TDCC endpoint。首頁的「同步所有資料」按鈕會啟動背景工作，只依序
同步最新成交行情、最新融資融券、最新 TDCC（沒有新資料時略過）與最新融資維持率估算；
歷史資料仍請使用前述個別 history-sync 指令。API 以 `/api/v1` 為前綴，主要端點如下：

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
* `POST /api/v1/sync/all`
* `GET /api/v1/sync/all/{job_id}`

歷史端點支援 `from`、`to`、`limit` 與 `offset`；日期為 ISO `YYYY-MM-DD`，
`limit` 最大為 1,000。錯誤統一回傳 `{"error": {"code": "...", "message": "..."}}`。
行情頁的「市場成交均價」明確使用 `成交金額 ÷ 成交股數`，不是融資買進均價。
TDCC 區塊會將歷史資料分成兩張混合圖：14 級距以上的持股比例總和，以及 6 級距
以下的持股比例總和以長條呈現，兩張圖都會疊加同一資料日期的收盤價折線；下方另保留
最新日期的級距明細。
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
* TWSE insider planned transfers: https://openapi.twse.com.tw/v1/opendata/t187ap12_L
* TWSE insider untransferred notices: https://openapi.twse.com.tw/v1/opendata/t187ap13_L
* TPEx insider planned transfers: https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap12_O
* TPEx insider untransferred notices: https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap13_O
* MOPS monthly insider holdings: https://mops.twse.com.tw/mops/api/query6_1

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
