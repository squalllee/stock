# 籌碼週報手機版

這個 Node.js 網站讀取 Supabase BillDB 的 `stocks` 與
`tdcc_distributions`，提供：

- 股票代碼／名稱搜尋。
- 搜尋與篩選卡片顯示最新一個交易日的收盤價。
- 最新大戶、散戶持股比例、持股張數與戶數。
- 首頁可依 TDCC 級距調整大戶統計範圍，例如 1,000 張以上或 800～1,000 張。
- 連續 2～12 週大戶持股比例上升篩選。
- 大戶由賣轉買、由買轉賣的方向轉折清單。
- 個股每日股價 K 線、5／10／20 日移動平均線、成交量，以及以年度最低持股為基準的 12／26／52 週增幅長條圖與每週明細。
- 個股最新融資餘額、融資限額與融資使用率（TWSE 由官方餘額／限額推算，TPEx 顯示官方百分比）。
- 個股公司內部人申報明細（事前預定轉讓與未轉讓通知）。

Supabase Secret key 只由 Node.js 後端讀取，不會送到瀏覽器。程式會先讀取專案根目錄
的 `.env`，也可使用本目錄的 `.env` 覆蓋設定。

## 安裝與執行

在 `mobile-web` 目錄執行：

```bash
npm install
npm run dev
```

電腦開啟 `http://localhost:3000`。同一個 Wi-Fi 的手機可使用電腦區域網路 IP 加上
`:3000` 開啟。

正式模式：

```bash
npm run build
npm start
```

正式模式網址預設為 `http://localhost:3000`。

## 部署到 Render

Repository 根目錄的 `render.yaml` 已設定好 Web Service。於 Render 選擇
`Blueprint` 並連接此 repository，Render 會使用 `mobile-web` 作為根目錄；
建立服務時，填入 `SUPABASE_SECRET_KEY`，不要填入前端或提交到 Git。
Render 會自動提供 `PORT`，不需要另外設定埠號。

資料庫需要先執行
[`supabase/schema/tdcc_mobile_web.sql`](../supabase/schema/tdcc_mobile_web.sql)，
建立搜尋、明細、連續增持、大戶動向與每日股價五個唯讀函式；另需建立
[`supabase/schema/insider_transactions.sql`](../supabase/schema/insider_transactions.sql)，
由 desktop.py 同步官方內部人申報後，個股頁才會顯示申報卡片。
另需執行 [`supabase/schema/margin_history.sql`](../supabase/schema/margin_history.sql)，
由 desktop.py 的「融資使用率」操作同步最新融資融券資料後，個股頁才會顯示融資卡片。
