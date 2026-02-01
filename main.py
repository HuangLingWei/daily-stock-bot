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
    print("抓取台股清單...")
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

        # ==========================================
        # 1. 計算所有指標
        # ==========================================
        
        # A. 基礎指標 (原本的)
        df.ta.macd(append=True) # MACD
        df.ta.adx(length=14, append=True) # DMI (ADX, DMP, DMN)
        
        # B. 新戰隊指標 (KDJ, BB, EMA)
        # KDJ: K=9, Signal=3
        df.ta.kdj(length=9, signal=3, append=True) 
        # Bollinger Bands: Length=20, Std=2
        df.ta.bbands(length=20, std=2, append=True)
        # EMA: 60 (趨勢線)
        df.ta.ema(length=60, append=True)
        
        # 取最後一段資料來檢查
        check_window = df.iloc[-4:]
        today = df.iloc[-1]
        
        # ==========================================
        # 2. 定義判斷邏輯函數
        # ==========================================

        # 檢查交叉 (通用)
        def check_crossover(subset, fast_col, slow_col):
            for i in range(1, len(subset)):
                curr, prev = subset.iloc[i], subset.iloc[i-1]
                if (curr[fast_col] > curr[slow_col]) and (prev[fast_col] <= prev[slow_col]):
                    return True
            return False

        # 檢查 KDJ 低檔金叉 (K < 30 且 金叉)
        def check_kdj_low_gold(subset):
            # K_9_3, D_9_3
            for i in range(1, len(subset)):
                curr, prev = subset.iloc[i], subset.iloc[i-1]
                # 金叉
                if (curr['K_9_3'] > curr['D_9_3']) and (prev['K_9_3'] <= prev['D_9_3']):
                    # 檢查當下 K 值是否在低檔 (小於 30)
                    if curr['K_9_3'] < 30:
                        return True
            return False

        # ==========================================
        # 3. 策略判定
        # ==========================================
        
        # --- 策略 1 (原本): 0軸下 MACD 金叉 + DMI 金叉 ---
        # MACD 金叉 且 快線 < 0
        macd_gold = check_crossover(check_window, 'MACD_12_26_9', 'MACDs_12_26_9')
        macd_low = today['MACD_12_26_9'] < 0
        # DMI 金叉 (+DI > -DI)
        dmi_gold = check_crossover(check_window, 'DMP_14', 'DMN_14')
        
        is_strategy_1 = macd_gold and macd_low and dmi_gold

        # --- 策略 2 (新戰隊): 短線波段 ---
        strategy_2_type = None # 標記是哪種訊號 (突破 或 回調)

        # 2A. 抓「壓縮爆發」 (Close > 上軌 + ADX>20 + 多頭趨勢)
        # BBU_20_2.0 是布林上軌
        is_breakout = (today['Close'] > today['BBU_20_2.0'])
        is_adx_strong = (today['ADX_14'] > 20) and (today['DMP_14'] > today['DMN_14'])
        
        if is_breakout and is_adx_strong:
            strategy_2_type = "🚀 突破爆發"

        # 2B. 抓「回調轉折」 (KDJ低檔金叉 + 股價在 EMA60 之上)
        # 確保趨勢是向上的 (收盤價 > 60EMA)
        is_trend_up = today['Close'] > today['EMA_60']
        is_kdj_buy = check_kdj_low_gold(check_window)
        
        if is_trend_up and is_kdj_buy:
            strategy_2_type = "⚡️ 回調上車"

        # 如果任何一個策略符合，就回傳資料
        if is_strategy_1 or strategy_2_type:
            return {
                "Symbol": ticker,
                "Price": f"{today['Close']:.2f}",
                "Is_S1": is_strategy_1,
                "S2_Type": strategy_2_type
            }

    except:
        return None
    return None

# --- 訊息發送小幫手 ---
def build_message(title, condition_desc, data_list, stats_text):
    msg_header = (
        f"<b>【{title}】</b>\n"
        f"📅 {datetime.date.today()}\n"
        f"🔎 {condition_desc}\n"
        f"{stats_text}"
    )
    
    if data_list:
        msg_body = "\n-----------------"
        for res in data_list:
            # 根據不同清單顯示不同標註
            tag = ""
            if "S2_Type" in res and res["S2_Type"]:
                tag = f" ({res['S2_Type']})"
            
            line = f"\n🔥 <b>{res['Symbol']}</b> ${res['Price']}{tag}"
            msg_body += line
        return msg_header + msg_body
    else:
        return msg_header + "\n\n😴 今日無符合訊號。"

# --- 主程式 ---
if __name__ == "__main__":
    print("=== 啟動選股程式===")
    
    sp500 = get_sp500_tickers_from_wiki()
    tw_all = get_tw_all_stocks_from_isin()
    all_targets = list(set(sp500 + tw_all))
    
    total_cnt = len(all_targets)
    us_cnt = len(sp500)
    twse_cnt = len([x for x in tw_all if x.endswith('.TW')])
    otc_cnt = len([x for x in tw_all if x.endswith('.TWO')])
    
    stats_text = f"📊 分析 {total_cnt} 檔 (美:{us_cnt}, 台:{twse_cnt+otc_cnt})"
    print(f"分析目標: {total_cnt} 檔")
    
    list_strategy_1 = [] # 存放原本的
    list_strategy_2 = [] # 存放新戰隊
    
    # 執行掃描 (維持 20 執行緒)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_to_stock = {executor.submit(analyze_stock, s): s for s in all_targets}
        for future in concurrent.futures.as_completed(future_to_stock):
            data = future.result()
            if data:
                # 分類
                if data['Is_S1']:
                    list_strategy_1.append(data)
                
                if data['S2_Type']: # 如果有抓到 2A 或 2B
                    list_strategy_2.append(data)
                    
                print(f"發現: {data['Symbol']}")

    # --- 發送第一則 (原本的策略) ---
    msg1 = build_message(
        "策略一：底部反轉訊號", 
        "MACD(0軸下)金叉 + DMI金叉", 
        list_strategy_1, 
        stats_text
    )
    if len(msg1) > 4000: msg1 = msg1[:4000] + "\n...(略)"
    send_telegram_notify(msg1)
    
    # 休息一下，避免訊息順序錯亂
    time.sleep(2) 
    
    # --- 發送第二則 (新的 KDJ/BB 戰隊) ---
    msg2 = build_message(
        "策略二：短線波段戰隊", 
        "A:布林突破 / B:EMA上回調(KDJ)", 
        list_strategy_2, 
        stats_text
    )
    if len(msg2) > 4000: msg2 = msg2[:4000] + "\n...(略)"
    send_telegram_notify(msg2)
