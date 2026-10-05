# 清華校園情報員 · Soft Glass

網頁和 LINE 訊息共用「淡紫畫布、白色表面、灰紫動作」的視覺語言。
使用者通常在手機上快速查校園資訊；優先讓標題、資訊、下一步一眼可辨，
而不是把整張卡片塗成高飽和紫色。

## 色票

| 用途 | 色碼 | 網頁 / Flex 對應 |
| --- | --- | --- |
| 淡紫畫布 | `#F3F1F7` | `--bg` / `background` |
| 白色表面 | `#FFFFFF` | `--surface` / `surface` |
| 標題淡紫底 | `#E9E4F1` | `--canvas-wash` / `tint` |
| 選取狀態 | `#E6E0F0` | `--selected` / `selected` |
| 主色、主要按鈕 | `#7362A2` | `--brand` / `brand` |
| 主要文字 | `#2D2935` | `--text` / `text` |
| 次要文字 | `#696173` | `--muted` / `muted` |
| 分隔線、內層細框 | `#DED9E7` | `--line` / `line` |

預設色票沿用 `frontend/style.css` 的淺色模式。
**所有 LINE Flex 的全域風格設定集中在
`templates/messages/flex_theme.py` 的 `FLEX_THEME`**，共用元件讀取同一個設定。
公車／圖書館 Jinja 模板只負責輸出 builder 的 JSON，不再重複寫色碼、字級或間距。
這份設定控制 LINE Flex；網站本身的淺色／深色外觀仍由 `frontend/style.css` 控制。

## 一次修改所有 Flex

| `FLEX_THEME` 群組 | 控制內容 |
| --- | --- |
| `colors` | 背景、白色表面、標題底、主色、文字、細框 |
| `typography` | `title` 標題、`body` 內文、`caption` 路線標籤、`section` 小標、`value` 數字、`arrival` 到站時間 |
| `spacing` | 標題、內文、footer、內層、資料列 padding，以及群組／項目間距 |
| `radii` | 內層資訊區、資料列、路線標籤的圓角 |
| `components` | 卡片尺寸、通知尺寸、框線粗細、按鈕高度與主／次樣式、附圖比例 |
| `routes` | 紅、綠、藍線的標籤文字、前景色 `ink` 與淡色底 `tint` |

例如，修改 `FLEX_THEME` 定義中的這幾個值即可一起換所有卡片，不必改 AI、
公車、圖書館或任何輪播的個別模板：

```python
FLEX_THEME["colors"]["brand"] = "#4D4480"
FLEX_THEME["typography"]["title"] = "xl"
FLEX_THEME["spacing"]["body"] = "24px"
FLEX_THEME["radii"]["inset"] = "20px"
FLEX_THEME["components"]["button_height"] = "md"
FLEX_THEME["routes"]["blue"]["tint"] = "#E7EEFA"
```

字級／尺寸使用 LINE 支援的值，間距／圓角使用如 `16px` 的合法長度，
按鈕高度為 `sm` 或 `md`。Flex 外框圓角與原生按鈕文字顏色仍受 LINE 限制。
編輯設定檔後重新啟動／部署後端，再重新產生預覽資料；已送出的 LINE 訊息不會被追溯修改。
選單快取會檢查 theme signature，執行中以原物件修改 token 時也不會繼續使用舊風格。
`tests/test_flex_theme.py` 驗證修改全域設定會同步套用到所有 builder、Jinja 模板與快取選單。

## 白玻璃如何跨平台

- **網頁**：白色半透明表面、白色邊緣、有限的背景模糊與柔和陰影；不支援模糊時退回實心白色。
- **LINE Flex**：不支援 CSS `backdrop-filter`、自訂陰影或網站字型。
  使用實心淡紫底、白色資訊區、1px 細框與圓角表達層次，不能宣稱有真實毛玻璃。
- Flex 外框圓角、字型與呈現細節交由 LINE 客戶端決定；不模擬網站深色模式，
  也不使用透明字色來賭聊天室底色。保留足夠對比。

## 卡片規則

標題使用淡紫底，主文字深灰，`lg` 粗體且可換行；一般內文 `sm`，
系統通知也不再用過小的 `xs`。標題和內文 padding 20px、footer 16px。
內層資訊區 padding 16px、圓角 16px、細框 1px；圖書館資料列為 12px padding / 圓角。

主要動作為灰紫底白字，次要動作為灰紫文字按鈕，不混用 LINE 預設綠色。
灰紫小字只放在白色／近白底上，淡紫標題底使用深灰文字，避免對比不足。
每張卡片只有一個主要動作。保留所有 URI、電話、postback、message、
Quick Reply、圖片與原本的資料篩選／分頁行為。
長名稱換行，不用固定列高截斷；零值明確顯示。
輪播每則最多 12 張，選單依 LINE 上限拆則且不重複列出指令別名。

## 各訊息的資訊層級

| 訊息 | 內容與動作 |
| --- | --- |
| AI 入口 | 說明 → 白色問題預覽 → 開啟原 LIFF 對話；保留 `q` / `s` 編碼與截斷限制 |
| 加好友歡迎 | 簡短說明 → 開始聊天 → 分享給 LINE 好友 |
| 加入／分享情報員 | 用途、LINE ID → 加入好友 → 分享邀請文字；不依賴第三方 QR 圖 |
| 公車 | 車種、站點／方向、紅綠藍路線標籤 → 到站時間 → 發車站點、時間、備註 → 更新到站資訊；保留 Quick Reply |
| 圖書館空間 | 區域／剩餘數量對齊 → 預約 → 更新 → 回到首頁；`0` 不轉成缺值 |
| 使用說明 | 提問、指令、提醒三段 → 聊天（有 LIFF ID 時） → 加好友 → 分享 |
| 功能選單、餐廳、公告、圖書館消息 | 共用 Flex 輪播；保留原動作與圖片，分頁確認也統一為 Flex |
| 系統通知 | 同色票的小型 `kilo` 卡片，清楚說明問題 |

`library_info.json.jinja` 的既有開館資訊模板也同步更新，
但目前沒有對應的上線指令；預覽展示不代表新增可查開館時間的功能。
純文字、位置訊息、時刻表圖片與 Rich Menu 圖片不在本次 Flex 改版範圍。

## 公車紅、綠、藍線

紅線與綠線代表校本部；**依使用者指定，藍色代表南大專車**，
API 的 `nanda_route_1` / `nanda_route_2` 顯示為「藍線 · 南大專車 · 路線一／二」。
這是介面的色彩分類，不宣稱 API 有一條名為藍線的官方路線。
83 號公車是車種，不直接當作藍線；大型／中型車也不能用來推斷紅綠線。

到站卡片的路線以右上角淡紅／淡綠／淡藍小標呈現，與車種標題同列，不再占用整條橫列。
小標字級由 `typography.badge` 控制，留白由 `spacing.badge_x`／`badge_y` 控制。
藍色小標僅顯示「藍線」，南大專車與路線一／二移到站點說明，避免長標籤擠壓標題。
同一色系的深色到站時間強化辨識。
卡片標題底、白色內層與灰紫操作按鈕仍共用主題，避免每條線變成完全不同的設計。
方向選擇卡展示三種路線，保留原本上山／下山 postback 與 Quick Reply。

`/buses/schedule?stop=...&details=true` 直接回傳詳細時刻表：
路線取自 `dep_info.line`（`main_red` / `main_green` / `nanda_route_1` / `nanda_route_2`），
發車資訊取自 `dep_info`，到站時間取自 `stops_time` 中指定站點的 `arrive_time`。
同一次請求取得全部資訊，不再額外查詢或比對路線。
上游依指定站點的到站時間篩選，因此保留已發車但尚未到站的車。
未提供路線時顯示「路線待確認」，不依車型猜測；回應缺少所選站點或格式錯誤時，
由指令處理器記錄錯誤並通知使用者稍後重試，不冒充沒有班次。

預覽包含紅、綠、藍（路線一／二）與待確認狀態，班次都為示範資料。

## 加好友與分享

加好友固定使用使用者指定的
`https://line.me/R/ti/p/@741vdfol`。
分享使用 LINE 文件列出的 `https://line.me/R/share?text=…`，
將「情報員介紹＋加好友網址」整段 UTF-8 percent-encode。
這會開啟 LINE 分享畫面，由使用者自行選好友並確認，不會自動傳送訊息。
分享的是邀請文字，不是 Flex 卡片，也不包含個人的 AI 問題或 session key。

`@神奇海螺/分享清華校園情報員`、`分享狗狗情報員`、
`分享給好友`、`新增情報員好友` 都通往同一張加入／分享卡片，
避免舊訊息和既有選單的 postback 失效。
使用說明與歡迎卡也提供分享入口。
LINE URL scheme 以 iOS／Android 為主要支援平台；桌機須另在手機 LINE 驗收。

參考：[LINE URL scheme](https://developers.line.biz/en/docs/messaging-api/using-line-url-scheme/)、
[Flex Message](https://developers.line.biz/en/docs/messaging-api/using-flex-messages/)。

## 預覽與維護

預覽在 `frontend/flex-preview.html`，免登入、免 API 或 LIFF 設定。
從正式 builder／模板產生資料，不另寫一套示意卡片。
公車、餐廳、空間、公告與消息皆為明確標示的假資料；所有預覽按鈕只顯示動作，
不會觸發 webhook 或開啟示例 LIFF。每則訊息的「複製 JSON」複製 `contents`
（單一 bubble 或 carousel），可直接貼到 LINE Flex Simulator；
瀏覽器禁止剪貼簿存取時顯示可選取的 JSON，讓使用者手動複製。
每個範例也可下載完整的 Flex message 陣列，保留 `altText`、Quick Reply 等資訊。
瀏覽器近似排版不是 LINE 官方渲染結果，發布前仍需手機端驗收。

```powershell
uv run python scripts\build_flex_preview.py
uv run python -m http.server 5500 --bind 127.0.0.1 --directory .
# 開啟 http://localhost:5500/frontend/flex-preview.html
uv run pytest tests\test_flex_theme.py tests\test_bus_routes.py tests\test_flex_preview.py tests\test_liff_message.py tests\test_modules.py tests\test_callback.py
```

更改 builder 或模板後，重新產生並一起提交 `frontend/flex-preview-data.js`，
避免文件與預覽落後。網站頁面使用既有 CSS tokens，不增加 UI 套件或字型下載。
LINE Flex 的平台特定尺寸留在 Python／Jinja，不把網站 CSS 單位直接送給 LINE。
Hosting 建置排除 `flex-preview*`，避免把示範資料、未啟用的開館模板與示例 LIFF ID
當成正式頁面發布。
