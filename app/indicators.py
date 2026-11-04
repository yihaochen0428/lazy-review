"""技术指标计算：MA / MACD / KDJ / RSI / 斐波那契 / 简化波浪"""


def ma(values, n):
    out, acc = [], 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= n:
            acc -= values[i - n]
        out.append(round(acc / n, 3) if i >= n - 1 else None)
    return out


def ema(values, n):
    k = 2.0 / (n + 1)
    out, prev = [], None
    for v in values:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < 2:
        return {"dif": [], "dea": [], "hist": []}
    fast_line, slow_line = ema(closes, fast), ema(closes, slow)
    dif = [f - s for f, s in zip(fast_line, slow_line)]
    dea = ema(dif, signal)
    hist = [d - e for d, e in zip(dif, dea)]
    return {
        "dif": [round(v, 3) for v in dif],
        "dea": [round(v, 3) for v in dea],
        "hist": [round(v, 3) for v in hist],
    }


def kdj(highs, lows, closes, n=9, m1=3, m2=3):
    k_list, d_list, j_list = [], [], []
    k_prev, d_prev = 50.0, 50.0
    for i in range(len(closes)):
        if i < n - 1:
            k_list.append(None)
            d_list.append(None)
            j_list.append(None)
            continue
        window_high = max(highs[i - n + 1:i + 1])
        window_low = min(lows[i - n + 1:i + 1])
        rsv = 50.0 if window_high == window_low else (closes[i] - window_low) / (window_high - window_low) * 100
        k_prev = (m1 - 1) * k_prev / m1 + rsv / m1
        d_prev = (m2 - 1) * d_prev / m2 + k_prev / m2
        k_list.append(round(k_prev, 2))
        d_list.append(round(d_prev, 2))
        j_list.append(round(3 * k_prev - 2 * d_prev, 2))
    return {"k": k_list, "d": d_list, "j": j_list}


def rsi(closes, n=14):
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, n + 1):
        diff = closes[i] - closes[i - 1]
        gains += max(diff, 0)
        losses += max(-diff, 0)
    avg_gain, avg_loss = gains / n, losses / n
    out[n] = round(100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss), 2)
    for i in range(n + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (n - 1) + max(diff, 0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-diff, 0)) / n
        out[i] = round(100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss), 2)
    return out


FIB_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]


def fib_levels(high, low, rising=True):
    """返回从高点向下回撤的关键位（rising 表示当前处于上升段）"""
    span = high - low
    levels = []
    for ratio in FIB_RATIOS:
        price = high - span * ratio if rising else low + span * ratio
        levels.append({"ratio": ratio, "price": round(price, 2), "label": f"{ratio:.3f}"})
    return levels


def zigzag(bars, threshold=0.04, max_points=40):
    """简化波浪识别：以收盘价为基础，按幅度阈值提取高低点"""
    if len(bars) < 10:
        return []
    closes = [b["close"] for b in bars]
    points = [{"index": 0, "price": closes[0], "type": "low" if closes[-1] > closes[0] else "high",
               "date": bars[0]["date"]}]
    direction = 0  # 1 上行 / -1 下行
    extreme_idx, extreme_price = 0, closes[0]
    for i in range(1, len(closes)):
        price = closes[i]
        if direction >= 0 and price > extreme_price:
            extreme_idx, extreme_price = i, price
            direction = 1
        elif direction <= 0 and price < extreme_price:
            extreme_idx, extreme_price = i, price
            direction = -1
        if direction == 1 and price <= extreme_price * (1 - threshold):
            points.append({"index": extreme_idx, "price": round(extreme_price, 2), "type": "high",
                           "date": bars[extreme_idx]["date"]})
            extreme_idx, extreme_price, direction = i, price, -1
        elif direction == -1 and price >= extreme_price * (1 + threshold):
            points.append({"index": extreme_idx, "price": round(extreme_price, 2), "type": "low",
                           "date": bars[extreme_idx]["date"]})
            extreme_idx, extreme_price, direction = i, price, 1
    points.append({"index": len(closes) - 1, "price": round(closes[-1], 2),
                   "type": "high" if direction >= 0 else "low", "date": bars[-1]["date"]})
    if len(points) > max_points:
        points = points[-max_points:]
    return points


def pct(a, b):
    if not b:
        return None
    return round((a - b) / b * 100, 2)
