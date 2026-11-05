"""研判引擎：情绪温度、指数技术面分析、AI 复盘文案"""
import json
import re
import urllib.request

from .config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, LLM_TIMEOUT
from .indicators import macd, kdj, rsi, ma, bollinger, fib_levels, zigzag
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
        label, tone = "沸点", "hot"
    elif score >= 65:
        label, tone = "火热", "warm"
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


# 产业链拆解：上游 -> 中游 -> 下游 + 核心传导逻辑与跟踪指标
CHAIN_MAP = {
    "半导体": {
        "up": ("半导体设备", "光刻胶/电子特气", "大硅片"),
        "mid": ("晶圆制造", "先进封装", "存储芯片"),
        "down": ("消费电子", "汽车电子", "AI 服务器"),
        "logic": "资本开支扩张时，设备与材料最先拿到订单；随后传导到晶圆厂产能利用率与代工报价；最终由终端需求（手机 / 汽车 / AI）决定景气高度与持续性。",
        "watch": "晶圆厂招标与中标公告、设备验证与验收进度、月度产能利用率与代工价格",
    },
    "AI 与算力": {
        "up": ("光芯片", "PCB / 铜箔", "电源与散热"),
        "mid": ("光模块", "AI 服务器", "交换机"),
        "down": ("云厂商资本开支", "IDC 与液冷", "AI 应用"),
        "logic": "云厂商资本开支决定算力订单总量，光模块与服务器最先反映在手订单，再向上游光芯片、PCB 传导，最后落到应用端变现。",
        "watch": "北美云厂商资本开支指引、800G / 1.6T 出货节奏、光模块月度出口数据",
    },
    "新能源": {
        "up": ("锂矿 / 镍钴", "正负极材料", "隔膜电解液"),
        "mid": ("电芯制造", "逆变器", "储能系统"),
        "down": ("整车", "储能电站", "电网消纳"),
        "logic": "原材料价格决定中游成本，电芯排产与产能利用率决定盈利弹性，终端装机与海外订单决定需求天花板。",
        "watch": "碳酸锂价格、月度装机与排产数据、海外大储订单与并网进度",
    },
    "医药": {
        "up": ("原料药", "CXO", "设备耗材"),
        "mid": ("创新药", "疫苗", "仿制药"),
        "down": ("医院终端", "医保支付", "出海渠道"),
        "logic": "研发与审批端决定管线价值，医保与集采决定价格与放量节奏，出海授权提供第二增长曲线。",
        "watch": "临床试验与审批节点、集采与医保谈判结果、海外授权首付款公告",
    },
    "地产链": {
        "up": ("土地成交", "水泥建材", "玻璃"),
        "mid": ("房企开发", "施工建设", "竣工交付"),
        "down": ("家居", "家电", "物业管理"),
        "logic": "政策先改善销售与融资预期，房企拿地与开工决定中游需求，竣工交付后才会传导到家居与家电。",
        "watch": "30 城商品房成交面积、房企融资与拿地数据、竣工面积与交付节奏",
    },
    "券商金融": {
        "up": ("交易通道", "两融业务", "资管产品"),
        "mid": ("券商", "银行", "保险"),
        "down": ("实体融资", "居民理财", "市场成交额"),
        "logic": "成交额与两融余额直接决定券商业绩弹性，利率与信贷决定银行息差，风险偏好决定保险与资管的产品销售。",
        "watch": "两市日成交额、两融余额、信贷与社融数据、政策对资本市场表述",
    },
    "消费": {
        "up": ("农产品", "包装材料", "物流仓储"),
        "mid": ("白酒", "食品饮料", "免税"),
        "down": ("渠道零售", "餐饮旅游", "出行"),
        "logic": "居民收入与消费信心决定需求，渠道库存与批价反映真实动销，成本端变化影响毛利与提价空间。",
        "watch": "高端白酒批价与动销、社零与CPI 食品分项、节假日出行与免税销售",
    },
    "军工": {
        "up": ("特种材料", "军用元器件", "芯片"),
        "mid": ("分系统", "发动机", "雷达"),
        "down": ("整机总装", "维修保障", "出口"),
        "logic": "订单与预付款决定排产，上游材料与元器件最先反映放量，整机交付节奏决定收入确认。",
        "watch": "年度订单与预付款公告、批产型号交付节奏、军工集团资本开支",
    },
    "资源品": {
        "up": ("矿产开采", "冶炼", "采选设备"),
        "mid": ("加工材", "化工品", "合金"),
        "down": ("制造业", "电力", "建筑基建"),
        "logic": "商品价格直接决定上游利润，加工费与库存决定中游盈利，下游需求强弱决定价格能否持续。",
        "watch": "商品期货价格与库存、加工费（TC/RC）、下游开工率与进口数据",
    },
}

# 无明确行业映射时，用宏观传导链兜底
MACRO_CHAIN = {
    "up": ("政策 / 事件本身", "流动性预期", "风险偏好"),
    "mid": ("权重蓝筹", "成长赛道", "主题概念"),
    "down": ("板块轮动", "个股弹性", "资金承接"),
    "logic": "宏观与政策类消息先改变风险偏好与流动性预期，再经由权重与成长风格传导到具体板块，最后体现为个股弹性与资金承接力度。",
    "watch": "政策细则与落地节奏、北向与两融资金变化、指数成交额能否同步放大",
}

TAG_ANALOGY = {
    "货币政策": "历史上货币宽松落地后，券商、地产与高股息往往在落地后 5 个交易日内相对占优；但若信贷与地产销售未同步改善，行情多在 2-3 周内回归基本面。",
    "海外流动性": "海外利率回落阶段，A股成长风格（半导体、算力）弹性通常强于价值；若美元重新走强，外资偏好的核心资产会先承压。",
    "政策": "产业政策从发布到订单落地通常需要 1-2 个季度，行情节奏多为“发布日脉冲—细则期发酵—业绩期分化”。",
    "财政": "财政发力对基建、设备更新与消费的传导存在时滞，市场通常先交易预期，再等待资金到位与项目开工数据验证。",
    "宏观数据": "宏观数据超预期时，顺周期与金融先反应；不及预期时，资金往往回流防御性板块与高股息。",
    "中美关系": "外部扰动多带来 1-3 个交易日的风险偏好冲击，出口链与科技链波动放大，但很少单独改变中期趋势。",
    "资金面": "增量资金入场往往先抬升指数与权重，再扩散到题材；ETF 与两融的方向性变化比单日数据更有参考意义。",
    "大宗商品": "商品价格上行初期利好资源品，中后期转为中游成本压力；价格见顶回落时，中游制造的毛利修复弹性更大。",
    "地缘政治": "地缘冲突升温时，黄金、原油与军工短线占优，风险资产整体承压，持续性取决于冲突是否外溢到供应链。",
    "海外经济": "海外衰退预期升温阶段，出口链承压而内需与政策受益方向相对占优，需同步跟踪美联储表态。",
}


def _news_facts(text, limit=4):
    """从正文中拆出关键事实句，作为分析的事实基础"""
    parts = re.split(r"[。；!?！？\n]", text or "")
    facts = []
    for part in parts:
        piece = part.strip(" ，,、")
        if len(piece) >= 10 and piece not in facts:
            facts.append(piece)
    return facts[:limit]


def _chain_for(sectors):
    for name in sectors or []:
        data = CHAIN_MAP.get(name)
        if data:
            return name, data
    return "", MACRO_CHAIN


def news_item_analysis(item):
    """单条新闻的 AI 深度解读：产业传导链、受益结构、节奏与跟踪点"""
    item = item or {}
    title = (item.get("title") or "").strip()
    summary = (item.get("summary") or "").strip()
    text = f"{title} {summary}"
    impact = item.get("impact") or "中性"
    tags = item.get("tags") or []
    level = int(item.get("level") or 1)
    upcoming = bool(item.get("upcoming"))
    scope = item.get("scope") or "国内"

    # 按关键词命中次数排序，避免“算力”类新闻被半导体优先抢占
    scored = []
    for name, words in INDUSTRY_HINTS.items():
        hits = sum(text.count(w) for w in words)
        if hits:
            scored.append((hits, name))
    scored.sort(key=lambda row: -row[0])
    sectors = [name for _, name in scored]

    core = summary if summary and len(summary) >= len(title) * 0.7 else title
    core = core.rstrip("。；;，,、 ")[:200]

    chain_name, chain = _chain_for(sectors)
    facts = _news_facts(summary or title)

    intensity = min(100, 40 + level * 10 + (12 if impact != "中性" else 0))
    persistence = min(100, 30 + level * 12 + (8 if upcoming else 0))
    certainty = min(100, 35 + level * 10 + (10 if scope == "国内" else 0))

    if impact == "利好":
        tone = "bull"
        path = "消息面偏正面，风险偏好抬升，资金更愿意流向弹性品种，短期有利于相关板块的估值修复与订单预期上修。"
        play = "优先看直接受益环节的龙头，等回踩确认后再介入；开盘一字不宜追高，仓位与止损同步设定。"
        benefit = (f"直接受益：{chain['up'][0]}、{chain['mid'][0]}；"
                   f"间接受益：{chain['down'][0]} 等下游应用与配套环节。")
        risks = [
            "预期差风险：市场往往提前交易预期，落地力度若不及预期，容易出现冲高回落。",
            "拥挤度风险：板块短期涨幅过大、成交额占比过高时，获利了结压力会显著放大波动。",
            "兑现时滞：从政策 / 事件到订单、再到报表利润通常滞后 1-2 个季度，业绩验证前不宜给过高估值。",
        ]
    elif impact == "利空":
        tone = "bear"
        path = "消息面偏负面，压制风险偏好，资金倾向防守，高估值与高位品种的承压幅度通常大于低估值品种。"
        play = "先控制仓位，回避直接受损方向；等待情绪宣泄后观察缩量止跌与龙头是否率先修复。"
        benefit = (f"直接承压：{chain['up'][0]}、{chain['mid'][0]}；"
                   f"情绪外溢：{chain['down'][0]} 等下游需求预期同步走弱。")
        risks = [
            "情绪扩散：若相关板块权重较高，可能拖累指数并引发被动减仓。",
            "二次冲击：后续数据或政策若继续恶化，压制时间会明显拉长。",
            "流动性负反馈：下跌中若伴随放量，需警惕融资盘被动平仓带来的加速下行。",
        ]
    else:
        tone = "neutral"
        path = "消息对指数影响有限，更多体现为结构性催化，需要结合板块资金流与量能确认方向。"
        play = "以观察为主，跟踪板块内龙头是否放量走强，出现合力后再决定是否参与。"
        benefit = (f"结构性影响：{chain['up'][0]} 与 {chain['mid'][0]} 更早反映预期，"
                   f"{chain['down'][0]} 需要实际需求验证才具备持续性。")
        risks = [
            "方向不明：中性消息若得不到资金认可，可能只形成一日游行情。",
            "结构分化：同一消息对不同环节影响不同，需区分直接受益与纯情绪炒作。",
            "信息衰减：快讯类消息生命周期短，需等待后续权威口径与数据确认。",
        ]

    if level >= 4:
        duration = "影响级别较高，通常可延续 3-10 个交易日，需持续跟踪后续落地进展与细则。"
    elif level == 3:
        duration = "影响偏中期，短期以情绪脉冲为主，重点看板块内龙头能否持续走强。"
    else:
        duration = "影响偏短期，多为日内情绪扰动，不宜据此单独做交易决策。"

    if upcoming:
        path = "属于尚未落地的前瞻事件，市场会提前交易预期，" + path
        duration = "事件落地前预期博弈主导，落地后需防范“利好兑现”式回落。"
        risks.append("预期兑现风险：事件真正落地时容易“利好出尽”，需提前设定退出条件。")

    if impact == "利空":
        impact_path = [
            {"stage": "事件", "text": core[:80]},
            {"stage": "预期", "text": f"风险偏好下降，{chain['up'][0]} 与 {chain['mid'][0]} 的估值先被压缩"},
            {"stage": "基本面", "text": "若影响持续，订单与排产预期下调，滞后 1-2 个季度体现在报表"},
            {"stage": "股价", "text": "短期急跌后往往有反抽，反弹高度取决于消息是否被证伪"},
        ]
    else:
        impact_path = [
            {"stage": "事件", "text": core[:80]},
            {"stage": "预期", "text": f"资金先交易预期，{chain['up'][0]} 与 {chain['mid'][0]} 的弹性最大"},
            {"stage": "基本面", "text": "订单与排产逐步验证，1-2 个季度后体现在收入与毛利"},
            {"stage": "股价", "text": "先急涨后分化，能兑现业绩的龙头走趋势，纯概念品种大概率回落"},
        ]

    watch = [chain["watch"],
             "跟踪板块成交额与龙头是否持续放量，确认资金是否形成合力",
             "跟踪同类消息的出现密度，单条快讯难以构成中期主线"]
    if upcoming:
        watch.insert(0, "锁定事件落地时间点，提前一周观察资金是否提前抢跑")

    sector_text = "、".join(sectors[:3]) if sectors else "无明确直接映射，更多是宏观层面的间接影响"
    drivers = [
        f"核心事实：{facts[0] if facts else core[:90]}",
        f"产业归属：{chain_name + '链' if chain_name else '宏观 / 主题层面'}（{sector_text}）",
        f"产业逻辑：{chain['logic']}",
        f"受益结构：{benefit}",
        f"周期判断：{duration}",
        f"应对思路：{play}",
    ]
    if tags:
        drivers.insert(0, "消息分类：" + "、".join(tags[:3]) + f"（{scope}）")

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

    primary_tag = tags[0] if tags else ""
    summary_text = (
        f"事件要点：{core[:130]}。"
        f"属性判断：{scope}{'前瞻' if upcoming else '已发生'}的"
        f"{impact if impact != '中性' else '中性信息'}，影响级别 L{level}；"
        f"强度 {intensity}／持续性 {persistence}／确定性 {certainty}（满分 100）。"
        f"主线性：{TAG_COMMENT.get(primary_tag, '关注消息对相关板块的传导力度')}。"
    )

    return {
        "title": title,
        "impact": impact,
        "tone": tone,
        "scope": scope,
        "level": level,
        "upcoming": upcoming,
        "sectors": sectors[:5],
        "chain_name": chain_name,
        "summary": summary_text,
        "core": core,
        "facts": facts,
        "impact_path": impact_path,
        "chain": {
            "name": chain_name or "宏观 / 主题",
            "up": list(chain["up"]),
            "mid": list(chain["mid"]),
            "down": list(chain["down"]),
            "logic": chain["logic"],
            "watch": chain["watch"],
        },
        "benefit": benefit,
        "risks": risks,
        "watch": watch,
        "score": {"intensity": intensity, "persistence": persistence, "certainty": certainty},
        "analogy": TAG_ANALOGY.get(primary_tag, "暂无高度相似的历史样本，建议以板块资金流与龙头走势作为主要验证信号。"),
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


# ---------------------------------------------------------------- 个股八指标研判
def _candle_pattern(bars):
    """蜡烛图形态识别：用最近 2-3 根日K给出形态结论（+1 看涨 / 0 中性 / -1 看跌）"""
    if len(bars) < 3:
        return {"name": "数据不足", "vote": 0, "desc": "K线不足 3 根，无法判断形态", "detail": "-"}
    last, prev, prev2 = bars[-1], bars[-2], bars[-3]

    def _body(b):
        return (b.get("close") or 0) - (b.get("open") or 0)

    def _span(b):
        return max((b.get("high") or b["close"]) - (b.get("low") or b["open"]), 0.001)

    def _ratio(b):
        return abs(_body(b)) / _span(b)

    def _shadows(b):
        top = (b.get("high") or b["close"]) - max(b.get("open"), b["close"])
        bottom = min(b.get("open"), b["close"]) - (b.get("low") or b["close"])
        return top, bottom

    lb, pb, ppb = _body(last), _body(prev), _body(prev2)
    top, bottom = _shadows(last)
    detail = (f"实体 {abs(lb) / _span(last) * 100:.0f}% / "
              f"上影 {top / _span(last) * 100:.0f}% / 下影 {bottom / _span(last) * 100:.0f}%")

    # 1. 三根K线组合：启明星 / 黄昏星
    mid_prev2 = (prev2["open"] + prev2["close"]) / 2
    if ppb < 0 and _ratio(prev) <= 0.35 and lb > 0 and last["close"] > mid_prev2 and abs(ppb) >= _span(prev2) * 0.5:
        return {"name": "启明星", "vote": 1,
                "desc": "下跌后小实体止跌、阳线收复前一根阴线一半，底部反转信号", "detail": detail}
    if ppb > 0 and _ratio(prev) <= 0.35 and lb < 0 and last["close"] < mid_prev2 and abs(ppb) >= _span(prev2) * 0.5:
        return {"name": "黄昏星", "vote": -1,
                "desc": "上涨后小实体滞涨、阴线吃掉前一根阳线一半，顶部反转信号", "detail": detail}

    # 2. 两根K线组合：吞没
    if pb < 0 and lb > 0 and last["close"] >= prev["open"] and last["open"] <= prev["close"]:
        return {"name": "看涨吞没", "vote": 1,
                "desc": "阳线实体完全覆盖前一根阴线，空头被打断", "detail": detail}
    if pb > 0 and lb < 0 and last["close"] <= prev["open"] and last["open"] >= prev["close"]:
        return {"name": "看跌吞没", "vote": -1,
                "desc": "阴线实体完全覆盖前一根阳线，多头被打断", "detail": detail}

    # 3. 连续三根同向
    three = bars[-3:]
    if all(_body(b) > 0 for b in three) and all(three[i]["close"] > three[i - 1]["close"] for i in (1, 2)):
        return {"name": "红三兵", "vote": 1, "desc": "连续三根阳线且收盘抬高，多头持续占优",
                "detail": detail}
    if all(_body(b) < 0 for b in three) and all(three[i]["close"] < three[i - 1]["close"] for i in (1, 2)):
        return {"name": "三只乌鸦", "vote": -1, "desc": "连续三根阴线且收盘走低，空头持续占优",
                "detail": detail}

    # 4. 单根长影线：锤子线 / 流星线
    if lb != 0 and bottom >= abs(lb) * 2 and top <= abs(lb) * 0.6 and _ratio(last) <= 0.55:
        return {"name": "锤子线", "vote": 1, "desc": "长下影显示低位有承接，止跌信号", "detail": detail}
    if lb != 0 and top >= abs(lb) * 2 and bottom <= abs(lb) * 0.6 and _ratio(last) <= 0.55:
        return {"name": "流星线", "vote": -1, "desc": "长上影显示冲高遇阻，回落风险上升", "detail": detail}

    # 5. 十字星 / 大实体
    if _ratio(last) <= 0.12:
        return {"name": "十字星", "vote": 0, "desc": "多空平衡的变盘信号，等下一根K线确认", "detail": detail}
    body_pct = lb / last["close"] * 100 if last["close"] else 0
    if body_pct >= 4:
        return {"name": "大阳线", "vote": 1, "desc": "单根放量长阳，日内多头强势", "detail": detail}
    if body_pct <= -4:
        return {"name": "大阴线", "vote": -1, "desc": "单根长阴，日内空头强势", "detail": detail}

    return {"name": "无明确形态", "vote": 0, "desc": "最近一根为普通K线，多空未形成明确形态",
            "detail": detail}


def analyze_stock_indicators(name, bars):
    """八个指标（均线、MACD、KDJ、RSI、布林带、K线形态、量能、支撑压力）综合给出次日判断"""
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
    boll = bollinger(closes, 20, 2.0)
    b_last = boll[-1] or {}
    b_prev = boll[-2] or {}
    candle = _candle_pattern(bars)

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

    add("candle", "K线形态", candle["vote"], candle["name"],
        f"{candle['desc']}（{candle['detail']}）")

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

    upper, lower, mid = b_last.get("upper"), b_last.get("lower"), b_last.get("mid")
    mid_prior = b_prev.get("mid")
    width = b_last.get("width")
    if upper and price > upper:
        add("boll", "布林带", -1, f"上轨 {upper:.2f} / 中轨 {mid:.2f}",
            "价格运行在上轨之外，短线过热，均值回归压力大")
    elif lower and price < lower:
        add("boll", "布林带", -1, f"下轨 {lower:.2f} / 中轨 {mid:.2f}",
            "跌破下轨且未见止跌，弱势延续")
    elif mid and mid_prior and price >= mid and mid > mid_prior:
        add("boll", "布林带", 1, f"中轨 {mid:.2f} / 带宽 {width}%",
            "站上中轨且中轨向上，强势区间运行")
    elif mid and mid_prior and price < mid and mid < mid_prior:
        add("boll", "布林带", -1, f"中轨 {mid:.2f} / 带宽 {width}%",
            "中轨下方且中轨下压，弱势格局")
    else:
        add("boll", "布林带", 0, f"中轨 {mid if mid else '-'} / 带宽 {width if width else '-'}%",
            "价格在中轨附近震荡，等带宽收窄后的方向选择")

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
    if score >= 3:
        direction, tone = "偏多", "bull"
    elif score <= -3:
        direction, tone = "偏空", "bear"
    else:
        direction, tone = "震荡", "neutral"

    bull_count = sum(1 for v in votes if v["vote"] > 0)
    bear_count = sum(1 for v in votes if v["vote"] < 0)
    expect = {
        "bull": f"八个指标中 {bull_count} 项偏多、{bear_count} 项偏空，次日大概率延续偏强走势，"
                f"上方关注 {resistance:.2f} 压力，站稳后有望打开空间。",
        "bear": f"八个指标中 {bear_count} 项偏空、{bull_count} 项偏多，次日偏向弱势震荡，"
                f"下方关注 {support:.2f} 支撑，失守则需减仓。",
        "neutral": f"八个指标多空分歧（{bull_count} 多 / {bear_count} 空），次日大概率维持震荡，"
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
        risks.append("若放量跌破支撑位，八指标共振转空，需及时止损")

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


# ---------------------------------------------------------------- 明日预期与应对策略
def build_stock_strategy(bars, indicator, valuation=None):
    """把八指标结论与价值分析合成「明日预期 + 短期/中期应对策略」"""
    if not indicator or not bars:
        return None
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    price = indicator.get("price") or (closes[-1] if closes else 0)
    if not price:
        return None
    levels = indicator.get("levels") or {}
    support = levels.get("support") or price
    resistance = levels.get("resistance") or price
    swing_low = levels.get("swing_low") or support
    bob = indicator.get("tomorrow") or {}
    tone = indicator.get("tone") or "neutral"
    direction = indicator.get("direction") or "震荡"
    score = indicator.get("score") or 0
    bull_count = bob.get("bull_count") or 0
    bear_count = bob.get("bear_count") or 0

    # 明日波动区间：以近 20 日平均振幅为半径，再叠加支撑压力参考
    changes = [abs(b.get("change_pct") or 0) for b in bars[-20:] if b.get("change_pct") is not None]
    amplitude = round(sum(changes) / len(changes), 2) if changes else 2.0
    amplitude = max(1.0, min(amplitude, 9.0))
    exp_low = round(price * (1 - amplitude / 100), 2)
    exp_high = round(price * (1 + amplitude / 100), 2)

    # 明日可执行的参考位：优先用最近均线，离现价过远则退回次日振幅上/下沿
    ma5 = ma(closes, 5)[-1] if len(closes) >= 5 else None
    ma10 = ma(closes, 10)[-1] if len(closes) >= 10 else None
    ma20 = ma(closes, 20)[-1] if len(closes) >= 20 else None
    ma60 = ma(closes, 60)[-1] if len(closes) >= 60 else None
    above = [(n, v) for n, v in (("MA5", ma5), ("MA10", ma10), ("MA20", ma20)) if v and v > price]
    below = [(n, v) for n, v in (("MA5", ma5), ("MA10", ma10), ("MA20", ma20)) if v and v < price]
    if above:
        res_name, near_res = min(above, key=lambda x: x[1])
        res_source = f"现价上方最近的 {res_name} {near_res:.2f}"
    else:
        near_res = resistance
        res_source = f"无均线压制，取斐波那契结构压力 {resistance:.2f}"
    if below:
        sup_name, near_sup = max(below, key=lambda x: x[1])
        sup_source = f"现价下方最近的 {sup_name} {near_sup:.2f}"
    else:
        near_sup = support
        sup_source = f"无均线支撑，取斐波那契结构支撑 {support:.2f}"
    if abs((near_res - price) / price * 100) > amplitude * 2:
        near_res = exp_high
        res_source = f"结构位距现价超过 2 倍日均振幅（{amplitude}%），改用区间上沿"
    if abs((price - near_sup) / price * 100) > amplitude * 2:
        near_sup = exp_low
        sup_source = f"结构位距现价超过 2 倍日均振幅（{amplitude}%），改用区间下沿"
    pct_near_sup = (near_sup - price) / price * 100
    pct_near_res = (near_res - price) / price * 100

    if abs(score) >= 5:
        confidence = "高"
    elif abs(score) >= 4:
        confidence = "中高"
    elif abs(score) >= 3:
        confidence = "中"
    else:
        confidence = "低"

    if tone == "bull":
        p_up = 60 if score >= 5 else 52
        rest = 100 - p_up
        p_chase = int(rest * 0.6)
        scenarios = [
            {"name": "延续上攻", "prob": p_up,
             "trigger": f"开盘 30 分钟站稳 {near_sup:.2f} 且量比大于 1",
             "plan": f"持股待涨，放量突破 {near_res:.2f} 后加仓，日内目标看 {exp_high:.2f}"},
            {"name": "冲高回落", "prob": p_chase,
             "trigger": f"早盘冲高 {near_res:.2f}（{pct_near_res:+.2f}%）附近滞涨、量能萎缩",
             "plan": "冲高不追，原仓位在压力位附近减 1/3，回落不破支撑再接回"},
            {"name": "放量破位", "prob": rest - p_chase,
             "trigger": f"放量跌破 {near_sup:.2f}（{pct_near_sup:+.2f}%）",
             "plan": "跌破立即减仓至 3 成以下，等重新站回支撑再评估"},
        ]
    elif tone == "bear":
        p_down = 60 if score <= -5 else 52
        scenarios = [
            {"name": "弱势下探", "prob": p_down,
             "trigger": f"反抽不过 {near_res:.2f}（{pct_near_res:+.2f}%）且量能萎缩",
             "plan": f"反弹至压力位附近以减仓为主，日内下探目标看 {exp_low:.2f}"},
            {"name": "低位止跌", "prob": 30,
             "trigger": f"{near_sup:.2f}（{pct_near_sup:+.2f}%）附近缩量收长下影",
             "plan": "只做超跌反弹，轻仓快进快出，不追高、不留隔夜重仓"},
            {"name": "加速下跌", "prob": 100 - p_down - 30,
             "trigger": f"放量击穿 {near_sup:.2f}，板块同步走弱",
             "plan": "立即降仓或清仓，等日线出现止跌信号再考虑接回"},
        ]
    else:
        scenarios = [
            {"name": "区间震荡", "prob": 50,
             "trigger": f"价格在 {near_sup:.2f} ~ {near_res:.2f} 内反复",
             "plan": "区间下沿低吸、上沿减仓，以做 T 为主，不提前埋伏突破"},
            {"name": "向上突破", "prob": 25,
             "trigger": f"放量站上 {near_res:.2f}（{pct_near_res:+.2f}%）且次日不回破",
             "plan": "突破确认后再加仓，避免日内假突破追高"},
            {"name": "向下跌破", "prob": 25,
             "trigger": f"收盘失守 {near_sup:.2f}（{pct_near_sup:+.2f}%）",
             "plan": "跌破减仓至 2-3 成，等重新企稳再回补"},
        ]

    watch = [
        f"{near_sup:.2f} 支撑 / {near_res:.2f} 压力是否得失",
        "开盘 30 分钟量能与昨日对比，放量方向决定强弱",
        "分时均价线上方运行、回踩是否有承接",
    ]
    if tone == "bull" and (score >= 5):
        watch.append("连阳后是否出现高位放量滞涨（冲高回落信号）")
    if tone == "bear":
        watch.append("反抽能否放量收复 5 日线，否则反弹视为逃命波")

    expect = {
        "bull": f"{bull_count} 项指标偏多、{bear_count} 项偏空，明日偏向强势运行，预期区间 "
                f"{exp_low:.2f} ~ {exp_high:.2f}；站稳 {near_res:.2f} 有望打开空间，跌破 {near_sup:.2f} 则结构转弱。",
        "bear": f"{bear_count} 项指标偏空、{bull_count} 项偏多，明日偏弱震荡概率较高，预期区间 "
                f"{exp_low:.2f} ~ {exp_high:.2f}；收复 {near_res:.2f} 才算转强，失守 {near_sup:.2f} 需减仓。",
        "neutral": f"多空分歧（{bull_count} 多 / {bear_count} 空），明日大概率区间震荡，参考 "
                   f"{near_sup:.2f} ~ {near_res:.2f}，放量选择方向后再跟随。",
    }[tone]

    # 短线：1-5 个交易日，以技术面为主
    # 短线过热：J 值破百、K 值高位或 RSI 进入超买区
    k_lines = kdj(highs, lows, closes)
    r_line = rsi(closes)
    jv, kv, rv = k_lines["j"][-1], k_lines["k"][-1], r_line[-1]
    overheated = bool(jv > 100 or kv > 90 or (rv is not None and rv > 75))
    if tone == "bull" and overheated:
        short_stance = "持有不追高"
        short_actions = [
            f"已有仓位继续持有，冲高至 {near_res:.2f} 附近先减 1/3 兑现",
            f"回踩 {near_sup:.2f} 不破可低吸，仓位维持 4-5 成",
            f"收盘跌破 {near_sup:.2f} 减仓，不扛单",
        ]
    elif tone == "bull":
        short_stance = "顺势做多"
        short_actions = [
            f"回踩 {near_sup:.2f} 附近低吸，仓位 5-6 成",
            f"放量突破 {near_res:.2f} 后加仓，短线目标 {exp_high:.2f}",
            f"跌破 {near_sup:.2f} 或 MA5 走平均减仓",
        ]
    elif tone == "bear":
        short_stance = "降仓防守"
        short_actions = [
            f"反弹至 {near_res:.2f} 附近减仓，不接下跌中的刀",
            "仓位降至 2-3 成或空仓，等日线止跌信号",
            f"跌破 {near_sup:.2f} 清仓离场",
        ]
    else:
        short_stance = "半仓区间操作"
        short_actions = [
            f"{near_sup:.2f} 附近低吸、{near_res:.2f} 附近减仓，做区间波段",
            "仓位 3-4 成，不在突破前重仓埋伏",
            f"任一方向放量突破（{near_res:.2f} / {near_sup:.2f}）再顺势加仓",
        ]
    short_guard = f"短线失效条件：收盘跌破 {near_sup:.2f}（{pct_near_sup:+.2f}%）或连续两日缩量阴跌"

    # 中线：1-3 个月，以价值与趋势为主
    val_score = (valuation or {}).get("score")
    latest = (valuation or {}).get("latest") or {}
    research = (valuation or {}).get("research") or {}
    uptrend = bool(ma20 and ma60 and price > ma20 > ma60)
    if val_score is None:
        mid_stance = "以短线为主"
    elif val_score >= 75:
        mid_stance = "逢低配置" if uptrend else "价值优先，等趋势确认"
    elif val_score >= 60:
        mid_stance = "回调分批建仓" if uptrend else "基本面良好，等右侧确认"
    elif val_score >= 45:
        mid_stance = "观察等待"
    else:
        mid_stance = "不宜中线重仓"

    mid_actions = []
    if uptrend:
        mid_actions.append(f"趋势向上（价格站上 MA20 {ma20:.2f}、MA60 {ma60:.2f}），回踩 MA20 附近可分批建仓")
    else:
        mid_actions.append(f"趋势未确认，等站稳 MA20 {ma20:.2f} 与 MA60 {ma60:.2f} 后再介入" if ma20 and ma60 else "均线数据不足，先观察 60 日均线是否走平")
    if val_score is not None:
        mid_actions.append(f"价值四维评分 {val_score} 分，"
                           + ("基本面良好，可按 3 成分批布局" if val_score >= 60
                              else "基本面中性，中线仓位不超过 2 成" if val_score >= 45
                              else "基本面偏弱，仅做短线，不重仓持有"))
        if latest.get("net_profit_yoy") is not None:
            mid_actions.append(f"最新报告期净利同比 {latest['net_profit_yoy']:+.1f}%、营收同比 "
                               f"{(latest.get('revenue_yoy') or 0):+.1f}%，作为基本面跟踪锚点")
    else:
        mid_actions.append("财务数据不足，中线按技术面轻仓参与，仓位不超过 2 成")
    if research.get("target_avg"):
        mid_actions.append(f"研报平均目标价 {research['target_avg']} 元（较现价 {research.get('upside_pct')}%），作中期空间参考")
    mid_guard = (f"中线逻辑失效：跌破 {swing_low:.2f} 前低"
                 + (f"或 MA60 {ma60:.2f}" if ma60 else "")
                 + "；或下一期财报净利同比转负")

    # 计算逻辑说明：参数、取值与规则逐项列出，前端折叠查看
    indicator_items = [
        {"label": v["title"], "value": v["value"],
         "hint": f"{v['desc']}（{'+1' if v['vote'] > 0 else ('-1' if v['vote'] < 0 else '0')} 分）"}
        for v in indicator.get("votes") or []
    ]
    mid_items = [
        {"label": "价值四维综合", "value": f"{val_score} 分" if val_score is not None else "数据不足",
         "hint": (valuation or {}).get("verdict") or "财务数据不足"},
    ]
    for dim in (valuation or {}).get("dimensions") or []:
        if dim.get("score") is None:
            continue
        facts = "、".join(f"{it['label']} {it['value']}" for it in (dim.get("items") or [])[:2])
        mid_items.append({"label": dim["title"], "value": f"{dim['score']:.1f} / 25", "hint": facts})
    mid_items.append({
        "label": "趋势判定",
        "value": "多头趋势" if uptrend else "趋势未确认",
        "hint": (f"价格 {price:.2f}、MA20 {ma20:.2f}、MA60 {ma60:.2f}；"
                 f"要求价格 > MA20 > MA60 才算多头"),
    })
    mid_items.append({"label": "仓位上限", "value": mid_stance, "hint": mid_guard})

    details_sections = [
        {
            "title": "八指标依据（日线）",
            "note": "均线、K线形态、MACD、KDJ、RSI、布林带、量能、支撑压力各投一票，决定明日多空基调。",
            "items": indicator_items,
            "formula": "总分 = 八项投票之和（+1 / 0 / -1）；≥ +3 判偏多，≤ -3 判偏空，其余为震荡。",
        },
        {
            "title": "明日区间与参考位",
            "note": f"现价 {price:.2f}，用近 20 个交易日涨跌幅绝对值的平均作为日均振幅。",
            "items": [
                {"label": "日均振幅", "value": f"{amplitude}%", "hint": "近 20 日 |涨跌幅| 均值，截断在 1%~9%"},
                {"label": "明日预期区间", "value": f"{exp_low:.2f} ~ {exp_high:.2f}", "hint": "现价 ×(1 ± 振幅)"},
                {"label": "明日参考支撑", "value": f"{near_sup:.2f}", "hint": sup_source},
                {"label": "明日参考压力", "value": f"{near_res:.2f}", "hint": res_source},
                {"label": "结构支撑 / 压力", "value": f"{support:.2f} / {resistance:.2f}",
                 "hint": "近 120 根日线的斐波那契回撤位，用于中线失效判断"},
            ],
            "formula": ("区间 = 收盘价 × (1 ± 日均振幅)；参考位优先取现价最近的上/下方均线，"
                        "距离超过 2 倍振幅时改用区间上/下沿。"),
        },
        {
            "title": "情景概率",
            "note": f"按多空基调与评分强度分配，当前 {direction}（{score:+d} 分）。",
            "items": [{"label": s["name"], "value": f"{s['prob']}%", "hint": s["trigger"]} for s in scenarios],
            "formula": ("偏多：评分 ≥ +5 时上攻给 60%，否则 52%，回落/破位按 6:4 分剩余概率；"
                        "偏空：评分 ≤ -5 时下探给 60%，否则 52%，止跌 30%，其余为加速下跌；"
                        "震荡：50% / 25% / 25%。"),
        },
        {
            "title": "短期策略规则",
            "note": "1-5 个交易日，技术面主导，仓位由八指标总分与过热状态决定。",
            "items": [
                {"label": "技术基调", "value": f"{direction}（{score:+d} 分）",
                 "hint": f"{bull_count} 项偏多 / {bear_count} 项偏空"},
                {"label": "过热修正", "value": "已触发" if overheated else "未触发",
                 "hint": (f"J {jv:.1f} / K {kv:.1f} / RSI {rv:.1f}；"
                          "J>100 或 K>90 或 RSI>75 视为过热，偏多基调降级为「持有不追高」")
                 if rv is not None else "KDJ：J>100 或 K>90 视为过热"},
                {"label": "操作参考区间", "value": f"{near_sup:.2f} ~ {near_res:.2f}",
                 "hint": "取自明日参考支撑 / 压力，用于低吸与减仓"},
            ],
            "formula": "偏多 → 低吸持股；偏空 → 降仓防守；震荡 → 半仓区间高抛低吸；跌破参考支撑为短线失效。",
        },
        {
            "title": "中期策略规则",
            "note": "1-3 个月，价值评分与均线趋势共同决定仓位上限。",
            "items": mid_items,
            "formula": ("综合分 = 四维各 25 分合计 ÷ 100 × 100；"
                        "≥75 逢低配置、≥60 回调分批、≥45 观察、<45 不宜重仓；趋势未确认时一律等右侧确认。"),
        },
    ]

    return {
        "basis": {
            "indicator": f"八指标 {score:+d} 分 · {indicator.get('direction')}（{bull_count} 多 / {bear_count} 空）",
            "valuation": ("价值数据不足" if val_score is None
                          else f"四维 {val_score} 分 · {(valuation or {}).get('verdict') or '暂无结论'}"),
        },
        "levels": {"support": round(support, 2), "resistance": round(resistance, 2),
                   "swing_low": round(swing_low, 2)},
        "tomorrow": {
            "bias": indicator.get("direction"),
            "tone": tone,
            "confidence": confidence,
            "amplitude": amplitude,
            "range": {"low": exp_low, "high": exp_high},
            "support": round(near_sup, 2),
            "resistance": round(near_res, 2),
            "expectation": expect,
            "scenarios": scenarios,
            "watch": watch,
        },
        "short_term": {"horizon": "1-5 个交易日", "stance": short_stance,
                       "actions": short_actions, "guard": short_guard},
        "mid_term": {"horizon": "1-3 个月", "stance": mid_stance,
                     "actions": mid_actions, "guard": mid_guard},
        "details": {"sections": details_sections},
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
