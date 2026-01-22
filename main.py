import requests
import pandas as pd
import pandas_ta as ta
import yfinance as yf
from io import StringIO
import concurrent.futures
import datetime
import os
import urllib3
import time

# 關閉 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 設定 Telegram
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

# --- 策略核心 (修正重點) ---
def analyze_stock(ticker):
    try:
        # 下載資料
        df = yf.download(ticker, period="6mo", interval="1d", auto_adjust=True, progress=False, threads=False)
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        if len(df) < 60: return None

        # 計算指標
        df['Vol_MA20'] = df['Volume'].rolling(window=20).mean()
        df.ta.adx(length=14, append=True)
        df.ta.macd(append=True)
        
        # 取最後 4 天來檢查
        check_window = df.iloc[-4:]
        today = df.iloc[-1]

        # --- 定義 DMI 金叉 (+DI 上穿 -DI) ---
        def check_dmi_crossover(subset):
            # DMP(+DI), DMN(-DI)
            for i in range(1, len(subset)):
                curr, prev = subset.iloc[i], subset.iloc[i-1]
                # 黃金交叉: 今天 +DI > -DI 且 昨天 +DI <= -DI
                if (curr['DMP_14'] > curr['DMN_14']) and (prev['DMP_14'] <= prev['DMN_14']):
                    return True
            return False

        # --- 定義 MACD 低檔金叉 (快線上穿慢線 且 在0軸下) ---
        def check_macd_crossover(subset):
            # MACD_12_26_9(快線), MACDs_12_26_9(慢線)
            for i in range(1, len(subset)):
                curr, prev = subset.iloc[i], subset.iloc[i-1]
                
                # 1. 交叉檢查: 快線 > 慢線 (今) 且 快線 <= 慢線 (昨)
                is_cross = (curr['MACD_12_26_9'] > curr['MACDs_12_26_9']) and \
                           (prev['MACD_12_26_9'] <= prev['MACDs_12_26_9'])
                
                # 2. 0軸檢查: 快線要在 0 以下 (符合您的新要求)
                is_below_zero = curr['MACD_12_26_9'] < 0
                
                if is_cross and is_below_zero:
                    return True
            return False

        # 執行判斷
        is_dmi_gold = check_dmi_crossover(check_window)
        is_macd_gold = check_macd_crossover(check_window)
        
        # 基礎條件：兩個都必須金叉
        if is_dmi_gold and is_macd_gold:
            # 進階條件：成交量 > 20日均量
            is_vol_match = today['Volume'] > today['Vol_MA20']
            
            return {
                "Symbol": ticker,
                "Price": f"{today['Close']:.2f}",
                "Vol_Strong": is_vol_match
            }
    except:
        return None
    return None

# --- 訊息發送小幫手 ---
def build_message(title, data_list, stats_text):
    msg_header = (
        f"<b>【每日選股報告 - {title}】</b>\n"
        f"📅 日期: {datetime.date.today()}\n"
        f"🔎 條件: 0軸下MACD金叉 + DMI金叉\n"
        f"{stats_text}"
    )
    if data_list:
        msg_body = f"\n以下是符合【{title}】的清單\n-----------------"
        for res in data_list:
            line = f"\n🔥 <b>{res['Symbol']}</b> (${res['Price']})"
            msg_body += line
        return msg_header + msg_body
    else:
        return msg_header + f"\n\n😴 今日無符合【{title}】的股票。"

# --- 主程式 ---
if __name__ == "__main__":
    print("=== 啟動選股程式 (MACD 0軸下修正版) ===")
    
    sp500 = get_sp500_tickers_from_wiki()
    tw_all = get_tw_all_stocks_from_isin()
    all_targets = list(set(sp500 + tw_all))
    
    total_cnt = len(all_targets)
    us_cnt = len(sp500)
    twse_cnt = len([x for x in tw_all if x.endswith('.TW')])
    otc_cnt = len([x for x in tw_all if x.endswith('.TWO')])
    
    stats_text = (
        f"📊 掃描統計總共分析 {total_cnt} 檔\n"
        f"(美股: {us_cnt} ,上市: {twse_cnt}, 上櫃: {otc_cnt} )"
    )
    print(f"分析目標: {total_cnt} 檔")
    
    results_basic = []
    results_vol = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_to_stock = {executor.submit(analyze_stock, s): s for s in all_targets}
        for future in concurrent.futures.as_completed(future_to_stock):
            data = future.result()
            if data:
                results_basic.append(data)
                if data['Vol_Strong']:
                    results_vol.append(data)
                print(f"發現: {data['Symbol']}")

    # 發送第一則 (低檔雙金叉)
    msg1 = build_message("低檔起漲版", results_basic, stats_text)
    if len(msg1) > 4000: msg1 = msg1[:4000] + "\n...(以下省略)"
    send_telegram_notify(msg1)
    
    # 停頓2秒
    time.sleep(2) 
    
    # 發送第二則 (低檔雙金叉 + 量增)
    msg2 = build_message("低檔噴發版(量增)", results_vol, stats_text)
    if len(msg2) > 4000: msg2 = msg2[:4000] + "\n...(以下省略)"
    send_telegram_notify(msg2)
