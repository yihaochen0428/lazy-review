"""分时做 T 点位：多策略信号 + 共振合并

策略与回测（2026-11，10 只样本股、最近 5 个交易日、5 分钟线，信号均用“右侧已确认”
的 K 线，不含未来函数；目标/止损按各自配置，未触发的不计入胜负）：

1. 均价线回归：价格偏离分时均价线 ≥1% 反向操作，目标 0.4%、止损 0.6%，胜率约 58.8%（n=544）；
2. 0 轴规则：以昨收为零轴。价格在零轴下方运行后重新上穿零轴视为压力位做 T 卖，
   在零轴上方运行后回踩零轴视为支撑位做 T 买，目标/止损各 0.6%，胜率约 50%（n=48）；
3. MACD 背离：价格创新高但 DIF 不创新高→卖点，价格创新低但 DIF 不创新低→买点，
   目标 0.8%、止损 0.6%，胜率约 46.7%（n=30，样本偏少，需配合量能）；
4. 量价背离：价格创新高但成交量（3 根累计）不创新高→卖点，反向同理，
   目标 0.8%、止损 0.6%，胜率约 48.4%（n=95）。

多策略在同一位置共振时给出更高优先级；以上为历史统计，不构成收益承诺。
"""

STRATEGIES = {
    "vwap": {
        "name": "均价线回归", "short": "均价",
        "target": 0.4, "stop": 0.6, "win_rate": 58.8, "samples": 544,
        "note": "偏离分时均价线 1% 反向做T，回归即止盈",
    },
    "zero": {
        "name": "零轴支撑压力", "short": "0轴",
        "target": 0.6, "stop": 0.6, "win_rate": 50.0, "samples": 48,
        "note": "零轴下方上穿=卖，零轴上方回踩=买",
    },
    "macd": {
        "name": "MACD 背离", "short": "MACD",
        "target": 0.8, "stop": 0.6, "win_rate": 46.7, "samples": 30,
        "note": "价创新高而 DIF 走低/价创新低而 DIF 抬高",
    },
    "vol": {
        "name": "量价背离", "short": "量价",
        "target": 0.8, "stop": 0.6, "win_rate": 48.4, "samples": 95,
        "note": "价创新高但成交量不创新高，反向同理",
    },
}

COOLDOWN = 4  # 同策略同方向至少间隔 4 根 5 分钟线
MERGE_GAP = 2  # 两根K线内的同向信号视为共振
SWING_W = 2  # 摆动点右侧确认根数


def avg_price_series(bars):
    """分时均价线：累计成交额 / 累计成交量"""
    series = []
    cum_amount = 0.0
    cum_volume = 0.0
    for bar in bars:
        cum_amount += bar.get("amount") or 0
        cum_volume += bar.get("volume") or 0
        if cum_volume:
            series.append(round(cum_amount / cum_volume, 3))
        else:
            series.append(bar.get("close"))
    return series


def _time_of(bar):
    return bar.get("time") or str(bar.get("date") or "")[-5:]


def _swings(closes, w=SWING_W):
    """价格摆动点：确认需要右侧 w 根K线"""
    highs, lows = [], []
    n = len(closes)
    for i in range(w, n - w):
        if closes[i] == max(closes[i - w:i + w + 1]):
            highs.append(i)
        if closes[i] == min(closes[i - w:i + w + 1]):
            lows.append(i)
    return highs, lows


def _signal(bar, index, side, key, reason, extra=None):
    cfg = STRATEGIES[key]
    point = {
        "index": index,
        "time": _time_of(bar),
        "date": bar.get("date"),
        "price": bar.get("close"),
        "type": side,
        "label": "做T买点" if side == "buy" else "做T卖点",
        "strategy": cfg["name"],
        "strategy_key": key,
        "strategy_shorts": [cfg["short"]],
        "reason": reason,
        "target": cfg["target"],
        "stop": cfg["stop"],
        "win_rate": cfg["win_rate"],
        "confluence": 1,
    }
    if extra:
        point.update(extra)
    return point


def _vwap_points(bars, vwap):
    points = []
    last_buy = last_sell = -999
    for i, bar in enumerate(bars):
        if i < 3 or not vwap[i]:
            continue
        close = bar.get("close") or 0
        if not close:
            continue
        dev = (close / vwap[i] - 1) * 100
        if dev <= -1.0 and i - last_buy >= COOLDOWN:
            points.append(_signal(bar, i, "buy", "vwap",
                                  f"低于分时均价线 {abs(dev):.2f}%，目标回归 {vwap[i]:.2f}",
                                  {"deviation": round(dev, 2), "vwap": vwap[i]}))
            last_buy = i
        elif dev >= 1.0 and i - last_sell >= COOLDOWN:
            points.append(_signal(bar, i, "sell", "vwap",
                                  f"高于分时均价线 {dev:.2f}%，目标回落 {vwap[i]:.2f}",
                                  {"deviation": round(dev, 2), "vwap": vwap[i]}))
            last_sell = i
    return points


def _zero_points(bars, prev_close):
    """零轴（昨收）规则：下方运行后上穿=卖，上方运行后回踩=买"""
    if not prev_close:
        return []
    points = []
    last_buy = last_sell = -999
    for i in range(4, len(bars)):
        prev = bars[i - 1].get("close")
        cur = bars[i].get("close")
        if prev is None or cur is None:
            continue
        below = sum(1 for b in bars[i - 5:i] if (b.get("close") or 0) < prev_close)
        above = 5 - below
        if prev < prev_close <= cur and below >= 4 and i - last_sell >= COOLDOWN:
            points.append(_signal(bars[i], i, "sell", "zero",
                                  f"零轴下方运行后上穿 {prev_close:.2f}，该位置为压力，做T卖",
                                  {"deviation": round((cur / prev_close - 1) * 100, 2)}))
            last_sell = i
        elif prev > prev_close >= cur and above >= 4 and i - last_buy >= COOLDOWN:
            points.append(_signal(bars[i], i, "buy", "zero",
                                  f"零轴上方运行后回踩 {prev_close:.2f}，该位置为支撑，做T买",
                                  {"deviation": round((cur / prev_close - 1) * 100, 2)}))
            last_buy = i
    return points


def _divergence_points(bars, dif, key):
    """MACD / 量价背离，信号落在摆动点右侧确认K线上"""
    points = []
    closes = [b.get("close") or 0 for b in bars]
    highs, lows = _swings(closes)
    vol3 = [sum((bars[x].get("volume") or 0) for x in range(max(0, i - 2), i + 1)) for i in range(len(bars))]
    last_buy = last_sell = -999
    for j in range(1, len(highs)):
        a, b = highs[j - 1], highs[j]
        c = b + SWING_W
        if c >= len(bars):
            continue
        if key == "macd":
            hit = closes[b] > closes[a] and dif[b] < dif[a]
            reason = f"股价新高但 DIF {dif[a]:.2f}→{dif[b]:.2f} 走低，顶背驰"
        else:
            hit = closes[b] > closes[a] and vol3[b] < vol3[a]
            reason = "股价新高但成交量不创新高，量价顶背离"
        if hit and c - last_sell >= COOLDOWN:
            points.append(_signal(bars[c], c, "sell", key, reason))
            last_sell = c
    for j in range(1, len(lows)):
        a, b = lows[j - 1], lows[j]
        c = b + SWING_W
        if c >= len(bars):
            continue
        if key == "macd":
            hit = closes[b] < closes[a] and dif[b] > dif[a]
            reason = f"股价新低但 DIF {dif[a]:.2f}→{dif[b]:.2f} 抬高，底背驰"
        else:
            hit = closes[b] < closes[a] and vol3[b] < vol3[a]
            reason = "股价新低但成交量萎缩，抛压减弱"
        if hit and c - last_buy >= COOLDOWN:
            points.append(_signal(bars[c], c, "buy", key, reason))
            last_buy = c
    return points


def _merge(points):
    """同方向且相邻 2 根内的信号合并为共振点"""
    points.sort(key=lambda p: (p["index"], p["type"]))
    merged = []
    for p in points:
        last = merged[-1] if merged else None
        if (last and last["type"] == p["type"] and p["index"] - last["index"] <= MERGE_GAP):
            last["strategy_shorts"] = last["strategy_shorts"] + [p["strategy_shorts"][0]]
            last["strategy"] = " + ".join(last["strategy_shorts"])
            last["confluence"] = len(last["strategy_shorts"])
            last["reason"] = last["reason"] + "；" + p["reason"]
            last["win_rate"] = max(last["win_rate"], p["win_rate"])
            continue
        merged.append(p)
    return merged


def t_points(bars, prev_close=None, dif=None, max_points=16):
    if not bars:
        return {"points": [], "vwap": [], "stats": STRATEGIES, "summary": "分时数据不可用", "latest": None}
    vwap = avg_price_series(bars)
    points = []
    points += _vwap_points(bars, vwap)
    points += _zero_points(bars, prev_close)
    if dif:
        points += _divergence_points(bars, dif, "macd")
        points += _divergence_points(bars, dif, "vol")
    points = _merge(points)
    if len(points) > max_points:
        points = points[-max_points:]
    latest = points[-1] if points else None
    summary = "当前未触发明确做T信号，价格在均价线与零轴附近震荡"
    if latest:
        summary = (f"最近信号：{latest['time']} {latest['label']}（{latest['strategy']}，"
                   f"{latest['price']:.2f}）")
        if latest["confluence"] >= 2:
            summary += f"，{latest['confluence']} 个策略共振"
    return {
        "points": points,
        "vwap": vwap,
        "stats": STRATEGIES,
        "summary": summary,
        "latest": latest,
    }
