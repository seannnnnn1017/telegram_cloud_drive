# Telegram Cloud Drive

個人 LAN 雲端硬碟 — 以 Telegram bot 為檔案儲存後端，Vault 深色介面。

## 快速開始

### 1. 前置需求

- Python 3.11+
- Telegram bot token（透過 [@BotFather](https://t.me/botfather) 建立）

### 2. 設定 Telegram 頻道

1. 建立一個**私人頻道**（Private Channel）
2. 將 bot 加入頻道，設為管理員，開啟「發佈訊息」與「刪除訊息」權限
3. 取得頻道的 `chat_id`：
   - 轉發頻道任一訊息給 [@getidsbot](https://t.me/getidsbot)
   - 或呼叫 `https://api.telegram.org/bot<TOKEN>/getUpdates` 取得

### 3. 安裝與啟動

```bash
pip install -e .

# 建立 .env
cp .env.example .env
# 填入 BOT_TOKEN 與 CHAT_ID

telecloud
```

如果 `.env` 不存在、是空檔，或缺少 `BOT_TOKEN` / `CHAT_ID`，`telecloud` 會自動進入設定模式：

1. 在 terminal 貼上 BotFather 給你的 `BOT_TOKEN`
2. 到 Telegram 對你的 bot 傳任意一則訊息
3. `telecloud` 會監聽 Telegram update、取得 `CHAT_ID`，並寫回 `.env`

`telecloud` 會自動尋找可用 port，預設開啟瀏覽器；閒置 15 分鐘後會自動關閉服務。

常用選項：

```bash
# 切換為區域網模式，自動使用本機 LAN IP 產生分享連結
telecloud --lan

# 手動指定 LAN 可存取
telecloud --host 0.0.0.0 --share-base-url http://192.168.1.100:8000

# 指定偏好的起始 port；若被占用會往後找可用 port
telecloud --port 8000

# 自訂閒置關閉秒數；0 代表不自動關閉
telecloud --idle-timeout 600
# 也可在網頁設定中調整，會寫入 TELECLOUD_IDLE_TIMEOUT

# 重新執行 bot 設定，強制重新抓 CHAT_ID
telecloud --setup

# 同時啟動多組伺服器 profile，每組使用自己的 .env
telecloud --server personal=.env --server work=.env.work
```

### 4. 區域網路存取

在同一網路的裝置可透過主機 IP 存取，例如 `http://192.168.1.100:8000`。

## 功能

| 功能 | 說明 |
|------|------|
| 上傳 | 點擊按鈕或拖放檔案（上限 20 MB） |
| 下載 | 點擊檔案或右鍵選單 |
| 刪除 | 右鍵選單 → 刪除 |
| 預覽 | 圖片與 PDF 可直接在瀏覽器預覽 |
| 搜尋 | 即時搜尋檔名 |
| 排序 | 點擊欄位標題（名稱 / 類型 / 時間 / 大小） |
| 多選 | 勾選 checkbox → 批次刪除 |

## 安全說明

本應用程式**未設置身份驗證**，設計為個人區域網路使用。請確認：
- 僅在受信任的私人網路環境下運行
- 不要將 port 8000 暴露至公共網際網路

## 限制

- 單檔上限 20 MB（Telegram Bot API 標準限制）
- 需大於 20 MB 支援：請自行架設 [Telegram Bot API Server](https://github.com/tdlib/telegram-bot-api)

## 執行測試

```bash
pytest -v
```
