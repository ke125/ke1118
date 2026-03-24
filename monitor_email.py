import time
import numpy as np
import requests
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ===================== 配置区 =====================
PROXY = ""
SCAN_INTERVAL = 900  # 15分钟 = 900秒
proxies = {"http": PROXY, "https": PROXY} if PROXY else None

# 主流币，确保不被过滤掉
MAIN_COINS = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT"}

# ===================== 邮箱配置 =====================
MAIL_HOST = "smtp.qq.com"
MAIL_PORT = 465
MAIL_SENDER = "24458229@qq.com"
MAIL_PASS = "fndzanabvaaxbgci"
MAIL_RECEIVER = "24458229@qq.com"

def send_email(title, content):
    try:
        msg = MIMEText(content, "plain", "utf-8")
        msg["From"] = MAIL_SENDER
        msg["To"] = MAIL_RECEIVER
        msg["Subject"] = title
        with smtplib.SMTP_SSL(MAIL_HOST, MAIL_PORT, timeout=10) as server:
            server.login(MAIL_SENDER, MAIL_PASS)
            server.sendmail(MAIL_SENDER, [MAIL_RECEIVER], msg.as_string())
        print("✅ 邮件发送成功")
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")

# ===================== 网络请求（带超时防卡死） =====================
session = requests.Session()
if proxies:
    session.proxies = proxies
session.verify = False
session.timeout = 10  # 全局 10 秒超时，API 没响应就跳过，不卡死

def get_top_symbols():
    """
    保持扫涨幅前 200 个币 + 主流币，加异常捕获
    """
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"获取币种列表失败: {e}")
        return []
    
    symbols = []
    for item in data:
        if isinstance(item, dict) and "symbol" in item and item["symbol"].endswith("USDT"):
            try:
                price_change_pct = float(item["priceChangePercent"])
                symbols.append( (-price_change_pct, item["symbol"]) )
            except (KeyError, ValueError):
                continue
    
    # 排序并取前 200
    symbols.sort()
    top_200 = [s for (_, s) in symbols[:200]]
    final_symbols = list(set(top_200 + list(MAIN_COINS)))
    
    print(f"📊 本次扫描币种数: {len(final_symbols)}")
    return final_symbols

def fetch_ohlcv(sym, interval, limit):
    """
    单个币种请求失败，直接跳过，不影响整轮
    """
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={interval}&limit={limit}"
    try:
        d = session.get(url, timeout=10).json()
        return np.array([[float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in d])
    except Exception as e:
        print(f"⚠️ 跳过 {sym} (请求超时/失败): {e}")
        return None

def ma(arr, n):
    return np.convolve(arr, np.ones(n)/n, mode="valid")[-1]

# ===================== 指标与形态 =====================
def check_ma33_slope(close_series):
    ma33_series = []
    for i in range(10):
        if len(close_series) < 33 + i:
            break
        window = close_series[-(33+i):-i] if i > 0 else close_series[-33:]
        ma33_series.append(np.mean(window))
    if len(ma33_series) < 10:
        return False
    slope_all = np.mean(np.diff(ma33_series))
    last_3 = ma33_series[-3:]
    slope_last3 = np.mean(np.diff(last_3))
    ok_overall = slope_all >= -0.0002
    ok_last3 = slope_last3 >= -0.00005
    return ok_overall and ok_last3

def is_bullish(ohlc):
    if len(ohlc) < 35:
        return False
    close = ohlc[:, 3]
    ma5 = ma(close, 5)
    ma10 = ma(close, 10)
    ma33 = ma(close, 33)
    return (ma5 > ma10 > ma33) and check_ma33_slope(close)

def check_gold_pit(close15, vol15, ma33):
    curr = close15[-1]
    if not (ma33*0.995 < curr < ma33*1.01):
        return False
    if not check_ma33_slope(close15):
        return False

    low_part = close15[-25:-8]
    pit_low = min(low_part)
    pit_low_idx = np.argmin(low_part) + len(close15) - 25
    if pit_low > ma33:
        return False

    post_pit = close15[pit_low_idx:]
    post_pit_vol = vol15[pit_low_idx:]
    if len(post_pit) < 8:
        return False

    vol_avg = np.mean(vol15[-20:-3])
    stand_idx = None
    for i in range(len(post_pit)):
        if post_pit[i] > ma33 and post_pit_vol[i] > vol_avg*1.1:
            stand_idx = i
            break
    if stand_idx is None:
        return False

    pullback_part = post_pit[stand_idx:]
    pullback_vol_part = post_pit_vol[stand_idx:]
    if len(pullback_part) < 3:
        return False
    if min(pullback_part) < ma33*0.995:
        return False
    if np.mean(pullback_vol_part) > np.mean(post_pit_vol[:stand_idx])*0.9:
        return False

    recent_high = max(pullback_part[:-3])
    if curr <= recent_high:
        return False
    if vol15[-1] < vol_avg*1.1:
        return False

    return True

def check_cup_handle(close15, vol15, ma33, sym):
    curr = close15[-1]
    if not (ma33*0.995 < curr < ma33*1.01):
        return False
    if not check_ma33_slope(close15):
        return False

    cup = close15[-50:-12]
    if len(cup) < 35:
        return False
    cup_low = min(cup)
    cup_neck = max(cup)
    cup_low_idx = np.argmin(cup)

    left_trend = np.mean(np.diff(cup[:cup_low_idx+1]))
    right_trend = np.mean(np.diff(cup[cup_low_idx:]))
    if not (left_trend < 0 and right_trend > 0):
        return False

    handle = close15[-12:-3]
    if min(handle) < cup_low:
        return False
    if (cup_neck - min(handle))/cup_neck > 0.05:
        return False

    if np.mean(vol15[-12:-3]) > np.mean(vol15[-50:-12])*0.85:
        return False

    if curr < cup_neck:
        return False

    vol_avg = np.mean(vol15[-20:-5])
    threshold = 1.2 if sym in MAIN_COINS else 1.35
    return vol15[-1] > vol_avg*threshold

# ===================== 主逻辑（加了异常捕获，单个币种失败不影响整轮） =====================
def analyze(sym):
    ohl4 = fetch_ohlcv(sym, "4h", 40)
    ohl1 = fetch_ohlcv(sym, "1h", 50)
    ohl15 = fetch_ohlcv(sym, "15m", 70)
    if ohl4 is None or ohl1 is None or ohl15 is None:
        return 0, None, None, None, ""

    if not is_bullish(ohl4):
        return 0, None, None, None, ""
    if not is_bullish(ohl1):
        return 0, None, None, None, ""
    if not is_bullish(ohl15):
        return 0, None, None, None, ""

    close15 = ohl15[:, 3]
    vol15 = ohl15[:, 4]
    ma33 = ma(close15, 33)

    is_gold = check_gold_pit(close15, vol15, ma33)
    is_cup = check_cup_handle(close15, vol15, ma33, sym)

    if not (is_gold or is_cup):
        return 0, None, None, None, ""

    pattern = "黄金坑(三重共振)" if is_gold else "杯柄(三重共振)"
    neck = ma33
    sl = neck*0.98
    tp = neck*1.06
    score = 95 if sym in MAIN_COINS else 99
    return score, neck, sl, tp, pattern

def main():
    print("="*60)
    print("📩 【防卡死版】4h+1h+15m三重共振黄金坑+杯柄")
    print("💡 配置：15分钟扫描一次 | 扫描涨幅前200币种")
    print("电脑关机也能跑，自动发邮件")
    print("="*60)
    last = set()

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now}] 开始扫描...")
        
        symbols = get_top_symbols()
        res = []
        
        for s in symbols:
            try:
                score, neck, sl, tp, pat = analyze(s)
                if score > 50:
                    res.append((-score, s, score, neck, sl, tp, pat))
            except Exception as e:
                print(f"⚠️ 跳过 {s} (分析失败): {e}")
                continue  # 单个币种失败，直接跳过，不卡死整轮

        res.sort()
        final = res[:5]
        
        if final:
            new_sig = []
            for _, s, sc, n, sl, tp, pat in final:
                if s not in last:
                    new_sig.append((s, sc, n, sl, tp, pat))
            
            if new_sig:
                title = f"【云端真信号】{len(new_sig)}个"
                content = ""
                for s, sc, n, sl, tp, pat in new_sig:
                    content += f"【{pat}】{s}\n得分:{sc}\n颈线:{n:.6f}\n止损:{sl:.6f}\n止盈:{tp:.6f}\n\n"
                send_email(title, content)
            
            last = set(s for _, s, _, _, _, _, _ in final)
            print(f"✅ 本轮符合: {len(final)}")
        else:
            print("ℹ 暂无真信号")

        print(f"ℹ 等待 {SCAN_INTERVAL/60} 分钟后继续扫描...")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
