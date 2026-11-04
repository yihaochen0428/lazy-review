"""缠论分析：K线包含合并 → 分型 → 笔 → 中枢 → 买卖点 → 未来推演

计算口径：
1. 先做包含关系合并（按前两根合并K线的方向取极值），消除噪音K线；
2. 在合并K线上识别顶/底分型，并把分型映射回原始K线下标，保证图表能回溯拟合历史；
3. 分型交替成笔（顶底互换、满足最小间隔），未确认的最新分型单独作为“候选”结构；
4. 连续三笔重叠区间构成中枢，中枢延伸时动态收缩上下沿；
5. 买卖点用同向笔端点比较 MACD DIF 背离，配合中枢位置给出三买/三卖；
6. 额外输出 future：结构、支撑压力、可买/可卖的具体触发价位与失效条件。

所有价格均做过 NaN 防护与四舍五入，输出仅作结构参考，不构成交易建议。
"""

MIN_BARS = 12
MIN_GAP = 3  # 合并K线上两分型的最小间隔，避免一笔跨度过短


def _num(value, default=None):
    """把 None / NaN / inf 统一转换成可用数值"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v or v in (float("inf"), float("-inf")):
        return default
    return v


def _price(value):
    v = _num(value)
    return round(v, 2) if v is not None else None


def _merge(bars):
    """包含关系合并，返回合并K线，保留原始下标区间"""
    merged = []
    for i, bar in enumerate(bars):
        high, low = _num(bar.get("high")), _num(bar.get("low"))
        if high is None or low is None:
            continue
        item = {"high": high, "low": low, "start": i, "end": i, "date": bar.get("date")}
        if merged:
            prev = merged[-1]
            prev2 = merged[-2] if len(merged) >= 2 else None
            up = True if prev2 is None else prev["high"] > prev2["high"]
            contained = ((high >= prev["high"] and low <= prev["low"])
                         or (high <= prev["high"] and low >= prev["low"]))
            if contained:
                # 向上取高高、向下取低低
                item["high"] = max(high, prev["high"]) if up else min(high, prev["high"])
                item["low"] = max(low, prev["low"]) if up else min(low, prev["low"])
                item["start"] = prev["start"]
                item["date"] = bar.get("date") or prev["date"]
                merged[-1] = item
                continue
        merged.append(item)
    return merged


def _fractals(merged):
    """三根合并K线识别顶/底分型，index 取分型K线的原始下标"""
    out = []
    for i in range(1, len(merged) - 1):
        prev, cur, nxt = merged[i - 1], merged[i], merged[i + 1]
        if cur["high"] > prev["high"] and cur["high"] > nxt["high"]:
            out.append({"index": cur["end"], "date": cur["date"], "price": _price(cur["high"]), "type": "top"})
        elif cur["low"] < prev["low"] and cur["low"] < nxt["low"]:
            out.append({"index": cur["end"], "date": cur["date"], "price": _price(cur["low"]), "type": "bottom"})
    out.sort(key=lambda x: x["index"])
    # 同类型相邻分型只保留更极端的一根
    dedup = []
    for f in out:
        if dedup and dedup[-1]["type"] == f["type"]:
            prev = dedup[-1]
            if ((f["type"] == "top" and f["price"] >= prev["price"])
                    or (f["type"] == "bottom" and f["price"] <= prev["price"])):
                dedup[-1] = f
            continue
        dedup.append(f)
    return dedup


def _strokes(fractals):
    """分型交替成笔，返回已确认的笔"""
    strokes = []
    start = None
    for f in fractals:
        if start is None:
            start = f
            continue
        if f["type"] == start["type"]:
            if ((f["type"] == "top" and f["price"] >= start["price"])
                    or (f["type"] == "bottom" and f["price"] <= start["price"])):
                start = f
            continue
        if f["index"] - start["index"] < MIN_GAP:
            continue
        if start["type"] == "bottom" and f["price"] <= start["price"]:
            continue
        if start["type"] == "top" and f["price"] >= start["price"]:
            continue
        strokes.append({
            "from": start,
            "to": f,
            "direction": "up" if start["type"] == "bottom" else "down",
            "high": _price(max(start["price"], f["price"])),
            "low": _price(min(start["price"], f["price"])),
            "range": [_price(min(start["price"], f["price"]))],
        })
        strokes[-1]["range"] = [strokes[-1]["low"], strokes[-1]["high"]]
        start = f
    return strokes, start


def _centers(strokes):
    centers = []
    i = 0
    while i <= len(strokes) - 3:
        seg = strokes[i:i + 3]
        zg = min(s["high"] for s in seg)
        zd = max(s["low"] for s in seg)
        if zg <= zd:
            i += 1
            continue
        end = i + 2
        while end + 1 < len(strokes):
            nxt = strokes[end + 1]
            if nxt["high"] < zd or nxt["low"] > zg:
                break
            zg = min(zg, nxt["high"])
            zd = max(zd, nxt["low"])
            end += 1
        centers.append({
            "start_index": seg[0]["from"]["index"],
            "end_index": strokes[end]["to"]["index"],
            "start_date": seg[0]["from"]["date"],
            "end_date": strokes[end]["to"]["date"],
            "zg": _price(zg),
            "zd": _price(zd),
            "strokes": end - i + 1,
        })
        i = end + 1
    return centers


def _swings(strokes, pending):
    """同向笔端点：低点用于底背驰，高点用于顶背驰；把未确认分型也纳入候选"""
    lows = [s["to"] for s in strokes if s["direction"] == "down"]
    highs = [s["to"] for s in strokes if s["direction"] == "up"]
    if pending:
        if pending["type"] == "bottom" and (not lows or pending["index"] > lows[-1]["index"]):
            lows.append(pending)
            lows[-1] = {**pending, "pending": True}
        elif pending["type"] == "top" and (not highs or pending["index"] > highs[-1]["index"]):
            highs.append(pending)
            highs[-1] = {**pending, "pending": True}
    return lows, highs


def _dif_at(dif, index):
    if not dif or not len(dif):
        return None
    i = max(0, min(index, len(dif) - 1))
    return _num(dif[i])


def _points(strokes, centers, lows, highs, dif):
    points = []

    def add(item):
        points.append({
            "date": item.get("date"),
            "price": _price(item["price"]),
            "index": item["index"],
            "type": item["type"],
            "side": item["side"],
            "confidence": item["confidence"],
            "confirmed": item.get("confirmed", True),
            "reason": item["reason"],
        })

    # 一买 / 二买：低点降低但 DIF 抬高（底背驰）；未确认分型给候选
    first_buys = []
    for j in range(1, len(lows)):
        a, b = lows[j - 1], lows[j]
        if b["price"] >= a["price"]:
            continue
        da, db = _dif_at(dif, a["index"]), _dif_at(dif, b["index"])
        if da is not None and db is not None and db > da:
            first_buys.append(b)
            add({"date": b["date"], "price": b["price"], "index": b["index"], "type": "一买", "side": "buy",
                 "confidence": "中高", "confirmed": not b.get("pending"),
                 "reason": f"{b['price']:.2f} 创出更低低点但 MACD 底背驰（DIF {da:.2f}→{db:.2f}），下跌动能衰竭"})
        elif b.get("pending"):
            add({"date": b["date"], "price": b["price"], "index": b["index"], "type": "一买候选", "side": "buy",
                 "confidence": "待确认", "confirmed": False,
                 "reason": (f"最新底分型 {b['price']:.2f}，已跌破前低 {a['price']:.2f}，"
                            f"DIF {da:.2f}→{db:.2f} 尚未确认底背驰，需次根K线站稳后确认")})
    for j, low in enumerate(lows):
        prev_low = lows[j - 1] if j > 0 else None
        if prev_low and prev_low in first_buys and low["price"] > prev_low["price"]:
            add({"date": low["date"], "price": low["price"], "index": low["index"], "type": "二买", "side": "buy",
                 "confidence": "中", "confirmed": not low.get("pending"),
                 "reason": f"回踩 {low['price']:.2f} 未破一买低点 {prev_low['price']:.2f}，构成第二类买点"})

    # 一卖 / 二卖：高点抬高但 DIF 走低（顶背驰）
    first_sells = []
    for j in range(1, len(highs)):
        a, b = highs[j - 1], highs[j]
        if b["price"] <= a["price"]:
            continue
        da, db = _dif_at(dif, a["index"]), _dif_at(dif, b["index"])
        if da is not None and db is not None and db < da:
            first_sells.append(b)
            add({"date": b["date"], "price": b["price"], "index": b["index"], "type": "一卖", "side": "sell",
                 "confidence": "中高", "confirmed": not b.get("pending"),
                 "reason": f"{b['price']:.2f} 创出更高高点但 MACD 顶背驰（DIF {da:.2f}→{db:.2f}），上涨动能衰竭"})
        elif b.get("pending"):
            add({"date": b["date"], "price": b["price"], "index": b["index"], "type": "一卖候选", "side": "sell",
                 "confidence": "待确认", "confirmed": False,
                 "reason": (f"最新顶分型 {b['price']:.2f}，已突破前高 {a['price']:.2f}，"
                            f"DIF {da:.2f}→{db:.2f} 尚未确认顶背驰，需次根K线回落确认")})
    for j, high in enumerate(highs):
        prev_high = highs[j - 1] if j > 0 else None
        if prev_high and prev_high in first_sells and high["price"] < prev_high["price"]:
            add({"date": high["date"], "price": high["price"], "index": high["index"], "type": "二卖", "side": "sell",
                 "confidence": "中", "confirmed": not high.get("pending"),
                 "reason": f"反抽 {high['price']:.2f} 未破一卖高点 {prev_high['price']:.2f}，构成第二类卖点"})

    # 三买 / 三卖：离开中枢后的回踩不破中枢
    if centers:
        last_center = centers[-1]
        after_lows = [x for x in lows if x["index"] > last_center["end_index"]]
        above = [x for x in after_lows if x["price"] > last_center["zg"]]
        if above:
            p = above[-1]
            add({"date": p["date"], "price": p["price"], "index": p["index"], "type": "三买", "side": "buy",
                 "confidence": "中", "confirmed": not p.get("pending"),
                 "reason": f"回踩 {p['price']:.2f} 不破中枢上沿 {last_center['zg']:.2f}，构成第三类买点"})
        after_highs = [x for x in highs if x["index"] > last_center["end_index"]]
        below = [x for x in after_highs if x["price"] < last_center["zd"]]
        if below:
            p = below[-1]
            add({"date": p["date"], "price": p["price"], "index": p["index"], "type": "三卖", "side": "sell",
                 "confidence": "中", "confirmed": not p.get("pending"),
                 "reason": f"反抽 {p['price']:.2f} 不上中枢下沿 {last_center['zd']:.2f}，构成第三类卖点"})

    # 同一日期同一方向只保留可信度最高的一条，避免候选点与三买卖重复堆叠
    rank = {"中高": 3, "中": 2, "待确认": 1}
    points.sort(key=lambda x: (x["index"], -rank.get(x["confidence"], 0)))
    dedup = []
    for p in points:
        if dedup and dedup[-1]["index"] == p["index"] and dedup[-1]["side"] == p["side"]:
            continue
        dedup.append(p)
    return dedup


def _future(bars, strokes, centers, lows, highs, pending, dif, points):
    """未来推演：当前结构 + 可买/可卖触发条件"""
    last = bars[-1]
    close = _price(last.get("close"))
    last_stroke = strokes[-1] if strokes else None
    trend = "震荡"
    if last_stroke:
        base = "向上笔" if last_stroke["direction"] == "up" else "向下笔"
        # 笔的末端已出现反向运行，标注为反抽/回踩段，避免“向下笔”与反弹现状冲突
        if close is not None:
            if last_stroke["direction"] == "down" and close > last_stroke["to"]["price"]:
                trend = f"{base}后的反弹段"
            elif last_stroke["direction"] == "up" and close < last_stroke["to"]["price"]:
                trend = f"{base}后的回落段"
            else:
                trend = base
        else:
            trend = base

    # 取离现价最近的上方/下方结构位，避免历史极值把触发价拉得过远
    above = [x["price"] for x in highs[-4:] if close is not None and x["price"] > close]
    below = [x["price"] for x in lows[-4:] if close is not None and x["price"] < close]
    resistance = _price(min(above)) if above else (_price(max([x["price"] for x in highs[-4:]])) if highs else None)
    support = _price(max(below)) if below else (_price(min([x["price"] for x in lows[-4:]])) if lows else None)

    levels = {}
    if support:
        levels["support"] = support
    if resistance:
        levels["resistance"] = resistance
    if centers:
        levels["center_zg"] = centers[-1]["zg"]
        levels["center_zd"] = centers[-1]["zd"]

    # 当前结构描述
    if centers:
        c = centers[-1]
        if close is not None and close > c["zg"]:
            position = f"运行于最后中枢 {c['zd']:.2f}~{c['zg']:.2f} 上方"
        elif close is not None and close < c["zd"]:
            position = f"运行于最后中枢 {c['zd']:.2f}~{c['zg']:.2f} 下方"
        else:
            position = f"处于最后中枢 {c['zd']:.2f}~{c['zg']:.2f} 区间内"
    else:
        position = "尚未形成有效中枢，当前为趋势段"

    bias = "中性"
    last_point = points[-1] if points else None
    if last_point:
        bias = "偏多" if last_point["side"] == "buy" else "偏空"

    buy_triggers = []
    sell_triggers = []
    if pending:
        if pending["type"] == "bottom":
            buy_triggers.append({
                "price": _price(pending["price"]),
                "condition": f"次根K线收盘站稳底分型高点上方，确认 {pending['price']:.2f} 底分型有效，可轻仓试多",
            })
            sell_triggers.append({
                "price": _price(pending["price"]),
                "condition": f"有效跌破底分型低点 {pending['price']:.2f}，结构破坏，止损离场",
            })
        else:
            sell_triggers.append({
                "price": _price(pending["price"]),
                "condition": f"次根K线收盘跌回顶分型下方，确认 {pending['price']:.2f} 顶分型有效，逢高减仓",
            })
            buy_triggers.append({
                "price": _price(pending["price"]),
                "condition": f"放量突破顶分型高点 {pending['price']:.2f}，结构延续，可追多",
            })
    if resistance:
        buy_triggers.append({
            "price": resistance,
            "condition": f"放量突破前高 {resistance:.2f} 且 MACD 柱放大，向上笔延伸，可加仓",
        })
    if support:
        sell_triggers.append({
            "price": support,
            "condition": f"跌破近端支撑 {support:.2f} 且反抽无力，向下笔延续，减仓",
        })
    if centers:
        c = centers[-1]
        buy_triggers.append({
            "price": c["zg"],
            "condition": f"回踩不破中枢上沿 {c['zg']:.2f}（三买）可低吸，跌破则观望",
        })
        sell_triggers.append({
            "price": c["zd"],
            "condition": f"反抽不上中枢下沿 {c['zd']:.2f}（三卖）需回避，站上再评估",
        })

    if not buy_triggers:
        buy_triggers.append({"price": resistance, "condition": "等待放量突破前高后再介入"})
    if not sell_triggers:
        sell_triggers.append({"price": support, "condition": "跌破近端支撑则减仓"})

    macd_state = "中性"
    if dif:
        d = _dif_at(dif, len(bars) - 1)
        if d is not None:
            macd_state = "零轴上方的多头动能" if d > 0 else "零轴下方的空头动能"

    parts = [f"当前{trend}，{position}，MACD 处于{macd_state}"]
    if last_point:
        parts.append(f"最新结构信号为{last_point['type']}（{last_point['date']}，{last_point['price']:.2f}）")
    if buy_triggers:
        parts.append(f"可买条件：{buy_triggers[0]['condition']}")
    if sell_triggers:
        parts.append(f"可卖条件：{sell_triggers[0]['condition']}")

    return {
        "trend": trend,
        "bias": bias,
        "position": position,
        "support": support,
        "resistance": resistance,
        "buy_triggers": buy_triggers[:2],
        "sell_triggers": sell_triggers[:2],
        "summary": "；".join(parts) + "。",
    }


def analyze(bars, dif=None, end_index=None):
    """返回缠论结构化结果：分型、笔、中枢、买卖点、未来推演"""
    if not bars or len(bars) < MIN_BARS:
        return None
    if end_index is None or end_index >= len(bars):
        end_index = len(bars) - 1
    segments = bars[:end_index + 1]
    if dif:
        dif = dif[:end_index + 1]

    merged = _merge(segments)
    if len(merged) < 5:
        return None
    fractals = _fractals(merged)
    if not fractals:
        return None
    strokes, pending = _strokes(fractals)
    # 序列尾部若还有未被笔消费的异类分型，作为“待确认”结构
    pending = pending if pending and (not strokes or pending["index"] > strokes[-1]["to"]["index"]) else None
    centers = _centers(strokes)
    lows, highs = _swings(strokes, pending)
    points = _points(strokes, centers, lows, highs, dif)
    future = _future(segments, strokes, centers, lows, highs, pending, dif, points)

    recent_points = points[-8:]
    summary = future["summary"]
    if recent_points:
        last = recent_points[-1]
        summary = f"最近出现{last['type']}（{last['date']}，{last['price']:.2f}）；" + summary

    return {
        "fractals": fractals[-30:],
        "strokes": strokes[-30:],
        "centers": centers[-4:],
        "points": recent_points,
        "trend": future["trend"],
        "levels": {k: v for k, v in {
            "support": future["support"],
            "resistance": future["resistance"],
            "center_zg": centers[-1]["zg"] if centers else None,
            "center_zd": centers[-1]["zd"] if centers else None,
        }.items() if v is not None},
        "pending": pending,
        "future": future,
        "summary": summary,
        "note": "包含关系已合并；笔/中枢可回溯历史K线。买卖点为 MACD 背离 + 中枢结构推测，需结合量能确认。",
    }
