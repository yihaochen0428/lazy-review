"""市场概况：涨跌分布九档 + 成交额对比 + 主力净流入（对标东方财富「市场概况」口径）"""
from datetime import datetime

from . import eastmoney as em
from .db import cached_call

MARKETS = ("1.000001", "0.399001", "0.899050")  # 沪市 / 深市 / 北交所
DIST_LABELS = (
    ("涨停", "up"), ("涨停-5%", "up"), ("5-1%", "warm"), ("1-0%", "warm"),
    ("平盘", "flat"), ("0-1%", "cool"), ("1-5%", "cool"), ("5%-跌停", "down"),
    ("跌停", "down"),
)


def _progress(now):
    """当日交易进度（0-1）：09:30-11:30 + 13:00-15:00，共 240 分钟"""
    m = now.hour * 60 + now.minute
    if m < 9 * 60 + 30:
        return 0.02
    if m < 11 * 60 + 30:
        return max((m - 570) / 240, 0.02)
    if m < 13 * 60:
        return 0.5
    if m < 15 * 60:
        return min((120 + m - 780) / 240, 1.0)
    return 1.0


def _daily_amounts():
    """三市每日成交额（亿元）：沪市 + 深市 + 北交所，按日期合并；东财不可用时退回同花顺（沪深两市）"""
    def producer():
        sums = {}
        ok = 0
        for secid in MARKETS:
            try:
                bars = em.fetch_kline(secid, 101, 65).get("bars") or []
            except em.DataError:
                bars = []
            usable = [bar for bar in bars if bar.get("amount") and bar.get("date")]
            if usable:
                ok += 1
            for bar in usable:
                date = bar["date"][:10]
                sums[date] = sums.get(date, 0) + bar["amount"]
        # 必须是「沪深两市都有金额」且历史够长，否则退回同花顺（备用源缺 amount 时 sums 会为空）
        if ok >= 2 and len(sums) >= 20:
            return {k: round(v / 1e8, 2) for k, v in sums.items()}
        try:
            ths = em.fetch_ths_amounts() or {}
        except em.DataError:
            ths = {}
        if ths:
            return ths
        return {k: round(v / 1e8, 2) for k, v in sums.items()}  # 两个源都残缺时的兜底

    return cached_call("ov:amount:v5", 300, producer) or {}


def _amount_block(now):
    sums = _daily_amounts()
    if not sums:
        return None
    dates = sorted(sums)
    today = now.strftime("%Y-%m-%d")
    latest = dates[-1]
    traded_today = latest == today
    current = sums[latest]
    prev = sums[dates[-2]] if len(dates) >= 2 else None
    prior = dates[:-1][-60:]
    avg60 = round(sum(sums[d] for d in prior) / len(prior), 2) if prior else None
    progress = _progress(now) if traded_today else 1.0
    forecast = round(current / progress, 2) if progress else current
    prev_same = round(prev * progress, 2) if prev else None
    delta = round(current - prev_same, 2) if prev_same else None
    delta_pct = round(delta / prev_same * 100, 2) if prev_same else None
    avg_pct = round((forecast / avg60 - 1) * 100, 2) if avg60 else None
    return {
        "date": latest,
        "is_today": traded_today,
        "total": current,
        "prev": prev,
        "delta": delta,
        "delta_pct": delta_pct,
        "forecast": forecast,
        "avg60": avg60,
        "avg60_pct": avg_pct,
        "progress": round(progress, 3),
    }


def _distribution(breadth, limit_up, limit_down):
    dist = em.fetch_distribution()
    total = dist.get("total") or breadth.get("total") or 0
    up = breadth.get("up") or 0
    flat = breadth.get("flat") or 0
    down = breadth.get("down") or 0
    ge5 = dist.get("ge5") or 0
    ge1 = dist.get("ge1") or 0
    ge_neg1 = dist.get("ge_neg1") or total
    ge_neg5 = dist.get("ge_neg5") or total
    counts = (
        limit_up,                              # 涨停
        max(ge5 - limit_up, 0),                # 涨停—5%
        max(ge1 - ge5, 0),                     # 5%—1%
        max(up - ge1, 0),                      # 1%—0%
        flat,                                  # 平盘
        max(down - (total - ge_neg1), 0),      # 0—-1%
        max(ge_neg5 - ge_neg1, 0),             # -1%—-5%
        max((total - ge_neg5) - limit_down, 0),  # -5%—跌停
        limit_down,                            # 跌停
    )
    return [{"label": label, "tone": tone, "count": int(count)}
            for (label, tone), count in zip(DIST_LABELS, counts)]


def build(breadth, sentiment, limit_up, limit_down, date, now=None):
    """组装市场概况卡片数据；任何子项失败都降级而不是整块报错"""
    if not breadth:
        return None
    now = now or datetime.now()
    try:
        distribution = _distribution(breadth, limit_up, limit_down)
    except Exception:  # noqa: BLE001
        distribution = []
    try:
        main_flow = em.fetch_market_flow()
    except Exception:  # noqa: BLE001
        main_flow = None
    return {
        "date": (date or "")[:10],
        "up": int(breadth.get("up") or 0),
        "flat": int(breadth.get("flat") or 0),
        "down": int(breadth.get("down") or 0),
        "limit_up": int(limit_up or 0),
        "limit_down": int(limit_down or 0),
        "distribution": distribution,
        "main_flow": main_flow,
        "sentiment": {
            "score": (sentiment or {}).get("score"),
            "label": (sentiment or {}).get("label"),
            "tone": (sentiment or {}).get("tone"),
        },
        "amount": _amount_block(now),
    }
