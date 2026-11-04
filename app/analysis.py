"""研判引擎：情绪温度、指数技术面分析、AI 复盘文案"""
import json
import urllib.request

from .config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, LLM_TIMEOUT
from .indicators import macd, kdj, rsi, ma, fib_levels, zigzag
from . import eastmoney as em

NOISE_CONCEPTS = {
    "融资融券", "沪股通", "深股通", "机构重仓", "基金重仓", "社保重仓", "QFII重仓", "券商重仓",
    "预盈预增", "预亏预减", "年报预增", "中报预增", "季报预增", "高送转", "破净股", "低价股",
    "百元股", "次新股", "创业板综", "深成500", "上证50_", "上证180_", "沪深300_", "中证500_",
    "MSCI中国", "富时罗素", "标准普尔", "央视50_", "HS300_", "转债标的", "AH股", "创业成份",
    "科创板做市商", "注册制次新股", "昨日涨停", "昨日连板", "昨日触板",
}


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


# ---------------------------------------------------------------- 情绪温度
def compute_sentiment(breadth):
    up, down = breadth.get("up") or 0, breadth.get("down") or 0
    zt, dt = breadth.get("limit_up") or 0, breadth.get("limit_down") or 0
    max_board = breadth.get("max_board") or 0
    lianban = breadth.get("lianban") or 0
    broken_rate = (breadth.get("broken_rate") or 0) / 100.0

    up_ratio = up / max(up + down, 1)
    zt_ratio = zt / max(zt + dt, 1) if (zt + dt) else 0.5
    s1 = clamp((up_ratio - 0.15) / 0.7)
    s2 = clamp((zt_ratio - 0.45) / 0.5)
    s3 = clamp(max_board / 7.0 * 0.6 + lianban / 40.0 * 0.4)
    s4 = clamp(1 - broken_rate)

    score = round((s1 * 0.4 + s2 * 0.3 + s3 * 0.2 + s4 * 0.1) * 100, 1)
    if score >= 80:
        label, tone = "过热", "hot"
    elif score >= 65:
        label, tone = "偏热", "warm"
    elif score >= 45:
        label, tone = "中性", "neutral"
    elif score >= 30:
        label, tone = "偏冷", "cool"
    else:
        label, tone = "冰点", "cold"

    return {
        "score": score,
        "label": label,
        "tone": tone,
        "components": [
            {"name": "涨跌家数", "weight": 40, "raw": round(up_ratio * 100, 1),
             "value": round(s1 * 100, 1), "desc": f"上涨 {up} / 下跌 {down}"},
            {"name": "涨跌停比", "weight": 30, "raw": round(zt_ratio * 100, 1),
             "value": round(s2 * 100, 1), "desc": f"涨停 {zt} / 跌停 {dt}"},
            {"name": "连板强度", "weight": 20, "raw": max_board,
             "value": round(s3 * 100, 1), "desc": f"最高 {max_board} 板 / 连板 {lianban} 只"},
            {"name": "封板质量", "weight": 10, "raw": round(broken_rate * 100, 1),
             "value": round(s4 * 100, 1), "desc": f"炸板率 {round(broken_rate * 100, 1)}%"},
        ],
    }


# ---------------------------------------------------------------- 指数技术研判
def _cross_index(fast_line, slow_line, lookback=4):
    """最近 lookback 根内是否发生交叉，返回 1 金叉 / -1 死叉 / 0 无"""
    n = len(fast_line)
    for i in range(max(n - lookback, 1), n):
        a0, b0 = fast_line[i - 1], slow_line[i - 1]
        a1, b1 = fast_line[i], slow_line[i]
        if None in (a0, b0, a1, b1):
            continue
        if a0 <= b0 and a1 > b1:
            return 1
        if a0 >= b0 and a1 < b1:
            return -1
    return 0


def _divergence(closes, macd_line, lookback=60):
    """MACD 背离：价格创新高但 DIF 未创新高（顶背离）；反之为底背离"""
    n = len(closes)
    if n < lookback:
        return 0
    window = range(n - lookback, n)
    highs = [i for i in window if i > 0 and closes[i] >= closes[i - 1]]
    peaks = [i for i in window if closes[i] == max(closes[max(0, i - 3):min(n, i + 4)])]
    troughs = [i for i in window if closes[i] == min(closes[max(0, i - 3):min(n, i + 4)])]
    if len(peaks) >= 2:
        p1, p2 = peaks[-2], peaks[-1]
        if closes[p2] > closes[p1] and macd_line[p2] < macd_line[p1]:
            return -1
    if len(troughs) >= 2:
        t1, t2 = troughs[-2], troughs[-1]
        if closes[t2] < closes[t1] and macd_line[t2] > macd_line[t1]:
            return 1
    return 0


def analyze_index(name, bars, period: str = "日"):
    unit = period if period in ("日", "周", "月") else "日"
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    if len(closes) < 15:
        raise ValueError("K线数据不足")

    m = macd(closes)
    k = kdj(highs, lows, closes)
    r = rsi(closes)
    ma5, ma10, ma20, ma60 = ma(closes, 5), ma(closes, 10), ma(closes, 20), ma(closes, 60)

    last = len(closes) - 1
    dif, dea, hist = m["dif"][last], m["dea"][last], m["hist"][last]
    kv, dv, jv = k["k"][last], k["d"][last], k["j"][last]
    rv = r[last]
    price = closes[last]

    recent = bars[-120:] if len(bars) >= 120 else bars
    swing_high = max(b["high"] for b in recent)
    swing_low = min(b["low"] for b in recent)
    rising = (ma20[last] or price) >= (ma20[last - 10] or price)
    fib = fib_levels(swing_high, swing_low, rising=rising)
    waves = zigzag(recent, 0.04)

    macd_cross = _cross_index(m["dif"], m["dea"])
    macd_div = _divergence(closes, m["dif"])
    rsi_div = _divergence(closes, [v or 50 for v in r])
    kdj_cross = _cross_index(k["k"], k["d"])

    score = 0.0
    if hist > 0:
        score += 1
    else:
        score -= 1
    if dif > 0:
        score += 0.5
    else:
        score -= 0.5
    score += 0.8 * macd_cross
    score += 0.8 * macd_div
    if price > (ma20[last] or price):
        score += 1
    else:
        score -= 1
    if (ma5[last] or price) > (ma20[last] or price):
        score += 0.5
    else:
        score -= 0.5
    if kdj_cross > 0 and kv < 40:
        score += 0.8
    elif kdj_cross < 0 and kv > 70:
        score -= 0.8
    if kv > 80:
        score -= 0.5
    elif kv < 20:
        score += 0.5
    if rv is not None:
        if rv > 70:
            score -= 0.3
        elif rv < 30:
            score += 0.3
    score += 0.6 * rsi_div

    if score >= 1.5:
        direction, tone = "偏多", "bull"
    elif score <= -1.5:
        direction, tone = "偏空", "bear"
    else:
        direction, tone = "震荡", "neutral"

    supports = sorted([lv["price"] for lv in fib if lv["price"] < price], reverse=True)
    resistances = sorted([lv["price"] for lv in fib if lv["price"] > price])
    support = supports[0] if supports else swing_low
    resistance = resistances[0] if resistances else swing_high

    # MACD 描述
    macd_points = []
    macd_points.append(f"DIF {dif:.2f}、DEA {dea:.2f}，位于零轴{'上' if dif > 0 else '下'}方")
    if macd_cross == 1:
        macd_points.append(f"近 4 个{unit}内出现金叉，柱状图由绿翻红，动能转强")
    elif macd_cross == -1:
        macd_points.append(f"近 4 个{unit}内出现死叉，柱状图由红翻绿，动能减弱")
    trend_word = "放大" if abs(hist) > abs(m["hist"][last - 1]) else "收敛"
    macd_points.append(f"MACD 柱 {hist:.2f}，较上一{unit}{trend_word}")
    if macd_div == -1:
        macd_points.append("价格创阶段新高而 DIF 未同步创新高，存在顶背离迹象")
    elif macd_div == 1:
        macd_points.append("价格创阶段新低而 DIF 未同步创新低，存在底背离迹象")

    kdj_points = [
        f"K {kv:.2f} / D {dv:.2f} / J {jv:.2f}",
        "处于超买区（>80）" if kv > 80 else ("处于超卖区（<20）" if kv < 20 else "处于中性区间"),
    ]
    if kdj_cross == 1:
        kdj_points.append("K 线上穿 D 线，短线金叉")
    elif kdj_cross == -1:
        kdj_points.append("K 线下穿 D 线，短线死叉")
    if jv > 100:
        kdj_points.append("J 值超过 100，短线过热，注意回踩")
    elif jv < 0:
        kdj_points.append("J 值为负，短线超跌，存在修复需求")

    rsi_points = [f"RSI(14) = {rv:.2f}" if rv is not None else "RSI 数据不足"]
    if rv is not None:
        if rv >= 70:
            rsi_points.append("处于强势区（>70），趋势强但需防高位钝化")
        elif rv <= 30:
            rsi_points.append("处于弱势区（<30），关注超跌反弹机会")
        else:
            rsi_points.append("处于中性区（30-70），多空力量均衡")
    if rsi_div == -1:
        rsi_points.append("价格新高而 RSI 走低，顶背离提示")
    elif rsi_div == 1:
        rsi_points.append("价格新低而 RSI 走高，底背离提示")

    wave_points = []
    for pt in waves[-4:]:
        wave_points.append(f"{pt['date']} {'高点' if pt['type'] == 'high' else '低点'} {pt['price']:.2f}")
    if len(waves) >= 3:
        last_leg_up = waves[-1]["type"] == "high"
        wave_points.append(
            "当前处于上行段，若突破前高则确认推动浪延续" if last_leg_up
            else "当前处于回调段，需观察前低支撑是否有效"
        )
    else:
        wave_points.append(f"近 120 根{unit}K波动幅度有限，浪型结构不明显，暂以箱体震荡看待")

    fib_points = [
        f"取近 120 根{unit}K区间 {swing_low:.2f} ~ {swing_high:.2f}，当前 {price:.2f}",
        "关键回撤位：" + "、".join(f"{lv['ratio']:.3f} → {lv['price']:.2f}" for lv in fib if lv["ratio"] in (0.382, 0.5, 0.618)),
    ]
    if support:
        fib_points.append(f"最近支撑 {support:.2f}（{((support - price) / price * 100):.2f}%）")
    if resistance:
        fib_points.append(f"最近压力 {resistance:.2f}（{((resistance - price) / price * 100):+.2f}%）")

    advice = {
        "bull": "趋势偏多，可维持 5-6 成仓位，回踩支撑不破可加仓，跌破支撑减仓。",
        "neutral": "方向未明，建议 3-4 成仓位，等待放量突破压力位或缩量回踩支撑再决策。",
        "bear": "趋势偏空，建议降至 2-3 成仓位，反弹至压力位附近以减仓为主。",
    }[tone]

    risks = []
    if macd_div == -1:
        risks.append("MACD 顶背离，警惕冲高回落")
    if kv > 80 or jv > 100:
        risks.append("KDJ 超买，短线有回踩需求")
    if rv is not None and rv > 75:
        risks.append("RSI 进入强超买区，追高性价比下降")
    if not risks:
        risks.append("若跌破最近支撑位，技术结构将转弱，需及时止损")

    return {
        "name": name,
        "price": price,
        "swing_high": swing_high,
        "swing_low": swing_low,
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "score": round(score, 2),
        "direction": direction,
        "tone": tone,
        "advice": advice,
        "risks": risks,
        "fib": fib,
        "fib_rising": rising,
        "waves": waves,
        "sections": [
            {"key": "macd", "title": "MACD 分析", "points": macd_points},
            {"key": "kdj", "title": "KDJ 分析", "points": kdj_points},
            {"key": "rsi", "title": "RSI 分析", "points": rsi_points},
            {"key": "wave", "title": "波浪理论", "points": wave_points},
            {"key": "fib", "title": "斐波那契", "points": fib_points},
        ],
        "series": {
            "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
            "dif": m["dif"], "dea": m["dea"], "hist": m["hist"],
            "k": k["k"], "d": k["d"], "j": k["j"], "rsi": r,
        },
    }


# ---------------------------------------------------------------- 情绪温度历史与趋势
def _clamp(value, low=3, high=97):
    return max(low, min(high, value))


def sentiment_history(bars, today_score=None):
    """用指数K线还原近 60 个交易日的市场情绪温度"""
    if not bars:
        return []
    closes = [b["close"] for b in bars]
    volumes = [b.get("volume") or 0 for b in bars]
    ma20 = ma(closes, 20)
    points = []
    for i in range(20, len(bars)):
        chg = (closes[i] / closes[i - 1] - 1) * 100 if closes[i - 1] else 0
        avg5 = sum(volumes[i - 4:i + 1]) / 5 if i >= 4 else volumes[i]
        vr = (volumes[i] / avg5) if avg5 else 1
        dev = ((closes[i] / ma20[i] - 1) * 100) if ma20[i] else 0
        score = _clamp(round(50 + chg * 6 + dev * 4 + (vr - 1) * 10, 1))
        points.append({
            "date": bars[i]["date"],
            "score": score,
            "close": round(closes[i], 2),
            "change_pct": round(chg, 2),
            "volume_ratio": round(vr, 2),
        })
    if points and today_score:
        points[-1]["score"] = round(today_score, 1)
        points[-1]["actual"] = True
    return points[-60:]


def sentiment_outlook(bars, today_score=None):
    """根据量价结构给出明确的情绪趋势判断（底部放量/区间震荡等）"""
    if not bars or len(bars) < 25:
        return None
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    volumes = [b.get("volume") or 0 for b in bars]
    ma20 = ma(closes, 20)
    ma60 = ma(closes, 60)
    last = len(bars) - 1
    price = closes[last]
    chg = (price / closes[last - 1] - 1) * 100 if closes[last - 1] else 0
    avg5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0
    vr = (volumes[last] / avg5) if avg5 else 1
    chg5 = (price / closes[-6] - 1) * 100 if len(closes) >= 6 and closes[-6] else 0
    chg10 = (price / closes[-11] - 1) * 100 if len(closes) >= 11 and closes[-11] else 0
    dev20 = ((price / ma20[last] - 1) * 100) if ma20[last] else 0
    high20 = max(highs[-21:-1]) if len(highs) >= 21 else max(highs)
    low20 = min(b["low"] for b in bars[-21:-1]) if len(bars) >= 21 else min(b["low"] for b in bars)
    amplitude = (high20 / low20 - 1) * 100 if low20 else 0

    pattern, tone = "震荡整理", "neutral"
    if price > high20 and vr >= 1.15:
        pattern, tone = "放量突破", "bull"
    elif chg5 <= -4 and chg >= -0.6 and vr >= 1.15:
        pattern, tone = "底部放量企稳", "bull"
    elif chg5 <= -3 and chg >= -0.5 and vr <= 0.9:
        pattern, tone = "缩量止跌", "neutral"
    elif chg10 >= 8 and chg <= 1 and vr >= 1.25:
        pattern, tone = "高位放量滞涨", "bear"
    elif chg <= -1 and vr >= 1.2:
        pattern, tone = "放量下跌", "bear"
    elif amplitude <= 4.5 and abs(dev20) <= 2 and abs(chg5) <= 3:
        pattern, tone = "区间震荡", "neutral"
    elif ma60[last] and price < ma20[last] < ma60[last] and vr < 1.05:
        pattern, tone = "弱势阴跌", "bear"

    detail = (
        f"近5日累计 {chg5:+.2f}%，近10日 {chg10:+.2f}%；今日量能为5日均量的 {vr:.2f} 倍，"
        f"收盘位于20日均线{'上方' if dev20 >= 0 else '下方'} {abs(dev20):.2f}%；"
        f"20日振幅 {amplitude:.2f}%。"
    )
    support = round(ma20[last] or low20, 2)
    resistance = round(max(high20, (ma20[last] or price) * 1.02), 2)
    if pattern == "底部放量企稳":
        detail += " 连续调整后出现放量且跌幅收窄，属于典型的恐慌盘出清信号。"
        expectation = "未来 3-5 个交易日有望筑底反弹，量能持续放大是关键确认条件。"
        advice = "可小仓位试错，跌破今日低点止损，站稳20日均线后加仓。"
    elif pattern == "放量突破":
        detail += " 指数以放量方式突破近20日高点，趋势资金开始占优。"
        expectation = "短线延续强势概率较大，回踩不破突破位则上行趋势成立。"
        advice = "持股为主，回踩突破位可加仓，追高需控制仓位。"
    elif pattern == "缩量止跌":
        detail += " 下跌过程量能萎缩，抛压正在衰减，但多头尚未接力。"
        expectation = "短期进入磨底阶段，需等待放量阳线确认方向。"
        advice = "观望为主，出现放量收复5日均线再介入。"
    elif pattern == "高位放量滞涨":
        detail += " 高位放量而指数滞涨，说明分歧加大、获利盘兑现。"
        expectation = "短线有回踩需求，若跌破5日均线可能进入调整。"
        advice = "降低仓位，避免追高，等待缩量回踩后的二次确认。"
    elif pattern == "放量下跌":
        detail += " 放量下跌意味着主动性抛售，尚未出现止跌信号。"
        expectation = "短期仍有下行惯性，需观察缩量或长下影出现。"
        advice = "控制仓位，反弹减仓，等待底部放量企稳。"
    elif pattern == "区间震荡":
        detail += " 指数围绕20日均线窄幅波动，量能平稳，多空处于平衡状态。"
        expectation = "短期大概率维持区间震荡，方向选择取决于量能是否放大。"
        advice = "高抛低吸为主，突破区间上沿再顺势操作。"
    elif pattern == "弱势阴跌":
        detail += " 指数运行在20日、60日均线下方且量能萎缩，属于弱势阴跌格局。"
        expectation = "短期难有趋势性行情，反弹力度有限。"
        advice = "以防守为主，等待均线走平与量能回升。"
    else:
        detail += " 量价关系中性，方向尚未明朗。"
        expectation = "短期维持震荡，等待量价共振信号。"
        advice = "半仓观望，跟随主线板块轮动。"

    return {
        "pattern": pattern,
        "tone": tone,
        "detail": detail,
        "expectation": expectation,
        "advice": advice,
        "support": support,
        "resistance": resistance,
        "volume_ratio": round(vr, 2),
        "change_5d": round(chg5, 2),
        "change_10d": round(chg10, 2),
        "dev20": round(dev20, 2),
        "score": round(today_score, 1) if today_score else None,
    }


# ---------------------------------------------------------------- 新闻解读
TAG_COMMENT = {
    "货币政策": "流动性预期变化直接影响银行、地产与高股息板块，留意后续操作落地节奏",
    "海外流动性": "美债利率与美元走向牵动外资风险偏好，A股成长风格对海外流动性更敏感",
    "政策": "产业与监管政策决定中期主线，关注受益方向的政策落地力度",
    "财政": "财政发力利好基建、设备更新与消费链条，观察资金到位速度",
    "宏观数据": "数据强弱决定市场对经济复苏的定价，关注数据与预期的偏离方向",
    "中美关系": "外部扰动压制风险偏好，出口链与科技链短期波动可能放大",
    "资金面": "增量资金进出直接影响指数弹性，ETF、两融与北向是观察窗口",
    "行业": "行业消息决定结构性机会，重点看是否形成板块效应与龙头带动",
    "大宗商品": "商品价格传导至资源品与中游制造的成本端，影响毛利预期",
    "地缘政治": "地缘风险推升避险情绪，压制全球风险资产，关注油价与黄金的联动",
    "海外经济": "海外增长与通胀决定全球流动性拐点，影响外资配置节奏",
}
INDUSTRY_HINTS = {
    "半导体": ("半导体", "芯片", "算力", "集成电路", "光刻", "存储"),
    "AI 与算力": ("人工智能", "算力", "大模型", "AI", "服务器", "光模块"),
    "新能源": ("光伏", "储能", "锂电", "新能源", "风电", "电池"),
    "医药": ("医药", "创新药", "医疗", "疫苗", "器械"),
    "地产链": ("地产", "房企", "房贷", "家居", "建材"),
    "券商金融": ("券商", "证券", "银行", "保险", "金融"),
    "消费": ("消费", "白酒", "食品", "旅游", "免税"),
    "军工": ("军工", "国防", "航天", "航空"),
    "资源品": ("黄金", "原油", "铜", "铝", "稀土", "煤炭"),
}

# 新闻对应的产业标的：龙头看业务确定性，小盘看技术弹性
SECTOR_PLAYS = {
    "半导体": {
        "leaders": [
            ("688981", "中芯国际", "晶圆代工龙头，先进制程与产能扩张直接反映国产替代景气"),
            ("002371", "北方华创", "半导体设备平台型龙头，晶圆厂扩产与设备验证是核心驱动"),
        ],
        "small": [("603078", "江化微"), ("300655", "晶瑞电材"), ("300236", "上海新阳")],
    },
    "AI 与算力": {
        "leaders": [
            ("300308", "中际旭创", "光模块全球龙头，800G/1.6T 产品直接承接算力资本开支"),
            ("000977", "浪潮信息", "AI 服务器出货龙头，订单与算力集群建设高度相关"),
        ],
        "small": [("300570", "太辰光"), ("002281", "光迅科技"), ("300394", "天孚通信")],
    },
    "新能源": {
        "leaders": [
            ("300750", "宁德时代", "动力与储能电池全球龙头，电池技术与排产是行业风向标"),
            ("300274", "阳光电源", "逆变器与储能系统龙头，海外大储订单决定业绩弹性"),
        ],
        "small": [("300438", "鹏辉能源"), ("600152", "维科技术"), ("300037", "新宙邦")],
    },
    "医药": {
        "leaders": [
            ("600276", "恒瑞医药", "创新药研发龙头，管线进展与出海授权是核心看点"),
            ("603259", "药明康德", "CXO 龙头，订单与产能利用率反映全球研发需求"),
        ],
        "small": [("300529", "健帆生物"), ("002821", "凯莱英"), ("688185", "康希诺")],
    },
    "地产链": {
        "leaders": [
            ("600048", "保利发展", "央企地产龙头，销售与拿地节奏反映政策效果"),
            ("000002", "万科A", "信用与交付是地产链风险偏好的核心观察点"),
        ],
        "small": [("002271", "东方雨虹"), ("000786", "北新建材"), ("603816", "顾家家居")],
    },
    "券商金融": {
        "leaders": [
            ("600030", "中信证券", "券商龙头，成交额与两融回升直接增厚业绩"),
            ("600036", "招商银行", "零售银行龙头，息差与资产质量决定估值中枢"),
        ],
        "small": [("600927", "永安期货"), ("002945", "华林证券"), ("601995", "中金公司")],
    },
    "消费": {
        "leaders": [
            ("600519", "贵州茅台", "高端白酒龙头，批价与动销是消费景气核心指标"),
            ("000858", "五粮液", "白酒次龙头，渠道库存与回款反映真实需求"),
        ],
        "small": [("603711", "香飘飘"), ("002557", "洽洽食品"), ("603345", "安井食品")],
    },
    "军工": {
        "leaders": [
            ("600760", "中航沈飞", "战斗机整机龙头，订单与交付节奏决定业绩"),
            ("002179", "中航光电", "军用连接器龙头，受益装备放量与国产化"),
        ],
        "small": [("300699", "光威复材"), ("002025", "航天电器"), ("300775", "三角防务")],
    },
    "资源品": {
        "leaders": [
            ("601899", "紫金矿业", "铜金资源龙头，金属价格与产量释放是核心变量"),
            ("600111", "北方稀土", "稀土龙头，配额与价格政策直接影响盈利"),
        ],
        "small": [("000831", "中国稀土"), ("601020", "华钰矿业"), ("600988", "赤峰黄金")],
    },
}


def _technical_snapshot(code, name):
    """小盘弹性筛选：站上 20 日线 + 近 5 日动能为正"""
    try:
        secid = f"{em.market_prefix(code)}.{code}"
        bars = em.fetch_kline(secid, 101, 40)["bars"]
    except Exception:  # noqa: BLE001
        return None
    if len(bars) < 25:
        return None
    closes = [b["close"] for b in bars]
    price = closes[-1]
    ma20 = sum(closes[-20:]) / 20
    chg5 = (price / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
    m = macd(closes)
    macd_state = "MACD 金叉" if m["dif"][-1] > m["dea"][-1] else "MACD 死叉"
    score = (price / ma20 - 1) * 100 + chg5
    return {
        "code": code,
        "name": name,
        "price": round(price, 2),
        "change_pct": bars[-1].get("change_pct"),
        "change_5d": round(chg5, 2),
        "above_ma20": price > ma20,
        "ma_state": "站上 20 日线" if price > ma20 else "仍在 20 日线下",
        "macd": macd_state,
        "score": round(score, 2),
    }


def _news_plays(sectors):
    """新闻落地的两条线：产业龙头（业务维度）+ 小盘弹性（技术维度）"""
    picked = [SECTOR_PLAYS[s] for s in sectors[:2] if s in SECTOR_PLAYS]
    if not picked:
        return {"leaders": [], "small_caps": [], "note": "新闻未明确映射到可跟踪产业，暂不给出个股。"}

    leader_meta = []
    for play in picked:
        for code, name, reason in play["leaders"]:
            if code not in [c for c, _, _ in leader_meta]:
                leader_meta.append((code, name, reason))
    codes = [c for c, _, _ in leader_meta]
    quotes = {}
    try:
        quotes = em.fetch_quotes(codes) if codes else {}
    except Exception:  # noqa: BLE001
        quotes = {}

    leaders = []
    for code, name, reason in leader_meta[:3]:
        quote = quotes.get(code) or {}
        tech = _technical_snapshot(code, name)
        chg5 = tech["change_5d"] if tech else None
        leaders.append({
            "code": code,
            "name": name,
            "price": quote.get("price") or (tech or {}).get("price"),
            "change_pct": quote.get("change_pct") if quote.get("change_pct") is not None else (tech or {}).get("change_pct"),
            "change_5d": chg5,
            "reason": reason,
            "role": "产业龙头",
        })

    candidates = []
    for play in picked:
        for code, name in play["small"]:
            if code not in [c for c, _ in candidates]:
                candidates.append((code, name))
    snaps = [s for s in (_technical_snapshot(c, n) for c, n in candidates) if s]
    strong = [s for s in snaps if s["above_ma20"] and s["change_5d"] > 0]
    strong.sort(key=lambda x: -x["score"])
    small_caps = []
    for s in strong[:2]:
        small_caps.append({
            "code": s["code"],
            "name": s["name"],
            "price": s["price"],
            "change_pct": s["change_pct"],
            "change_5d": s["change_5d"],
            "reason": (f"近 5 日 {s['change_5d']:+.2f}%，{s['ma_state']}，{s['macd']}，"
                       f"在板块催化下弹性更大"),
            "role": "小盘弹性",
        })

    note = "龙头看业务确定性与订单兑现，小盘看技术形态与短期弹性；小盘仓位需更轻、止损更严。"
    if not small_caps:
        note += " 当前候选小盘股均未站上 20 日线或 5 日动能为负，暂无技术面合格的弹性标的。"
    return {"leaders": leaders, "small_caps": small_caps, "note": note}


def _format_play(play, key, label):
    rows = play.get(key) or []
    if not rows:
        return f"{label}：暂无符合条件标的"
    return f"{label}：" + "；".join(
        f"{r['name']}({r['code']}) {r['reason']}" for r in rows[:2])




def analyze_news(items):
    """把新闻聚合成复盘可用的解读段落"""
    items = items or []
    if not items:
        return {
            "summary": "暂未获取到有效新闻，消息面以观望为主。",
            "points": [],
            "industry": [],
            "liquidity": "消息面暂无显著增量信息。",
        }
    domestic = [i for i in items if i.get("scope") == "国内"]
    global_ = [i for i in items if i.get("scope") == "国际"]
    tags = {}
    for item in items:
        for tag in item.get("tags") or []:
            tags[tag] = tags.get(tag, 0) + 1
    top_tags = sorted(tags.items(), key=lambda kv: -kv[1])[:3]

    bull = sum(1 for i in items if i.get("impact") == "利好")
    bear = sum(1 for i in items if i.get("impact") == "利空")
    tone = "偏暖" if bull - bear >= 2 else "偏冷" if bear - bull >= 2 else "中性"

    summary = (
        f"今日共梳理 {len(items)} 条重要消息，国内 {len(domestic)} 条、国际 {len(global_)} 条，"
        f"消息面整体{tone}（利好 {bull} 条 / 利空 {bear} 条）。"
    )
    if top_tags:
        summary += "焦点集中在" + "、".join(f"{name}" for name, _ in top_tags) + "。"

    points = []
    for item in items[:4]:
        comment = TAG_COMMENT.get((item.get("tags") or ["行业"])[0], "关注消息对相关板块的传导")
        points.append({
            "title": item["title"],
            "scope": item.get("scope"),
            "impact": item.get("impact"),
            "comment": f"{comment}；当前判断偏{ {'利好': '积极', '利空': '谨慎'}.get(item.get('impact'), '中性') }。",
        })

    industry = []
    for name, words in INDUSTRY_HINTS.items():
        hits = [i["title"] for i in items if any(w in i["title"] or w in (i.get("summary") or "") for w in words)]
        if hits:
            industry.append({"name": name, "count": len(hits), "reason": hits[0][:40]})
    industry.sort(key=lambda x: -x["count"])

    liquidity = "流动性层面："
    if any(t == "货币政策" for t, _ in top_tags):
        liquidity += "货币政策消息占据主导，短期资金价格与降准降息预期是核心变量；"
    if any(t == "海外流动性" for t, _ in top_tags):
        liquidity += "海外利率与美元波动影响外资流向，需同步跟踪美债收益率；"
    if any(t == "资金面" for t, _ in top_tags):
        liquidity += "ETF、两融与回购等资金行为提供边际增量；"
    if liquidity == "流动性层面：":
        liquidity += "暂无直接冲击流动性的消息，维持存量博弈格局。"

    return {
        "summary": summary,
        "points": points,
        "industry": industry[:3],
        "liquidity": liquidity,
        "tone": "bull" if tone == "偏暖" else "bear" if tone == "偏冷" else "neutral",
    }


def news_item_analysis(item):
    """单条新闻的 AI 深度解读：影响路径、受益/受损方向、持续性与应对"""
    item = item or {}
    title = item.get("title") or ""
    summary = item.get("summary") or ""
    text = f"{title} {summary}"
    impact = item.get("impact") or "中性"
    tags = item.get("tags") or []

    sectors = []
    for name, words in INDUSTRY_HINTS.items():
        if any(w in text for w in words):
            sectors.append(name)

    core = summary.strip()[:160] if summary.strip() else title
    if len(core) < len(title) * 0.7:
        core = title

    if impact == "利好":
        path = "消息面偏正面，风险偏好提升，资金更愿意流向弹性品种，短期有利于相关板块估值修复。"
        play = "关注直接受益方向的龙头与低位补涨，回踩确认后再介入，避免开盘一字追高。"
        tone = "bull"
    elif impact == "利空":
        path = "消息面偏负面，压制风险偏好，资金倾向于防守，高估值与高位品种承压更明显。"
        play = "控制仓位，优先回避直接受损方向，等待情绪宣泄后观察缩量止跌信号。"
        tone = "bear"
    else:
        path = "消息对指数影响有限，更多体现为结构性催化，需结合板块资金流与量能确认。"
        play = "以观察为主，跟踪板块内龙头是否放量走强，再决定是否参与。"
        tone = "neutral"

    level = item.get("level") or 1
    if level >= 4:
        duration = "影响级别较高，通常可延续 3-10 个交易日，需持续跟踪后续落地进展。"
    elif level == 3:
        duration = "影响偏中期，短期以情绪脉冲为主，重点看板块内龙头是否持续走强。"
    else:
        duration = "影响偏短期，多为日内情绪扰动，不宜据此单独做交易决策。"

    if item.get("upcoming"):
        path = "属于尚未落地的前瞻事件，市场会提前交易预期，" + path
        duration = "事件落地前预期博弈主导，落地后需防范“利好兑现”式回落。"

    sector_text = "、".join(sectors[:3]) if sectors else "无明确直接映射，更多是宏观层面的间接影响"
    drivers = [f"核心信息：{core[:90]}", f"影响路径：{path}", f"关联方向：{sector_text}",
               f"周期判断：{duration}", f"应对思路：{play}"]
    if tags:
        drivers.insert(0, "消息分类：" + "、".join(tags[:3]) + f"（{item.get('scope') or '国内'}）")

    plays = {"leaders": [], "small_caps": [], "note": ""}
    try:
        plays = _news_plays(sectors)
    except Exception:  # noqa: BLE001
        pass
    if plays.get("leaders"):
        drivers.append(_format_play(plays, "leaders", "产业龙头（业务维度）"))
    if plays.get("small_caps"):
        drivers.append(_format_play(plays, "small_caps", "小盘弹性（技术维度）"))
    if plays.get("note") and (plays.get("leaders") or plays.get("small_caps")):
        drivers.append(plays["note"])

    return {
        "title": title,
        "impact": impact,
        "tone": tone,
        "scope": item.get("scope"),
        "level": level,
        "upcoming": bool(item.get("upcoming")),
        "sectors": sectors[:3],
        "summary": core,
        "path": path,
        "duration": duration,
        "playbook": play,
        "drivers": drivers,
        "leaders": plays.get("leaders") or [],
        "small_caps": plays.get("small_caps") or [],
        "play_note": plays.get("note") or "",
        "source": item.get("source") or "华尔街见闻",
        "url": item.get("url"),
    }


# ---------------------------------------------------------------- 个股六指标研判
def analyze_stock_indicators(name, bars):
    """六个指标（均线、MACD、KDJ、RSI、量能、支撑压力）综合给出次日判断"""
    if not bars or len(bars) < 30:
        raise ValueError("K线数据不足")
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    volumes = [b.get("volume") or 0 for b in bars]

    m = macd(closes)
    k = kdj(highs, lows, closes)
    r = rsi(closes)
    ma5, ma10, ma20, ma60 = ma(closes, 5), ma(closes, 10), ma(closes, 20), ma(closes, 60)
    last = len(closes) - 1
    price = closes[last]
    dif, dea, hist = m["dif"][last], m["dea"][last], m["hist"][last]
    kv, dv, jv = k["k"][last], k["d"][last], k["j"][last]
    rv = r[last]
    macd_cross = _cross_index(m["dif"], m["dea"])
    kdj_cross = _cross_index(k["k"], k["d"])

    recent = bars[-120:] if len(bars) >= 120 else bars
    swing_high = max(b["high"] for b in recent)
    swing_low = min(b["low"] for b in recent)
    fib = fib_levels(swing_high, swing_low, rising=price >= (swing_high + swing_low) / 2)
    pivot = next((lv["price"] for lv in fib if abs(lv["ratio"] - 0.5) < 1e-6), (swing_high + swing_low) / 2)
    supports = sorted([lv["price"] for lv in fib if lv["price"] < price], reverse=True)
    resistances = sorted([lv["price"] for lv in fib if lv["price"] > price])
    support = supports[0] if supports else swing_low
    resistance = resistances[0] if resistances else swing_high

    votes = []

    def add(key, title, vote, value, desc):
        votes.append({"key": key, "title": title, "vote": vote, "value": value, "desc": desc})

    if price > (ma5[last] or price) > (ma10[last] or price) > (ma20[last] or price):
        add("ma", "均线系统", 1, f"MA5 {ma5[last]:.2f} / MA20 {ma20[last]:.2f}",
            "多头排列，价格站上短中期均线，趋势结构完好")
    elif price < (ma5[last] or price) and price < (ma20[last] or price):
        add("ma", "均线系统", -1, f"MA5 {ma5[last]:.2f} / MA20 {ma20[last]:.2f}",
            "价格跌破短中期均线，均线形成压制")
    else:
        add("ma", "均线系统", 0, f"MA5 {ma5[last]:.2f} / MA20 {ma20[last]:.2f}",
            "均线缠绕，方向待选择")

    if macd_cross == 1 or (hist > 0 and dif > dea):
        add("macd", "MACD", 1, f"DIF {dif:.2f} / DEA {dea:.2f} / 柱 {hist:.2f}",
            "红柱延续" if hist > 0 else "刚形成金叉，动能转强")
    elif macd_cross == -1 or (hist < 0 and dif < dea):
        add("macd", "MACD", -1, f"DIF {dif:.2f} / DEA {dea:.2f} / 柱 {hist:.2f}",
            "绿柱延续" if hist < 0 else "刚形成死叉，动能转弱")
    else:
        add("macd", "MACD", 0, f"DIF {dif:.2f} / DEA {dea:.2f}", "多空动能接近平衡")

    if jv > 100 or kv > 90:
        add("kdj", "KDJ", -1, f"K {kv:.1f} / D {dv:.1f} / J {jv:.1f}", "指标进入严重超买区，短线回踩概率上升")
    elif kdj_cross == 1 and kv < 80:
        add("kdj", "KDJ", 1, f"K {kv:.1f} / D {dv:.1f} / J {jv:.1f}", "K 线上穿 D 线，短线金叉")
    elif kdj_cross == -1 and kv > 70:
        add("kdj", "KDJ", -1, f"K {kv:.1f} / D {dv:.1f} / J {jv:.1f}", "高位死叉，短线走弱")
    elif kv > 80:
        add("kdj", "KDJ", 0, f"K {kv:.1f} / D {dv:.1f} / J {jv:.1f}", "高位钝化，趋势仍强但不宜追高")
    else:
        add("kdj", "KDJ", 0, f"K {kv:.1f} / D {dv:.1f} / J {jv:.1f}", "指标处于中性区间")

    if rv is not None and 50 <= rv <= 70:
        add("rsi", "RSI", 1, f"RSI {rv:.1f}", "强势但未超买，多头仍有余力")
    elif rv is not None and rv < 50:
        add("rsi", "RSI", -1, f"RSI {rv:.1f}", "弱势区间，反弹力度有限")
    else:
        add("rsi", "RSI", 0, f"RSI {rv:.1f}" if rv is not None else "RSI -", "进入超买区，注意高位钝化")

    vol_ma5 = sum(volumes[-5:]) / 5 if volumes else 0
    today_vol = volumes[-1] if volumes else 0
    if today_vol and vol_ma5:
        ratio = today_vol / vol_ma5
        if closes[last] >= closes[last - 1] and ratio >= 1.1:
            add("volume", "量能", 1, f"量比 5 日均量 {ratio:.2f}", "放量上涨，资金承接积极")
        elif closes[last] < closes[last - 1] and ratio >= 1.1:
            add("volume", "量能", -1, f"量比 5 日均量 {ratio:.2f}", "放量下跌，抛压较重")
        else:
            add("volume", "量能", 0, f"量比 5 日均量 {ratio:.2f}", "量能温和，观望情绪占优")
    else:
        add("volume", "量能", 0, "量能 -", "成交量数据不足")

    if price >= pivot and price > support:
        add("level", "支撑压力", 1, f"支撑 {support:.2f} / 压力 {resistance:.2f}",
            "价格站上斐波那契 0.5 位，结构偏强")
    elif price <= support * 1.02:
        add("level", "支撑压力", 0, f"支撑 {support:.2f} / 压力 {resistance:.2f}",
            "贴近支撑位，观察能否止跌企稳")
    else:
        add("level", "支撑压力", -1, f"支撑 {support:.2f} / 压力 {resistance:.2f}",
            "跌破关键回撤位，下方支撑需重新确认")

    score = sum(v["vote"] for v in votes)
    if score >= 2:
        direction, tone = "偏多", "bull"
    elif score <= -2:
        direction, tone = "偏空", "bear"
    else:
        direction, tone = "震荡", "neutral"

    bull_count = sum(1 for v in votes if v["vote"] > 0)
    bear_count = sum(1 for v in votes if v["vote"] < 0)
    expect = {
        "bull": f"六个指标中 {bull_count} 项偏多、{bear_count} 项偏空，次日大概率延续偏强走势，"
                f"上方关注 {resistance:.2f} 压力，站稳后有望打开空间。",
        "bear": f"六个指标中 {bear_count} 项偏空、{bull_count} 项偏多，次日偏向弱势震荡，"
                f"下方关注 {support:.2f} 支撑，失守则需减仓。",
        "neutral": f"六个指标多空分歧（{bull_count} 多 / {bear_count} 空），次日大概率维持震荡，"
                   f"区间参考 {support:.2f} ~ {resistance:.2f}。",
    }[tone]
    action = {
        "bull": "持股待涨，回踩不破支撑可加仓，冲高至压力位附近不宜追高。",
        "bear": "控制仓位，反弹至压力位减仓，跌破支撑止损。",
        "neutral": "半仓观望，等待放量突破压力或缩量回踩支撑再决策。",
    }[tone]

    risks = []
    if jv > 100:
        risks.append("KDJ 的 J 值超过 100，短线存在回踩需求")
    if rv is not None and rv > 75:
        risks.append("RSI 进入超买区，追高性价比下降")
    if hist < 0 and dif < dea:
        risks.append("MACD 绿柱未收敛，动能仍在走弱")
    if not risks:
        risks.append("若放量跌破支撑位，六指标共振转空，需及时止损")

    return {
        "name": name,
        "price": round(price, 2),
        "score": score,
        "direction": direction,
        "tone": tone,
        "votes": votes,
        "levels": {"support": round(support, 2), "resistance": round(resistance, 2),
                   "pivot": round(pivot, 2), "swing_high": round(swing_high, 2),
                   "swing_low": round(swing_low, 2)},
        "tomorrow": {"bias": direction, "expectation": expect, "action": action,
                     "bull_count": bull_count, "bear_count": bear_count},
        "risks": risks,
    }


# ---------------------------------------------------------------- AI 复盘
def _fmt_amount(value):
    if value is None:
        return "-"
    if value >= 1e12:
        return f"{value / 1e12:.2f} 万亿"
    return f"{value / 1e8:.0f} 亿"


def build_ai_review(data):
    indices = data.get("indices") or []
    breadth = data.get("breadth") or {}
    sentiment = data.get("sentiment") or {}
    sectors = data.get("sectors") or {}
    ladder = data.get("ladder") or {}
    limitup = data.get("limitup") or {}
    prev_amount = data.get("prev_amount") or 0

    up, down = breadth.get("up") or 0, breadth.get("down") or 0
    zt = breadth.get("limit_up") or 0
    dt = breadth.get("limit_down") or 0
    broken = breadth.get("broken") or 0
    broken_rate = breadth.get("broken_rate") or 0
    max_board = breadth.get("max_board") or 0
    lianban = breadth.get("lianban") or 0

    total_amount = sum((i.get("amount") or 0) for i in indices[:2])
    amount_change = None
    if prev_amount:
        amount_change = (total_amount - prev_amount) / prev_amount * 100

    leader = max(indices, key=lambda i: i.get("change_pct") or -999) if indices else None
    laggard = min(indices, key=lambda i: i.get("change_pct") or 999) if indices else None

    if up / max(up + down, 1) >= 0.7:
        market_tone = "普涨格局"
    elif up / max(up + down, 1) <= 0.3:
        market_tone = "普跌格局"
    else:
        market_tone = "涨跌分化"

    summary = (
        f"今日{market_tone}，{leader['name'] if leader else '指数'}"
        f"{'上涨' if (leader or {}).get('change_pct', 0) >= 0 else '下跌'} "
        f"{abs((leader or {}).get('change_pct') or 0):.2f}%，"
        f"全市场 {up} 家上涨 / {down} 家下跌，涨停 {zt} 家、跌停 {dt} 家，"
        f"情绪温度 {sentiment.get('score', '-')}（{sentiment.get('label', '-')}）。"
    )

    sections = []

    index_body = "；".join(
        f"{i['name']} {i['price']:.2f}（{i['change_pct']:+.2f}%）成交 {_fmt_amount(i.get('amount'))}"
        for i in indices
    )
    if amount_change is not None:
        index_body += f"。沪深两市合计成交 {_fmt_amount(total_amount)}，较前一交易日{'放大' if amount_change > 0 else '缩小'} {abs(amount_change):.1f}%。"
    sections.append({"title": "市场总览", "body": summary + index_body})

    strongest = sorted(sectors.get("concept") or [], key=lambda s: s["change_pct"], reverse=True)[:3]
    hot_lines = []
    for s in strongest:
        hot_lines.append(f"{s['name']} {s['change_pct']:+.2f}%（领涨 {s['leader']} {s['leader_pct']:+.2f}%）")
    industry_top = sorted(sectors.get("industry") or [], key=lambda s: s["change_pct"], reverse=True)[:3]
    industry_line = "、".join(f"{s['name']} {s['change_pct']:+.2f}%" for s in industry_top)
    board_body = f"行业主线：{industry_line or '暂无'}。概念热点：{'；'.join(hot_lines) or '暂无'}。"
    if industry_top and industry_top[0].get("up") is not None:
        board_body += f" 资金主要聚焦 {industry_top[0]['name']}，板块内 {industry_top[0]['up']} 家上涨、{industry_top[0]['down']} 家下跌。"
    sections.append({"title": "板块主线", "body": board_body})

    cycle_phase = {
        "hot": "情绪高位（发酵/高潮期）",
        "warm": "情绪回暖（主升期）",
        "neutral": "情绪分歧（混沌期）",
        "cool": "情绪回落（退潮期）",
        "cold": "情绪冰点（冰点期）",
    }.get(sentiment.get("tone"), "情绪中性")
    ladder_body = (
        f"情绪温度 {sentiment.get('score', '-')} 分，处于{cycle_phase}。"
        f"涨停 {zt} 家、连板 {lianban} 家、最高 {max_board} 板，"
        f"炸板 {broken} 家（炸板率 {broken_rate}%），晋级率 {(ladder.get('stats') or {}).get('promote_rate', '-')}%。"
    )
    if limitup.get("stats", {}).get("twenty_cm"):
        ladder_body += f" 20cm 涨停 {limitup['stats']['twenty_cm']} 家，赚钱效应集中在高弹性品种。"
    sections.append({"title": "情绪研判", "body": ladder_body})

    # 资金与量能
    amount_body = f"沪深两市合计成交 {_fmt_amount(total_amount)}"
    if amount_change is not None:
        amount_body += f"，较前一交易日{'放大' if amount_change > 0 else '缩小'} {abs(amount_change):.1f}%"
        if amount_change > 20 and up / max(up + down, 1) > 0.6:
            amount_body += "，属于放量普涨，增量资金入场信号明确"
        elif amount_change < -15:
            amount_body += "，缩量明显，反弹持续性需观察后续补量"
    amount_body += "。"
    top_flow = [s for s in (sectors.get("concept") or []) if (s.get("main_flow") or 0) > 0][:3]
    if top_flow:
        amount_body += "资金净流入靠前的概念：" + "、".join(
            f"{s['name']}（{_fmt_amount(s.get('main_flow'))}）" for s in top_flow) + "。"
    amount_body += f" 涨停 {zt} 家中 20cm 品种 {limitup.get('stats', {}).get('twenty_cm', 0)} 家，"
    amount_body += f"封板率 {limitup.get('stats', {}).get('seal_rate', '-')}%，情绪承接{'良好' if (limitup.get('stats', {}).get('seal_rate') or 0) >= 70 else '一般'}。"
    sections.append({"title": "资金与量能", "body": amount_body})

    # 新闻解读
    news_view = analyze_news(data.get("news") or [])
    news_body = news_view["summary"] + " " + news_view["liquidity"]
    if news_view["industry"]:
        news_body += " 对相关板块的映射：" + "、".join(
            f"{n['name']}（{n['count']} 条）" for n in news_view["industry"]) + "。"
    sections.append({"title": "新闻解读", "body": news_body})

    # 明日预期
    tech = data.get("tech") or {}
    tech_dir = tech.get("direction") or "震荡"
    tech_score = tech.get("score") or 0
    support, resistance = tech.get("support"), tech.get("resistance")
    bias = tech_dir
    if sentiment.get("tone") == "hot" and tech_dir == "偏多":
        bias = "偏多但需防冲高回落"
    elif sentiment.get("tone") in ("cold", "cool") and tech_dir == "偏空":
        bias = "偏空，等待情绪修复"
    scenarios = []
    base_prob = 45 if tech_dir == "震荡" else 60
    scenarios.append({
        "name": "延续" + ("上行" if tech_dir != "偏空" else "调整"),
        "prob": base_prob,
        "trigger": f"明日不破 {support:.2f}" if support else "开盘承接稳定",
        "plan": "维持仓位，主线板块回踩可低吸" if tech_dir != "偏空" else "反弹减仓，控制仓位",
    })
    scenarios.append({
        "name": "震荡整理",
        "prob": 100 - base_prob - 15 if tech_dir != "震荡" else 30,
        "trigger": f"指数在 {support:.2f} ~ {resistance:.2f} 区间内反复" if support and resistance else "量能萎缩",
        "plan": "以个股结构性机会为主，避免追高",
    })
    scenarios.append({
        "name": "反向" + ("回落" if tech_dir != "偏空" else "反抽"),
        "prob": 15,
        "trigger": f"有效跌破 {support:.2f}" if support else "外围利空扰动",
        "plan": "减仓避险，等待企稳信号",
    })
    tomorrow_body = (
        f"技术面综合评分 {tech_score}，方向判断为{tech_dir}；"
        f"情绪温度 {sentiment.get('score', '-')}（{sentiment.get('label', '-')}），"
        f"消息面{ {'bull': '偏暖', 'bear': '偏冷'}.get(news_view.get('tone'), '中性') }。"
    )
    if support and resistance:
        tomorrow_body += f"上方压力 {resistance:.2f}，下方支撑 {support:.2f}，明日重点观察这两个位置的得失。"
    tomorrow_body += "操作上：" + (tech.get("advice") or "控制仓位，跟随主线。")
    sections.append({"title": "明日预期", "body": tomorrow_body})

    risks = []
    if broken_rate >= 25:
        risks.append(f"炸板率 {broken_rate}% 偏高，封板质量一般，追高需谨慎")
    if dt >= 20:
        risks.append(f"跌停 {dt} 家，局部亏钱效应明显，注意补跌风险")
    if amount_change is not None and amount_change < -8 and (leader or {}).get("change_pct", 0) > 0:
        risks.append("指数上涨但成交额明显萎缩，量价背离需警惕")
    if max_board >= 5 and not any(it.get("board") == max_board - 1 for it in (limitup.get("stocks") or [])):
        risks.append(f"{max_board} 板高度与前排梯队断层，高位股分歧加大")
    if sentiment.get("score", 0) >= 80:
        risks.append("情绪处于过热区间，谨防冲高回落与高位股兑现")
    if not risks:
        risks.append("暂无显著风险信号，仍需关注外围市场与消息面变化")
    sections.append({"title": "风险提示", "body": "；".join(risks) + "。"})

    review = {
        "summary": summary,
        "sections": sections,
        "tomorrow": {
            "bias": bias,
            "direction": tech_dir,
            "score": tech_score,
            "support": support,
            "resistance": resistance,
            "support_pct": round((support - (tech.get("price") or 0)) / tech.get("price") * 100, 2) if support and tech.get("price") else None,
            "resistance_pct": round((resistance - (tech.get("price") or 0)) / tech.get("price") * 100, 2) if resistance and tech.get("price") else None,
            "scenarios": scenarios,
            "watch": ["支撑/压力位的得失", "成交额能否延续放大", "消息面与外围市场扰动"],
        },
        "news_view": news_view,
        "engine": "rule",
    }
    if LLM_API_KEY:
        polished = _llm_review(data, review)
        if polished:
            review.update(polished)
            review["engine"] = "llm"
    return review


def _llm_review(data, draft):
    """可选：调用大模型润色（OpenAI 兼容接口），失败时回退规则文案"""
    context = {
        "date": data.get("date"),
        "indices": [{k: i.get(k) for k in ("name", "price", "change_pct", "amount")} for i in data.get("indices", [])],
        "breadth": data.get("breadth"),
        "sentiment": {k: data.get("sentiment", {}).get(k) for k in ("score", "label")},
        "top_industry": (data.get("sectors", {}).get("industry") or [])[:5],
        "top_concept": (data.get("sectors", {}).get("concept") or [])[:5],
        "ladder": data.get("ladder", {}).get("levels", [])[:3],
        "tech": {k: (data.get("tech") or {}).get(k) for k in ("direction", "score", "support", "resistance")},
        "news": [{"title": i.get("title"), "scope": i.get("scope"), "impact": i.get("impact"), "tags": i.get("tags")}
                 for i in (data.get("news") or [])[:8]],
        "news_view": (draft.get("news_view") or {}).get("summary"),
        "tomorrow": draft.get("tomorrow"),
    }
    prompt = (
        "你是A股复盘分析师。请基于以下结构化数据，输出JSON："
        '{"summary": "今日行情一句话总览",'
        '"sections": [{"title":"市场总览|板块主线|情绪研判|资金与量能|新闻解读|明日预期|风险提示","body":"2-4句"}],'
        '"tomorrow": {"bias":"偏多/震荡/偏空","scenarios":[{"name":"","prob":0,"trigger":"","plan":""}],"watch":[""]}}。'
        "要求：复盘要具体（引用涨跌幅、成交额、涨停家数、板块名称）；"
        "明日预期必须结合 MACD/KDJ/RSI/支撑压力与技术评分，并分析新闻对行业和流动性的影响；"
        "只用给定数据、不编造新闻、不做个股推荐、语气客观。数据："
        + json.dumps(context, ensure_ascii=False)
    )
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LLM_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(content)
        result = {}
        if parsed.get("summary") and parsed.get("sections"):
            result = {"summary": parsed["summary"], "sections": parsed["sections"]}
        if parsed.get("tomorrow"):
            merged = draft.get("tomorrow") or {}
            merged.update(parsed["tomorrow"])
            result["tomorrow"] = merged
        return result or None
    except Exception:  # noqa: BLE001
        return None
    return None
