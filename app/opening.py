"""开盘买入决策：指数竞价 / 板块 ETF 竞价 / 个股竞价强度打分

说明：开盘强度一律用「今开 vs 昨收」。09:30 前今开字段还是 0，此时用集合竞价的
最新匹配价（现价字段）代替；盘后今开就是当日真实开盘价，作为下一交易日开盘参考。
不要用「现价/收盘 vs 昨收」——那会把盘中或尾盘涨幅误当成竞价高开。
"""
import time
from datetime import datetime

from . import catalyst
from . import dkline
from . import eastmoney as em
from . import tenv
from .db import cached_call

INDEX_SYMBOLS = (("sh000001", "上证指数"), ("sz399006", "创业板指"))


def _symbol(secid):
    market, code = secid.split(".")
    return ("sh" if market == "1" else "sz") + code


def _pct(price, prev):
    if not price or not prev:
        return None
    return round((price / prev - 1) * 100, 2)


def _clamp(value, low=-1.0, high=1.0):
    return max(low, min(high, value))


def _quotes(symbols):
    """一次批量取腾讯快照，30 秒缓存"""
    def producer():
        try:
            return em._tencent_quotes([s for s, _ in symbols])
        except Exception:  # noqa: BLE001
            return {}

    key = "open:quotes:" + ",".join(s for s, _ in symbols)
    return cached_call(key, 30, producer) or {}


def _phase():
    now = datetime.now()
    hm = now.strftime("%H:%M")
    if hm < "09:15":
        return "盘前", "09:15 进入集合竞价，届时刷新可看到竞价强度"
    if hm < "09:25":
        return "集合竞价", "竞价撮合中，价格与匹配量仍在变化"
    if hm < "09:30":
        return "竞价结束", "等待 09:30 开盘成交"
    if hm < "10:00":
        return "开盘半小时", "以开盘后 15 分钟能否站稳均价线作为确认"
    if hm < "15:00":
        return "盘中", "开盘窗口已过，以下为即时环境参考"
    return "盘后", "以下一交易日开盘为参考（按今日收盘环境计算）"


def score_gaps(idx_pct, sec_pct, gap):
    """竞价三因子打分：指数 0.45 / 板块 0.35 / 个股相对 0.2（与实盘建议同一套公式）"""
    idx_score = _clamp((idx_pct or 0) / 1.2)
    sec_score = _clamp((sec_pct or 0) / 1.5) if sec_pct is not None else idx_score
    stock_score = _clamp((gap or 0) / 2.0)
    relative = round(stock_score - sec_score, 3)
    total = round(_clamp(0.45 * idx_score + 0.35 * sec_score + 0.2 * relative), 3)
    return {
        "index_score": idx_score,
        "sector_score": sec_score,
        "stock_score": stock_score,
        "relative": relative,
        "total": total,
    }


def _support(score):
    """低吸分 = -追高分

    近 20 日行业龙头回测显示方向是反的：个股高开 0.5%~1% 胜率仅 6.2%、
    1%~2% 为 19%，而低开 0.5%~1% 达 44.9%，指数高开 1% 以上只有 16.7%。
    即开盘「强共振追买」是负期望，低开且相对板块抗跌才值得低吸。
    """
    return round(-score, 3)


def _decide(support, gap, idx_pct, sec_gap, dk=None, mkt=None, pattern=None, sent=None,
            momentum=None, cat=None, reversal=False, sector_dk=None):
    """开盘决策 = 竞价低吸分 × 个股日K × 板块日K × 大盘趋势/情绪 × 催化

    两条通道：
    1. 低吸通道（gap < 0.5%）：竞价低开 + 日K 偏多 + 大盘不弱 + 板块同步 → 低吸；
       若个股日K 出现底部反转（位置低、站上 MA5、MA5 拐头、放量），有板块与催化配合时
       升级为「反转修复」买入；
    2. 强势通道（gap >= 0.5%）：高开本身胜率低，但个股日K 与板块日K 共振、且外盘/消息有催化时，
       允许小仓位半路跟随（不再一刀切否决）。
    """
    if pattern == "高位放量滞涨" and (sent or 0) >= 45:
        return "不建议开盘买入", "low", "0-1 成", "大盘高位放量滞涨，个股高开/低开都容易补跌"
    if gap is not None and gap >= 2.0:
        if ((momentum or 0) >= 0.50 and (cat or 0) >= 0.25
                and (sector_dk or 0) >= 0.25 and gap <= 6.0):
            return ("强势可追（半路）", "high", "1 成",
                    "个股与板块日K 共振、外盘/消息有催化，强势高开可半路 1 成试错")
        return "不建议开盘买入", "low", "0-1 成", "竞价高开 2% 以上且缺少板块/催化共振，不追第一笔"
    if gap is not None and gap >= 0.5:
        if (momentum or 0) >= 0.50 and (cat or 0) >= 0.10:
            return ("回踩可轻仓", "mid", "1 成",
                    "高开但日K/板块共振，等回踩分时均价线再轻仓介入")
        return "高开不追，等回踩", "low", "0-1 成", "个股高开 0.5% 以上，第一笔买入性价比低"
    if dk is not None and dk < -0.15 and not reversal:
        return "观望", "low", "0-1 成", "日K 偏空（跌破 MA20 / 均线空头），低开不接飞刀"
    combo = _combo(support, dk, mkt)
    if reversal and (sector_dk or 0) >= 0.15 and (cat or 0) >= 0.2:
        return ("可以买入（反转修复）", "high", "2-3 成",
                "个股日K 底部反转 + 板块日K 同步 + 催化配合，按反转修复对待")
    if combo >= 0.35:
        return "可以买入（低吸）", "high", "3-5 成", "日K 偏多、大盘不弱且竞价低开，日内修复概率较高"
    if combo >= 0.15:
        return "可轻仓试买", "mid", "2-3 成", "日K 中性偏多、竞价偏弱，先小仓位试错"
    if combo >= -0.1:
        return "观望", "low", "0-2 成", "竞价、日K、板块与大盘都不支持低吸，等分时确认"
    return "不建议开盘买入", "low", "0-1 成", "日K 与大盘同步走弱，开盘不接力"


def _combo(support, dk, mkt=None):
    """综合分：竞价低吸 0.60 / 个股日K 0.25 / 大盘 0.15（缺项时按剩余权重归一）"""
    if dk is None and mkt is None:
        return support
    if dk is None:
        return round(0.75 * support + 0.25 * mkt, 3)
    if mkt is None:
        return round(0.65 * support + 0.35 * dk, 3)
    return round(0.60 * support + 0.25 * dk + 0.15 * mkt, 3)


def _combo_label(score):
    if score >= 0.35:
        return "值得低吸"
    if score >= 0.15:
        return "可小仓试"
    if score > -0.1:
        return "中性"
    return "不参与"


# 大盘剧本：情绪分档给仓位上限，K 线形态给入场/止损/目标
_SENT_CAP = ((30, 1), (45, 2), (65, 4), (101, 3))
_PATTERN_PLAN = {
    "放量突破": {"cap": 4, "stop": 0.6, "target": 1.5,
                "entry": "09:35 前站稳开盘价与分时均价线，回踩不破再低吸",
                "why": "指数放量突破 20 日高点，个股低开多为错杀"},
    "底部放量企稳": {"cap": 4, "stop": 0.5, "target": 1.5,
                  "entry": "指数分时止跌回升、个股回踩均价线不破再买",
                  "why": "指数放量止跌，超跌修复窗口打开"},
    "缩量止跌": {"cap": 2, "stop": 0.5, "target": 1.2,
                "entry": "等 09:45 后指数缩量横盘、个股止跌再介入",
                "why": "缩量止跌只是止跌，反转需放量确认"},
    "高位放量滞涨": {"cap": 1, "stop": 0.4, "target": 0.8,
                  "entry": "不参与开盘低吸，等指数回踩 5 日线企稳",
                  "why": "指数高位放量滞涨，个股低开容易补跌"},
    "偏离 MA20 偏大": {"cap": 2, "stop": 0.5, "target": 1.0,
                   "entry": "只做分时明显止跌的个股，仓位压到上限内",
                   "why": "指数偏离 MA20 过大，均值回归压力大"},
    "超跌偏离 MA20": {"cap": 3, "stop": 0.6, "target": 1.2,
                   "entry": "指数分时不再新低后低吸，反弹到均价线附近先减",
                   "why": "指数超跌，低开修复概率提升但要快进快出"},
    "震荡整理": {"cap": 3, "stop": 0.6, "target": 1.0,
               "entry": "09:35–09:45 分时止跌、指数不新低再分批低吸",
               "why": "指数无方向，按个股自身强弱做"},
}


def _cap_position(position, cap):
    """把「3-5 成」按大盘剧本压到上限"""
    text = position.replace(" 成", "")
    lo, hi = (int(x) for x in text.split("-")) if "-" in text else (int(text), int(text))
    lo, hi = min(lo, cap), min(hi, cap)
    return f"{lo} 成" if lo == hi else f"{lo}-{hi} 成"


def _momentum(dk, sector_dk, mkt):
    """强弱动量：个股日K 0.45 + 板块日K 0.30 + 大盘 0.25（缺项归一）"""
    parts = [(w, v) for w, v in ((0.45, dk), (0.30, sector_dk), (0.25, mkt)) if v is not None]
    if not parts:
        return None
    total = sum(w for w, _ in parts)
    return round(sum(w * v for w, v in parts) / total, 3)


def _market_plan(mkt):
    """情绪指数 + K 线形态 -> 具体剧本（仓位上限 / 入场 / 止损 / 目标）"""
    if not mkt:
        return None
    sent = mkt["sent"]
    pattern = mkt["forecast"]["pattern"]
    cfg = _PATTERN_PLAN.get(pattern) or _PATTERN_PLAN["震荡整理"]
    cap = min(next(c for limit, c in _SENT_CAP if sent["score"] < limit), cfg["cap"])
    return {
        "regime": f"{sent['label']} · {pattern}",
        "cap": cap,
        "stop": cfg["stop"],
        "target": cfg["target"],
        "entry": cfg["entry"],
        "why": cfg["why"],
    }


def _auction_gap(fields):
    """开盘竞价涨幅：今开 vs 昨收；集合竞价阶段（今开为 0）用最新匹配价

    注意不能用「现价 vs 昨收」，那会把盘中/尾盘涨幅误当成开盘竞价（尾盘 +3.65% 的票
    可能开盘是水下低吸机会）。
    """
    if not fields:
        return None
    prev = em.num(fields[4])
    open_ = em.num(fields[5])
    price = em.num(fields[3])
    return _pct(open_ if open_ else price, prev)


def advice(meta, board_names):
    """返回开盘买入建议（数据缺失时尽量降级为指数环境判断）"""
    sector = tenv._match_sector(board_names)
    etf = tenv.ETF_PROXY.get(sector) if sector else None
    symbols = list(INDEX_SYMBOLS)
    if etf:
        symbols.append((_symbol(etf[0]), etf[1]))
    quotes = _quotes(symbols)

    idx_items = []
    for sym, name in INDEX_SYMBOLS:
        fields = quotes.get(sym)
        if not fields:
            continue
        idx_items.append({
            "name": name,
            "price": em.num(fields[3]),
            "open": em.num(fields[5]),
            "gap_pct": _auction_gap(fields),
        })
    idx_pct = round(sum(i["gap_pct"] or 0 for i in idx_items) / len(idx_items), 2) if idx_items else None

    sec_gap = None
    etf_name = None
    if etf:
        fields = quotes.get(_symbol(etf[0]))
        etf_name = etf[1]
        if fields:
            sec_gap = _auction_gap(fields)

    price = (meta or {}).get("price")
    prev = (meta or {}).get("prev_close")
    open_ = (meta or {}).get("open")
    gap = _pct(open_, prev) if (open_ and prev) else (meta or {}).get("change_pct")
    if gap is None:
        gap = _pct(price, prev)
    scores = score_gaps(idx_pct, sec_gap if etf else None, gap)
    idx_score = scores["index_score"]
    relative = scores["relative"]
    support = _support(scores["total"])
    code = str((meta or {}).get("code") or "")
    dk = dkline.daily(f"{em.market_prefix(code)}.{code}") if code else None
    dk_score = (dk or {}).get("score")
    sector_dk = dkline.daily(etf[0]) if etf else None
    sector_dk_score = (sector_dk or {}).get("score")
    mkt = dkline.index_daily()
    mkt_score = (mkt or {}).get("score")
    combo = _combo(support, dk_score, mkt_score)

    plan = _market_plan(mkt)
    pattern = (mkt or {}).get("forecast", {}).get("pattern") if mkt else None
    sent_score = mkt["sent"]["score"] if mkt else None
    keywords = [sector] + [n for n in (board_names or [])] + [(meta or {}).get("industry")]
    cat = catalyst.build(sector, keywords, mkt)
    momentum = _momentum(dk_score, sector_dk_score, mkt_score)
    reversal = bool((dk or {}).get("reversal"))
    action, level, position, why = _decide(support, gap, idx_pct, sec_gap, dk_score, mkt_score,
                                           pattern, sent_score, momentum, cat["score"],
                                           reversal, sector_dk_score)
    if plan:
        # 情绪/形态给出的仓位上限覆盖 _decide 的默认仓位
        position = _cap_position(position, plan["cap"])
    phase, phase_note = _phase()

    reasons = [
        f"指数竞价：{idx_pct if idx_pct is not None else '-'}%（上证/创业板平均，{tenv._label(idx_score)}）",
        ("板块竞价：%s（%s）%s%%" % (sector, etf_name, sec_gap)
         if etf and sec_gap is not None else "板块竞价：未匹配到行业 ETF，用指数环境替代"),
        f"个股竞价：{gap if gap is not None else '-'}%，相对板块 {relative}",
        (f"日K 背景：{dk['label']}（{dk['align']}，MA20 偏离 {dk['dev20']}%，"
         f"20 日分位 {dk['pos20']}%，近 5 日 {dk['ret5']}%）" if dk else "日K 背景：日线数据不足"),
        (f"大盘环境：{mkt['label']}（近 5 日 {mkt['forecast']['chg5']}% · "
         f"情绪 {mkt['sent']['score']} {mkt['sent']['label']} · {mkt['forecast']['pattern']}）"
         if mkt else "大盘环境：指数日线数据不足"),
        (f"板块日K：{sector_dk['label']}（{sector_dk['align']}，MA20 偏离 {sector_dk['dev20']}%，"
         f"近 5 日 {sector_dk['ret5']}%）" if sector_dk else "板块日K：ETF 日线数据不足"),
        (f"催化：{cat['news']['label']}（{cat['news']['count']} 条匹配）· "
         f"{cat['overseas']['label']}{(' ' + str(cat['overseas'].get('avg_pct')) + '%') if cat['overseas'].get('avg_pct') is not None else ''}"
         f" · 情绪 5 日变化 {cat['sent_trend'] if cat['sent_trend'] is not None else '-'}"),
    ]
    if plan:
        reasons.append(f"大盘剧本：{plan['regime']} → 仓位上限 {plan['cap']} 成、"
                       f"目标 +{plan['target']}%（{plan['why']}）")
    if mkt_score is not None and mkt_score < -0.45:
        reasons.append("大盘趋势/情绪偏弱，指数下行时个股低开容易被继续拖累，低吸仓位减半")
    elif mkt_score is not None and mkt_score >= 0.45:
        reasons.append("大盘偏强，低开多属日内错杀，修复概率更高")
    if gap is not None and gap > 0 and level == "low":
        reasons.append("高开越多胜率越低（回测：高开 0.5%~1% 仅 6.2%），不追第一笔")
    if level == "high" and gap is not None and gap >= 0.5:
        reasons.append("强势/催化通道：回测窗口内高开样本无一满足该条件，属规则外推，只用 1-2 成试错")
    if momentum is not None and gap is not None:
        reasons.append(f"动量分 {momentum}（个股日K {dk_score} · 板块日K {sector_dk_score} · 大盘 {mkt_score}）")
    if cat["news"]["items"]:
        top = cat["news"]["items"][0]
        reasons.append(f"消息催化：{top['impact']}（{top.get('time_text') or '-'}）"
                       f"{(top.get('title') or '')[:30]}")
    if dk_score is not None and dk_score < -0.15:
        reasons.append("日K 偏空/空头，低开先视为下跌中继，不接飞刀")
    elif gap is not None and gap < 0 and dk_score is not None and dk_score >= 0.15:
        reasons.append("日K 多头且当日低开，属回踩修复，是主要低吸机会")

    plans = [
        "高开 0.5% 以上只在「个股日K + 板块日K 共振 + 催化」时用 1 成做半路，跌破分时均价线离场",
        "日K 偏多且低开时，09:35–09:45 分时止跌、指数不继续新低，再分批低吸",
        "日K 空头（跌破 MA20 且均线空头）时不接低开，等日线转强",
        "跌破开盘价 0.6% 或指数继续走弱，当日买入计划取消",
    ]
    note = (f"综合分 {combo}（竞价低吸 {support} · 个股日K "
            f"{dk_score if dk_score is not None else '-'} · 板块日K "
            f"{sector_dk_score if sector_dk_score is not None else '-'} · 大盘 "
            f"{mkt_score if mkt_score is not None else '-'} · 催化 "
            f"{cat['score'] if cat['score'] is not None else '-'}）：低开 + 日K 偏多 + 大盘不弱的"
            "低吸组合回测胜率约 68%；高开仅在「个股日K + 板块日K 共振 + 外盘/消息催化」时开 1 成"
            "强势通道（该通道回测窗口内无样本，属规则外推）。"
            "本建议基于竞价/即时快照、T-1 日线、板块 ETF 日线与外盘，开盘后需按分时确认。")

    return {
        "phase": phase,
        "phase_note": phase_note,
        "updated_at": time.strftime("%H:%M:%S"),
        "index": {"gap_pct": idx_pct, "label": tenv._label(idx_score), "items": idx_items},
        "sector": {"name": sector or "", "etf": etf_name or "", "gap_pct": sec_gap,
                   "matched": bool(etf), "label": tenv._label(scores["sector_score"])},
        "stock": {"gap_pct": gap, "relative": relative, "label": tenv._label(scores["stock_score"]),
                  "price": price, "prev_close": prev, "open": open_,
                  "change_pct": (meta or {}).get("change_pct")},
        "dk": dk,
        "market": mkt,
        "sector_dk": sector_dk,
        "catalyst": cat,
        "momentum": momentum,
        "reversal": reversal,
        "plan": plan,
        "score": combo,
        "label": _combo_label(combo),
        "action": action,
        "level": level,
        "position": position,
        "reason": why,
        "entry": ("开盘站稳分时均价线后分批，跌破均价线不追"
                  if (gap is not None and gap >= 0.5 and level != "low")
                  else plan["entry"] if plan
                  else ("低开不追跌，09:35–09:45 止跌后分批低吸" if level == "high"
                        else "等分时止跌或回踩均价线后再介入")),
        "stop": ("开盘价下方 1.2% 或跌破分时均价线"
                 if (gap is not None and gap >= 0.5 and level != "low")
                 else f"开盘价下方 {plan['stop']}%（或分时均价线失守）" if plan
                 else "开盘价下方 0.6%（或分时均价线失守）"),
        "target": ("日内 +2.0%，冲高不放量先减半"
                   if (gap is not None and gap >= 0.5 and level != "low")
                   else f"日内 +{plan['target']}%，冲高不放量先减半" if plan
                   else "日内 +1.0%~1.5%，冲高不放量先减半"),
        "reasons": reasons,
        "plans": plans,
        "note": note,
    }


# ---------------------------------------------------------------- 开盘策略回测
def _map_from(bars):
    """日线 bars -> date: {open, close, prev_close}"""
    out = {}
    prev = None
    for bar in bars or []:
        date = (bar.get("date") or "")[:10]
        if not date:
            continue
        out[date] = {"open": bar.get("open"), "close": bar.get("close"), "prev_close": prev}
        prev = bar.get("close")
    return out


def _daily_map(secid, limit=40):
    """date -> {open, close, prev_close}，用于还原当日开盘竞价涨幅"""
    try:
        return _map_from(em.fetch_kline(secid, 101, limit)["bars"])
    except Exception:  # noqa: BLE001
        return {}


def _gap(bar):
    if not bar or not bar.get("prev_close") or not bar.get("open"):
        return None
    return round((bar["open"] / bar["prev_close"] - 1) * 100, 2)


def _simulate(day_bars):
    """开盘价买入：先止损（-0.6%）后目标（+1.0%），未触发按收盘平仓"""
    if not day_bars:
        return None
    entry = day_bars[0].get("open") or day_bars[0].get("close")
    if not entry:
        return None
    target = entry * 1.010
    stop = entry * 0.994
    for bar in day_bars:
        if (bar.get("low") or entry) <= stop:
            return round((stop / entry - 1) * 100, 2)
        if (bar.get("high") or entry) >= target:
            return round((target / entry - 1) * 100, 2)
    close = day_bars[-1].get("close") or entry
    return round((close / entry - 1) * 100, 2)


def _stat(rows):
    if not rows:
        return {"samples": 0, "win_rate": None, "avg_return": None}
    return {
        "samples": len(rows),
        "win_rate": round(sum(1 for r in rows if r["ret"] > 0) / len(rows) * 100, 1),
        "avg_return": round(sum(r["ret"] for r in rows) / len(rows), 2),
    }


def _collect(days=20):
    from .analysis import SECTOR_PLAYS

    days = max(5, min(int(days or 20), 20))
    idx_maps = {secid: _daily_map(secid, days + 10) for secid, _ in tenv.INDEXES}
    idx_bars = em.fetch_kline("1.000001", 101, days + 90)["bars"]
    rows = []
    limit_days = 0
    for sector, info in SECTOR_PLAYS.items():
        etf = tenv.ETF_PROXY.get(sector)
        etf_map = _daily_map(etf[0], days + 10) if etf else {}
        etf_bars = tenv._daily_bars(etf[0], days + 40) if etf else []
        for code, name, _reason in [x for x in info.get("leaders", [])][:2]:
            secid = f"{em.market_prefix(code)}.{code}"
            daily = tenv._daily_bars(secid, days + 40)
            stock_map = _map_from(daily)
            by_day, _prev = tenv._series(secid, days)
            streaks = tenv._streak_map(daily, code)
            all_dates = sorted(stock_map)
            for day in sorted(d for d in by_day if d in stock_map):
                prev_day = all_dates[all_dates.index(day) - 1] if day in all_dates and all_dates.index(day) > 0 else None
                # 与做T回测一致：剔除连板/涨停封板日
                if (streaks.get(day) or 0) >= 1 or (streaks.get(prev_day) or 0) >= 2:
                    limit_days += 1
                    continue
                gap = _gap(stock_map[day])
                if gap is None:
                    continue
                idx_gaps = [_gap(idx_maps[secid_idx].get(day)) for secid_idx, _ in tenv.INDEXES]
                idx_gaps = [g for g in idx_gaps if g is not None]
                if not idx_gaps:
                    continue
                idx_pct = round(sum(idx_gaps) / len(idx_gaps), 2)
                sec_gap = _gap(etf_map.get(day)) if etf else None
                scores = score_gaps(idx_pct, sec_gap, gap)
                support = _support(scores["total"])
                dk = dkline.state(daily, day) or {}
                mkt = dkline.index_ctx(idx_bars, day) or {}
                sector_dk = dkline.state(etf_bars, day) or {}
                momentum = _momentum(dk.get("score"), sector_dk.get("score"), mkt.get("score"))
                action, level, _pos, _why = _decide(
                    support, gap, idx_pct, sec_gap, dk.get("score"), mkt.get("score"),
                    (mkt.get("forecast") or {}).get("pattern"),
                    (mkt.get("sent") or {}).get("score"),
                    momentum, None, bool(dk.get("reversal")), sector_dk.get("score"))
                ret = _simulate(by_day.get(day))
                if ret is None:
                    continue
                rows.append({
                    "sector": sector, "name": name, "code": code, "date": day,
                    "action": action, "level": level, "score": support,
                    "rel": scores["relative"], "idx": idx_pct, "sec": sec_gap, "gap": gap, "ret": ret,
                    "dk": dk.get("score"), "pos20": dk.get("pos20"), "ret5": dk.get("ret5"),
                    "ret10": dk.get("ret10"), "volr": dk.get("volr"), "dev20": dk.get("dev20"),
                    "align": dk.get("align"),
                    "mkt": mkt.get("score"), "sent": (mkt.get("sent") or {}).get("score"),
                    "mkt_trend": (mkt.get("trend") or {}).get("score"),
                    "pattern": (mkt.get("forecast") or {}).get("pattern"),
                    "sector_dk": sector_dk.get("score"), "momentum": momentum,
                    "reversal": bool(dk.get("reversal")),
                })
    return rows, {"limit_days": limit_days}


def _run_backtest(days=20):
    rows, extra = _collect(days)
    buy_actions = ("可以买入（低吸）", "可以买入（反转修复）", "强势可追（半路）", "回踩可轻仓")
    actionable = [r for r in rows if r["action"] in buy_actions or r["action"] == "可轻仓试买"]
    avoid = [r for r in rows if r["action"] not in buy_actions and r["action"] != "可轻仓试买"]
    # 对照：不含日K、只用竞价低吸分的旧口径
    support_only = [r for r in rows if r["gap"] < 0.5 and (r["score"] or 0) >= 0.1]
    groups = {}
    for row in rows:
        groups.setdefault(row["action"], []).append(row)
    by_action = [{"action": key, **_stat(value)} for key, value in groups.items()]
    return {
        "days": days,
        "updated_at": time.strftime("%Y-%m-%d %H:%M"),
        "samples": len(rows),
        "baseline": _stat(rows),
        "filtered": _stat(actionable),
        "support_only": _stat(support_only),
        "avoid": _stat(avoid),
        "by_action": by_action,
        "excluded_limit_days": extra["limit_days"],
        "note": ("开盘价买入、日内先止损（-0.6%）后目标（+1.0%），未触发按收盘平仓；"
                 "样本为行业龙头近 N 个交易日，已剔除连板/涨停日；"
                 "个股日K 与大盘日K 只使用 T-1 及之前的日线（无未来函数），未计手续费与滑点。"),
    }


def backtest(days=20, force=False, phase=""):
    """策略结论按 09:25 / 15:00 / 24:00 三个时段换挡，同一时段内复用缓存"""
    days = max(5, min(int(days or 20), 20))
    if force:
        return _run_backtest(days)
    stamp = f"{datetime.now():%Y%m%d}" + (f":{phase}" if phase else "")
    return cached_call(f"open:bt:v5:{days}:{stamp}", 86400, lambda: _run_backtest(days))

