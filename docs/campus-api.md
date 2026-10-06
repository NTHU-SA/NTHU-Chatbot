# 校園 API

[回到 README](../README.md) · [AI 對話](architecture.md#ai-對話) · [公車介面規範](../design.md#公車紅綠藍線)

## NTHU API v2 遷移

依據 2026-10-05 的 [OpenAPI](https://api.nthusa.tw/openapi.json)（`info.version=2.0.0`）與 MCP 工具清單，
所有校園資料呼叫改用未棄用介面。聊天室內的 `@` 指令使用 REST API，
AI 對話則透過 NTHU Data MCP（預設 `https://api.nthusa.tw/mcp`）查詢。

| 功能 | 端點與資料處理 |
| --- | --- |
| 公車 | `/buses/stops` 取得站點；`/buses/schedule?stop=...&details=true` 回傳 `dep_info` 與 `stops_time`。使用 `route`（all / main / nanda）、`limit` 篩選，從所選站的 `arrive_time` 顯示到站時間，保留已發車但尚未到站的班次 |
| 餐廳 | `/dining` 保留建築與餐廳巢狀格式；以 `schedule` 篩選營業日 |
| 圖書館 | `/libraries/spaces` 保留剩餘數量 0；`/libraries/rss/{rss_type}` 處理空頁與 nullable 連結 |
| 地圖 | `/locations?name=...&fuzzy=true` 模糊搜尋，同一端點不帶參數時列出地點 |
| 公告 | `/announcements` 使用 department / language 篩選；佈告欄名稱在本地篩選，處理 nullable 文章欄位 |
| 系所 | `/directory` 取得單位目錄；對外的 chatbot `/api/departments` 維持不變，只回傳學術單位名稱 |

## 相容性與路線

公車路線代碼為 `main_red`、`main_green`、`nanda_route_1`、`nanda_route_2`，
從詳細時刻表的 `dep_info.line` 直接讀取，不再額外查詢比對路線。
既有 LINE postback 的 `bus_type` / `limits` 仍會轉成新參數。
未提供路線時顯示「路線待確認」，不依車型猜測；缺少所選站點或格式錯誤時，
由指令處理器記錄錯誤並通知使用者稍後重試，不當成沒有班次。
紅／綠／藍路線的介面分類見 [design.md](../design.md#公車紅綠藍線)。

## MCP 工具遷移

AI 公車工具 `get_bus_schedule` 取代 `get_next_buses` 與 `get_bus_stops`，
回應與參數格式由 MCP 動態載入；公車結果不快取。
若部署環境有自訂 `MCP_ALLOWED_TOOLS`，需同步替換舊工具名稱，
或移除此變數以使用新版預設清單。
