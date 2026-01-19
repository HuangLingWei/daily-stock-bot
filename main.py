import requests
import pandas as pd
import pandas_ta as ta
import yfinance as yf
from io import StringIO
import concurrent.futures
import datetime
import os
import urllib3

# 關閉 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 設定 Telegram (從 GitHub Secrets 讀取)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_notify(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ 未設定 Telegram Token")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"發送失敗: {e}")

# --- 獲取清單 ---
def get_sp500_tickers_from_wiki():
    print("抓取美股 S&P 500...")
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        tables = pd.read_html(StringIO(response.text))
        sp500 = next((t for t in tables if 'Symbol' in t.columns), None)
        if sp500 is None: return []
        return sp500['Symbol'].str.replace('.', '-', regex=False).tolist()
    except:
        return []

def get_tw_all_stocks_from_isin():
    print("抓取台股清單 (ISIN)...")
    urls = [
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", ".TW"), 
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=4", ".TWO")
    ]
    all_tickers = []
    for url, suffix in urls:
        try:
            res = requests.get(url)
            df = pd.read_html(StringIO(res.text))[0]
            if '有價證券代號及名稱' not in df.columns:
                df = df.iloc[2:]
                target_col = 0
            else:
                target_col = df.columns.get_loc('有價證券代號及名稱')
                
            for _, row in df.iterrows():
                content = str(row.iloc[target_col])
                parts = content.split()
                if len(parts) >= 1:
                    code = parts[0]
                    if len(code) == 4 and code.isdigit():
                        all_tickers.append(f"{code}{suffix}")
        except:
            continue
    
    if not all_tickers: return ['2330.TW', '2317.TW']
    return all_tickers

# --- 策略核心 ---
def analyze_stock(ticker):
    try:
        # 下載資料
        df = yf.download(ticker, period="6mo", interval="1d", auto_adjust=True, progress=False)
        
        # 🔥 修復 yfinance 雙層標題問題 (必備!)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        if len(df) < 60: return None

        df['Vol_MA60'] = df['Volume'].rolling(window=60).mean()
        df.ta.adx(length=14, append=True)
        df.ta.macd(append=True)
        
        check_window = df.iloc[-4:]
        today = df.iloc[-1]

        def has_crossover(subset, col_fast, col_slow):
            for i in range(1, len(subset)):
                curr, prev = subset.iloc[i], subset.iloc[i-1]
                if (curr[col_fast] > curr[col_slow]) and (prev[col_fast] <= prev[col_slow]):
                    return True
            return False

        if has_crossover(check_window, 'DMP_14', 'DMN_14') and \
           has_crossover(check_window, 'MACD_12_26_9', 'MACDs_12_26_9'):
            
            vol_msg = f"{today['Volume']/today['Vol_MA60']:.1f}x" if today['Vol_MA60'] > 0 else "N/A"
            return {
                "Symbol": ticker,
                "Price": f"{today['Close']:.2f}",
                "Vol": vol_msg
            }
    except:
        return None
    return None

# --- 主程式 ---
if __name__ == "__main__":
    print("=== 啟動選股程式 ===")
    
    # 1. 抓取所有清單
    sp500 = get_sp500_tickers_from_wiki()
    tw_all = get_tw_all_stocks_from_isin()
    all_targets = list(set(sp500 + tw_all))
    
    # 2. 計算數量 (為了顯示在 Telegram)
    total_count = len(all_targets)
    us_count = len(sp500)
    twse_count = len([x for x in tw_all if x.endswith('.TW')])
    otc_count = len([x for x in tw_all if x.endswith('.TWO')])
    
    print(f"分析目標: {total_count} 檔")
    
    results = []
    # 3. 開始掃描
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        future_to_stock = {executor.submit(analyze_stock, s): s for s in all_targets}
        for future in concurrent.futures.as_completed(future_to_stock):
            data = future.result()
            if data:
                results.append(data)
                print(f"發現: {data['Symbol']}")

    # --- 4. 準備 Telegram 訊息 ---
    
    # 這裡就是您要的統計資訊格式
    stats_info = f"\n📊 <b>掃描統計</b>\n總共分析 {total_count} 檔\n(🇺🇸美股: {us_count}, 🇹🇼上市: {twse_count}, 櫃: {otc_count})"
    
    msg_header = f"<b>【每日選股報告】</b>\n📅 日期: {datetime.date.today()}\n🔎 條件: 3日內雙金叉{stats_info}\n-----------------"
    
    if results:
        msg_body = ""
        for res in results:
            line = f"\n🔥 <b>{res['Symbol']}</b> (${res['Price']}) 量:{res['Vol']}"
            msg_body += line
        
        full_msg = msg_header + msg_body
        
        # 避免訊息過長
        if len(full_msg) > 4000: 
            full_msg = full_msg[:4000] + "\n...(以下省略)"
            
        send_telegram_notify(full_msg)
    else:
        # 即使沒選到股票，也會顯示統計資訊，讓您知道程式有在跑
        send_telegram_notify(msg_header + "\n😴 今日無符合條件的股票。")
