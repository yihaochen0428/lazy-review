"""日K量价买卖信号：6 类买点 + 5 类卖点

量价为主动条件，MACD / KDJ / RSI 只做共振加分；环境只用指数（上证 + 创业板）日K分。

口径约定：
- 指标按整条 bars 数组计算后按下标取第 i 日，只依赖 <= i 的数据（MA/EMA/MACD/KDJ/RSI 均为因果指标），
  回测与线上共用 detect()，天然无未来函数。
- 盘中（analysis_phase == "auction"）日K最后一根未收完，调用方传 skip_last=True 跳过。
- 信号只做辅助判断，不改动 8 指标研判 / 做T加权 / 开盘建议。
"""
import time
from datetime import datetime

from . import dkline
from . import eastmoney as em
from .db import cached_call
from .indicators import kdj, ma, macd, rsi

INDEX_SECIDS = ("1.000001", "0.399006")

NOTE = "信号按日K收盘确认，量价为主、指标共振为辅；仅辅助判断，不构成交易建议。"


def _grade(score):
    if score >= 65:
        return "强"
    if score >= 52:
        return "中"
    if score >= 45:
        return "弱"
    return None


def _env_label(score):
    if score is None:
        return "未知"
    if score >= 0.45:
        return "多头"
    if score >= 0.15:
        return "偏多"
    if score > -0.15:
        return "震荡"
    if score > -0.45:
        return "偏空"
    return "空头"


def _combined_env(idx_score, sector_score):
    """大盘 0.35 / 板块 0.65 加权：板块轮动常先于大盘反弹，权重向板块倾斜；缺项时归一"""
    vals = [(0.35, idx_score), (0.65, sector_score)]
    vals = [(w, v) for w, v in vals if v is not None]
    if not vals:
        return None
    total = sum(w for w, _ in vals)
    return round(sum(w * v for w, v in vals) / total, 3)


def _downgrade(grade):
    return {"强": "中", "中": "弱", "弱": "弱"}.get(grade, grade)


# ---------------------------------------------------------------- 指标序列
def _arrays(bars):
    closes = [b.get("close") or 0 for b in bars]
    highs = [b.get("high") or b.get("close") or 0 for b in bars]
    lows = [b.get("low") or b.get("close") or 0 for b in bars]
    opens = [b.get("open") or b.get("close") or 0 for b in bars]
    vols = [b.get("volume") or 0 for b in bars]
    m = macd(closes)
    k = kdj(highs, lows, closes)
    r = rsi(closes)
    obv, acc = [], 0.0
    for i, c in enumerate(closes):
        if i:
            acc += (1 if c > closes[i - 1] else -1 if c < closes[i - 1] else 0) * vols[i]
        obv.append(acc)
    return {
        "close": closes, "high": highs, "low": lows, "open": opens, "volume": vols,
        "ma5": ma(closes, 5), "ma10": ma(closes, 10), "ma20": ma(closes, 20),
        "vm5": ma(vols, 5), "vm20": ma(vols, 20),
        "dif": m["dif"], "dea": m["dea"], "hist": m["hist"],
        "k": k["k"], "d": k["d"], "rsi": r, "obv": obv,
    }


def _metrics(i, a):
    """第 i 日的量价度量；数据不足返回 None"""
    closes, highs, lows = a["close"], a["high"], a["low"]
    if i < 1 or i >= len(closes):
        return None
    c, o, h, l, v = closes[i], a["open"][i], highs[i], lows[i], a["volume"][i]
    pc, ma20 = closes[i - 1], a["ma20"][i]
    vm5, vm20 = a["vm5"][i], a["vm20"][i]
    if not pc or not ma20 or not vm5 or not vm20:
        return None
    hi20, lo20 = max(highs[i - 19:i + 1]), min(lows[i - 19:i + 1])
    rng = h - l
    atr = sum((highs[j] - lows[j]) / max(closes[j - 1], 0.01) for j in range(i - 9, i + 1)) / 10 * 100
    return {
        "c": c, "o": o, "h": h, "l": l, "v": v, "pc": pc,
        "pos20": (c - lo20) / (hi20 - lo20) if hi20 > lo20 else 0.5,
        "dev20": (c / ma20 - 1) * 100,
        "vr": v / vm5 if vm5 else 1.0,
        "volr": vm5 / vm20 if vm20 else 1.0,
        "chg": (c / pc - 1) * 100,
        "atr": atr,
        "upper": (h - max(o, c)) / pc * 100,
        "lower": (min(o, c) - l) / pc * 100,
        "close_pos": (c - l) / rng if rng > 0 else 1.0,
        "prior_high20": max(highs[i - 20:i]) if i >= 20 else None,
    }


def _gold_cross(a, i, lookback=2):
    k, d = a["k"], a["d"]
    for j in range(max(1, i - lookback), i + 1):
        if None in (k[j - 1], d[j - 1], k[j], d[j]):
            continue
        if k[j - 1] <= d[j - 1] and k[j] > d[j]:
            return True
    return False


def _dead_cross(a, i, lookback=2):
    k, d = a["k"], a["d"]
    for j in range(max(1, i - lookback), i + 1):
        if None in (k[j - 1], d[j - 1], k[j], d[j]):
            continue
        if k[j - 1] >= d[j - 1] and k[j] < d[j]:
            return True
    return False


# ---------------------------------------------------------------- 买点
def _buy_hits(i, a, m):
    hits = []
    c, o, h, l, pc = m["c"], m["o"], m["h"], m["l"], m["pc"]
    ma5, ma10, ma20 = a["ma5"][i], a["ma10"][i], a["ma20"][i]
    if not (ma5 and ma10 and ma20):
        return hits

    # 1) 缩量回踩均线：上升结构里回踩 MA10/MA20 不破，量能萎缩
    gap10, gap20 = abs(l - ma10) / ma10, abs(l - ma20) / ma20
    if ma5 > ma20 and min(gap10, gap20) <= 0.012:
        ref, ref_ma = ("MA10", ma10) if gap10 <= gap20 else ("MA20", ma20)
        if c >= ma10 * 0.995 and m["vr"] <= 0.85 and 0.25 <= m["pos20"] <= 0.75 and pc >= ma20 * 0.97:
            hits.append({"key": "pullback", "title": "缩量回踩均线",
                         "base": 46, "detail": f"缩量回踩 {ref}（量能 {m['vr']:.2f} 倍）"})

    # 2) 放量突破平台：收盘越过前 20 日高点且量能明显放大
    ph = m["prior_high20"]
    if ph and c > ph and m["vr"] >= 1.5 and m["volr"] >= 1.15 and m["chg"] >= 1.5 and m["dev20"] <= 12:
        hits.append({"key": "breakout", "title": "放量突破平台",
                     "base": 48, "detail": f"放量突破 20 日高点 {ph:.2f}（量能 {m['vr']:.2f} 倍）"})

    # 3) 地量止跌：低位极度缩量后收阳，留下影
    k_val = a["k"][i]
    if (m["volr"] <= 0.75 and m["vr"] <= 0.85 and m["pos20"] <= 0.30 and c > o
            and c >= pc * 0.995 and (m["lower"] >= 1.2 or m["close_pos"] >= 0.6)
            and k_val is not None and k_val <= 40):
        hits.append({"key": "reversal", "title": "地量止跌",
                     "base": 42, "detail": f"地量收阳（5/20 量比 {m['volr']:.2f}，K {k_val:.1f}）"})

    # 4) 量价底背离：价创新低但 OBV 抬高，且放量收回
    obv = a["obv"]
    if i >= 15:
        if (l <= min(a["low"][i - 14:i + 1]) and obv[i] > min(obv[i - 14:i])
                and c > o and m["vr"] >= 1.15 and c >= l * 1.015):
            hits.append({"key": "divergence", "title": "量价底背离",
                         "base": 44, "detail": "价格新低但 OBV 抬高，放量收回"})

    # 5) 强势股缩量回踩 MA5
    if (ma5 > ma10 > ma20 and l <= ma5 * 1.005 and c >= ma5 * 0.995
            and m["vr"] <= 0.90 and m["volr"] >= 1.0 and i >= 10
            and (c / a["close"][i - 10] - 1) * 100 >= 4):
        hits.append({"key": "strong", "title": "强势回踩 MA5",
                     "base": 40, "detail": f"多头排列缩量回踩 MA5（10 日 {((c / a['close'][i - 10] - 1) * 100):.1f}%）"})

    # 6) 零轴上方二次金叉：DIF 在零轴上方回踩后再金叉
    dif, dea = a["dif"], a["dea"]
    if dif[i] > 0 and m["vr"] >= 1.05 and c > ma20 and i >= 11:
        recent_cross = any(dif[j - 1] <= dea[j - 1] and dif[j] > dea[j]
                           for j in range(max(1, i - 2), i + 1))
        prior_dead = any(dif[j] < dea[j] for j in range(max(1, i - 10), i))
        if recent_cross and prior_dead:
            hits.append({"key": "macd", "title": "零轴上方二次金叉",
                         "base": 42, "detail": "DIF 零轴上方二次金叉且温和放量"})
    return hits


# ---------------------------------------------------------------- 卖点
def _sell_hits(i, a, m):
    hits = []
    c, o, h, pc = m["c"], m["o"], m["h"], m["pc"]
    ma20 = a["ma20"][i]
    if not ma20:
        return hits

    # 1) 放量滞涨：高位放量但价格不涨，收盘离最高价有明显距离
    if m["volr"] >= 1.8 and m["chg"] <= 0.5 and c < h * 0.985 and m["pos20"] >= 0.70:
        hits.append({"key": "stall", "title": "放量滞涨",
                     "base": 48, "detail": f"高位放量滞涨（量比 {m['volr']:.2f}，涨幅 {m['chg']:.1f}%）"})

    # 2) 量价顶背离：价创新高但 OBV 走低，收盘走弱
    obv = a["obv"]
    if i >= 15 and h >= max(a["high"][i - 14:i + 1]) and obv[i] < max(obv[i - 14:i]):
        if (c < o or (c / h - 1) <= -0.015) and m["pos20"] >= 0.75:
            hits.append({"key": "top_div", "title": "量价顶背离",
                         "base": 50, "detail": "价格新高但 OBV 走低，冲高回落"})

    # 3) 放量跌破 MA20：从均线上方转为跌破
    if c < ma20 and pc >= (a["ma20"][i - 1] or 0) and m["vr"] >= 1.3 and m["dev20"] < -1:
        hits.append({"key": "break_ma20", "title": "放量跌破 MA20",
                     "base": 46, "detail": f"放量跌破 MA20（量能 {m['vr']:.2f} 倍）"})

    # 4) 高位爆量长上影
    if (i >= 5 and (c / a["close"][i - 5] - 1) * 100 >= 12 and m["vr"] >= 2.2
            and (m["upper"] >= 1.5 or c < o) and m["pos20"] >= 0.80):
        hits.append({"key": "blowoff", "title": "高位爆量长上影",
                     "base": 46, "detail": f"5 日涨 {((c / a['close'][i - 5] - 1) * 100):.1f}% 后爆量长上影"})

    # 5) 高位死叉：缩量反弹无力
    k_val = a["k"][i]
    if (m["pos20"] >= 0.80 and m["dev20"] >= 6 and k_val is not None and k_val > 65
            and m["vr"] <= 1.0 and _dead_cross(a, i)):
        hits.append({"key": "kdj_dead", "title": "高位死叉",
                     "base": 42, "detail": f"高位缩量，KDJ 死叉（K {k_val:.1f}）"})

    # 6) 反弹衰竭：下跌途中（跌破 MA20 较远）冲高回落，收盘贴近当日最低
    #    覆盖松发 11-09 这类形态：非高位放量的卖点盲区
    if (m["c"] < ma20 and m["dev20"] <= -3 and m["chg"] >= -3
            and (m["upper"] >= 2.0 or m["close_pos"] <= 0.25)
            and 0.7 <= m["vr"] <= 1.6 and m["pos20"] <= 0.45):
        hits.append({"key": "fade", "title": "反弹衰竭",
                     "base": 48, "detail": f"下跌途中冲高回落（上影 {m['upper']:.1f}%，收在全天{int(m['close_pos'] * 100)}% 位置）"})
    return hits


# ---------------------------------------------------------------- 打分
def _score_buy(i, a, m, hits, env):
    score = max(h["base"] for h in hits) + min(8 * (len(hits) - 1), 16)
    notes = []
    hist = a["hist"]
    if hist[i] is not None and hist[i - 1] is not None and hist[i] > 0 and hist[i] > hist[i - 1]:
        score += 4
        notes.append("MACD 红柱放大")
    k_val = a["k"][i]
    if k_val is not None and k_val < 60 and _gold_cross(a, i):
        score += 4
        notes.append("KDJ 低位金叉")
    r = a["rsi"][i]
    if r is not None and a["rsi"][i - 1] is not None and 40 <= r <= 65 and r > a["rsi"][i - 1]:
        score += 3
        notes.append(f"RSI {r:.1f} 温和上行")
    if 0.20 <= m["pos20"] <= 0.65 and -6 <= m["dev20"] <= 5:
        score += 5
        notes.append("位置不高不低")
    if env is not None:
        if env >= 0.15:
            score += 5
            notes.append("指数环境偏多")
        elif env <= -0.15:
            score -= 5
            notes.append("指数环境偏空")
    if m["dev20"] > 12:
        score -= 8
        notes.append("偏离 MA20 过大")
    if m["volr"] >= 2.5 and m["chg"] < 0.5:
        score -= 10
        notes.append("放量不涨")
    ma20s = a["ma20"]
    if i >= 5 and ma20s[i - 5] and m["c"] < ma20s[i] and ma20s[i] < ma20s[i - 5]:
        score -= 10
        notes.append("MA20 下行且股价在其下方")
    if m["chg"] <= -5:
        score -= 12
        notes.append("当日大跌")
    return max(0, min(100, round(score))), notes


def _score_sell(i, a, m, hits, env):
    score = max(h["base"] for h in hits) + min(8 * (len(hits) - 1), 16)
    notes = []
    hist = a["hist"]
    if hist[i] is not None and hist[i - 1] is not None and hist[i] < 0 and hist[i] < hist[i - 1]:
        score += 4
        notes.append("MACD 绿柱放大")
    k_val = a["k"][i]
    if k_val is not None and k_val > 65 and _dead_cross(a, i):
        score += 4
        notes.append("KDJ 高位死叉")
    r = a["rsi"][i]
    if r is not None and a["rsi"][i - 1] is not None and r > 70 and r < a["rsi"][i - 1]:
        score += 3
        notes.append(f"RSI {r:.1f} 高位回落")
    if m["pos20"] >= 0.75:
        score += 5
        notes.append("处于 20 日高位")
    if m["dev20"] >= 10:
        score += 4
        notes.append("偏离 MA20 过大")
    if env is not None and env <= -0.15:
        score += 5
        notes.append("指数环境偏空")
    ma20 = a["ma20"][i]
    if m["pos20"] <= 0.25 and ma20 and m["c"] >= ma20:
        score -= 10
        notes.append("低位不喊卖")
    if m["vr"] <= 0.8 and m["chg"] > -3:
        score -= 6
        notes.append("缩量小幅回调")
    if ma20 and i >= 5 and a["ma20"][i - 5] and m["c"] > ma20 and ma20 > a["ma20"][i - 5] and m["vr"] < 1.2:
        score -= 6
        notes.append("上升趋势缩量")
    return max(0, min(100, round(score))), notes


# ---------------------------------------------------------------- 去重
def _dedup(items, gap=3):
    kept = []
    for side in ("buy", "sell"):
        group = sorted((x for x in items if x["side"] == side), key=lambda x: x["index"])
        picked = []
        for item in group:
            if picked and item["index"] - picked[-1]["index"] < gap:
                if item["score"] > picked[-1]["score"]:
                    picked[-1] = item
            else:
                picked.append(item)
        kept.extend(picked)
    by_index = {}
    for item in kept:
        prev = by_index.get(item["index"])
        if (prev is None or item["score"] > prev["score"]
                or (item["score"] == prev["score"] and item["side"] == "buy" and prev["side"] == "sell")):
            by_index[item["index"]] = item
    return sorted(by_index.values(), key=lambda x: x["index"])


def _env_at(env, i):
    if env is None:
        return None
    if isinstance(env, (list, tuple)):
        return env[i] if i < len(env) else None
    return float(env)


# ---------------------------------------------------------------- 主入口
def detect(bars, env=None, sector_env=None, skip_last=False, max_items=40):
    """识别买卖信号

    env: 大盘环境（None / 单个分 / 与 bars 等长数组）
    sector_env: 板块环境（同上证口径，加权 0.5）
    skip_last: 盘中跳过未收完的最后一根日K

    策略调整：大盘+板块加权环境 <= -0.15 时买点降级（强→中→弱），
    >= 0.15 时卖点降级，避免逆势操作。
    """
    n = len(bars or [])
    empty = {"items": [], "latest_buy": None, "latest_sell": None,
             "counts": {"buy": 0, "sell": 0}, "summary": "日K数据不足，暂无量价信号",
             "env": None, "env_label": "未知", "caution": "", "note": NOTE}
    if n < 40:
        return empty
    a = _arrays(bars)
    end = n - 1 - (1 if skip_last else 0)
    raw = []
    for i in range(25, end + 1):
        m = _metrics(i, a)
        if not m:
            continue
        env_v = _combined_env(_env_at(env, i), _env_at(sector_env, i))
        buys, sells = _buy_hits(i, a, m), _sell_hits(i, a, m)
        if buys:
            score, notes = _score_buy(i, a, m, buys, env_v)
            grade = _grade(score)
            if grade and score >= 45:
                if env_v is not None and env_v <= -0.15:
                    grade = _downgrade(grade)
                    notes = notes + ["大盘/板块偏空，降级"]
                raw.append(_item(bars, i, "buy", m, buys, score, notes, grade))
        if sells:
            score, notes = _score_sell(i, a, m, sells, env_v)
            grade = _grade(score)
            if grade and score >= 45:
                if env_v is not None and env_v >= 0.15:
                    grade = _downgrade(grade)
                    notes = notes + ["大盘/板块偏强，降级"]
                raw.append(_item(bars, i, "sell", m, sells, score, notes, grade))
    items = _dedup(raw)[-max_items:]
    buys = [x for x in items if x["side"] == "buy"]
    sells = [x for x in items if x["side"] == "sell"]
    latest_buy = buys[-1] if buys else None
    latest_sell = sells[-1] if sells else None
    parts = []
    if latest_buy:
        parts.append(f"最近买点 {latest_buy['date'][5:]} {latest_buy['type']}（{latest_buy['score']} 分 · {latest_buy['grade']}）")
    if latest_sell:
        parts.append(f"最近卖点 {latest_sell['date'][5:]} {latest_sell['type']}（{latest_sell['score']} 分 · {latest_sell['grade']}）")
    if not parts:
        parts.append("近期未出现有效量价信号")
    env_end = _combined_env(_env_at(env, end), _env_at(sector_env, end))
    idx_end = _env_at(env, end)
    sec_end = _env_at(sector_env, end)
    caution = ""
    if env_end is not None and env_end <= -0.15 and buys:
        caution = (f"大盘{_env_label(idx_end)}、板块{_env_label(sec_end)}，买点已降级，"
                   "下跌市里量价买点胜率偏低，只宜小仓位试错")
    elif env_end is not None and env_end >= 0.15 and sells:
        caution = (f"大盘{_env_label(idx_end)}、板块{_env_label(sec_end)}，卖点已降级，"
                   "强势环境里减仓信号容易卖飞，可等跌破 MA20 再执行")
    return {
        "items": items,
        "latest_buy": latest_buy,
        "latest_sell": latest_sell,
        "counts": {"buy": len(buys), "sell": len(sells)},
        "summary": "；".join(parts),
        "env": round(env_end, 3) if env_end is not None else None,
        "env_index": round(idx_end, 3) if idx_end is not None else None,
        "env_sector": round(sec_end, 3) if sec_end is not None else None,
        "env_label": _env_label(env_end),
        "env_index_label": _env_label(idx_end),
        "env_sector_label": _env_label(sec_end),
        "caution": caution,
        "note": NOTE,
    }


def _item(bars, i, side, m, hits, score, notes, grade=None):
    reasons = [h["detail"] for h in hits] + notes
    return {
        "index": i,
        "date": (bars[i].get("date") or "")[:10],
        "side": side,
        "key": hits[0]["key"],
        "type": hits[0]["title"],
        "score": score,
        "strength": score,
        "grade": grade or _grade(score),
        "price": round(m["c"], 2),
        "high": round(m["h"], 2),
        "low": round(m["l"], 2),
        "reasons": reasons,
        "reason": "；".join(reasons),
    }


# ---------------------------------------------------------------- 环境
def _index_bars(secid):
    def producer():
        try:
            return em.fetch_kline(secid, 101, 200)["bars"]
        except Exception:  # noqa: BLE001
            return []

    return cached_call(f"ksig:idx:{secid}", 600, producer) or []


def env_score_live():
    """实盘环境分：上证 + 创业板日K分均值（各自只用 T-1 及之前）"""
    today = datetime.now().strftime("%Y-%m-%d")
    scores = []
    for secid in INDEX_SECIDS:
        ctx = dkline.index_ctx(_index_bars(secid), today)
        if ctx and ctx.get("score") is not None:
            scores.append(ctx["score"])
    return round(sum(scores) / len(scores), 3) if scores else None


# ---------------------------------------------------------------- 回测
def _entry(bars, i):
    j = i + 1
    if j >= len(bars):
        return None, None
    prev = bars[i].get("close") or 0
    op = bars[j].get("open") or 0
    if not op or not prev:
        return None, "no_entry"
    if op >= prev * 1.095:
        return None, "limit_up"
    return op, None


def _swing(bars, i, entry, atr_pct):
    stop = min(0.08, max(0.03, 1.5 * atr_pct / 100))
    target = min(0.18, max(0.06, 3.0 * atr_pct / 100))
    last = min(i + 21, len(bars) - 1)
    for j in range(i + 2, last + 1):
        low = bars[j].get("low") or 0
        high = bars[j].get("high") or 0
        if low and low <= entry * (1 - stop):
            return round(-stop * 100, 2), j - (i + 1), "stop"
        if high and high >= entry * (1 + target):
            return round(target * 100, 2), j - (i + 1), "target"
    if last > i + 1:
        close = bars[last].get("close") or entry
        return round((close / entry - 1) * 100, 2), last - (i + 1), "time"
    return None


def _short_stat(rows, n):
    vals = [r[f"t{n}"] for r in rows if r.get(f"t{n}") is not None]
    if not vals:
        return {"samples": 0, "win_rate": None, "avg_return": None}
    return {
        "samples": len(vals),
        "win_rate": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
        "avg_return": round(sum(vals) / len(vals), 2),
    }


def _swing_stat(rows):
    vals = [r for r in rows if r.get("swing_ret") is not None]
    if not vals:
        return {"samples": 0, "win_rate": None, "avg_return": None,
                "avg_days": None, "payoff": None, "exits": {}}
    rets = [r["swing_ret"] for r in vals]
    wins = [v for v in rets if v > 0]
    losses = [v for v in rets if v <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0
    return {
        "samples": len(vals),
        "win_rate": round(len(wins) / len(vals) * 100, 1),
        "avg_return": round(sum(rets) / len(vals), 2),
        "avg_days": round(sum(r["swing_days"] for r in vals) / len(vals), 1),
        "payoff": round(avg_win / avg_loss, 2) if avg_loss else None,
        "exits": {k: sum(1 for r in vals if r.get("exit") == k) for k in ("stop", "target", "time")},
    }


def _base_stat(rows):
    vals = [r["ret5"] for r in rows if r.get("ret5") is not None]
    if not vals:
        return {"samples": 0, "win_rate": None, "avg_return": None}
    return {
        "samples": len(vals),
        "win_rate": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
        "avg_return": round(sum(vals) / len(vals), 2),
    }


def _pool():
    from .analysis import SECTOR_PLAYS
    from . import tenv

    seen, pool = set(), []
    for sector, info in SECTOR_PLAYS.items():
        picks = list((info.get("leaders") or [])[:2]) + list((info.get("small") or [])[:1])
        for item in picks:
            code, name = item[0], item[1]
            if code in seen:
                continue
            seen.add(code)
            pool.append((code, name, sector if sector in tenv.ETF_PROXY else None))
    return pool


def _run_backtest(days=60):
    days = max(20, min(int(days or 60), 120))
    pool = _pool()
    from . import tenv

    env_cache = {}

    def env_of(day):
        if day in env_cache:
            return env_cache[day]
        scores = []
        for secid in INDEX_SECIDS:
            ctx = dkline.index_ctx(_index_bars(secid), day)
            if ctx and ctx.get("score") is not None:
                scores.append(ctx["score"])
        env_cache[day] = round(sum(scores) / len(scores), 3) if scores else None
        return env_cache[day]

    etf_bars_cache = {}

    def _etf_bars(sector):
        if sector not in etf_bars_cache:
            secid = tenv.ETF_PROXY[sector][0]
            try:
                etf_bars_cache[sector] = em.fetch_kline(secid, 101, days + 90)["bars"]
            except Exception:  # noqa: BLE001
                etf_bars_cache[sector] = []
        return etf_bars_cache[sector]

    sector_env_cache = {}

    def sector_env_of(sector, day):
        if not sector:
            return None
        key = (sector, day)
        if key not in sector_env_cache:
            ctx = dkline.index_ctx(_etf_bars(sector), day)
            sector_env_cache[key] = ctx.get("score") if ctx else None
        return sector_env_cache[key]

    rows, base_rows, sell_rows = [], [], []
    skipped = {"limit_day": 0, "limit_up": 0, "no_entry": 0}
    for code, name, sector in pool:
        secid = f"{em.market_prefix(code)}.{code}"
        try:
            bars = em.fetch_kline(secid, 101, days + 90)["bars"]
        except Exception:  # noqa: BLE001
            continue
        n = len(bars)
        if n < 60:
            continue
        dates = [(b.get("date") or "")[:10] for b in bars]
        env_series = [env_of(day) for day in dates]
        sec_series = [sector_env_of(sector, day) for day in dates]
        try:
            detected = detect(bars, env=env_series, sector_env=sec_series)
        except Exception:  # noqa: BLE001
            continue
        item_map = {it["index"]: it for it in detected["items"]}
        lo, hi = max(25, n - days - 20), n - 21
        for i in range(lo, hi + 1):
            entry, why = _entry(bars, i)
            if entry is None:
                if why:
                    skipped[why] = skipped.get(why, 0) + 1
                continue
            prev_close = bars[i - 1].get("close") or 0
            chg = (bars[i]["close"] / prev_close - 1) * 100 if prev_close else 0
            if abs(chg) >= 9.8:
                skipped["limit_day"] += 1
                continue
            if i + 6 < n:
                base_rows.append({"ret5": round((bars[i + 6]["close"] / entry - 1) * 100, 2)})
            item = item_map.get(i)
            if not item:
                continue
            row = {"code": code, "name": name, "date": item["date"], "side": item["side"],
                   "key": item["key"], "type": item["type"], "score": item["score"], "grade": item["grade"]}
            if item["side"] == "buy":
                for k in (1, 3, 5):
                    j = i + 1 + k
                    row[f"t{k}"] = round((bars[j]["close"] / entry - 1) * 100, 2) if j < n else None
                swing = _swing(bars, i, entry, _atr_pct(bars, i))
                if swing:
                    row["swing_ret"], row["swing_days"], row["exit"] = swing
                rows.append(row)
            elif i + 5 < n:
                row["after5"] = round((bars[i + 5]["close"] / bars[i]["close"] - 1) * 100, 2)
                sell_rows.append(row)

    by_grade = {}
    for grade in ("强", "中"):
        sub = [r for r in rows if r["grade"] == grade]
        if sub:
            by_grade[grade] = {
                "samples": len(sub),
                "t1": _short_stat(sub, 1), "t3": _short_stat(sub, 3), "t5": _short_stat(sub, 5),
                "swing": _swing_stat(sub),
            }

    grouped = {}
    for r in rows:
        grouped.setdefault(r["key"], {"key": r["key"], "title": r["type"], "rows": []})["rows"].append(r)
    by_type = [{
        "key": v["key"], "title": v["title"], "samples": len(v["rows"]),
        "t5": _short_stat(v["rows"], 5), "swing": _swing_stat(v["rows"]),
    } for v in grouped.values()]
    by_type.sort(key=lambda x: x["samples"], reverse=True)

    after = [r["after5"] for r in sell_rows if r.get("after5") is not None]
    sell_group = {}
    for r in sell_rows:
        sell_group.setdefault(r["key"], {"key": r["key"], "title": r["type"], "vals": []})["vals"].append(r["after5"])
    sell_types = [{
        "key": v["key"], "title": v["title"], "samples": len(v["vals"]),
        "after5_avg": round(sum(v["vals"]) / len(v["vals"]), 2),
        "down_ratio": round(sum(1 for x in v["vals"] if x < 0) / len(v["vals"]) * 100, 1),
    } for v in sell_group.values() if v["vals"]]
    sell_types.sort(key=lambda x: x["samples"], reverse=True)

    idx_bars = _index_bars("1.000001")
    index_chg = None
    if len(idx_bars) > days:
        base_close = idx_bars[-days - 1].get("close") or 0
        if base_close:
            index_chg = round((idx_bars[-1]["close"] / base_close - 1) * 100, 2)

    return {
        "days": days,
        "updated_at": time.strftime("%Y-%m-%d %H:%M"),
        "samples": len(rows),
        "pool": len(pool),
        "regime": {
            "index_chg": index_chg,
            "label": ("下跌市" if (index_chg or 0) < -3
                      else "上涨市" if (index_chg or 0) > 3 else "震荡市"),
        },
        "buy": {"baseline": _base_stat(base_rows), "by_grade": by_grade, "by_type": by_type},
        "sell": {
            "samples": len(after),
            "after5_avg": round(sum(after) / len(after), 2) if after else None,
            "after5_down_ratio": round(sum(1 for v in after if v < 0) / len(after) * 100, 1) if after else None,
            "by_type": sell_types,
        },
        "skipped": skipped,
        "note": ("次日开盘买入；短线按 T+1/T+3/T+5 收盘价计；波段按 ATR 止损止盈、最长持 20 日；"
                 "样本为行业龙头，已剔除连板/涨停日与一字板买不到的情况；"
                 "个股指标只使用当日及之前数据，指数环境只使用 T-1，无未来函数；未计手续费与滑点。"),
    }


def _atr_pct(bars, i, n=10):
    total = 0.0
    for j in range(max(1, i - n + 1), i + 1):
        prev = bars[j - 1].get("close") or 0
        if not prev:
            return 2.5
        total += ((bars[j].get("high") or prev) - (bars[j].get("low") or prev)) / prev
    return total / max(1, min(n, i)) * 100


def backtest(days=60, force=False, phase=""):
    """信号回测：短线 T+1/T+3/T+5 + 波段 ATR 止盈止损"""
    days = max(20, min(int(days or 60), 120))
    if force:
        return _run_backtest(days)
    stamp = f"{datetime.now():%Y%m%d}" + (f":{phase}" if phase else "")
    return cached_call(f"ksig:bt:v1:{days}:{stamp}", 86400, lambda: _run_backtest(days))
