"""业务编排：每日快照、板块、个股、涨停复盘、指数研判"""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from . import eastmoney as em
from . import news as news_src
from . import research
from . import chan as chan_src
from . import intraday
from . import tenv
from . import tscore
from . import persona
from . import dkline
from . import ksig
from . import opening
from . import overview as overview_src
from . import ths
from .analysis import (build_ai_review, compute_sentiment, analyze_index, analyze_stock_indicators,
                       build_stock_strategy, sentiment_history, sentiment_outlook, news_item_analysis,
                       NOISE_CONCEPTS)
from .config import SNAPSHOT_TTL, HISTORY_SNAPSHOT_TTL
from .db import snapshot_get, snapshot_put, snapshot_dates, cached_call
from .indicators import macd, kdj, rsi, ma, fib_levels


def compact(date: str) -> str:
    return (date or "").replace("-", "")


def iso(compact_date: str) -> str:
    if not compact_date or len(compact_date) != 8:
        return compact_date
    return f"{compact_date[:4]}-{compact_date[4:6]}-{compact_date[6:]}"


def _safe(future, default):
    try:
        return future.result()
    except Exception:  # noqa: BLE001
        return default


def latest_trading_date():
    kline = em.fetch_kline("1.000001", 101, 40)
    dates = [bar["date"] for bar in kline["bars"]]
    if not dates:
        raise em.DataError("无法获取交易日")
    return dates[-1]


def _clean_concepts(concepts, industry=""):
    cleaned = []
    for name in concepts or []:
        if not name or name == "-":
            continue
        if name.endswith("板块") or name in NOISE_CONCEPTS or name.endswith("_"):
            continue
        if name == industry:
            continue
        if name not in cleaned:
            cleaned.append(name)
    return cleaned[:3]


def _reason(stock, concepts):
    lead = concepts[0] if concepts else (stock.get("industry") or "题材")
    board = stock.get("board") or 1
    if board == 1:
        text = f"{lead} 题材催化，{stock.get('industry') or '板块'}内首板"
    else:
        text = f"{lead} 主线延续，{board} 连板晋级"
    if stock.get("break_times"):
        text += f"，盘中开板 {stock['break_times']} 次"
    return text


def _build_limitup(zt, dt, zb, concept_map, prev_zt):
    stocks = []
    for item in zt.get("items", []):
        info = concept_map.get(item["code"]) or {}
        concepts = _clean_concepts(info.get("concepts"), item.get("industry"))
        stock = dict(item)
        stock["concepts"] = concepts or ([item["industry"]] if item.get("industry") else [])
        stock["industry"] = info.get("industry") or item.get("industry")
        stock["reason"] = _reason(stock, concepts)
        stocks.append(stock)

    stocks.sort(key=lambda s: (-s["board"], s["seal_time"]))

    twenty_cm = [s for s in stocks if abs(s["change_pct"]) >= 19]
    lianban = [s for s in stocks if s["board"] >= 2]
    first_board = [s for s in stocks if s["board"] == 1]
    broken = zb.get("items", [])
    zt_count = len(stocks)
    broken_rate = round(len(broken) / max(zt_count + len(broken), 1) * 100, 1)
    seal_rate = round(zt_count / max(zt_count + len(broken), 1) * 100, 1)

    prev_ladder = [it for it in prev_zt.get("items", []) if it["board"] >= 2]
    today_map = {it["code"]: it for it in stocks}
    promoted = [p for p in prev_ladder if today_map.get(p["code"], {}).get("board", 0) > p["board"]]
    broken_codes = [p["code"] for p in prev_ladder if p["code"] not in today_map]
    quotes = em.fetch_quotes(broken_codes) if broken_codes else {}
    broken_stocks = []
    for p in prev_ladder:
        if p["code"] in today_map:
            continue
        quote = quotes.get(p["code"]) or {}
        broken_stocks.append({
            "code": p["code"],
            "name": p["name"],
            "board": p["board"],
            "change_pct": quote.get("change_pct"),
            "price": quote.get("price"),
            "industry": p.get("industry"),
        })
    broken_perf = None
    perf_values = [b["change_pct"] for b in broken_stocks if b["change_pct"] is not None]
    if perf_values:
        broken_perf = round(sum(perf_values) / len(perf_values), 2)

    stats = {
        "limit_up": zt_count,
        "first_board": len(first_board),
        "lianban": len(lianban),
        "twenty_cm": len(twenty_cm),
        "broken": len(broken),
        "broken_rate": broken_rate,
        "seal_rate": seal_rate,
        "limit_down": dt.get("total", 0),
        "max_board": max([s["board"] for s in stocks], default=0),
        "promote_rate": round(len(promoted) / len(prev_ladder) * 100, 1) if prev_ladder else 0,
        "prev_lianban": len(prev_ladder),
        "promoted": len(promoted),
        "broken_avg": broken_perf,
    }

    # 按概念聚合
    groups = {}
    for stock in stocks:
        for name in stock["concepts"][:2]:
            groups.setdefault(name, []).append(stock)
    concepts_view = []
    for name, items in groups.items():
        items_sorted = sorted(items, key=lambda s: (-s["board"], s["seal_time"]))
        concepts_view.append({
            "name": name,
            "count": len(items),
            "leader": items_sorted[0]["name"],
            "leader_code": items_sorted[0]["code"],
            "max_board": items_sorted[0]["board"],
            "stocks": [{"code": s["code"], "name": s["name"], "board": s["board"],
                        "change_pct": s["change_pct"], "seal_time": s["seal_time"]} for s in items_sorted],
        })
    concepts_view.sort(key=lambda c: (-c["count"], -c["max_board"], c["name"]))
    concepts_view = concepts_view[:24]

    return {
        "stats": stats,
        "stocks": stocks,
        "concepts": concepts_view,
        "broken": broken,
        "limit_down": dt.get("items", []),
        "broken_stocks": broken_stocks,
    }


def _build_ladder(limitup):
    levels = {}
    for stock in limitup["stocks"]:
        levels.setdefault(stock["board"], []).append(stock)
    ladder_levels = []
    for board in sorted(levels.keys(), reverse=True):
        items = sorted(levels[board], key=lambda s: -(s.get("amount") or 0))
        ladder_levels.append({
            "board": board,
            "label": "首板" if board == 1 else f"{board}板",
            "count": len(items),
            "stocks": [{
                "code": s["code"], "name": s["name"], "board": s["board"],
                "change_pct": s["change_pct"], "reason": s["reason"],
                "concepts": s["concepts"], "seal_time": s["seal_time"],
                "amount": s["amount"], "turnover": s["turnover"],
            } for s in items],
        })
    return {"levels": ladder_levels, "stats": limitup["stats"]}


def _index_from_kline(index_meta, bars, target_date):
    bar = next((b for b in bars if b["date"] == target_date), None)
    if not bar:
        return None
    return {
        "code": index_meta["code"],
        "secid": index_meta["secid"],
        "name": index_meta["name"],
        "short": index_meta["short"],
        "price": bar["close"],
        "change": bar["change"],
        "change_pct": bar["change_pct"],
        "amount": bar["amount"],
        "spark": [b["close"] for b in bars[-30:]],
    }


def build_dashboard(date: str = None, force: bool = False):
    latest = latest_trading_date()
    target = date or latest
    cached = snapshot_get(target)
    ttl = SNAPSHOT_TTL if target == latest else HISTORY_SNAPSHOT_TTL
    phase = analysis_phase()[0]
    same_phase = (cached or {}).get("payload", {}).get("refresh", {}).get("phase") == phase
    if cached and not force and same_phase and time.time() - cached["built_at"] < ttl:
        payload = cached["payload"]
        payload["cached"] = True
        return payload

    with ThreadPoolExecutor(max_workers=8) as pool:
        f_indices = pool.submit(em.fetch_indices)
        f_breadth = pool.submit(em.fetch_breadth)
        f_zt = pool.submit(em.fetch_pool, "zt", compact(target))
        f_dt = pool.submit(em.fetch_pool, "dt", compact(target))
        f_zb = pool.submit(em.fetch_pool, "zb", compact(target))
        f_industry = pool.submit(em.fetch_boards, "industry")
        f_concept = pool.submit(em.fetch_boards, "concept")
        f_spark = pool.submit(em.fetch_kline, "1.000001", 101, 80)
        f_spark2 = pool.submit(em.fetch_kline, "0.399001", 101, 40)
        f_tech = pool.submit(em.fetch_kline, "1.000001", 101, 260)
        f_news = pool.submit(news_src.fetch_news)

    indices = _safe(f_indices, [])
    breadth_raw = _safe(f_breadth, None)
    zt = _safe(f_zt, {"total": 0, "items": []})
    dt = _safe(f_dt, {"total": 0, "items": []})
    zb = _safe(f_zb, {"total": 0, "items": []})
    industry = _safe(f_industry, [])
    concept = _safe(f_concept, [])
    spark_data = _safe(f_spark, {"bars": []})
    spark_data2 = _safe(f_spark2, {"bars": []})
    tech_data = _safe(f_tech, {"bars": []})
    news_data = _safe(f_news, {"items": [], "groups": {"国内": [], "国际": []}})

    if not indices and cached:
        stale = dict(cached["payload"])
        stale["cached"] = True
        stale["stale"] = True
        return stale

    spark_closes = [b["close"] for b in spark_data.get("bars", [])][-30:]
    for idx in indices:
        idx["spark"] = spark_closes
        if idx.get("amount") is None and spark_data.get("bars"):
            idx["amount"] = spark_data["bars"][-1]["amount"]

    # 前一日涨停池（用于晋级率/断板）
    bars_1m = spark_data.get("bars", [])
    dates = [b["date"] for b in bars_1m]
    prev_date = None
    if target in dates:
        i = dates.index(target)
        prev_date = dates[i - 1] if i > 0 else None
    elif len(dates) >= 2:
        prev_date = dates[-2]
    prev_zt = {"items": []}
    if prev_date:
        prev_zt = em.fetch_pool("zt", compact(prev_date))

    concept_map = {}
    if zt.get("items"):
        raw_map = em.fetch_concept_map([it["code"] for it in zt["items"]], concept)
        for item in zt["items"]:
            concept_map[item["code"]] = {"concepts": raw_map.get(item["code"], []), "industry": item.get("industry")}

    limitup = _build_limitup(zt, dt, zb, concept_map, prev_zt)
    ladder = _build_ladder(limitup)

    is_latest = target == latest
    breadth = None
    sentiment = None
    ai = None
    if is_latest and breadth_raw:
        limitup_stats = limitup["stats"]
        breadth = {
            **breadth_raw,
            "limit_up": limitup_stats["limit_up"],
            "limit_down": limitup_stats["limit_down"],
            "broken": limitup_stats["broken"],
            "broken_rate": limitup_stats["broken_rate"],
            "seal_rate": limitup_stats["seal_rate"],
            "lianban": limitup_stats["lianban"],
            "max_board": limitup_stats["max_board"],
            "promote_rate": limitup_stats["promote_rate"],
            "twenty_cm": limitup_stats["twenty_cm"],
        }
        sentiment = compute_sentiment(breadth)

    prev_amount = 0
    prev_parts = []
    for bars in (bars_1m, spark_data2.get("bars", [])):
        if len(bars) >= 2 and bars[-2].get("amount"):
            prev_parts.append(bars[-2]["amount"])
    if len(prev_parts) == 2:
        prev_amount = sum(prev_parts)

    if not is_latest:
        indices = []
        for meta in em.INDICES:
            kline = em.fetch_kline(meta["secid"], 101, 120)
            item = _index_from_kline(meta, kline["bars"], target)
            if item:
                indices.append(item)

    tech = {}
    if tech_data.get("bars"):
        try:
            full = analyze_index("上证指数", tech_data["bars"])
            tech = {k: full.get(k) for k in ("direction", "score", "support", "resistance",
                                             "advice", "risks", "price", "swing_high", "swing_low")}
        except Exception:  # noqa: BLE001
            tech = {}

    if is_latest and sentiment:
        try:
            sentiment["history"] = sentiment_history(bars_1m, sentiment.get("score"))
            sentiment["outlook"] = sentiment_outlook(bars_1m, sentiment.get("score"))
        except Exception:  # noqa: BLE001
            sentiment["history"] = []
            sentiment["outlook"] = None

    if is_latest and indices:
        # 复盘看板：指数卡片叠加今日分时走势（相对昨收的涨跌幅序列）
        def _intra(meta):
            try:
                d = em.fetch_minute_kline(meta["secid"], 5, 240, 1)
                pc = d.get("prev_close")
                bars = d.get("bars") or []
                if not pc or not bars:
                    return None
                return {"prev": pc,
                        "pct": [round((b["close"] / pc - 1) * 100, 2) for b in bars]}
            except Exception:  # noqa: BLE001
                return None

        with ThreadPoolExecutor(max_workers=4) as pool2:
            intra_rows = list(pool2.map(_intra, em.INDICES))
        intra_map = {m["code"]: r for m, r in zip(em.INDICES, intra_rows)}
        for idx in indices:
            intra = intra_map.get(idx.get("code"))
            if intra:
                idx["intra"] = intra

    overview = None
    if is_latest and breadth and sentiment:
        try:
            overview = overview_src.build(breadth, sentiment, breadth["limit_up"],
                                          breadth["limit_down"], target)
        except Exception:  # noqa: BLE001
            overview = None

    payload = {
        "date": target,
        "latest_date": latest,
        "is_latest": is_latest,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "refresh": _analysis_meta(),
        "indices": indices,
        "breadth": breadth,
        "sentiment": sentiment,
        "overview": overview,
        "sectors": {"industry": industry, "concept": concept},
        "limitup": limitup,
        "ladder": ladder,
        "prev_amount": prev_amount,
        "tech": tech,
        "news": (news_data.get("items") or []) if is_latest else [],
        "news_updated_at": news_data.get("updated_at") if is_latest else None,
    }
    if is_latest:
        payload["ai"] = build_ai_review(payload)
    else:
        payload["ai"] = None
    snapshot_put(target, payload)
    payload["cached"] = False
    return payload


def sectors(board_type: str = "industry", sort: str = "f3", limit: int = 30):
    if board_type not in ("industry", "concept"):
        board_type = "industry"
    if sort not in ("f3", "f6", "f62"):
        sort = "f3"
    items = em.fetch_boards(board_type, sort, limit)
    return {"type": board_type, "sort": sort, "items": items}


def sector_detail(code: str, sort: str = "f3"):
    meta, stocks = em.fetch_board_meta(code), em.fetch_board_stocks(code, sort)
    items = stocks["items"]
    leaders = sorted(items, key=lambda s: s["change_pct"], reverse=True)[:3]
    limit_up_count = sum(1 for s in items if s["change_pct"] >= 9.8)
    return {
        "info": meta,
        "leaders": leaders,
        "stocks": items,
        "total": stocks["total"],
        "limit_up_count": limit_up_count,
    }


def analysis_phase(now=None):
    """策略快照时段：09:25 竞价 / 15:00 收盘 / 24:00 隔夜新闻推演，各重算一次"""
    now = now or datetime.now()
    minutes = now.hour * 60 + now.minute
    if minutes < 9 * 60 + 25:
        return "night", "隔夜推演（00:00 新闻快照）", "09:25"
    if minutes < 15 * 60:
        return "auction", "09:25 竞价快照", "15:00"
    return "close", "15:00 收盘快照", "24:00 隔夜推演"


def _analysis_meta():
    phase, label, next_update = analysis_phase()
    return {
        "phase": phase,
        "label": label,
        "next_update": next_update,
        "computed_at": datetime.now().strftime("%H:%M"),
    }


def _session_analysis(scope, code, klt, producer):
    """同交易日同一时段复用上次结论；跨过 09:25 / 15:00 / 24:00 后自动重算"""
    phase, _, _ = analysis_phase()
    key = f"ana9:{scope}:{code}:{klt}:{datetime.now():%Y%m%d}:{phase}"
    return cached_call(key, 86400, producer)


def stock_detail(code: str, klt: int = 101):
    if klt not in (101, 102, 103, 105):
        klt = 101
    if klt == 105:
        return _intraday_detail(code)
    meta = em.fetch_stock_meta(code)
    kline = em.fetch_kline(f"{em.market_prefix(code)}.{code}", klt, 260)
    bars = kline["bars"]
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    recent = bars[-120:] if len(bars) >= 120 else bars
    swing_high = max(b["high"] for b in recent) if recent else 0
    swing_low = min(b["low"] for b in recent) if recent else 0
    price = closes[-1] if closes else 0
    rising = price >= (swing_high + swing_low) / 2 if swing_high else True
    m = macd(closes)
    k = kdj(highs, lows, closes)
    r = rsi(closes)
    try:
        context = _stock_context(code, meta, bars, price)
    except Exception:  # noqa: BLE001
        context = None
    try:
        valuation = _valuation(code, meta, [b["name"] for b in (context or {}).get("boards", [])],
                               (context or {}).get("research"))
    except Exception:  # noqa: BLE001
        valuation = None
    # 开盘买入决策：指数 / 板块 ETF / 个股竞价强度
    try:
        open_advice = opening.advice(meta, [b.get("name") for b in (context or {}).get("boards", [])])
    except Exception:  # noqa: BLE001
        open_advice = None

    def build_bundle():
        ind = analyze_stock_indicators(meta.get("name") or code, bars)
        try:
            strategy = build_stock_strategy(bars, ind, valuation)
        except Exception:  # noqa: BLE001
            strategy = None
        return {
            "indicator": ind,
            "analysis": _analysis_meta(),
            # 明日预期与短中期策略跟随收盘快照一起冻结，盘中不反复变动
            "strategy": strategy,
        }

    try:
        bundle = _session_analysis("stk", code, klt, build_bundle)
    except Exception:  # noqa: BLE001
        bundle = None
    indicator = (bundle or {}).get("indicator")
    analysis = (bundle or {}).get("analysis") or _analysis_meta()
    strategy = (bundle or {}).get("strategy")
    try:
        chan = chan_src.analyze(bars, m["dif"])
    except Exception:  # noqa: BLE001
        chan = None
    # 日K量价买卖信号：量价为主、指标共振为辅；盘中跳过未收完的最后一根日K
    try:
        signals = ksig.detect(bars, env=ksig.env_score_live(),
                              skip_last=analysis_phase()[0] == "auction")
    except Exception:  # noqa: BLE001
        signals = None
    return {
        "meta": meta,
        "period": klt,
        "bars": bars,
        "signals": signals,
        "fib": fib_levels(swing_high, swing_low, rising),
        "fib_range": {"high": swing_high, "low": swing_low},
        "indicator": indicator,
        "analysis": analysis,
        "strategy": strategy,
        "context": context,
        "chan": chan,
        "valuation": valuation,
        "open_advice": open_advice,
        "series": {
            "ma5": ma(closes, 5), "ma10": ma(closes, 10),
            "ma20": ma(closes, 20), "ma60": ma(closes, 60),
            "dif": m["dif"], "dea": m["dea"], "hist": m["hist"],
            "k": k["k"], "d": k["d"], "j": k["j"], "rsi": r,
        },
    }


def t_backtest(days: int = 10, force: bool = False):
    """做T策略回测：全信号基准 vs 叠加指数/板块环境后的胜率"""
    return tenv.backtest(days, force, analysis_phase()[0])


def k_signal_backtest(days: int = 60, force: bool = False):
    """日K量价信号回测：短线 T+1/T+3/T+5 + 波段 ATR 止盈止损"""
    return ksig.backtest(days, force, analysis_phase()[0])


def open_backtest(days: int = 20, force: bool = False):
    """开盘买入建议回测：全部开盘买入 vs 建议买入组的胜率"""
    return opening.backtest(days, force, analysis_phase()[0])


def _bars_of(secid):
    try:
        return em.fetch_minute_kline(secid, 5, 240, 1).get("bars") or []
    except Exception:  # noqa: BLE001
        return []


def _energy_at(bars, index):
    if not bars:
        return None
    idx = len(bars) - 1 if index is None else max(0, min(index, len(bars) - 1))
    return tscore.energy_state(bars, idx)


def _blend_energy(stock_e, sector_e, index_e):
    """个股 50% / 板块 30% / 大盘 20% 投票出综合量能方向"""
    parts = [(stock_e, 0.5), (sector_e, 0.3), (index_e, 0.2)]
    total = 0.0
    score = 0.0
    for item, weight in parts:
        if not item:
            continue
        total += weight
        if item.get("label") == "expanding":
            score += weight
        elif item.get("label") == "shrinking":
            score -= weight
    if not total:
        return "normal"
    ratio = score / total
    if ratio >= 0.5:
        return "expanding"
    if ratio <= -0.3:
        return "shrinking"
    return "normal"


def advanced_t(code: str, force: bool = False):
    """深度加权做T分析：量能空间判断 + 分层权重 + 20 日股性经验库

    用户手动触发，结果缓存 5 分钟，避免分时自动刷新时重复计算。
    """
    secid = f"{em.market_prefix(code)}.{code}"
    stamp = datetime.now().strftime("%Y%m%d") + ":" + analysis_phase()[0]

    def producer():
        meta = em.fetch_stock_meta(code)
        minute = em.fetch_minute_kline(secid, 5, 240, 1)
        bars = minute.get("bars") or []
        if not bars:
            raise em.DataError("分时数据不可用")
        daily = em.fetch_kline(secid, 101, 120)["bars"]
        price = bars[-1].get("close")
        open_price = bars[0].get("open") or bars[0].get("close")
        tier, weights = tscore.tier_of(meta.get("float_cap"))
        persona_data = {"profile": {"ok": False}, "experience": {"ok": False}, "base_vol": [None] * 8}
        try:
            persona_data = persona.snapshot(secid, code)
        except Exception:  # noqa: BLE001
            pass
        profile = persona_data.get("profile") or {"ok": False}
        experience = persona_data.get("experience") or {"ok": False}
        base_vol = persona_data.get("base_vol") or [None] * 8

        # 环境分：指数 / 板块 ETF / 个股相对（复用现有口径）
        try:
            context = _stock_context(code, meta, daily, price)
        except Exception:  # noqa: BLE001
            context = None
        boards = [b.get("name") for b in (context or {}).get("boards", [])]
        env = {"score": 0.0, "label": "中性", "note": ""}
        try:
            dk = dkline.state(daily, datetime.now().strftime("%Y-%m-%d"))
            mkt = dkline.index_daily()
            env = tenv.build_env(bars, minute.get("prev_close"), boards, dk, mkt)
        except Exception:  # noqa: BLE001
            pass
        limit = tenv.limit_state(daily, code)

        # 板块 / 大盘 量能（各取一次分时线，逐点按位置计算）
        sector_bars = []
        sector_name = ""
        try:
            sector = tenv._match_sector(boards)  # noqa: SLF001
            if sector:
                proxy = tenv.ETF_PROXY.get(sector)
                sector_name = sector
                if proxy:
                    sector_bars = _bars_of(proxy[0])
        except Exception:  # noqa: BLE001
            pass
        sector_e = _energy_at(sector_bars, None)
        index_bars = _bars_of(tenv.INDEXES[0][0])
        index_e = _energy_at(index_bars, None)

        closes = [b.get("close") or 0 for b in bars]
        dif = macd(closes)["dif"]
        base = intraday.t_points(bars, minute.get("prev_close"), dif, max_points=40)
        points = base.get("points") or []
        if limit.get("distorted"):
            points = []

        scored = []
        for point in points:
            b = tscore.bin_index(point.get("time"))
            stock_e = tscore.energy_state(bars, point.get("index") or 0,
                                          base_vol[b] if b is not None else None)
            sector_i = _energy_at(sector_bars, point.get("index"))
            index_i = _energy_at(index_bars, point.get("index"))
            label = _blend_energy(stock_e, sector_i, index_i)
            blended = dict(stock_e)
            blended["label"] = label
            blended["label_cn"] = tscore.ENERGY_LABELS[label]
            ctx = {
                "weights": weights,
                "env_score": env.get("score") or 0.0,
                "energy": blended,
                "bars": bars,
                "open_price": open_price,
                "spike_fade": (profile.get("spike_fade_prob") or 0) >= 55,
                "prior_wr": None,
                "prior_samples": 0,
                "strategy_weight": persona.strategy_weight(experience, point.get("strategy_key")),
            }
            prior_wr, prior_samples = persona.prior_win_rate(
                experience, point.get("strategy_key"), point.get("type"),
                point.get("time"), label)
            ctx["prior_wr"] = prior_wr
            ctx["prior_samples"] = prior_samples
            item = tscore.score_point(point, ctx)
            item["stock_energy"] = stock_e
            scored.append(item)

        # 把信号收敛成可执行计划：一天最多两次买点，T出画在图上
        scored.sort(key=lambda x: x.get("index") or 0)
        plan = tscore.make_plan(scored, open_price)
        stock_overall = stock_energy_state(bars, base_vol) or {}
        t_out = plan.get("t_out")
        summary = (
            f"{tier}股 · 买点 {plan['buy_count']} 次（当日上限 {plan['max_buys']} 次）；"
            f"个股量能{stock_overall.get('label_cn') or '常规'}"
            + (f"；T出参考 {t_out['price']:.2f}" if t_out else "")
        )
        return {
            "code": code,
            "name": meta.get("name") or code,
            "tier": tier,
            "float_cap": meta.get("float_cap"),
            "weights": tscore.weight_payload(weights),
            "env": {
                "score": env.get("score"),
                "label": env.get("label"),
                "note": env.get("note"),
                "sector": (env.get("sector") or {}).get("name"),
                "relative": env.get("relative"),
            },
            "energy": {
                "stock": stock_energy_state(bars, base_vol),
                "sector": {"name": sector_name, **(sector_e or {})} if sector_e else None,
                "index": index_e,
            },
            "plan": plan,
            "signal_count": len(scored),
            "persona": profile,
            "experience": experience,
            "limit": limit,
            "summary": summary,
            "note": "加权为量能 + 股性 + 开盘价的置信度评估，目标价为预期空间，不构成收益承诺",
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }

    if force:
        return cached_call(f"tadv:force:{code}:{stamp}:{time.time()}", 1, producer)
    return cached_call(f"tadv:v2:{code}:{stamp}", 300, producer)


def stock_energy_state(bars, base_vol):
    """最新一根的量能状态（个股）"""
    if not bars:
        return None
    idx = len(bars) - 1
    b = tscore.bin_index(bars[idx].get("time"))
    return tscore.energy_state(bars, idx, base_vol[b] if b is not None else None)


def _intraday_detail(code: str):
    """分时界面：5 分钟线 + 成交量 + MACD + 做T点位"""
    meta = em.fetch_stock_meta(code)
    secid = f"{em.market_prefix(code)}.{code}"
    minute = em.fetch_minute_kline(secid, 5, 240, 1)
    bars = minute["bars"]
    if not bars:
        raise em.DataError("分时数据不可用")
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    price = closes[-1]
    m = macd(closes)
    k = kdj(highs, lows, closes)
    r = rsi(closes)
    t_data = intraday.t_points(bars, minute.get("prev_close"), m["dif"])
    # 与日K页共用同一份日线数据，保证两处「八指标」口径一致（同一快照键）
    daily = em.fetch_kline(secid, 101, 260)["bars"]
    try:
        context = _stock_context(code, meta, daily, price)
    except Exception:  # noqa: BLE001
        context = None
    # 做T点位叠加当天指数 / 板块 / 个股相对强弱 + T-1 日K 背景
    try:
        dk = dkline.state(daily, datetime.now().strftime("%Y-%m-%d"))
        mkt = dkline.index_daily()
        env = tenv.build_env(bars, minute.get("prev_close"),
                             [b.get("name") for b in (context or {}).get("boards", [])], dk, mkt)
        t_data = tenv.annotate(t_data, env)
    except Exception:  # noqa: BLE001
        env = None
    # 连板/涨停封板日分时波动失真，做T点位不适用
    limit = tenv.limit_state(daily, code)
    if limit.get("distorted"):
        t_data["points"] = []
        t_data["latest"] = None
        t_data["summary"] = f"{limit['label']}：连板/封板导致分时指标失真，暂不生成做T点位"
    try:
        chan = chan_src.analyze(daily, macd([b["close"] for b in daily])["dif"])
    except Exception:  # noqa: BLE001
        chan = None
    try:
        valuation = _valuation(code, meta, [b["name"] for b in (context or {}).get("boards", [])],
                               (context or {}).get("research"))
    except Exception:  # noqa: BLE001
        valuation = None
    # 开盘买入决策：指数 / 板块 ETF / 个股竞价强度
    try:
        open_advice = opening.advice(meta, [b.get("name") for b in (context or {}).get("boards", [])])
    except Exception:  # noqa: BLE001
        open_advice = None
    # 八指标结论按日线计算，只在 09:25 / 15:00 / 24:00 更新，盘中分时行情照常实时刷新

    def build_bundle():
        ind = analyze_stock_indicators(meta.get("name") or code, daily)
        try:
            strategy = build_stock_strategy(daily, ind, valuation)
        except Exception:  # noqa: BLE001
            strategy = None
        return {
            "indicator": ind,
            "analysis": _analysis_meta(),
            "strategy": strategy,
        }

    try:
        bundle = _session_analysis("stk", code, 101, build_bundle)
    except Exception:  # noqa: BLE001
        bundle = None
    indicator = (bundle or {}).get("indicator")
    analysis = (bundle or {}).get("analysis") or _analysis_meta()
    strategy = (bundle or {}).get("strategy")
    return {
        "meta": meta,
        "period": 105,
        "period_label": "分时",
        "bars": bars,
        "intraday": {
            "bars": bars,
            "prev_close": minute.get("prev_close"),
            "vwap": t_data["vwap"],
            "t_points": t_data["points"],
            "summary": t_data["summary"],
            "stats": t_data["stats"],
            "latest": t_data["latest"],
            "env": env,
            "limit": limit,
        },
        "indicator": indicator,
        "analysis": analysis,
        "strategy": strategy,
        "context": context,
        "chan": chan,
        "valuation": valuation,
        "open_advice": open_advice,
        "series": {
            "ma5": ma(closes, 5), "ma10": ma(closes, 10),
            "ma20": ma(closes, 20), "ma60": ma(closes, 60),
            "dif": m["dif"], "dea": m["dea"], "hist": m["hist"],
            "k": k["k"], "d": k["d"], "j": k["j"], "rsi": r,
            "change_pct": [b.get("change_pct") for b in bars],
        },
    }


def _score(value, low, high, full=25, reverse=False):
    """把指标映射到 0~full 分，缺失返回 None"""
    if value is None:
        return None
    if high == low:
        return full
    ratio = (high - value) / (high - low) if reverse else (value - low) / (high - low)
    return round(max(0.0, min(1.0, ratio)) * full)


def _mean_score(scores):
    valid = [s for s in scores if s is not None]
    return round(sum(valid) / len(valid)) if valid else None


def _valuation(code, meta, board_names=None, report=None):
    """价值分析：估值、成长、盈利质量、财务健康、研报观点与综合评分"""
    try:
        business = em.fetch_main_business(code)
    except Exception:  # noqa: BLE001
        business = {"report_date": "", "products": [], "scope": ""}
    products = [p for p in (business.get("products") or []) if (p.get("ratio") or 0) >= 3][:3]
    main_business = products[0]["name"] if products else ""
    board_list = [n for n in (board_names or []) if n and not n.endswith("Ⅲ")]
    industry = main_business or meta.get("industry") or ""
    concept_tags = [n for n in board_list if n not in (main_business, meta.get("industry"))][:3]
    try:
        finance = em.fetch_finance(code)
    except Exception:  # noqa: BLE001
        finance = []
    reports = []
    # 研报按东财行业 / 概念板块检索，主营产品名通常不是研报行业分类
    report_names = ([meta["industry"]] if meta.get("industry") else []) + board_list[:3] + ([main_business] if main_business else [])
    for name in report_names:
        try:
            reports = em.fetch_industry_reports(name)
        except Exception:  # noqa: BLE001
            reports = []
        if reports:
            break
    latest = finance[0] if finance else None

    def fmt(value, unit="", digits=2):
        return "—" if value is None else f"{value:.{digits}f}{unit}"

    # 估值与规模
    annual = [r for r in finance if str(r.get("date") or "")[5:7] == "12"]
    ttm_rev = annual[0]["revenue"] if annual else (latest or {}).get("revenue")
    cap = meta.get("total_cap")
    ps = round(cap / ttm_rev, 2) if cap and ttm_rev and ttm_rev > 0 else None
    pe = meta.get("pe")
    pb = meta.get("pb")
    eps = (latest or {}).get("eps")
    bps = (latest or {}).get("bps")

    # 营收/净利 CAGR（用年报首尾年份）
    cagr = None
    if len(annual) >= 2:
        newest, oldest = annual[0], annual[-1]
        years = int(str(newest["date"])[:4]) - int(str(oldest["date"])[:4])
        if years >= 1 and oldest.get("revenue") and newest.get("revenue") and oldest["revenue"] > 0:
            cagr = round(((newest["revenue"] / oldest["revenue"]) ** (1 / years) - 1) * 100, 1)

    growth_scores = [
        _score((latest or {}).get("revenue_yoy"), -20, 40),
        _score((latest or {}).get("net_profit_yoy"), -30, 60),
        _score(cagr, -10, 30),
    ]
    profit_scores = [
        _score((latest or {}).get("gross_margin"), 10, 50),
        _score((latest or {}).get("net_margin"), 0, 20),
        _score((latest or {}).get("roe"), 0, 20),
    ]
    health_scores = [
        _score((latest or {}).get("debt_ratio"), 20, 80, reverse=True),
        _score((latest or {}).get("cash_flow_ps"), 0, 2),
    ]
    val_scores = [_score(pe, 5, 80, reverse=True) if pe and pe > 0 else None, _score(pb, 0.8, 8, reverse=True)]

    dimensions = [
        {
            "key": "valuation", "title": "估值水平", "score": _mean_score(val_scores),
            "items": [
                {"label": "市盈率 PE", "value": fmt(pe), "judge": "—" if pe is None else ("亏损" if pe <= 0 else "低估" if pe < 20 else "合理" if pe < 40 else "偏高")},
                {"label": "市净率 PB", "value": fmt(pb), "judge": "—" if pb is None else ("破净" if pb < 1 else "合理" if pb < 3 else "偏高")},
                {"label": "市销率 PS", "value": fmt(ps), "judge": "按最新年报营收估算" if ps else "数据不足"},
                {"label": "总市值", "value": f"{cap / 1e8:.1f} 亿" if cap else "—", "judge": "规模参考"},
            ],
        },
        {
            "key": "growth", "title": "成长性", "score": _mean_score(growth_scores),
            "items": [
                {"label": "营收同比", "value": fmt((latest or {}).get("revenue_yoy"), "%", 1), "judge": "—" if (latest or {}).get("revenue_yoy") is None else ("高增" if latest["revenue_yoy"] >= 20 else "稳健" if latest["revenue_yoy"] >= 0 else "承压")},
                {"label": "净利同比", "value": fmt((latest or {}).get("net_profit_yoy"), "%", 1), "judge": "—" if (latest or {}).get("net_profit_yoy") is None else ("强弹性" if latest["net_profit_yoy"] >= 30 else "稳增" if latest["net_profit_yoy"] >= 0 else "下滑")},
                {"label": "营收 CAGR", "value": fmt(cagr, "%", 1), "judge": "近 " + str(len(annual)) + " 个年报复合增速" if cagr is not None else "年报样本不足"},
                {"label": "扣非净利同比", "value": fmt((latest or {}).get("deduct_yoy"), "%", 1), "judge": "剔除一次性收益后的盈利质量"},
            ],
        },
        {
            "key": "profit", "title": "盈利质量", "score": _mean_score(profit_scores),
            "items": [
                {"label": "毛利率", "value": fmt((latest or {}).get("gross_margin"), "%", 1), "judge": "—" if (latest or {}).get("gross_margin") is None else ("高毛利" if latest["gross_margin"] >= 40 else "中等" if latest["gross_margin"] >= 20 else "偏低")},
                {"label": "净利率", "value": fmt((latest or {}).get("net_margin"), "%", 1), "judge": "—" if (latest or {}).get("net_margin") is None else ("强" if latest["net_margin"] >= 15 else "一般" if latest["net_margin"] >= 5 else "偏弱")},
                {"label": "ROE", "value": fmt((latest or {}).get("roe"), "%", 1), "judge": "—" if (latest or {}).get("roe") is None else ("优秀" if latest["roe"] >= 15 else "良好" if latest["roe"] >= 8 else "偏低")},
                {"label": "每股收益 / 每股净资产", "value": f"{fmt(eps)} / {fmt(bps)}", "judge": "EPS 与 BPS 对比价格评估安全边际"},
            ],
        },
        {
            "key": "health", "title": "财务健康", "score": _mean_score(health_scores),
            "items": [
                {"label": "资产负债率", "value": fmt((latest or {}).get("debt_ratio"), "%", 1), "judge": "—" if (latest or {}).get("debt_ratio") is None else ("稳健" if latest["debt_ratio"] < 40 else "适中" if latest["debt_ratio"] < 60 else "偏高")},
                {"label": "每股经营现金流", "value": fmt((latest or {}).get("cash_flow_ps")), "judge": "—" if (latest or {}).get("cash_flow_ps") is None else ("现金流转正" if latest["cash_flow_ps"] >= 0 else "现金流为负，需警惕")},
                {"label": "报告期", "value": (latest or {}).get("name") or "—", "judge": "最新一期财报口径"},
            ],
        },
    ]
    valid_scores = [d["score"] for d in dimensions if d["score"] is not None]
    score = round(sum(valid_scores) * 100 / (25 * len(valid_scores))) if valid_scores else None
    if score is None:
        verdict = "财务数据不足，暂不评分"
    elif score >= 75:
        verdict = "综合评分较高，具备中长期配置价值"
    elif score >= 60:
        verdict = "基本面良好，可结合技术面择机参与"
    elif score >= 45:
        verdict = "基本面中性，更适合短线博弈或等待改善"
    else:
        verdict = "基本面偏弱，需警惕业绩与估值双杀"

    research = {
        "available": bool((report or {}).get("available")),
        "total": (report or {}).get("total"),
        "target_avg": (report or {}).get("target_avg"),
        "upside_pct": (report or {}).get("upside_pct"),
        "trend": (report or {}).get("trend"),
        "windows": (report or {}).get("windows") or {},
        "recent": ((report or {}).get("recent") or [])[:3],
    }

    notes = []
    if latest:
        rev_yoy = latest.get("revenue_yoy")
        profit_yoy = latest.get("net_profit_yoy")
        if rev_yoy is not None and rev_yoy >= 20:
            notes.append(f"{latest['name']}营收同比 {rev_yoy:+.1f}%，收入端保持高增")
        elif rev_yoy is not None and rev_yoy < 0:
            notes.append(f"{latest['name']}营收同比 {rev_yoy:+.1f}%，收入端承压")
        if profit_yoy is not None and profit_yoy >= 30:
            notes.append(f"归母净利同比 {profit_yoy:+.1f}%，利润弹性强于收入")
        elif profit_yoy is not None and profit_yoy < 0:
            notes.append(f"归母净利同比 {profit_yoy:+.1f}%，盈利端出现下滑")
        if latest.get("gross_margin") is not None:
            same_quarter = next(
                (r for r in finance[1:]
                 if r.get("type") == latest.get("type") and r["date"][:4] != latest["date"][:4]),
                None,
            )
            if same_quarter and same_quarter.get("gross_margin"):
                diff = latest["gross_margin"] - same_quarter["gross_margin"]
                direction = "提升" if diff > 0 else "下降"
                notes.append(f"毛利率 {latest['gross_margin']:.2f}%，同比{direction} {abs(diff):.2f}pct")
        if latest.get("roe") is not None:
            notes.append(f"ROE {latest['roe']:.2f}%，" + ("盈利能力较强" if latest["roe"] >= 12 else "盈利能力一般"))
        if latest.get("debt_ratio") is not None and latest["debt_ratio"] >= 65:
            notes.append(f"资产负债率 {latest['debt_ratio']:.1f}%，杠杆偏高，需关注偿债与扩产压力")
    if not notes:
        notes.append("财务数据暂不可用或样本不足")
    if research["available"] and research.get("target_avg"):
        notes.append(f"近 {research.get('total')} 篇研报平均目标价 {research['target_avg']} 元，"
                     f"较现价空间 {research.get('upside_pct')}%，{research.get('trend')}")
    if score is not None:
        notes.append(f"四维评分 {score} 分：{verdict}")

    return {
        "industry": industry,
        "sector": meta.get("industry") or "",
        "main_business": main_business,
        "business_products": products,
        "business_date": business.get("report_date") or "",
        "concept_tags": concept_tags,
        "finance": finance[:4],
        "latest": latest,
        "industry_reports": reports,
        "notes": notes,
        "pe": pe,
        "pb": pb,
        "ps": ps,
        "cagr": cagr,
        "dimensions": dimensions,
        "score": score,
        "verdict": verdict,
        "research": research,
    }


PERIODS = {101: ("日线", "日", 260), 102: ("周线", "周", 200), 103: ("月线", "月", 120)}
# 板块名 → 未来事件检索关键词（会议名称往往用产业链词，而非板块名）
EVENT_KEYWORDS = {
    "电池": ("锂", "储能", "新能源", "动力电池", "固态电池"),
    "电子化学品": ("半导体", "芯片", "集成电路", "电子材料"),
    "宁组合": ("新能源", "储能", "锂", "光伏"),
    "光模块": ("算力", "光通信", "数据中心", "AI"),
    "CPO": ("算力", "光通信", "数据中心"),
    "通信设备": ("通信", "光通信", "5G", "算力"),
    "半导体": ("半导体", "芯片", "集成电路"),
    "芯片": ("芯片", "半导体", "集成电路"),
    "光伏": ("光伏", "储能", "新能源"),
    "储能概念": ("储能", "新能源", "电力"),
    "电力设备": ("电力", "储能", "新能源"),
    "医药": ("医药", "医疗", "创新药"),
    "军工": ("军工", "航天", "国防"),
    "稀土": ("稀土", "有色", "资源"),
    "有色": ("有色", "铜", "黄金"),
    "白酒": ("白酒", "消费", "食品"),
    "证券": ("证券", "资本市场", "金融"),
    "银行": ("银行", "金融", "利率"),
}
INDEX_NEWS_HINTS = {
    "000001": ("大盘", "沪指", "上证", "A股", "沪深", "流动性", "央行", "政策", "北向"),
    "399001": ("深市", "深成指", "深证", "成长", "制造业"),
    "399006": ("创业板", "成长", "新能源", "医药", "光伏"),
    "000688": ("科创", "半导体", "芯片", "硬科技", "算力"),
}


def _index_related_news(meta, klt: int):
    try:
        items = news_src.fetch_news(30).get("items") or []
    except Exception:  # noqa: BLE001
        return []
    words = INDEX_NEWS_HINTS.get(meta["code"], ())
    hits = []
    for item in items:
        text = f"{item.get('title') or ''} {item.get('summary') or ''}"
        if item.get("tags") and any(t in ("货币政策", "政策", "海外流动性", "宏观数据") for t in item["tags"]):
            hits.append(item)
        elif any(w in text for w in words):
            hits.append(item)
    seen, out = set(), []
    for item in hits:
        if item["title"] in seen:
            continue
        seen.add(item["title"])
        out.append({
            "title": item["title"],
            "time_text": item.get("time_text"),
            "impact": item.get("impact"),
            "scope": item.get("scope"),
            "level": item.get("level"),
            "url": item.get("url"),
            "tag": (item.get("tags") or ["消息"])[0],
        })
    out.sort(key=lambda x: -(x.get("level") or 0))
    return out[:6]


def index_detail(code: str, klt: int = 101):
    meta = next((i for i in em.INDICES if i["code"] == code), None)
    if not meta:
        meta = em.INDICES[0]
    if klt == 105:
        minute = em.fetch_minute_kline(meta["secid"], 5, 240, 1)
        bars = minute["bars"]
        if not bars:
            raise em.DataError("分时数据不可用")
        closes = [b["close"] for b in bars]
        m = macd(closes)
        # 分时页也给出研判结论，但按 09:25 / 15:00 / 24:00 三个时点重算，盘中不反复变更
        daily = em.fetch_kline(meta["secid"], 101, 260)["bars"]
        try:
            bundle = _session_analysis("idxintra", meta["code"], 101, lambda: {
                "analysis": {k: v for k, v in analyze_index(meta["name"], daily, "日").items()
                             if k in ("direction", "tone", "score", "advice", "risks",
                                      "support", "resistance", "swing_low", "swing_high")},
                "meta": _analysis_meta(),
            })
        except Exception:  # noqa: BLE001
            bundle = None
        analysis = dict((bundle or {}).get("analysis") or {})
        if analysis:
            analysis["basis"] = "日线"
            analysis.update((bundle or {}).get("meta") or _analysis_meta())
        # 指数的成交额/成交量是全市场合计值，比值不是价格，均价线对指数无意义
        return {
            "date": latest_trading_date(),
            "code": meta["code"],
            "secid": meta["secid"],
            "name": meta["name"],
            "short": meta["short"],
            "period": "intraday",
            "period_label": "分时",
            "period_unit": "分钟",
            "price": closes[-1],
            "intraday": {"bars": bars, "prev_close": minute.get("prev_close"), "vwap": []},
            "analysis": analysis or None,
            "series": {"dif": m["dif"], "dea": m["dea"], "hist": m["hist"],
                       "change_pct": [b.get("change_pct") for b in bars]},
            "related_news": _index_related_news(meta, klt),
        }
    if klt not in PERIODS:
        klt = 101
    label, unit, limit = PERIODS[klt]
    kline = em.fetch_kline(meta["secid"], klt, limit)
    bars = kline["bars"]
    result = analyze_index(meta["name"], bars, unit)
    # 结论（方向/评分/建议/风险/各指标解读）按 09:25、15:00 与 24:00 的快照复用，盘中不反复变动
    try:
        opinion = _session_analysis("idx", meta["code"], klt, lambda: {
            "direction": result["direction"], "tone": result["tone"], "score": result["score"],
            "advice": result["advice"], "risks": result["risks"], "sections": result["sections"],
            "analysis": _analysis_meta(),
        })
    except Exception:  # noqa: BLE001
        opinion = None
    if opinion:
        result.update(opinion)
    result.update({
        "code": meta["code"],
        "secid": meta["secid"],
        "short": meta["short"],
        "period": klt,
        "period_label": label,
        "period_unit": unit,
        "bars": bars,
        "related_news": _index_related_news(meta, klt),
        "news_period_hint": {
            101: "日线级别消息以当日与次日为主，短线情绪扰动更明显。",
            102: "周线级别更看重政策与流动性的中期方向，单条消息影响会被平滑。",
            103: "月线级别由产业周期与宏观流动性主导，需结合季度数据与政策周期判断。",
        }[klt],
    })
    return result


def news_page():
    """新闻独立页：未来将要发生 / 过去已发生 + AI 解读"""
    from .analysis import analyze_news
    data = news_src.fetch_news(30)
    items = data.get("items") or []
    upcoming = [i for i in items if i.get("upcoming")]
    past = [i for i in items if not i.get("upcoming")]
    for group in (upcoming, past):
        group.sort(key=lambda x: (-(x.get("level") or 0), -(x.get("time") or 0)))
    try:
        calendar = news_src.fetch_events(21)
    except Exception:  # noqa: BLE001
        calendar = {"events": [], "hotspots": [], "window": "", "source": "东方财富财经日历"}
    return {
        "updated_at": data.get("updated_at"),
        "counts": {"upcoming": len(upcoming), "past": len(past),
                   "events": len(calendar.get("events") or [])},
        "upcoming": upcoming,
        "past": past,
        "events": calendar.get("events") or [],
        "hotspots": calendar.get("hotspots") or [],
        "event_window": calendar.get("window"),
        "event_source": calendar.get("source"),
        "analysis": analyze_news(items),
        "archive": news_src.archive_meta(),
        "filters": {
            "impact": ["利好", "利空", "中性"],
            "scope": ["国内", "国际"],
        },
    }


def news_history(days: int = 90, impact: str = "全部", scope: str = "全部",
                 per_day: int = 6, day: str = ""):
    """过去 N 天的新闻归档（默认 90 天），带真实日期与利好/利空统计"""
    days = max(1, min(int(days or 90), 120))
    return news_src.archive_payload(days, impact, scope, max(1, min(int(per_day or 6), 60)), day or "")


def news_analyze(item):
    return news_item_analysis(item)


# ---------------------------------------------------------------- 严重异动监控
# 交易所口径：连续 10 个交易日收盘价涨跌幅偏离值累计达到 +100%，
# 或连续 30 个交易日达到 +200%，即触发严重异常波动，随后进入约 10 个交易日的重点监控
UNUSUAL_DAYS = 45
UNUSUAL_WATCH_WINDOW = 10


def _dedupe_unusual(rows):
    """同一股票多日触发时，保留最近且偏离值最大的一条"""
    best = {}
    for row in rows:
        key = row["code"]
        cur = best.get(key)
        if cur is None or (row["date"], abs(row["dev"] or 0)) > (cur["date"], abs(cur["dev"] or 0)):
            best[key] = row
    return list(best.values())


def _trade_days_since(trade_dates, date_str):
    """date_str 距今相隔的交易日数；非交易日或超出窗口返回 None"""
    try:
        return len(trade_dates) - 1 - trade_dates.index(date_str)
    except ValueError:
        return None


def _unusual_producer():
    try:
        triggered = em.fetch_unusual('(IS_HAPPEN="1")', 100)
    except Exception:  # noqa: BLE001
        triggered = []
    try:
        watch = em.fetch_unusual('(IS_SYSDATE="1")', 100)
    except Exception:  # noqa: BLE001
        watch = []

    # 交易日序列用于换算“触发后过了几个交易日”，判断监管期是否即将结束
    try:
        index_bars = em.fetch_kline("1.000001", 101, 60)["bars"]
        trade_dates = [b["date"] for b in index_bars]
    except Exception:  # noqa: BLE001
        trade_dates = []

    cutoff = (datetime.now() - timedelta(days=UNUSUAL_DAYS)).strftime("%Y-%m-%d")
    deduped = _dedupe_unusual(triggered)
    near = [r for r in _dedupe_unusual(watch)
            if not r["happened"] and (r["dev"] or 0) >= 70]

    # 上涨触发：只保留仍在重点监控期内（触发后 10 个交易日内）的股票；
    # 监管期剩余不超过 4 个交易日的单独列入“即将出异动”
    up, exiting = [], []
    for r in deduped:
        if (r["dev"] or 0) < 100:
            continue
        ago = _trade_days_since(trade_dates, r["date"]) if trade_dates else None
        if ago is None:
            if r["date"] < cutoff:
                continue
            up.append(r)
            continue
        if ago > UNUSUAL_WATCH_WINDOW:
            continue
        up.append(r)
        remaining = UNUSUAL_WATCH_WINDOW - ago
        if remaining <= 4:
            rec = dict(r)
            rec["days_since"] = ago
            rec["remaining"] = remaining
            exiting.append(rec)

    up.sort(key=lambda x: (x["date"], x["dev"] or 0), reverse=True)
    near.sort(key=lambda x: x["dev"] or 0, reverse=True)
    exiting.sort(key=lambda x: x["remaining"])

    # 异动列表补充当日涨跌幅（腾讯批量，失败不影响主流程）
    flow_rows = up + near + exiting
    try:
        changes = em.fetch_changes([r["code"] for r in flow_rows])
    except Exception:  # noqa: BLE001
        changes = {}
    for row in flow_rows:
        row["change_pct"] = changes.get(row["code"])

    return {
        "date": latest_trading_date(),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rule": {
            "window": 10,
            "up": 100,
            "window2": 30,
            "up2": 200,
            "watch_window": UNUSUAL_WATCH_WINDOW,
            "desc": "10 个交易日累计偏离值 ≥+100%，或 30 个交易日 ≥+200% 触发严重异动；触发后重点监控约 10 个交易日",
        },
        "groups": {"up": up, "near": near, "exiting": exiting},
        "counts": {"up": len(up), "near": len(near), "exiting": len(exiting)},
    }


def abnormal_list():
    return cached_call("abnormal:v5", 300, _unusual_producer)


def search_stocks(keyword: str):
    return {"items": em.suggest_stocks(keyword, 8)}


def watch_quotes(codes):
    if not codes:
        return {"items": []}
    quotes = em.fetch_quotes(codes)
    today = datetime.now().strftime("%Y-%m-%d")

    def load(code):
        try:
            bars = em.fetch_kline(f"{em.market_prefix(code)}.{code}", 101, 30)["bars"]
            return code, bars
        except Exception:  # noqa: BLE001
            return code, []

    def load_intra(code):
        try:
            d = em.fetch_minute_kline(f"{em.market_prefix(code)}.{code}", 5, 240, 1)
            pc = d.get("prev_close")
            minute_bars = d.get("bars") or []
            if not pc or not minute_bars:
                return code, None
            return code, {"prev": pc,
                          "pct": [round((b["close"] / pc - 1) * 100, 2) for b in minute_bars]}
        except Exception:  # noqa: BLE001
            return code, None

    bars_map = {}
    intra_map = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for code, bars in pool.map(load, codes[:20]):
            bars_map[code] = bars
    with ThreadPoolExecutor(max_workers=6) as pool:
        for code, intra in pool.map(load_intra, codes[:20]):
            if intra:
                intra_map[code] = intra

    items = []
    for code in codes:
        info = quotes.get(code) or {}
        price = info.get("price")
        bars = [dict(b) for b in bars_map.get(code, [])]
        if bars and price:
            last = bars[-1]
            if last["date"] == today:
                last["close"] = price
                last["high"] = max(last["high"], price)
                last["low"] = min(last["low"], price)
                base = bars[-2]["close"] if len(bars) >= 2 else last.get("open")
                if base:
                    last["change_pct"] = round((price / base - 1) * 100, 2)
        prev_close = info.get("prev_close") or (bars[-2]["close"] if len(bars) >= 2 else None)
        intra = intra_map.get(code)
        # 量比 = 当日累计成交量 / (近5日平均每分钟成交量 × 当日已交易分钟数)
        # 注意单位：行情 volume 为手，日K volume 为股，统一换算成股
        volume_ratio = None
        if intra and info.get("volume"):
            past5 = [b["volume"] for b in bars if b["date"] < today and b.get("volume")][-5:]
            minutes = len(intra["pct"]) * 5
            if past5 and minutes:
                avg_per_min = (sum(past5) / len(past5)) / 240
                if avg_per_min:
                    volume_ratio = round(info["volume"] * 100 / (avg_per_min * minutes), 2)
        # 5分钟涨速 = 分时序列最后一格与前一格之差（5分钟bar）
        speed_5m = None
        if intra and len(intra["pct"]) >= 2:
            speed_5m = round(intra["pct"][-1] - intra["pct"][-2], 2)
        items.append({
            "code": code,
            "name": info.get("name") or code,
            "price": price,
            "change_pct": info.get("change_pct"),
            "prev_close": prev_close,
            "turnover": info.get("turnover"),
            "volume": info.get("volume"),
            "amount": info.get("amount"),
            "volume_ratio": volume_ratio,
            "speed_5m": speed_5m,
            "main_flow": info.get("main_flow"),
            "main_flow_pct": info.get("main_flow_pct"),
            "intra": intra,
            "bars": bars[-24:],
        })
    return {"updated_at": datetime.now().strftime("%H:%M:%S"), "realtime": True,
            "poll_seconds": 5, "items": items}


LEADER_MAP = (
    ("光模块", "300308", "中际旭创"), ("CPO", "300308", "中际旭创"),
    ("算力", "300308", "中际旭创"), ("通信设备", "300308", "中际旭创"),
    ("电池", "300750", "宁德时代"), ("锂电", "300750", "宁德时代"),
    ("新能源车", "002594", "比亚迪"), ("汽车", "002594", "比亚迪"),
    ("白酒", "600519", "贵州茅台"), ("光伏", "601012", "隆基绿能"),
    ("储能", "300274", "阳光电源"), ("半导体", "688981", "中芯国际"),
    ("芯片", "688981", "中芯国际"), ("消费电子", "601138", "工业富联"),
    ("证券", "600030", "中信证券"), ("银行", "600036", "招商银行"),
    ("保险", "601318", "中国平安"), ("稀土", "600111", "北方稀土"),
    ("有色", "601899", "紫金矿业"), ("医药", "603259", "药明康德"),
    ("家电", "000333", "美的集团"), ("电力", "600900", "长江电力"),
)

# 板块成分样本：东财板块 K 线被限流时，用核心成分股等权合成板块指数 K 线
SECTOR_PEERS = {
    "光模块": [("300308", "中际旭创"), ("300502", "新易盛"), ("300394", "天孚通信"),
               ("002281", "光迅科技"), ("300570", "太辰光")],
    "CPO": [("300308", "中际旭创"), ("300502", "新易盛"), ("300394", "天孚通信")],
    "通信设备": [("300308", "中际旭创"), ("000063", "中兴通讯"), ("600487", "亨通光电"),
                 ("002281", "光迅科技")],
    "算力": [("300308", "中际旭创"), ("000977", "浪潮信息"), ("603019", "中科曙光")],
    "电池": [("300750", "宁德时代"), ("300014", "亿纬锂能"), ("002074", "国轩高科"),
             ("300207", "欣旺达"), ("002812", "恩捷股份"), ("300438", "鹏辉能源")],
    "电池化学品": [("002709", "天赐材料"), ("002812", "恩捷股份"), ("603659", "璞泰来"),
                   ("300037", "新宙邦"), ("300073", "当升科技"), ("600884", "杉杉股份")],
    "电子化学品Ⅱ": [("002409", "雅克科技"), ("603078", "江化微"), ("300655", "晶瑞电材"),
                     ("300037", "新宙邦"), ("603650", "彤程新材"), ("300236", "上海新阳")],
    "电子化学品Ⅲ": [("002409", "雅克科技"), ("603078", "江化微"), ("300655", "晶瑞电材"),
                     ("300037", "新宙邦"), ("603650", "彤程新材"), ("300236", "上海新阳")],
    "宁组合": [("300750", "宁德时代"), ("002594", "比亚迪"), ("601012", "隆基绿能"),
               ("300014", "亿纬锂能"), ("002812", "恩捷股份"), ("300274", "阳光电源"),
               ("300124", "汇川技术")],
    "固态电池": [("300750", "宁德时代"), ("002460", "赣锋锂业"), ("002466", "天齐锂业"),
                 ("300073", "当升科技"), ("002812", "恩捷股份"), ("603659", "璞泰来")],
    "钠离子电池": [("002866", "传艺科技"), ("600348", "华阳股份"), ("600152", "维科技术"),
                   ("300750", "宁德时代"), ("300438", "鹏辉能源")],
    "锂电池概念": [("300750", "宁德时代"), ("300014", "亿纬锂能"), ("002594", "比亚迪"),
                   ("002074", "国轩高科"), ("002812", "恩捷股份")],
    "锂电池": [("300750", "宁德时代"), ("300014", "亿纬锂能"), ("002594", "比亚迪"),
               ("002074", "国轩高科"), ("002812", "恩捷股份")],
    "电池技术": [("300750", "宁德时代"), ("300014", "亿纬锂能"), ("002812", "恩捷股份"),
                 ("002709", "天赐材料"), ("300037", "新宙邦")],
    "电力设备": [("300274", "阳光电源"), ("300750", "宁德时代"), ("300124", "汇川技术"),
                 ("600089", "特变电工"), ("601012", "隆基绿能")],
    "新能源车": [("002594", "比亚迪"), ("601127", "赛力斯"), ("000625", "长安汽车"),
                 ("300124", "汇川技术")],
    "储能概念": [("300274", "阳光电源"), ("300750", "宁德时代"), ("002074", "国轩高科"),
                 ("688063", "派能科技")],
    "白酒": [("600519", "贵州茅台"), ("000858", "五粮液"), ("000568", "泸州老窖")],
    "光伏": [("601012", "隆基绿能"), ("002459", "晶澳科技"), ("600438", "通威股份")],
    "半导体": [("688981", "中芯国际"), ("688041", "海光信息"), ("603986", "兆易创新")],
    "芯片": [("688981", "中芯国际"), ("688041", "海光信息"), ("603986", "兆易创新")],
    "证券": [("600030", "中信证券"), ("601688", "华泰证券"), ("601995", "中金公司")],
    "稀土": [("600111", "北方稀土"), ("000831", "中国稀土")],
    "医药": [("603259", "药明康德"), ("600276", "恒瑞医药"), ("300760", "迈瑞医疗")],
    "家电": [("000333", "美的集团"), ("000651", "格力电器"), ("600690", "海尔智家")],
    "银行": [("600036", "招商银行"), ("601166", "兴业银行"), ("601398", "工商银行")],
    "保险": [("601318", "中国平安"), ("601601", "中国太保"), ("601628", "中国人寿")],
    "有色": [("601899", "紫金矿业"), ("600111", "北方稀土"), ("603993", "洛阳钼业")],
}

# 东财板块归属里每个板块的龙头（按板块精确匹配，避免「电池化学品」被「电池」抢占）
BOARD_LEADERS = {
    "电池": ("300750", "宁德时代"),
    "电池化学品": ("002709", "天赐材料"),
    "电子化学品Ⅱ": ("002409", "雅克科技"),
    "电子化学品Ⅲ": ("002409", "雅克科技"),
    "宁组合": ("300750", "宁德时代"),
    "固态电池": ("300750", "宁德时代"),
    "钠离子电池": ("002866", "传艺科技"),
    "锂电池概念": ("300750", "宁德时代"),
    "锂电池": ("300750", "宁德时代"),
    "电池技术": ("300750", "宁德时代"),
    "电力设备": ("300274", "阳光电源"),
    "新能源车": ("002594", "比亚迪"),
    "储能概念": ("300274", "阳光电源"),
    "光模块": ("300308", "中际旭创"),
    "CPO": ("300308", "中际旭创"),
    "通信设备": ("300308", "中际旭创"),
    "算力": ("300308", "中际旭创"),
    "白酒": ("600519", "贵州茅台"),
    "光伏": ("601012", "隆基绿能"),
    "半导体": ("688981", "中芯国际"),
    "芯片": ("688981", "中芯国际"),
    "证券": ("600030", "中信证券"),
    "稀土": ("600111", "北方稀土"),
    "医药": ("603259", "药明康德"),
    "家电": ("000333", "美的集团"),
    "银行": ("600036", "招商银行"),
    "保险": ("601318", "中国平安"),
    "有色": ("601899", "紫金矿业"),
}

# 产业链关联板块：东财 F10 未直接归属，但成分高度重叠（电池材料 ↔ 电子化学品，通信设备 ↔ 光模块）
BOARD_CHAIN = {"电池化学品": "电子化学品Ⅲ", "通信设备": "光模块"}
BOARD_LIMIT = 3
BOARD_KLINE_TTL = 600


def _leader_for(text):
    for keyword, code, name in LEADER_MAP:
        if keyword in (text or ""):
            return {"code": code, "name": name}
    return None


def _sector_by_code(code):
    for keyword, leader_code, _ in LEADER_MAP:
        if leader_code == code:
            return keyword
    return ""


def _board_leader(name):
    hit = BOARD_LEADERS.get(name)
    return {"code": hit[0], "name": hit[1]} if hit else None


def _select_boards(code, memberships, industry, concepts):
    """从东财 F10 板块归属中挑选可用板块：保留最细的两级行业板块 + 主题概念，最后补产业链关联板块"""
    eligible = []
    for board in memberships or []:
        name = board.get("name") or ""
        if name not in SECTOR_PEERS or any(p["name"] == name for p in eligible):
            continue
        rank = board.get("rank") or 99
        eligible.append({
            "code": board.get("code") or "",
            "name": name,
            "rank": rank,
            "kind": "industry" if rank <= 3 else "concept",
        })
    # 行业板块只保留最细的两级（一级行业过宽，如「电力设备」），主题概念按排名补充
    industry_boards = sorted([b for b in eligible if b["kind"] == "industry"], key=lambda b: b["rank"])[-2:]
    concept_boards = sorted([b for b in eligible if b["kind"] == "concept"], key=lambda b: b["rank"])
    picked = (industry_boards + concept_boards)[:BOARD_LIMIT]
    if not picked:
        # F10 不可用时的兜底：行业名 / 龙头映射反推板块
        kw = industry or _sector_by_code(code)
        if kw not in SECTOR_PEERS:
            kw = next((name for name in SECTOR_PEERS if name in " ".join(concepts)), "")
        if kw in SECTOR_PEERS:
            picked.append({"code": "", "name": kw, "kind": "concept"})
    names = [p["name"] for p in picked]
    for board in list(picked):
        extra = BOARD_CHAIN.get(board["name"])
        if extra and extra in SECTOR_PEERS and extra not in names:
            picked.append({"code": "", "name": extra, "kind": "chain"})
            names.append(extra)
    return picked


def _board_trend(name, peers):
    """用核心成分股等权合成板块指数 K 线（含 OHLC，用于缩略 K 线与 0 轴判断）"""
    def producer():
        by_date = {}
        latest = {}
        for peer_code, peer_name in peers:
            try:
                bars = em.fetch_kline(f"{em.market_prefix(peer_code)}.{peer_code}", 101, 60)["bars"]
            except Exception:  # noqa: BLE001
                continue
            if not bars:
                continue
            latest[peer_code] = {
                "code": peer_code,
                "name": peer_name,
                "price": bars[-1]["close"],
                "change_pct": bars[-1].get("change_pct"),
            }
            for i, bar in enumerate(bars):
                if i == 0:
                    continue
                base = bars[i - 1]["close"]
                if not base:
                    continue
                by_date.setdefault(bar["date"], []).append({
                    "o": bar["open"] / base,
                    "h": bar["high"] / base,
                    "l": bar["low"] / base,
                    "c": bar["close"] / base,
                    "p": (bar["close"] / base - 1) * 100,
                })
        need = max(2, len(peers) // 2)
        dates = sorted(d for d, values in by_date.items() if len(values) >= need)
        if len(dates) < 6:
            raise em.DataError("板块成分 K 线不足")
        series = []
        level = 1000.0
        for date in dates:
            rows = by_date[date]
            n = len(rows)
            avg_o = sum(r["o"] for r in rows) / n
            avg_h = sum(r["h"] for r in rows) / n
            avg_l = sum(r["l"] for r in rows) / n
            avg_c = sum(r["c"] for r in rows) / n
            open_ = round(level * avg_o, 2)
            close = round(level * avg_c, 2)
            high = round(max(level * avg_h, open_, close), 2)
            low = round(min(level * avg_l, open_, close), 2)
            pcts = [r["p"] for r in rows]
            series.append({
                "date": date,
                "open": open_, "close": close, "high": high, "low": low,
                "change_pct": round((avg_c - 1) * 100, 2),
                "up": len([v for v in pcts if v > 0]),
                "down": len([v for v in pcts if v < 0]),
            })
            level = close
        closes = [s["close"] for s in series]
        last = series[-1]
        prev_close = series[-2]["close"] if len(series) >= 2 else None
        ma5 = round(sum(closes[-5:]) / 5, 2) if len(closes) >= 5 else None
        ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else None
        if ma5 is not None and ma20 is not None:
            state = "上行" if last["close"] > ma5 > ma20 else "下行" if last["close"] < ma5 < ma20 else "震荡"
        else:
            state = "震荡"
        return {
            "change_pct": last["change_pct"],
            "up": last["up"],
            "down": last["down"],
            "prev_close": prev_close,
            "below_zero": last["change_pct"] < 0,
            "today_high_pct": round((last["high"] / prev_close - 1) * 100, 2) if prev_close else None,
            "today_low_pct": round((last["low"] / prev_close - 1) * 100, 2) if prev_close else None,
            "change_5d": round((closes[-1] / closes[-6] - 1) * 100, 2) if len(closes) >= 6 else None,
            "change_20d": round((closes[-1] / closes[-21] - 1) * 100, 2) if len(closes) >= 21 else None,
            "ma5": ma5,
            "ma20": ma20,
            "state": state,
            "bars": series[-30:],
            "peers": sorted(latest.values(), key=lambda p: -(p.get("change_pct") or -99))[:6],
            "members": len(latest),
        }

    return cached_call(f"bkline:{name}", BOARD_KLINE_TTL, producer)


def _board_trend_ths(name, clid):
    """同花顺板块指数日线：任意板块都能拿到 OHLC，不依赖硬编码成分表"""
    def producer():
        data = ths.board_kline(clid, 60)
        series = data["bars"]
        if len(series) < 6:
            raise ths.ThsError("板块 K 线不足")
        closes = [b["close"] for b in series]
        last = series[-1]
        prev_close = series[-2]["close"]
        ma5 = round(sum(closes[-5:]) / 5, 2) if len(closes) >= 5 else None
        ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else None
        if ma5 is not None and ma20 is not None:
            state = "上行" if last["close"] > ma5 > ma20 else "下行" if last["close"] < ma5 < ma20 else "震荡"
        else:
            state = "震荡"
        return {
            "change_pct": last["change_pct"],
            "up": None,
            "down": None,
            "prev_close": prev_close,
            "below_zero": (last["change_pct"] or 0) < 0,
            "today_high_pct": round((last["high"] / prev_close - 1) * 100, 2) if prev_close else None,
            "today_low_pct": round((last["low"] / prev_close - 1) * 100, 2) if prev_close else None,
            "change_5d": round((closes[-1] / closes[-6] - 1) * 100, 2),
            "change_20d": round((closes[-1] / closes[-21] - 1) * 100, 2) if len(closes) >= 21 else None,
            "ma5": ma5,
            "ma20": ma20,
            "state": state,
            "bars": series[-30:],
            "peers": [],
            "members": None,
            "board_name": data.get("name") or name,
            "source": "同花顺板块指数",
        }

    return cached_call(f"thstrend:{clid}", BOARD_KLINE_TTL, producer)


def _ths_boards(code, change_pct):
    """通用板块：按个股在同花顺的概念板块页取板块，再取板块指数 K 线"""
    try:
        picked = ths.stock_boards(code)
    except Exception:  # noqa: BLE001
        return []
    if not picked:
        return []
    boards = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_board_trend_ths, b["name"], b["code"]) for b in picked]
        for board_meta, future in zip(picked, futures):
            try:
                trend = future.result()
            except Exception:  # noqa: BLE001
                continue
            boards.append({
                "code": board_meta["code"],
                "name": board_meta["name"],
                "kind": board_meta["kind"],
                "leader": _board_leader(board_meta["name"]),
                "relative": round(change_pct - trend["change_pct"], 2)
                            if change_pct is not None and trend.get("change_pct") is not None else None,
                **trend,
            })
    return boards


def _f10_boards(code, industry, concepts, change_pct):
    """兜底：东财 F10 板块归属 + 内置成分表合成板块 K 线"""
    try:
        memberships = em.fetch_stock_boards(code)
    except Exception:  # noqa: BLE001
        memberships = []
    picked = _select_boards(code, memberships, industry, concepts)

    boards = []
    if picked:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_board_trend, b["name"], SECTOR_PEERS[b["name"]]) for b in picked]
            for board_meta, future in zip(picked, futures):
                try:
                    trend = future.result()
                except Exception:  # noqa: BLE001
                    trend = None
                if not trend:
                    continue
                boards.append({
                    "code": board_meta["code"],
                    "name": board_meta["name"],
                    "kind": board_meta["kind"],
                    "leader": _board_leader(board_meta["name"]),
                    "relative": round(change_pct - trend["change_pct"], 2)
                                if change_pct is not None and trend.get("change_pct") is not None else None,
                    **trend,
                })
    return boards


def _stock_context(code, meta, bars, price):
    name = meta.get("name") or code
    industry = meta.get("industry") or ""
    change_pct = bars[-1].get("change_pct") if bars else None
    concepts = meta.get("concepts") or []

    boards = _ths_boards(code, change_pct)
    if not boards:
        boards = _f10_boards(code, industry, concepts, change_pct)

    primary = boards[0] if boards else None
    board = None
    board_stocks = []
    if primary:
        board = {
            "code": primary["code"],
            "name": primary["name"],
            "change_pct": primary["change_pct"],
            "up": primary["up"],
            "down": primary["down"],
            "amount": None,
        }
        board_stocks = primary["peers"]

    leader_hint = _board_leader(primary["name"]) if primary else None
    if not leader_hint:
        leader_hint = _leader_for(" ".join([industry, _sector_by_code(code)] + concepts).strip())
    if board_stocks and not leader_hint:
        top = board_stocks[0]
        leader_hint = {"code": top["code"], "name": top["name"]}

    leader = None
    leader_bars = []
    if leader_hint:
        try:
            quote = em.fetch_quotes([leader_hint["code"]]).get(leader_hint["code"]) or {}
            leader = {
                "code": leader_hint["code"],
                "name": leader_hint["name"],
                "price": quote.get("price"),
                "change_pct": quote.get("change_pct"),
                "is_self": leader_hint["code"] == code,
            }
            if quote.get("name"):
                leader["name"] = quote["name"]
        except Exception:  # noqa: BLE001
            leader = {"code": leader_hint["code"], "name": leader_hint["name"],
                      "price": None, "change_pct": None, "is_self": leader_hint["code"] == code}
        try:
            leader_bars = em.fetch_kline(
                f"{em.market_prefix(leader_hint['code'])}.{leader_hint['code']}", 101, 12)["bars"]
            if leader_bars:
                closes = [b["close"] for b in leader_bars]
                leader["change_5d"] = round((closes[-1] / closes[-6] - 1) * 100, 2) if len(closes) >= 6 else None
        except Exception:  # noqa: BLE001
            pass

    related_news = []
    events = []
    try:
        items = news_src.fetch_news(30).get("items") or []
        words = [name] + ([leader["name"]] if leader and leader.get("name") else [])
        words += [b["name"] for b in boards]
        words += [w for w in [industry] + concepts if w and len(w) >= 2]
        for item in items:
            text = f"{item.get('title') or ''} {item.get('summary') or ''}"
            if any(w in text for w in words if w):
                related_news.append({
                    "title": item["title"],
                    "time_text": item.get("time_text"),
                    "impact": item.get("impact"),
                    "level": item.get("level"),
                    "url": item.get("url"),
                    "source": item.get("source") or "华尔街见闻",
                    "upcoming": bool(item.get("upcoming")),
                })
    except Exception:  # noqa: BLE001
        related_news = []

    try:
        start = datetime.now().strftime("%Y-%m-%d")
        end = (datetime.now() + timedelta(days=21)).strftime("%Y-%m-%d")
        calendar = em.fetch_calendar(start, end, 600)
        keywords = [name, industry] + [b["name"] for b in boards] + concepts
        for board in boards:
            if board.get("kind") == "chain":
                continue
            for key, extra in EVENT_KEYWORDS.items():
                if key in (board.get("name") or ""):
                    keywords += list(extra)
        seen_keywords = []
        for word in keywords:
            if word and len(word) >= 2 and word not in seen_keywords:
                seen_keywords.append(word)
        seen_events = set()
        for item in calendar:
            text = f"{item.get('name') or ''} {item.get('content') or ''}"
            if any(w in text for w in seen_keywords):
                key = (item.get("name") or "")[:18]
                if key in seen_events:
                    continue
                seen_events.add(key)
                events.append({
                    "date": item["date"],
                    "end_date": item.get("end_date"),
                    "name": item["name"],
                    "type": item.get("type"),
                    "content": item.get("content"),
                    "city": item.get("city"),
                    "source": item.get("source"),
                    "url": item.get("url"),
                })
        events.sort(key=lambda x: x["date"])
    except Exception:  # noqa: BLE001
        events = []

    try:
        report = research.summarize_reports(code, price)
    except Exception:  # noqa: BLE001
        report = {"available": False, "windows": {}, "recent": [], "trend": "研报数据获取失败"}

    leader_pct = leader.get("change_pct") if leader else None
    summary, drivers = _context_text(name, change_pct, boards, leader, report, related_news)
    industry_names = [b["name"] for b in boards if b["kind"] == "industry"]
    industry_name = industry or (industry_names[-1] if industry_names else (boards[0]["name"] if boards else ""))
    return {
        "industry": industry_name,
        "boards": boards,
        "board": board,
        "board_stocks": board_stocks[:6],
        "leader": leader,
        "relative": {
            "to_board": primary.get("relative") if primary else None,
            "to_leader": round(change_pct - leader_pct, 2) if change_pct is not None and leader_pct is not None else None,
        },
        "news": related_news[:5],
        "events": events[:5],
        "research": report,
        "analysis": {"summary": summary, "drivers": drivers},
    }


def _pct_text(value, digits=2):
    return f"{value:+.{digits}f}%" if value is not None else "—"


def _context_text(name, change_pct, boards, leader, report, news):
    summary = f"{name} 今日{'上涨' if (change_pct or 0) > 0 else '下跌'} {abs(change_pct or 0):.2f}%"
    drivers = []
    if boards:
        summary += "，东财所属板块同步表现：" + "、".join(
            f"{b['name']} {_pct_text(b.get('change_pct'))}" for b in boards)
        core = [b for b in boards if b.get("kind") != "chain"] or boards
        main = max(core, key=lambda b: abs(b.get("change_pct") or 0))
        drivers.append(
            f"按东财板块 K 线（核心成分等权合成）看，{main['name']} 今日波动最大（{_pct_text(main.get('change_pct'))}），"
            f"是今日个股走势的主要外部驱动；板块内 {main.get('up') or 0} 家上涨 / {main.get('down') or 0} 家下跌，"
            f"赚钱效应{'偏强' if (main.get('up') or 0) >= (main.get('down') or 0) else '偏弱'}。")
        for board in boards:
            label = board["name"] + ("（产业链关联）" if board.get("kind") == "chain" else "")
            gap = board.get("relative")
            trend = (f"近 5 日 {_pct_text(board.get('change_5d'))}、近 20 日 {_pct_text(board.get('change_20d'))}，"
                     f"均线结构{board.get('state') or '震荡'}")
            if gap is None:
                drivers.append(f"{label}：{trend}。")
            elif gap >= 1.5:
                drivers.append(f"{label}：个股超额 {gap:+.2f}%，明显强于板块（{trend}），属板块内主攻品种。")
            elif gap <= -1.5:
                drivers.append(f"{label}：个股落后板块 {abs(gap):.2f}%，板块上涨但个股未同步，"
                               f"存在自身压力或资金分歧（{trend}）。")
            else:
                drivers.append(f"{label}：个股与板块基本同步（超额 {gap:+.2f}%），{trend}，涨跌主要由板块节奏驱动。")
        strong_boards = [b for b in core if b.get("change_5d") is not None]
        if len(strong_boards) > 1:
            best = max(strong_boards, key=lambda b: b["change_5d"])
            drivers.append(f"中期看，{best['name']} 近 5 日最强（{_pct_text(best['change_5d'])}），"
                           f"可作为个股中期方向的参照；板块与个股同步走强时趋势延续概率更高。")
        if boards[0].get("peers"):
            drivers.append("板块内强势标的：" + "、".join(
                f"{s['name']}({_pct_text(s.get('change_pct'))})" for s in boards[0]["peers"][:3]))
    else:
        drivers.append("东财板块归属或板块 K 线暂不可用（行情源限流），可结合行业属性与相关新闻判断联动关系。")
    leader_pct = leader.get("change_pct") if leader else None
    if leader and leader.get("is_self"):
        summary += "，该股本身即为板块龙头，其走势直接代表板块方向"
        drivers.append(f"{leader['name']}为板块龙头，资金定价权集中于此，观察其量能与封单强度可判断板块持续性。")
        if leader.get("change_5d") is not None:
            drivers.append(f"龙头自身近 5 日累计 {leader['change_5d']:+.2f}%，可作为板块中期强弱的锚点。")
    elif leader and leader_pct is not None:
        summary += f"，板块龙头{leader['name']}{'涨' if leader_pct > 0 else '跌'} {abs(leader_pct):.2f}%"
        if change_pct is not None:
            gap = change_pct - leader_pct
            if gap >= 2:
                drivers.append(f"相对龙头{leader['name']}超额 {gap:.2f}%，说明资金在板块内部做高低切换，个股获得补涨资金。")
            elif gap <= -2:
                drivers.append(f"明显弱于龙头{leader['name']} {abs(gap):.2f}%，龙头吸金导致个股资金被分流，弹性偏弱。")
            else:
                drivers.append(f"与龙头{leader['name']}走势基本同步，板块定价权仍由龙头掌握，可参考龙头判断板块方向。")
        if leader.get("change_5d") is not None:
            drivers.append(f"龙头近 5 日累计 {leader['change_5d']:+.2f}%，可作为板块中期强弱的锚点。")
    if news:
        drivers.append("相关消息：" + "；".join(n["title"][:42] for n in news[:2]) + "，需结合消息性质判断持续性。")
    else:
        drivers.append("近 24 小时无直接相关的行业或个股新闻，涨跌更多由板块轮动与资金行为驱动。")
    if report.get("available"):
        target = report.get("target_avg")
        upside = report.get("upside_pct")
        drivers.append(f"研报覆盖 {report.get('total')} 篇，近 1/3/6 个月分别为 "
                       f"{report['windows']['1m']['count']}/{report['windows']['3m']['count']}/"
                       f"{report['windows']['6m']['count']} 篇；{report.get('trend')}")
        if target and upside is not None:
            drivers.append(f"机构一致目标价约 {target:.2f} 元，相对现价仍有 {upside:+.2f}% 空间。")
        else:
            drivers.append("近期研报未给出明确目标价，需结合估值与业绩预期自行判断。")
    else:
        drivers.append("暂无有效研报覆盖，缺少机构一致预期参考。")
    summary += "。"
    return summary, drivers


def stock_context(code: str):
    meta = em.fetch_stock_meta(code)
    bars = em.fetch_kline(f"{em.market_prefix(code)}.{code}", 101, 60)["bars"]
    price = bars[-1]["close"] if bars else (meta.get("price") or 0)
    return _stock_context(code, meta, bars, price)



def history():
    dates = em.fetch_kline("1.000001", 101, 15)["bars"]
    cached = {row["date"]: row for row in snapshot_dates()}
    items = []
    for bar in reversed(dates):
        row = cached.get(bar["date"])
        items.append({
            "date": bar["date"],
            "change_pct": bar["change_pct"],
            "close": bar["close"],
            "amount": bar["amount"],
            "cached": bool(row),
        })
    return {"items": items[:10]}
