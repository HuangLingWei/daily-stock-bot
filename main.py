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

# --- 策略核心 (4H 級別修正版) ---
def analyze_stock(ticker):
    try:
        # 下載 1小時資料，最大範圍 730天
        df = yf.download(ticker, period="730d", interval="1h", auto_adjust=True, progress=False, threads=False)
        
        # 處理雙層標題
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        if df.empty: return None

        # 🔥 修改處：合成 4小時 (Resample 4h)
        # 這樣 K 棒數量會比 8h 多一倍，更容易形成技術指標
        ohlc_dict = {
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }
        
        try:
            # 將 1h 資料合成 4h
            df_4h = df.resample('4h').agg(ohlc_dict).dropna()
        except:
            return None 

        # 檢查長度: EMA60 至少需要 60 根 + 前置運算，設 65 比較保險
        if len(df_4h) < 65: return None
        
        # 使用 4h 資料進行計算
        df = df_4h 

        # ==========================================
        # 1. 計算所有指標 (4H級別)
        # ==========================================
        
        df.ta.macd(append=True) 
        df.ta.adx(length=14, append=True) 
        df.ta.kdj(length=9, signal=3, append=True) 
        df.ta.bbands(length=20, std=2, append=True)
        df.ta.ema(length=60, append=True)
        
        # 取最後一段資料來檢查
        check_window = df.iloc[-4:]
        today = df.iloc[-1]
        
        # ==========================================
        # 2. 定義判斷邏輯函數
        # ==========================================

        def check_crossover(subset, fast_col, slow_col):
            for i in range(1, len(subset)):
                curr, prev = subset.iloc[i], subset.iloc[i-1]
                if (curr[fast_col] > curr[slow_col]) and (prev[fast_col] <= prev[slow_col]):
                    return True
            return False

        def check_kdj_low_gold(subset):
            for i in range(1, len(subset)):
                curr, prev = subset.iloc[i], subset.iloc[i-1]
                # 金叉
                if (curr['K_9_3'] > curr['D_9_3']) and (prev['K_9_3'] <= prev['D_9_3']):
                    # 低檔標準：4H 級別設 35
                    if curr['K_9_3'] < 35:
                        return True
            return False

        # ==========================================
        # 3. 策略判定
        # ==========================================
        
        # --- 策略 1: 0軸下 MACD 金叉 + DMI 金叉 ---
        macd_gold = check_crossover(check_window, 'MACD_12_26_9', 'MACDs_12_26_9')
        # MACD 柱狀體翻紅或快線向上，且在 0 軸下
        macd_low = today['MACD_12_26_9'] < 0
        dmi_gold = check_crossover(check_window, 'DMP_14', 'DMN_14')
        
        is_strategy_1 = macd_gold and macd_low and dmi_gold

        # --- 策略 2: 短線波段戰隊 ---
        strategy_2_type = None 

        # 2A. 抓「壓縮爆發」
        # 收盤價突破布林上軌
        is_breakout = (today['Close'] > today['BBU_20_2.0'])
        # ADX > 20 代表有趨勢，且是多方趨勢 (+DI > -DI)
        is_adx_strong = (today['ADX_14'] > 20) and (today['DMP_14'] > today['DMN_14'])
        
        if is_breakout and is_adx_strong:
            strategy_2_type = "🚀 4H突破"

        # 2B. 抓「回調轉折」
        # 股價在 EMA60 之上 (多頭)
        is_trend_up = today['Close'] > today['EMA_60']
        # KDJ 低檔金叉
        is_kdj_buy = check_kdj_low_gold(check_window)
        
        if is_trend_up and is_kdj_buy:
            strategy_2_type = "⚡️ 4H回調"

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
        f"⏳ 級別: 4小時 (4H)\n"
        f"🔎 {condition_desc}\n"
        f"{stats_text}"
    )
    
    if data_list:
        msg_body = "\n-----------------"
        for res in data_list:
            tag = ""
            if "S2_Type" in res and res["S2_Type"]:
                tag = f" ({res['S2_Type']})"
            
            line = f"\n🔥 <b>{res['Symbol']}</b> (${res['Price']}{tag})"
            msg_body += line
        return msg_header + msg_body
    else:
        return msg_header + "\n\n😴 今日無符合訊號。"

# --- 主程式 ---
if __name__ == "__main__":
    print("=== 啟動選股程式 (4H級別版) ===")
    
    sp500 = get_sp500_tickers_from_wiki()
    tw_all = get_tw_all_stocks_from_isin()
    all_targets = list(set(sp500 + tw_all))
    
    total_cnt = len(all_targets)
    us_cnt = len(sp500)
    twse_cnt = len([x for x in tw_all if x.endswith('.TW')])
    otc_cnt = len([x for x in tw_all if x.endswith('.TWO')])
    
    stats_text = f"📊 分析 {total_cnt} 檔 (美:{us_cnt}, 台:{twse_cnt+otc_cnt})"
    print(f"分析目標: {total_cnt} 檔")
    
    list_strategy_1 = [] 
    list_strategy_2 = [] 
    
    # 維持 20 執行緒
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_to_stock = {executor.submit(analyze_stock, s): s for s in all_targets}
        for future in concurrent.futures.as_completed(future_to_stock):
            data = future.result()
            if data:
                if data['Is_S1']:
                    list_strategy_1.append(data)
                if data['S2_Type']:
                    list_strategy_2.append(data)
                    
                print(f"發現: {data['Symbol']}")

    msg1 = build_message(
        "策略一：4H底部反轉", 
        "MACD(0軸下) + DMI金叉", 
        list_strategy_1, 
        stats_text
    )
    if len(msg1) > 4000: msg1 = msg1[:4000] + "\n...(略)"
    send_telegram_notify(msg1)
    
    time.sleep(2) 
    
    msg2 = build_message(
        "策略二：4H波段戰隊", 
        "A:布林突破 / B:EMA回調", 
        list_strategy_2, 
        stats_text
    )
    if len(msg2) > 4000: msg2 = msg2[:4000] + "\n...(略)"
    send_telegram_notify(msg2)
