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

# --- 策略核心 ---
def analyze_stock(ticker):
    try:
        # 下載資料 (threads=False 確保數據正確)
        df = yf.download(ticker, period="6mo", interval="1d", auto_adjust=True, progress=False, threads=False)
        
        # 處理雙層標題
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        if len(df) < 60: return None

        # 計算指標
        df['Vol_MA20'] = df['Volume'].rolling(window=20).mean() # 20日均量
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

        # 基礎條件：雙金叉
        is_basic_match = has_crossover(check_window, 'DMP_14', 'DMN_14') and \
                         has_crossover(check_window, 'MACD_12_26_9', 'MACDs_12_26_9')
        
        if is_basic_match:
            # 進階條件：成交量 > 20日均量
            is_vol_match = today['Volume'] > today['Vol_MA20']
            
            return {
                "Symbol": ticker,
                "Price": f"{today['Close']:.2f}",
                "Vol_Strong": is_vol_match # 標記是否符合量增
            }
    except:
        return None
    return None

# --- 訊息發送小幫手 ---
def build_message(title, data_list, stats_text):
    msg_header = (
        f"<b>【每日選股報告 - {title}】</b>\n"
        f"📅 日期: {datetime.date.today()}\n"
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
    print("=== 啟動選股程式 (雙重篩選版) ===")
    
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
    
    results_basic = [] # 存放第一則 (雙金叉)
    results_vol = []   # 存放第二則 (雙金叉 + 量增)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_to_stock = {executor.submit(analyze_stock, s): s for s in all_targets}
        for future in concurrent.futures.as_completed(future_to_stock):
            data = future.result()
            if data:
                # 1. 加入基礎清單
                results_basic.append(data)
                
                # 2. 如果符合量增，也加入精選清單
                if data['Vol_Strong']:
                    results_vol.append(data)
                
                print(f"發現: {data['Symbol']} (量增: {data['Vol_Strong']})")

    # --- 發送第一則訊息：原始策略 ---
    msg1 = build_message("純雙金叉版", results_basic, stats_text)
    if len(msg1) > 4000: msg1 = msg1[:4000] + "\n...(以下省略)"
    send_telegram_notify(msg1)
    
    # --- 發送第二則訊息：量增濾網 ---
    # 稍微停頓一下確保順序
    import time
    time.sleep(2) 
    
    msg2 = build_message("雙金叉+量增版", results_vol, stats_text)
    if len(msg2) > 4000: msg2 = msg2[:4000] + "\n...(以下省略)"
    send_telegram_notify(msg2)
