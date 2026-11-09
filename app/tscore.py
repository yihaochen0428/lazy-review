"""做T加权评分：量能状态 → 空间判断 → 动态目标位

设计要点：
1. 保留全部买卖点，不用持仓状态机；每个点自带「对手方目标价」（买点给卖出的高位、
   卖点给回补的低位），因此不会出现「只有买点没有卖点」的观感。
2. 目标位不再写死，按「量能 + 日内位置」动态放大或收窄：
   缩量高位 → 回落空间大；放量高位 → 还能冲高，目标上移。
3. 总分按流通市值分层加权：大盘股指数与量能权重高，小盘股以指数为主、量能与股性只做轻量修正
   （10/10/10 交叉验证显示中小盘的量能/股性信号噪声更大）。
"""
from .intraday import STRATEGIES

# 分层权重：env=指数/板块/个股相对，vol=量能配合度，prior=股性先验
# 交叉验证（scripts/validate_tiers.py，10/10/10 样本）后回调：大盘放大量能与股性权重，
# 中小盘量能/股性噪声偏大，降低其权重，避免把噪声当成信号。
TIERS = (
    ("大盘", 500e8, {"env": 0.40, "vol": 0.30, "prior": 0.22}),
    ("中盘", 100e8, {"env": 0.32, "vol": 0.20, "prior": 0.14}),
    ("小盘", 0.0, {"env": 0.22, "vol": 0.12, "prior": 0.08}),
)

ENERGY_LABELS = {"expanding": "放量", "shrinking": "缩量", "normal": "常规"}

# 过去 20 日时段均量的分档（与 persona.BINS 对应）
BIN_RANGES = (
    ("09:30", "10:00"), ("10:00", "10:30"), ("10:30", "11:00"), ("11:00", "11:30"),
    ("13:00", "13:30"), ("13:30", "14:00"), ("14:00", "14:30"), ("14:30", "15:00"),
)
BIN_LABELS = tuple(f"{a}-{b}" for a, b in BIN_RANGES)


def bin_index(time_text):
    """把 HH:MM 映射到 8 个时段之一；盘前/午休归到相邻时段"""
    text = (time_text or "")[:5]
    for i, (start, end) in enumerate(BIN_RANGES):
        if start <= text < end:
            return i
    if text and text < "09:30":
        return 0
    if text and "11:30" <= text < "13:00":
        return 3
    return 7 if text else None


def tier_of(float_cap):
    """按流通市值（元）分层，返回 (层级名, 权重字典)"""
    cap = float_cap or 0
    for name, threshold, weights in TIERS:
        if cap >= threshold:
            return name, weights
    return "小盘", TIERS[-1][2]


def weight_payload(weights):
    """把内部权重换算成界面展示的百分比（三者归一）"""
    total = sum(weights.values()) or 1
    return {
        "env": round(weights["env"] / total * 100),
        "vol": round(weights["vol"] / total * 100),
        "prior": round(weights["prior"] / total * 100),
    }


def day_position(bars, index):
    """日内位置：0=当日最低，1=当日最高"""
    upto = bars[:index + 1]
    if not upto:
        return 0.5
    high = max((b.get("high") or b.get("close") or 0) for b in upto)
    low = min((b.get("low") or b.get("close") or 0) for b in upto)
    price = upto[-1].get("close") or 0
    if not high or high <= low:
        return 0.5
    return round(max(0.0, min(1.0, (price - low) / (high - low))), 3)


def energy_state(bars, index, base_hist=None, window=6):
    """近 window 根（默认 30 分钟）的量能状态

    base_hist: 过去 20 日同一时段的均量，用于剔除「早盘天然放量」的季节性。
    """
    if not bars or index < 0:
        return {"label": "normal", "ratio": 1.0, "vr_day": 1.0, "vr_hist": None}
    start = max(0, index - window + 1)
    recent = [b.get("volume") or 0 for b in bars[start:index + 1]]
    now = sum(recent) / len(recent) if recent else 0
    day_vols = [b.get("volume") or 0 for b in bars[:index + 1]]
    day_avg = sum(day_vols) / len(day_vols) if day_vols else 0
    vr_day = (now / day_avg) if day_avg else 1.0
    vr_hist = (now / base_hist) if base_hist else None
    if vr_hist is None:
        label = "expanding" if vr_day >= 1.35 else ("shrinking" if vr_day <= 0.7 else "normal")
    elif vr_hist >= 1.2 and vr_day >= 1.1:
        label = "expanding"
    elif vr_hist <= 0.8:
        label = "shrinking"
    else:
        label = "normal"
    return {
        "label": label,
        "label_cn": ENERGY_LABELS[label],
        "ratio": round(vr_hist if vr_hist is not None else vr_day, 2),
        "vr_day": round(vr_day, 2),
        "vr_hist": round(vr_hist, 2) if vr_hist is not None else None,
    }


def space(side, pos, energy_label):
    """量能 + 日内位置 → 空间系数 K 与结论文案"""
    high, low = pos >= 0.7, pos <= 0.3
    if side == "sell":
        if energy_label == "expanding" and high:
            return 1.6, "量能放大、趋势延续，还能冲高，目标上移别急卖"
        if energy_label == "shrinking" and high:
            return 1.35, "上涨无量、动能衰竭，回落空间大"
        if energy_label == "expanding" and low:
            return 1.0, "下跌放量，仍有下跌空间但不追空"
        if energy_label == "shrinking" and low:
            return 0.6, "缩量止跌，回补空间有限"
        if energy_label == "shrinking":
            return 1.15, "量能萎缩，反弹乏力，回落概率偏高"
        if energy_label == "expanding":
            return 1.1, "量能放大，回落仍可期待"
        return 1.0, "常规空间，按策略目标执行"
    if energy_label == "shrinking" and low:
        return 1.4, "抛压衰竭，反弹空间大"
    if energy_label == "expanding" and low:
        return 0.5, "放量下跌，还有下跌空间，等缩量再买"
    if energy_label == "expanding" and high:
        return 0.6, "放量高位，追高风险大"
    if energy_label == "shrinking" and high:
        return 0.7, "无量上冲，容易回落"
    if energy_label == "shrinking":
        return 1.1, "缩量回踩，下探风险有限"
    if energy_label == "expanding":
        return 1.05, "放量向上，顺势做T"
    return 1.0, "常规空间，按策略目标执行"


def vol_alignment(side, energy_label, pos):
    """量能对该方向的配合度 ∈ [-1, 1]"""
    high, low = pos >= 0.7, pos <= 0.3
    if energy_label == "normal":
        return 0.0
    if side == "buy":
        if energy_label == "shrinking":
            return 0.8 if low else 0.4
        return -0.8 if low else -0.55
    if energy_label == "shrinking":
        return 0.8 if high else -0.3
    return 0.35 if high else 0.2


def _clamp(value, low=5.0, high=98.0):
    return max(low, min(high, value))


def score_point(point, ctx):
    """给单个买卖点加权打分，并写入动态目标价

    ctx 字段：
      weights       分层权重
      env_score     环境分 [-1, 1]
      energy        个股量能状态（dict）
      open_price    当日开盘价（开盘价纳入做T判断）
      spike_fade    该股是否早盘冲高回落型（股性）
      prior_wr      股性先验胜率（0-100，可为 None）
      prior_samples 先验样本数
      strategy_weight 该策略在本股历史表现换算的乘数（0.8-1.3）
    """
    p = dict(point)
    key = p.get("strategy_key") or ""
    base_cfg = STRATEGIES.get(key, {})
    base_wr = base_cfg.get("win_rate", 50)
    sw = ctx.get("strategy_weight") or 1.0
    weights = ctx.get("weights") or TIERS[-1][2]
    env_score = ctx.get("env_score") or 0.0
    energy = ctx.get("energy") or {"label": "normal"}
    label = energy.get("label") or "normal"

    bars = ctx.get("bars") or []
    index = p.get("index") or 0
    pos = day_position(bars, index)
    k, note = space(p.get("type"), pos, label)

    shape = (base_wr - 50) * 0.8 * sw
    confluence = (p.get("confluence") or 1) - 1
    conf_part = confluence * 6
    env_part = weights["env"] * env_score * 40
    vol_part = weights["vol"] * vol_alignment(p.get("type"), label, pos) * 25
    prior_wr = ctx.get("prior_wr")
    prior_samples = ctx.get("prior_samples") or 0
    prior_eff = None
    prior_part = 0.0
    if prior_wr is not None:
        # 按样本量向 50% 收缩，避免「3 例全胜 = 100%」直接拉满强度
        prior_eff = round(50 + (prior_wr - 50) * prior_samples / (prior_samples + 8), 1)
        prior_part = weights["prior"] * (prior_eff - 50) * 1.2

    # 开盘价参照：买点低于开盘价更安全；冲高回落型在开盘上方追买要降权
    price = p.get("price") or 0
    open_price = ctx.get("open_price")
    open_gap = None
    open_bias = 0.0
    if open_price and price:
        open_gap = round((price / open_price - 1) * 100, 2)
        open_bias = max(-1.0, min(1.0, open_gap / 1.5))
        if p.get("type") == "buy":
            open_bias = -open_bias
            if ctx.get("spike_fade") and open_gap > 0.8:
                open_bias -= 0.6
        open_bias = max(-1.4, min(1.4, open_bias))

    strength = _clamp(50 + shape + conf_part + env_part + vol_part + prior_part + open_bias * 2)
    p["strength"] = int(round(strength))
    p["grade"] = "强" if p["strength"] >= 70 else ("中" if p["strength"] >= 50 else "弱")
    p["open_gap"] = open_gap
    p["env_score"] = round(env_score, 3)
    p["energy"] = label
    p["energy_label"] = ENERGY_LABELS.get(label, "常规")
    p["energy_ratio"] = energy.get("ratio")
    p["pos"] = pos
    p["space_k"] = k
    p["space_note"] = note
    p["prior_wr"] = prior_wr
    p["prior_eff"] = prior_eff
    p["prior_samples"] = ctx.get("prior_samples")
    p["strategy_weight"] = round(sw, 2)

    base_target = p.get("target") or base_cfg.get("target") or 0.4
    dyn = round(max(0.25, min(1.8, base_target * k)), 2)
    p["dyn_target"] = dyn
    p["target"] = dyn
    if price:
        factor = 1 + dyn / 100 if p.get("type") == "buy" else 1 - dyn / 100
        p["target_price"] = round(price * factor, 2)
        direction = "反弹至" if p.get("type") == "buy" else "回落至"
        p["expect"] = f"{note}，预期{direction} {p['target_price']:.2f}（{dyn:+.2f}%）"
    else:
        p["target_price"] = None
        p["expect"] = note
    p["weighted"] = True
    return p


def score_all(points, ctx):
    """批量加权并按强度降序，附带原始时间顺序信息"""
    scored = []
    for i, p in enumerate(points):
        item = score_point(p, ctx)
        item["seq"] = i
        scored.append(item)
    scored.sort(key=lambda x: (-(x.get("strength") or 0), x.get("index") or 0))
    return scored


MAX_BUYS = 2
BUY_FLOOR = 50          # 低于该强度不给买点建议
GAP_BASE = 1.5          # 未反弹时，下一次买点需再跌 1.5%
GAP_EXPAND = 2.0        # 放量下跌时还需再跌 2%（还有下跌空间）


def _gap_of(point):
    return GAP_EXPAND if point.get("energy") == "expanding" else GAP_BASE


def make_plan(scored, open_price):
    """把加权后的信号收敛成「可执行的做T计划」

    规则（按用户口径）：
    1. 一天最多两次买点，同一时间只给一个加权结论，不再罗列一堆点位；
    2. 前一个买点之后如果没有反弹，必须再跌 1.5%（放量下跌 2%）才给下一次买点；
    3. 有卖点信号时用最强卖点做 T 出；没有卖点时不硬凑，用多指标共振（策略目标 ×
       量能空间 × 共振数）给出预估 T 出价，只画虚线不给文字建议。
    """
    buys = sorted((p for p in scored if p.get("type") == "buy"), key=lambda x: x.get("index") or 0)
    sells = [p for p in scored if p.get("type") == "sell"]

    accepted, gap_note = [], ""
    for p in buys:
        if len(accepted) >= MAX_BUYS:
            break
        if not accepted:
            if (p.get("strength") or 0) >= BUY_FLOOR:
                accepted.append(p)
            continue
        last = accepted[-1]
        gap = _gap_of(p)
        if p.get("price") and last.get("price") and p["price"] <= last["price"] * (1 - gap / 100):
            p["gap_from_last"] = round((last["price"] / p["price"] - 1) * 100, 2)
            accepted.append(p)
            gap_note = f"较上一个买点再跌 {p['gap_from_last']}%"
    if not accepted and buys:
        accepted.append(max(buys, key=lambda x: (x.get("strength") or 0)))
        gap_note = "没有达标的强买点，只保留最强的一个供参考"

    strong_sells = [p for p in sells if (p.get("strength") or 0) >= BUY_FLOOR]
    sell = max(strong_sells, key=lambda x: x.get("strength") or 0) if strong_sells else None

    lines = []
    if sell:
        lines.append({"price": sell.get("price"), "label": "T出 · 卖点",
                      "time": sell.get("time"), "kind": "sell"})
        mode = "sell"
    else:
        # 没有卖点：不做复杂的卖点搜索，用多指标共振放大策略目标
        for i, p in enumerate(accepted):
            price = p.get("price") or 0
            if not price:
                continue
            conf = max(1, p.get("confluence") or 1)
            pct = round(min(1.8, (p.get("dyn_target") or 0.4) * (1 + 0.06 * (conf - 1))), 2)
            lines.append({"price": round(price * (1 + pct / 100), 2),
                          "label": f"T出{i + 1} 预估（{conf} 指标共振）",
                          "time": p.get("time"), "kind": "estimate"})
        mode = "confluence"

    next_buy = None
    if accepted and len(accepted) < MAX_BUYS:
        last = accepted[-1]
        gap = _gap_of(last)
        next_buy = {"price": round((last.get("price") or 0) * (1 - gap / 100), 2), "gap": gap}

    open_gap = None
    if open_price and scored:
        latest = scored[-1].get("price") or 0
        open_gap = round((latest / open_price - 1) * 100, 2)

    return {
        "buys": accepted,
        "max_buys": MAX_BUYS,
        "buy_count": len(accepted),
        "sell_signal": sell,
        "t_out": lines[0] if lines else None,
        "t_out_lines": lines,
        "t_out_mode": mode,
        "next_buy": next_buy,
        "open": open_price,
        "open_gap": open_gap,
        "gap_note": gap_note,
        "rule": (f"一天最多 {MAX_BUYS} 次买点；未反弹需再跌 {GAP_BASE}%"
                 f"（放量下跌 {GAP_EXPAND}%）才给下一次"),
    }
