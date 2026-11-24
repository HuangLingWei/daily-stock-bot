# 📈 Daily Stock Bot - 台美股多策略量化選股與 Telegram 自動通知機器人

    ![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
    ![GitHub Actions](https://img.shields.io/badge/CI%2FCD-GitHub_Actions-2088FF?logo=githubactions)
    ![Telegram](https://img.shields.io/badge/Notify-Telegram_Bot-2CA5E0?logo=telegram)
    ![yfinance](https://img.shields.io/badge/Data-yfinance-green)

    這是一個全自動化的量化選股推播機器人。透過 **GitHub Actions 每日定時無伺服器排程**，抓取**美股 S&P 500** 與
    **全台股（上市/上櫃）** 標的，合成 4 小時 K 線並以多項技術指標（MACD、KDJ、Bollinger Bands、ADX、EMA）交叉篩選買訊，自動向
    Telegram 發送推播通知。

    ---

    ## 💡 選股核心策略與指標

    系統下載 730 天每小時 K 線，並合成 **4 小時 K 線 (4h Resample)** 消除短期雜訊：

    | 指標 | 參數設定 | 判斷邏輯 |
    | :--- | :--- | :--- |
    | **MACD** | 預設快慢線 | 黃金交叉、柱狀圖翻正、零軸以上起漲 |
    | **KDJ** | 9, 3 | 低檔黃金交叉 (超賣區買點) |
    | **Bollinger Bands** | 20 日, 2 倍標準差 | 帶量突破上軌、壓縮後帶量擴張 |
    | **ADX** | 14 | 趨勢強度過濾 (排除盤整無動能標的) |
    | **EMA** | 60 | 站穩中長期均線作為多頭保護保護傘 |

    > 採用 `concurrent.futures.ThreadPoolExecutor` 進行多執行緒加速平行運算，大幅縮減全市場掃描時間。

    ---

    ## 🤖 Telegram 推播通知範例

    當篩選出強勢訊號時，Telegram 機器人將推播 HTML 格式訊息：
    ```text
    🚀【每日量化強勢選股清單】
    -------------------------------
    代碼：2330.TW (台積電)
    週期：4小時線 (4H)
    訊號：MACD 黃金交叉 + 突破布林上軌
    趨勢：EMA60 多頭排列，ADX 強勢動能 (>25)
    收盤價：XXX 元
    -------------------------------
  ──────
  ⚙️ 部署與設定教學

  1. 取得 Telegram Bot 金鑰與 ID

  1. 在 Telegram 中搜尋 @BotFather，輸入 /newbot 建立機器人並取得 TELEGRAM_TOKEN。
  2. 搜尋 @userinfobot 獲取個人的 TELEGRAM_CHAT_ID。

  2. 設定 GitHub Repository Secrets

  到您的 GitHub 倉庫：Settings ➔ Secrets and variables ➔ Actions ➔ 點選 New repository secret，新增兩組變數：

  • TELEGRAM_TOKEN：您的 Telegram Bot Token
  • TELEGRAM_CHAT_ID：您的 Chat ID

  3. 排程設定

  本專案已在 .github/workflows/main.yml 中設定定時排程（每週一至週五自動觸發），您也可以在 GitHub 的 Actions 分頁點選 Run workflow
  手動立即執行。
  ──────
  💻 本地執行測試

    pip install -r requirements.txt
    export TELEGRAM_TOKEN="your_token"
    export TELEGRAM_CHAT_ID="your_chat_id"
    python main.py
