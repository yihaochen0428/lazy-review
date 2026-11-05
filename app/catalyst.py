"""开盘催化因子：板块日K、消息面、外盘映射、大盘情绪反转

这些因子只用于「加仓 / 放行」的方向性判断，不替代原有的竞价低吸与日K 打分：
- 板块日K：用行业 ETF 的日线状态代理板块趋势（板块指数没有公开日线接口）；
- 消息面：从财经快讯里按板块 / 行业关键词匹配，按 利好/利空 与重要级别加权；
- 外盘映射：美股对应行业 ETF 的隔夜涨跌（光伏→TAN、半导体→SOXX、医药→XBI…）；
- 情绪反转：上证情绪分相对 5 个交易日前的抬升幅度。
"""
import urllib.request

from . import news
from .db import cached_call

US_PROXY = {
    "新能源": (("tan", "Invesco 太阳能 ETF"), ("qcln", "清洁能源 ETF")),
    "半导体": (("soxx", "半导体 ETF"), ("smh", "半导体 ETF")),
    "AI 与算力": (("qqq", "纳指 100 ETF"), ("smh", "半导体 ETF")),
    "医药": (("xbi", "生物科技 ETF"),),
    "地产链": (("itb", "美国房屋建筑 ETF"),),
    "券商金融": (("xlf", "美国金融 ETF"),),
    "消费": (("xly", "可选消费 ETF"),),
    "军工": (("ita", "航空航天国防 ETF"),),
    "资源品": (("xle", "能源 ETF"), ("gdx", "黄金矿业 ETF")),
}
US_DEFAULT = (("spy", "标普 500 ETF"),)


def _clamp(value, low=-1.0, high=1.0):
    return max(low, min(high, value))


def _fetch_us(symbols):
    """新浪美股快照：返回 {代码: 隔夜涨跌幅}"""
    query = ",".join(f"gb_{s}" for s in symbols)
    url = f"https://hq.sinajs.cn/list={query}"
    req = urllib.request.Request(url, headers={
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    })
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = resp.read().decode("gbk", "ignore")
    out = {}
    for line in raw.split(";"):
        if "hq_str_gb_" not in line:
            continue
        body = line.split("=", 1)[1].strip().strip('"')
        fields = body.split(",")
        if len(fields) < 3:
            continue
        try:
            pct = float(fields[2])
        except (TypeError, ValueError):
            continue
        symbol = line.split("hq_str_gb_", 1)[1].split("=")[0].strip()
        out[symbol] = pct
    return out


def overseas(sector):
    """外盘映射：同一行业的美股 ETF 隔夜涨跌幅 -> [-1, 1]"""
    proxies = US_PROXY.get(sector) or US_DEFAULT

    def producer():
        try:
            return _fetch_us([s for s, _ in proxies])
        except Exception:  # noqa: BLE001
            return {}

    quotes = cached_call(f"cat:us:{sector or 'default'}", 600, producer) or {}
    items = [{"symbol": s.upper(), "name": name, "pct": quotes.get(s)}
             for s, name in proxies if quotes.get(s) is not None]
    if not items:
        return {"score": None, "items": [], "label": "未取到"}
    avg = sum(i["pct"] for i in items) / len(items)
    score = round(_clamp(avg / 5.0), 3)
    label = "外盘大涨" if avg >= 4 else "外盘偏强" if avg >= 1.5 else "外盘偏弱" if avg <= -1.5 else "外盘平淡"
    return {"score": score, "avg_pct": round(avg, 2), "items": items, "label": label}


def _news_items():
    def producer():
        try:
            data = news.fetch_news(limit=40)
            return (data or {}).get("items") or []
        except Exception:  # noqa: BLE001
            return []

    return cached_call("cat:news", 600, producer) or []


def news_catalyst(keywords, limit=3):
    """消息面催化：按板块/行业关键词匹配，利好 +、利空 -"""
    keys = [k for k in (keywords or []) if k and len(str(k)) >= 2]
    if not keys:
        return {"score": None, "items": [], "label": "无关键词"}
    hits = []
    for item in _news_items():
        text = f"{item.get('title') or ''} {item.get('summary') or ''} {' '.join(item.get('tags') or [])}"
        hit = [k for k in keys if k in text]
        if not hit:
            continue
        impact = item.get("impact") or "中性"
        level = int(item.get("level") or 1)
        weight = 0
        if impact == "利好":
            weight = 1.5 if level >= 3 else 1.0
        elif impact == "利空":
            weight = -1.5 if level >= 3 else -1.0
        hits.append({"title": item.get("title"), "impact": impact, "level": level,
                     "time_text": item.get("time_text"), "hit": hit[:2], "weight": weight})
    raw = sum(h["weight"] for h in hits)
    score = round(_clamp(raw / 3.0), 3) if hits else 0.0
    label = "消息偏多" if raw >= 3 else "消息偏空" if raw <= -3 else "消息平淡"
    hits.sort(key=lambda h: (-abs(h["weight"]), -(h["level"] or 0)))
    return {"score": score, "raw": round(raw, 1), "items": hits[:limit], "label": label,
            "count": len(hits)}


def build(sector, keywords, mkt=None):
    """合成催化分：消息 0.45 + 外盘 0.35 + 情绪反转 0.20（缺项按剩余权重归一）"""
    news_part = news_catalyst(keywords)
    us_part = overseas(sector)
    sent_trend = ((mkt or {}).get("sent") or {}).get("trend")
    parts = []
    if news_part["score"] is not None and news_part["count"]:
        parts.append((0.45, news_part["score"]))
    if us_part["score"] is not None:
        parts.append((0.35, us_part["score"]))
    if sent_trend is not None:
        parts.append((0.20, _clamp(sent_trend / 8.0)))
    if not parts:
        score = None
    else:
        total = sum(w for w, _ in parts)
        score = round(sum(w * v for w, v in parts) / total, 3)
    return {"score": score, "news": news_part, "overseas": us_part,
            "sent_trend": sent_trend}
