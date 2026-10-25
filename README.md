# Pokémon GO 週末活動通知

每週五早上 9 點（台灣時間）自動檢查這週末有沒有社群日或其他活動，用 Discord 通知你。跑在 GitHub Actions 上，免費、不用開電腦。

## 運作方式

- **活動時間**：讀 LeekDuck 的活動行事曆（ScrapedDuck 整理成的 JSON），找出跟本週六、日重疊的活動。社群日會排第一個並加上醒目標題。
- **官網公告**：讀 pokemongo.com 繁中新聞頁，附上最新幾則跟活動有關的公告連結。
- 賽季、GO 對戰聯盟、每週輪替的團體戰頭目、超過 10 天的長期活動不列入；平日的聚焦時刻、團體戰時間也自然不會出現。
- 只在特定城市舉辦的實體活動（城市野生區、GO Tour 等）只保留全球版和台灣城市的場次，可在 `LOCAL_KEEP_KEYWORDS` 調整。
- 沒活動時也會傳一則「這週末沒有排定的活動」（不想收就把 `NOTIFY_WHEN_EMPTY` 改成 `"0"`）。

## 設定步驟

### 1. 建立 Discord 通知管道（二選一）

**A. Webhook（推薦，最簡單）**
1. 在你的 Discord 伺服器開一個頻道（例如 `#pokemon-go`）
2. 頻道設定 → 整合 → Webhook → 新增 Webhook → 複製 Webhook 網址

**B. 機器人私訊你**
1. 到 [Discord Developer Portal](https://discord.com/developers/applications) 建立 Application → Bot → 複製 Token
2. 用 OAuth2 URL Generator（scope 勾 `bot`）把機器人邀進一個你也在的伺服器（機器人要跟你有共同伺服器才能私訊你）
3. Discord 設定 → 進階 → 開啟「開發者模式」，對自己的頭像按右鍵 → 複製使用者 ID

### 2. 放上 GitHub
1. 在 GitHub 建一個新的 repo（private 也可以），把這個資料夾的檔案全部上傳，包含 `.github/workflows/weekend-notify.yml`
2. repo 的 Settings → Secrets and variables → Actions → New repository secret：
   - 方式 A：新增 `DISCORD_WEBHOOK_URL`
   - 方式 B：新增 `DISCORD_BOT_TOKEN` 和 `DISCORD_USER_ID`

### 3. 測試
到 repo 的 Actions 頁面 → 「Pokémon GO 週末活動通知」→ Run workflow。幾十秒內 Discord 就會收到訊息。

## 本機測試

```bash
pip install -r requirements.txt
python pogo_weekend_notifier.py --dry-run     # 只印出訊息內容，不發送
DISCORD_WEBHOOK_URL="你的網址" python pogo_weekend_notifier.py
```

## 常見調整

| 想改什麼 | 改哪裡 |
|---|---|
| 通知時間 | workflow 的 `cron`（UTC 時間，台灣 = UTC+8） |
| 排除的活動類型 | `EXCLUDED_TYPES` |
| 官網公告關鍵字 | `NEWS_KEYWORDS` |
| 長期活動的天數門檻 | `MAX_EVENT_DAYS` |

## 注意

- GitHub Actions 的排程尖峰時段可能延遲幾分鐘到幾十分鐘。
- repo 連續 60 天沒有任何 commit，GitHub 會暫停排程，屆時到 Actions 頁面按一下重新啟用即可。
- 官網改版的話，公告區塊可能抓不到（活動時間不受影響，因為那部分來自 LeekDuck）。
