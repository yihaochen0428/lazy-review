"""做 T 的环境自适应：指数环境 + 板块环境 + 个股相对强弱

核心思路：
1. 指数环境：上证与创业板 5 分钟线的涨跌幅、是否站上分时均价、近 6 根斜率；
2. 板块环境：用对应行业 ETF 的分时表现做代理（板块指数没有公开 5 分钟线，
   ETF 与板块高度相关且可交易，用来衡量“板块向上/向下”）；
3. 个股相对：个股自身分时状态减去板块状态，衡量个股是否同步走强。

三者加权得到环境分 env ∈ [-1, 1]，再对做 T 买点做置信度加减：
板块向上且个股同步向上时，买点强度上调；环境偏弱时买点降级。
"""
import time
from datetime import datetime

from . import dkline
from . import eastmoney as em
from .db import cached_call
from .indicators import macd
from .intraday import t_points

INDEXES = (("1.000001", "上证指数"), ("0.399006", "创业板指"))

# 行业 -> ETF 代理（板块无法直接取 5 分钟线，用 ETF 衡量板块方向）
ETF_PROXY = {
    "半导体": ("1.512480", "半导体ETF"),
    "AI 与算力": ("1.515070", "AI ETF"),
    "新能源": ("1.515030", "新能源车ETF"),
    "医药": ("1.512010", "医药ETF"),
    "地产链": ("1.512200", "房地产ETF"),
    "券商金融": ("1.512000", "券商ETF"),
    "消费": ("0.159928", "消费ETF"),
    "军工": ("1.512660", "军工ETF"),
    "资源品": ("1.512400", "有色ETF"),
}

# 关联板块名关键词 -> 行业（用于个股所属板块的模糊匹配）
ETF_KEYWORDS = {
    "半导体": "半导体", "芯片": "半导体", "集成电路": "半导体", "光刻": "半导体", "存储": "半导体",
    "算力": "AI 与算力", "人工智能": "AI 与算力", "光模块": "AI 与算力", "服务器": "AI 与算力",
    "CPO": "AI 与算力", "数据中心": "AI 与算力", "AIGC": "AI 与算力",
    "光伏": "新能源", "储能": "新能源", "锂": "新能源", "电池": "新能源", "新能源": "新能源",
    "风电": "新能源", "充电桩": "新能源",
    "医药": "医药", "医疗": "医药", "创新药": "医药", "疫苗": "医药", "CXO": "医药", "中药": "医药",
    "地产": "地产链", "房地产": "地产链", "建材": "地产链", "家居": "地产链", "建筑": "地产链",
    "券商": "券商金融", "证券": "券商金融", "银行": "券商金融", "保险": "券商金融", "金融": "券商金融",
    "消费": "消费", "白酒": "消费", "食品": "消费", "旅游": "消费", "免税": "消费", "零售": "消费",
    "军工": "军工", "国防": "军工", "航天": "军工", "航空": "军工", "无人机": "军工",
    "黄金": "资源品", "铜": "资源品", "铝": "资源品", "稀土": "资源品", "煤炭": "资源品",
    "有色": "资源品", "钢铁": "资源品", "石油": "资源品",
}


def _clamp(value, low=-1.0, high=1.0):
    return max(low, min(high, value))


def _vwap(bars):
    amount = sum(b.get("amount") or 0 for b in bars)
    volume = sum(b.get("volume") or 0 for b in bars)
    return amount / volume if volume else None


def _state(bars, prev_close=None):
    """用一段分时序列刻画方向：涨跌幅 + 是否站上均价 + 近 6 根斜率"""
    if not bars:
        return None
    closes = [b.get("close") or 0 for b in bars]
    if not closes[-1]:
        return None
    base = prev_close or closes[0]
    if not base:
        return None
    change = (closes[-1] / base - 1) * 100
    vwap = _vwap(bars)
    above = bool(vwap and closes[-1] >= vwap)
    tail = min(6, len(closes))
    slope = (closes[-1] / closes[-tail] - 1) * 100 if tail > 1 else 0
    score = _clamp(change / 1.5 * 0.6 + (0.25 if above else -0.25) + _clamp(slope / 0.6) * 0.15)
    return {
        "change_pct": round(change, 2),
        "above_vwap": above,
        "slope": round(slope, 2),
        "score": round(score, 3),
        "price": round(closes[-1], 2),
    }


def _label(score):
    if score >= 0.5:
        return "强势"
    if score >= 0.15:
        return "偏强"
    if score > -0.15:
        return "中性"
    if score > -0.5:
        return "偏弱"
    return "弱势"


def _fetch(secid, days=1):
    # 5 分钟线每个交易日约 48 根；多取一天用于计算首日的前收盘（新浪单次上限约 1800 根）
    bars_per_day = 48
    limit = min(bars_per_day * (max(1, int(days)) + 1), 1800)
    try:
        return em.fetch_minute_kline(secid, 5, limit, days)
    except Exception:  # noqa: BLE001
        return {"bars": [], "prev_close": None}


def index_env():
    items = []
    for secid, name in INDEXES:
        data = _fetch(secid)
        state = _state(data.get("bars"), data.get("prev_close"))
        if state:
            state["name"] = name
            items.append(state)
    if not items:
        return {"score": 0.0, "change_pct": 0.0, "label": "未知", "items": []}
    score = round(sum(i["score"] for i in items) / len(items), 3)
    change = round(sum(i["change_pct"] for i in items) / len(items), 2)
    return {"score": score, "change_pct": change, "label": _label(score), "items": items}


def _match_sector(board_names):
    for name in board_names or []:
        for keyword, sector in ETF_KEYWORDS.items():
            if keyword in (name or ""):
                return sector
    return None


def sector_env(board_names):
    names = [n for n in (board_names or []) if n]
    sector = _match_sector(names)
    if not sector:
        return {"matched": False, "score": None, "name": names[0] if names else "",
                "label": "未匹配", "items": []}
    secid, etf_name = ETF_PROXY[sector]
    data = _fetch(secid)
    state = _state(data.get("bars"), data.get("prev_close"))
    if not state:
        return {"matched": False, "score": None, "name": sector, "label": "数据缺失", "items": []}
    state.update({"name": sector, "etf": etf_name})
    return {"matched": True, "score": state["score"], "change_pct": state["change_pct"],
            "label": _label(state["score"]), "name": sector, "etf": etf_name, "items": [state]}


def build_env(stock_bars, prev_close, board_names, dk=None, mkt=None):
    idx = index_env()
    sec = sector_env(board_names)
    stock = _state(stock_bars, prev_close) or {}
    effective = sec["score"] if sec.get("matched") else idx["score"]
    stock_score = stock.get("score", 0) or 0
    relative = round(stock_score - effective, 3)
    total = round(_clamp(0.45 * idx["score"] + 0.35 * effective + 0.2 * relative), 3)
    sector_pct = sec.get("change_pct") or 0
    stock_pct = stock.get("change_pct") or 0
    aligned = bool(sec.get("matched") and sector_pct > 0 and stock_pct > 0)
    both_down = bool(sec.get("matched") and sector_pct < 0 and stock_pct < 0)
    if aligned:
        note = "板块向上、个股同步走强，买点置信度上调"
    elif both_down:
        note = "板块与个股同步走弱，买点降级、卖点优先"
    else:
        note = "板块与个股方向不一致，做T以快进快出为主"
    return {
        "score": total,
        "label": _label(total),
        "index": idx,
        "sector": sec,
        "stock": stock,
        "relative": relative,
        "sector_up": sector_pct > 0,
        "stock_up": stock_pct > 0,
        "aligned_up": aligned,
        "buy_boost": int(round(total * 25)),
        "dk": dk,
        "mkt": mkt,
        "note": note,
    }


def apply_env(points, env):
    """按环境分调整每个做T点位的置信度与目标

    实盘回测结论：环境分处于 [-0.5, 0.5) 时买点胜率最高，极端强势（追高）与
    极端弱势（逆势接飞刀）都明显变差，因此对两端做降级而不是一味加分。
    """
    total = env.get("score") or 0
    boost = env.get("buy_boost") or 0
    aligned = env.get("aligned_up")
    for p in points or []:
        base = 50 + (p.get("win_rate", 50) - 50) * 1.1 + (p.get("confluence", 1) - 1) * 9
        if p["type"] == "buy":
            strength = base + boost + (6 if aligned else 0)
            if total >= 0.5:
                strength -= 10
                p["target"] = max(0.3, (p.get("target") or 0.4) - 0.1)
                p["env_note"] = "指数/板块偏热，避免追高，买点降为低置信"
            elif total <= -0.5:
                strength -= 12
                p["target"] = 0.3
                p["env_note"] = "指数与板块共振走弱，买点降级（仅超跌反弹可做）"
            elif total <= -0.15:
                strength -= 4
                p["env_note"] = "环境偏弱，买点需个股相对更强才可执行"
            else:
                p["env_note"] = env.get("note")
        else:
            strength = base - boost + (4 if total <= -0.15 else 0)
            if total >= 0.15:
                strength -= 10
                p["env_note"] = "环境向上，卖点容易卖飞，降为低置信"
            elif total <= -0.15 and p.get("strategy_key") == "vwap":
                strength += 6
                p["env_note"] = "弱势环境下的均价回归卖点，信号更强"
            elif total <= -0.15:
                strength -= 4
                p["env_note"] = "单边下跌中背离卖点胜率偏低，降级"
            else:
                p["env_note"] = env.get("note")
        # 日K 背景（T-1 日线）：空头排列时做T买点胜率明显偏低，多头时卖点容易卖飞
        dk_raw = env.get("dk")
        dk = dk_raw.get("score") if isinstance(dk_raw, dict) else dk_raw
        if dk is not None:
            p["dk_score"] = dk
            if p["type"] == "buy":
                if dk < -0.45:
                    strength -= 10
                    p["target"] = max(0.3, (p.get("target") or 0.4) - 0.1)
                    p["dk_note"] = "日K 空头排列，做T买点胜率偏低（回测 51.6%）"
                elif dk >= 0.15:
                    strength += 5
                    p["dk_note"] = "日K 偏多/多头，回调做T胜率更高"
                else:
                    strength += 2
                    p["dk_note"] = "日K 中性，按分时信号执行"
            elif dk >= 0.45:
                strength -= 10
                p["dk_note"] = "日K 多头，背离卖点容易卖飞（回测 27.7%）"
            elif dk < -0.45:
                strength += 6
                p["dk_note"] = "日K 空头，反弹卖点更有效"
        # 大盘背景（上证日K 趋势 + 情绪温度）：方向与个股日K 一致，幅度减半
        mkt_raw = env.get("mkt")
        mkt = mkt_raw.get("score") if isinstance(mkt_raw, dict) else mkt_raw
        if mkt is not None:
            p["mkt_score"] = mkt
            if p["type"] == "buy":
                if mkt <= -0.45:
                    strength -= 5
                    p["mkt_note"] = "大盘趋势/情绪走弱，做T买点降级"
                elif mkt >= 0.15:
                    strength += 2
                    p["mkt_note"] = "大盘偏强，回调买点顺风"
            elif mkt >= 0.15:
                strength -= 6
                p["mkt_note"] = "大盘偏强，反弹卖点容易卖飞"
            elif mkt <= -0.45:
                strength += 5
                p["mkt_note"] = "大盘走弱，反弹卖点更有效"
        strength = int(max(5, min(98, strength)))
        # 0 轴策略在最新 10 日回测中胜率仅约 35%，仅作辅助确认，不单独构成高置信信号
        if p.get("strategy_key") == "zero":
            strength -= 8
            strength = int(max(5, strength))
        p["strength"] = strength
        p["grade"] = "强" if strength >= 70 else ("中" if strength >= 50 else "弱")
        p["env_score"] = total
    return points


def annotate(t_data, env):
    """给做T结果挂上环境，并刷新摘要"""
    points = apply_env(t_data.get("points") or [], env)
    t_data["points"] = points
    latest = points[-1] if points else None
    t_data["latest"] = latest
    if latest:
        extra = f"，强度 {latest['strength']}" if latest.get("strength") else ""
        if latest.get("confluence", 1) >= 2:
            extra += f"，{latest['confluence']} 个策略共振"
        t_data["summary"] = (f"最近信号：{latest['time']} {latest['label']}"
                             f"（{latest['strategy']}，{latest['price']:.2f}{extra}）")
    t_data["env"] = env
    return t_data


# ---------------------------------------------------------------- 连板过滤
def _limit_threshold(code):
    """涨停幅度阈值：创业板/科创板 20%，北交所 30%，主板 10%（按 9.5% 判定留出四舍五入余量）"""
    if code[:3] in ("300", "301", "688", "689"):
        return 19.0
    if code[:1] in ("4", "8") or code[:3] == "920":
        return 29.0
    return 9.5


def _streak_map(daily_bars, code):
    """date -> 截至该日连续涨停天数"""
    th = _limit_threshold(code)
    out = {}
    streak = 0
    for bar in daily_bars or []:
        date = (bar.get("date") or "")[:10]
        if (bar.get("change_pct") or 0) >= th:
            streak += 1
        else:
            streak = 0
        if date:
            out[date] = streak
    return out


def limit_state(daily_bars, code):
    """连板状态：连板及连板后首日分时波动失真，做T点位不适用"""
    streaks = _streak_map(daily_bars, code)
    if not streaks:
        return {"streak": 0, "prev_streak": 0, "distorted": False,
                "label": "无日线数据", "limit_up_today": False}
    dates = sorted(streaks)
    streak = streaks[dates[-1]]
    prev = streaks[dates[-2]] if len(dates) > 1 else 0
    distorted = bool(streak >= 2 or prev >= 2)
    if streak >= 2:
        label = f"{streak} 连板"
    elif streak == 1:
        label = "今日涨停（首板）"
    elif prev >= 2:
        label = "连板后首个交易日"
    else:
        label = "非连板"
    return {
        "streak": streak,
        "prev_streak": prev,
        "distorted": distorted,
        "label": label,
        "limit_up_today": streak >= 1,
        "date": dates[-1],
    }


def _daily_bars(secid, limit=60):
    try:
        return em.fetch_kline(secid, 101, limit)["bars"]
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------- 回测
def _by_date(bars):
    out = {}
    for b in bars:
        day = (b.get("date") or "")[:10]
        if day:
            out.setdefault(day, []).append(b)
    return out


def _series(secid, days):
    data = _fetch(secid, days)
    by_day = _by_date(data.get("bars") or [])
    dates = sorted(by_day)
    prev = {}
    for i, day in enumerate(dates):
        prev[day] = by_day[dates[i - 1]][-1]["close"] if i else data.get("prev_close")
    return by_day, prev


def _state_at(day_bars, prev_close, time_text):
    upto = [b for b in day_bars if (b.get("time") or "15:00") <= time_text]
    if not upto:
        return None
    return _state(upto, prev_close)


def _resolve(day_bars, index, point):
    """按目标/止损撮合，日内未触发则收盘平仓"""
    price = point["price"]
    target = point.get("target") or 0.4
    stop = point.get("stop") or 0.6
    if point["type"] == "buy":
        tp = price * (1 + target / 100)
        sl = price * (1 - stop / 100)
        for bar in day_bars[index + 1:]:
            if (bar.get("low") or price) <= sl:
                return "loss", round(-stop, 2)
            if (bar.get("high") or price) >= tp:
                return "win", round(target, 2)
        last = day_bars[-1].get("close") or price
        ret = (last / price - 1) * 100
    else:
        tp = price * (1 - target / 100)
        sl = price * (1 + stop / 100)
        for bar in day_bars[index + 1:]:
            if (bar.get("high") or price) >= sl:
                return "loss", round(-stop, 2)
            if (bar.get("low") or price) <= tp:
                return "win", round(target, 2)
        last = day_bars[-1].get("close") or price
        ret = (price - last) / price * 100
    return ("win" if ret > 0 else "loss"), round(ret, 2)


def _summary(records, result_key="result", return_key="return"):
    settled = [r for r in records if r.get(result_key) in ("win", "loss")]
    if not settled:
        return {"samples": 0, "win_rate": None, "avg_return": None}
    wins = sum(1 for r in settled if r[result_key] == "win")
    avg = sum(r[return_key] for r in settled) / len(settled)
    return {
        "samples": len(settled),
        "win_rate": round(wins / len(settled) * 100, 1),
        "avg_return": round(avg, 2),
    }


def _collect(days=10):
    """逐个样本股、逐日、逐点位采集回测记录（含当时的环境分项）"""
    from .analysis import SECTOR_PLAYS

    days = max(4, min(int(days or 10), 20))
    index_series = {secid: _series(secid, days) for secid, _ in INDEXES}
    try:
        idx_bars = em.fetch_kline("1.000001", 101, days + 90)["bars"]
    except Exception:  # noqa: BLE001
        idx_bars = []
    mkt_cache = {}
    records = []
    skipped = {"limit_days": 0, "stocks": 0}
    for sector, info in SECTOR_PLAYS.items():
        proxy = ETF_PROXY.get(sector)
        etf_by, etf_prev = _series(proxy[0], days) if proxy else ({}, {})
        for code, name, _reason in [x for x in info.get("leaders", [])][:2]:
            secid = f"{em.market_prefix(code)}.{code}"
            stock_by, stock_prev = _series(secid, days)
            # 连板/涨停封板日的 5 分钟线被“一字板/封板无波动”污染，做T回测样本剔除
            daily = _daily_bars(secid, days + 40)
            streaks = _streak_map(daily, code)
            dates = sorted(stock_by)
            for di in range(1, len(dates)):
                day = dates[di]
                prev_day = dates[di - 1]
                if (streaks.get(day) or 0) >= 1 or (streaks.get(prev_day) or 0) >= 2:
                    skipped["limit_days"] += 1
                    continue
                prev_close = stock_prev.get(day)
                day_bars = stock_by[day]
                if not prev_close or len(day_bars) < 6:
                    continue
                closes = [b.get("close") or 0 for b in day_bars]
                dif = macd(closes)["dif"]
                points = t_points(day_bars, prev_close, dif, max_points=40)["points"]
                dk = dkline.state(daily, day) or {}
                if day not in mkt_cache:
                    mkt_cache[day] = dkline.index_ctx(idx_bars, day) or {}
                mkt = mkt_cache[day]
                for point in points:
                    index_scores = []
                    for secid_idx, _ in INDEXES:
                        by_day, prev = index_series[secid_idx]
                        state = _state_at(by_day.get(day) or [], prev.get(day), point["time"])
                        if state:
                            index_scores.append(state["score"])
                    idx_score = sum(index_scores) / len(index_scores) if index_scores else 0.0
                    sec_state = _state_at(etf_by.get(day) or [], etf_prev.get(day), point["time"]) if proxy else None
                    sec_score = sec_state["score"] if sec_state else idx_score
                    stock_state = _state(day_bars[:point["index"] + 1], prev_close) or {}
                    relative = stock_state.get("score", 0) - sec_score
                    total = round(_clamp(0.45 * idx_score + 0.35 * sec_score + 0.2 * relative), 3)
                    env = {
                        "score": total,
                        "buy_boost": int(round(total * 25)),
                        "aligned_up": bool(sec_state and (sec_state.get("change_pct") or 0) > 0
                                           and (stock_state.get("change_pct") or 0) > 0),
                        "dk": dk.get("score"),
                        "mkt": mkt.get("score"),
                        "note": "",
                    }
                    raw_point = dict(point)
                    point = apply_env([point], env)[0]
                    result, ret = _resolve(day_bars, point["index"], point)
                    raw_result, raw_ret = _resolve(day_bars, raw_point["index"], raw_point)
                    records.append({
                        "sector": sector,
                        "name": name,
                        "code": code,
                        "date": day,
                        "time": point["time"],
                        "type": point["type"],
                        "strategy": point["strategy"],
                        "strength": point["strength"],
                        "env": total,
                        "idx": round(idx_score, 3),
                        "sec": round(sec_score, 3),
                        "rel": round(relative, 3),
                        "aligned": env["aligned_up"],
                        "dk": dk.get("score"),
                        "dk_label": dk.get("label"),
                        "pos20": dk.get("pos20"),
                        "dev20": dk.get("dev20"),
                        "ret5": dk.get("ret5"),
                        "mkt": mkt.get("score"),
                        "mkt_sent": (mkt.get("sent") or {}).get("score"),
                        "mkt_pattern": (mkt.get("forecast") or {}).get("pattern"),
                        "result": result,
                        "return": ret,
                        "raw_result": raw_result,
                        "raw_return": raw_ret,
                    })
            skipped["stocks"] += 1
    return records, skipped


def _env_pass(r):
    """环境过滤：买点只在环境分 [-0.5, 0.5) 且日K 非空头（≥-0.45）时执行，
    卖点只保留强度≥70 且环境不强势"""
    if r["type"] == "buy":
        return -0.5 <= r["env"] < 0.5 and (r.get("dk") is None or r["dk"] >= -0.45)
    return r["strength"] >= 70 and r["env"] <= 0.15


def _strong_pass(r):
    """强共振：买点要求板块向上且个股同步走强（或强度≥70），卖点要求高置信"""
    if r["type"] == "buy":
        return r["aligned"] or r["strength"] >= 70
    return r["strength"] >= 70 and r["env"] <= 0.15


def _run_backtest(days=10):
    days = max(4, min(int(days or 10), 20))
    records, skipped = _collect(days)

    baseline = _summary(records, "raw_result", "raw_return")
    env_side = [r for r in records if _env_pass(r)]
    strong = [r for r in records if _strong_pass(r)]
    by_strategy = []
    for key in ("均价", "0轴", "MACD", "量价"):
        subset = [r for r in records if key in (r["strategy"] or "")]
        base = _summary(subset, "raw_result", "raw_return")
        filtered = _summary([r for r in subset if _env_pass(r)])
        by_strategy.append({**base, "name": key, "env_samples": filtered["samples"],
                            "env_win_rate": filtered["win_rate"]})
    return {
        "days": days,
        "updated_at": time.strftime("%Y-%m-%d %H:%M"),
        "baseline": baseline,
        "env_filtered": _summary(env_side),
        "strong": _summary(strong),
        "by_strategy": by_strategy,
        "buy": _summary([r for r in records if r["type"] == "buy"], "raw_result", "raw_return"),
        "sell": _summary([r for r in records if r["type"] == "sell"], "raw_result", "raw_return"),
        "buy_env": _summary([r for r in records if r["type"] == "buy" and _env_pass(r)]),
        "sell_env": _summary([r for r in records if r["type"] == "sell" and _env_pass(r)]),
        "excluded_limit_days": skipped["limit_days"],
        "stocks": skipped["stocks"],
        "note": ("5 分钟线回测：样本已剔除连板/涨停封板日（连板及连板后首日分时失真），"
                 "信号右侧确认，目标/止损按各策略参数，日内未触发按收盘价平仓。"
                 "环境过滤 = 买点只在环境分 [-0.5, 0.5) 且日K 非空头（≥-0.45）执行、"
                 "卖点只保留强度≥70 且环境不强势；"
                 "强共振 = 板块向上且个股同步走强（或强度≥70）。"
                 "日K 仅用 T-1 及之前的日线（MA5/10/20 排列、MA20 偏离、20 日分位、近 10 日涨幅），"
                 "并把日K 加减分写进点位强度（空头 -10、偏多 +5、中性 +2，多头卖点 -10）；"
                 "大盘因子（上证日K 趋势 0.6 + 情绪温度 0.4）同样只取 T-1："
                 "买点大盘弱 -5 / 偏强 +2，卖点大盘偏强 -6 / 大盘弱 +5。"),
    }


def backtest(days=10, force=False, phase=""):
    """策略结论按 09:25 / 15:00 / 24:00 三个时段换挡，同一时段内复用缓存"""
    days = max(4, min(int(days or 10), 20))
    if force:
        return _run_backtest(days)
    stamp = f"{datetime.now():%Y%m%d}" + (f":{phase}" if phase else "")
    return cached_call(f"tenv:bt:v4:{days}:{stamp}", 86400, lambda: _run_backtest(days))
