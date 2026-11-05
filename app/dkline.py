"""日K 环境：用 T-1 的日线刻画趋势 / 位置 / 量能，为开盘与做T提供中期背景

所有计算都只使用「当日之前」的日线（before_date 过滤），避免未来函数。
"""
from . import eastmoney as em
from .db import cached_call


def _clamp(value, low=-1.0, high=1.0):
    return max(low, min(high, value))


def _ma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _label(score):
    if score >= 0.45:
        return "多头"
    if score >= 0.15:
        return "偏多"
    if score > -0.15:
        return "震荡"
    if score > -0.45:
        return "偏空"
    return "空头"


def _before(bars, before_date):
    return [b for b in (bars or [])
            if b.get("close") and (not before_date or (b.get("date") or "")[:10] < before_date)]


def _sent_label(score):
    if score >= 80:
        return "沸点"
    if score >= 65:
        return "火热"
    if score >= 45:
        return "中性"
    if score >= 30:
        return "偏冷"
    return "冰点"


def index_ctx(bars, before_date=None):
    """大盘环境：日K 趋势 + 情绪温度 + 量价形态预判（全部基于 before_date 之前的日线）

    情绪温度与仪表盘 sentiment_history 同一套量价公式，无需涨跌家数，
    因此可以在回测里逐日复现，不会引入未来数据。
    """
    bars = _before(bars, before_date)
    if len(bars) < 21:
        return None
    closes = [b["close"] for b in bars]
    highs = [b.get("high") or b["close"] for b in bars]
    lows = [b.get("low") or b["close"] for b in bars]
    vols = [b.get("volume") or 0 for b in bars]
    trend = state(bars)
    chg = (closes[-1] / closes[-2] - 1) * 100 if closes[-2] else 0
    ma20 = sum(closes[-20:]) / 20
    dev = (closes[-1] / ma20 - 1) * 100 if ma20 else 0
    avg5 = sum(vols[-5:]) / 5
    vr = (vols[-1] / avg5) if avg5 else 1.0
    sent = max(0, min(100, round(50 + chg * 6 + dev * 4 + (vr - 1) * 10, 1)))
    chg5 = (closes[-1] / closes[-6] - 1) * 100 if closes[-6] else 0
    chg10 = (closes[-1] / closes[-11] - 1) * 100 if closes[-11] else 0
    high20 = max(highs[-21:-1]) if len(highs) >= 21 else max(highs)
    if closes[-1] > high20 and vr >= 1.15:
        pattern, tone = "放量突破", "bull"
    elif chg5 <= -4 and chg >= -0.6 and vr >= 1.15:
        pattern, tone = "底部放量企稳", "bull"
    elif chg5 <= -3 and chg >= -0.5 and vr <= 0.9:
        pattern, tone = "缩量止跌", "neutral"
    elif chg10 >= 8 and chg <= 1 and vr >= 1.25:
        pattern, tone = "高位放量滞涨", "bear"
    elif dev >= 5:
        pattern, tone = "偏离 MA20 偏大", "bear"
    elif dev <= -5:
        pattern, tone = "超跌偏离 MA20", "neutral"
    else:
        pattern, tone = "震荡整理", "neutral"
    # 情绪反转：情绪分相对 5 个交易日前的抬升幅度，配合指数站上 MA5 视为修复启动
    def _sent_at(idx):
        c, p = closes[idx], closes[idx - 1]
        ch = (c / p - 1) * 100 if p else 0
        m = sum(closes[max(0, idx - 19):idx + 1]) / min(20, idx + 1)
        dv = (c / m - 1) * 100 if m else 0
        a5 = sum(vols[max(0, idx - 4):idx + 1]) / min(5, idx + 1)
        v = vols[idx] / a5 if a5 else 1.0
        return max(0, min(100, round(50 + ch * 6 + dv * 4 + (v - 1) * 10, 1)))

    prior = _sent_at(len(closes) - 6) if len(closes) >= 26 else sent
    sent_trend = round(sent - prior, 1)
    ma5 = trend.get("ma5")
    reversal = bool(sent_trend >= 6 and ma5 and closes[-1] > ma5 and trend["score"] >= -0.3)
    return {
        "date": trend["date"],
        "close": trend["close"],
        "trend": trend,
        "reversal": reversal,
        "sent": {"score": sent, "label": _sent_label(sent), "prior": prior,
                 "trend": sent_trend,
                 "change_pct": round(chg, 2), "dev20": round(dev, 2), "vol_ratio": round(vr, 2)},
        "forecast": {"pattern": pattern, "tone": tone,
                     "chg5": round(chg5, 2), "chg10": round(chg10, 2)},
        # 综合大盘分 [-1, 1]：趋势 0.6 + 情绪（相对 50 轴）0.4
        "score": round(_clamp(0.6 * trend["score"] + 0.4 * (sent - 50) / 50), 3),
        "label": _label(round(_clamp(0.6 * trend["score"] + 0.4 * (sent - 50) / 50), 3)),
    }


def index_daily(ttl=600):
    """实盘用：取上证指数最新日线环境（10 分钟缓存）"""
    def producer():
        try:
            return em.fetch_kline("1.000001", 101, 100)["bars"]
        except Exception:  # noqa: BLE001
            return []

    bars = cached_call("dk:index", ttl, producer) or []
    if not bars:
        return None
    # 盘中最后一根可能是不完整的当日 K，按当日日期切掉
    from datetime import datetime
    return index_ctx(bars, datetime.now().strftime("%Y-%m-%d"))


def state(bars, before_date=None):
    """before_date 为当日日期（不含），回测时按日切片；实盘留空则取最新日线"""
    bars = [b for b in (bars or [])
            if b.get("close") and (not before_date or (b.get("date") or "")[:10] < before_date)]
    if len(bars) < 21:
        return None
    closes = [b["close"] for b in bars]
    highs = [b.get("high") or b["close"] for b in bars]
    lows = [b.get("low") or b["close"] for b in bars]
    vols = [b.get("volume") or 0 for b in bars]
    close = closes[-1]
    ma5, ma10, ma20 = _ma(closes, 5), _ma(closes, 10), _ma(closes, 20)
    hi20, lo20 = max(highs[-20:]), min(lows[-20:])
    pos20 = (close - lo20) / (hi20 - lo20) if hi20 > lo20 else 0.5
    ret5 = (close / closes[-6] - 1) * 100
    ret10 = (close / closes[-11] - 1) * 100
    base_vol = sum(vols[-20:]) / 20
    volr = (sum(vols[-5:]) / 5) / base_vol if base_vol else 1.0
    atr = sum((highs[i] - lows[i]) / closes[i] for i in range(-10, 0)) / 10 * 100
    dev20 = (close / ma20 - 1) * 100 if ma20 else 0
    if ma5 > ma10 > ma20:
        align = "多头排列"
    elif ma5 < ma10 < ma20:
        align = "空头排列"
    else:
        align = "均线纠缠"
    # 底部反转：位置偏低 + 站上 MA5 + MA5 拐头向上 + 放量，用于放行「超跌修复」买入
    ma5_prior = sum(closes[-6:-1]) / 5 if len(closes) >= 6 else ma5
    reversal = bool(pos20 <= 0.35 and close > ma5 and ma5 > ma5_prior and volr >= 1.05
                    and ret5 >= -2)
    score = _clamp(
        0.35 * _clamp(dev20 / 5.0)
        + 0.25 * _clamp((ma5 / ma20 - 1) * 100 / 3.0 if ma20 else 0)
        + 0.20 * (pos20 - 0.5) * 2
        + 0.20 * _clamp(ret10 / 10.0)
    )
    return {
        "date": (bars[-1].get("date") or "")[:10],
        "close": round(close, 2),
        "ma5": round(ma5, 2) if ma5 else None,
        "ma10": round(ma10, 2) if ma10 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "dev20": round(dev20, 2),
        "pos20": round(pos20 * 100, 1),
        "ret5": round(ret5, 2),
        "ret10": round(ret10, 2),
        "volr": round(volr, 2),
        "atr": round(atr, 2),
        "align": align,
        "reversal": reversal,
        "score": round(score, 3),
        "label": _label(score),
    }


def daily(secid, ttl=600):
    """取最新日线状态（实盘用，10 分钟缓存）"""
    def producer():
        try:
            return em.fetch_kline(secid, 101, 90)["bars"]
        except Exception:  # noqa: BLE001
            return []

    bars = cached_call(f"dk:{secid}", ttl, producer) or []
    return state(bars)
