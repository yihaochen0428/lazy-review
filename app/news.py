"""重要新闻聚合：华尔街见闻实时快讯 + 新浪财经滚动，按影响力打分归类"""
import hashlib
import json
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from . import eastmoney as em
from . import ths
from .db import (cached_call, news_archive_put, news_archive_query, news_archive_span,
                 news_archive_stats)

WSCN_LIVE = "https://api-one.wallstcn.com/apiv1/content/lives?channel={channel}&client=pc&limit=40"
SINA_ROLL = ("https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&num=30&page=1")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://wallstreetcn.com/live/global",
}
NEWS_TTL = 600

# 关键词 -> (分类, 影响力权重, 倾向)
KEYWORDS = {
    "降准": ("货币政策", 5, "bull"), "降息": ("货币政策", 5, "bull"), "加息": ("货币政策", 5, "bear"),
    "MLF": ("货币政策", 4, "neutral"), "LPR": ("货币政策", 4, "neutral"), "逆回购": ("货币政策", 3, "neutral"),
    "美联储": ("海外流动性", 5, "neutral"), "鲍威尔": ("海外流动性", 4, "neutral"),
    "CPI": ("宏观数据", 4, "neutral"), "PPI": ("宏观数据", 3, "neutral"), "PMI": ("宏观数据", 4, "neutral"),
    "非农": ("宏观数据", 4, "neutral"), "GDP": ("宏观数据", 4, "neutral"), "社融": ("宏观数据", 4, "neutral"),
    "国务院": ("政策", 5, "bull"), "发改委": ("政策", 4, "bull"), "工信部": ("政策", 4, "bull"),
    "证监会": ("政策", 4, "neutral"), "财政部": ("政策", 4, "bull"), "央行": ("货币政策", 4, "neutral"),
    "专项债": ("财政", 4, "bull"), "减税": ("财政", 4, "bull"), "补贴": ("产业政策", 3, "bull"),
    "关税": ("中美关系", 5, "bear"), "制裁": ("中美关系", 5, "bear"), "出口管制": ("中美关系", 5, "bear"),
    "北向": ("资金面", 4, "neutral"), "融资余额": ("资金面", 3, "neutral"), "ETF": ("资金面", 3, "neutral"),
    "IPO": ("资金面", 3, "bear"), "减持": ("资金面", 3, "bear"), "回购": ("资金面", 3, "bull"),
    "半导体": ("行业", 3, "neutral"), "芯片": ("行业", 3, "neutral"), "算力": ("行业", 3, "neutral"),
    "人工智能": ("行业", 3, "neutral"), "光伏": ("行业", 3, "neutral"), "储能": ("行业", 3, "neutral"),
    "新能源汽车": ("行业", 3, "neutral"), "医药": ("行业", 3, "neutral"), "地产": ("行业", 3, "neutral"),
    "军工": ("行业", 3, "neutral"), "机器人": ("行业", 3, "neutral"), "黄金": ("行业", 3, "neutral"),
    "原油": ("大宗商品", 3, "neutral"), "OPEC": ("大宗商品", 4, "neutral"),
    "战争": ("地缘政治", 5, "bear"), "冲突": ("地缘政治", 4, "bear"), "大选": ("地缘政治", 4, "bear"),
    "衰退": ("海外经济", 5, "bear"), "通胀": ("海外经济", 4, "bear"), "债务上限": ("海外经济", 4, "bear"),
}
SCOPE_GLOBAL = ("美联储", "美国", "欧洲", "日本", "英国", "俄", "乌克兰", "伊朗", "以色列", "特朗普",
                "海外", "全球", "OPEC", "欧元区", "关税", "制裁", "纳斯达克", "美股", "黄金", "原油",
                "美元", "离岸", "地缘")
SCOPE_DOMESTIC = ("国务院", "发改委", "工信部", "证监会", "央行", "财政部", "A股", "沪深", "北向",
                  "国内", "中国", "人民币", "我国", "五部门", "沪指", "深成指", "创业板")

TAG_ORDER = ["货币政策", "海外流动性", "政策", "财政", "宏观数据", "中美关系", "资金面", "行业",
             "大宗商品", "地缘政治", "海外经济"]
BLOCK_WORDS = ("索赔", "受损股民", "登记", "律师提示", "紧急通知", "一字跌停",
               "龙虎榜", "游资", "涨停揭秘", "机构调研", "评级买入", "目标价", "股权激励",
               "业绩预告", "业绩快报", "重大合同", "中标", "收购", "重组", "问询函", "关注函",
               "异动公告", "股东减持", "股东增持", "解禁", "分红", "配股", "定增", "调研纪要",
               "公司公告", "董事会")
LEADER_WORDS = ("宁德时代", "中际旭创", "贵州茅台", "比亚迪", "隆基绿能", "阳光电源", "寒武纪",
                "中芯国际", "工业富联", "东方财富", "中信证券", "紫金矿业", "北方稀土", "长江电力",
                "中国平安", "招商银行", "美的集团", "格力电器", "药明康德", "海光信息", "新易盛")
UPCOMING_WORDS = ("将于", "即将", "下周", "明日", "本周五", "本周四", "未来", "预计将", "日程",
                  "会议", "听证", "议息", "公布", "发布", "召开", "论坛", "峰会", "生效", "实施",
                  "截止", "到期", "表决", "访华", "窗口期", "预告")


def _fetch(url: str, headers=None, timeout: int = 10, retries: int = 2):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "ignore")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"新闻源请求失败：{last}")


def _wscn(channel: str, cursor=None):
    payload = json.loads(_fetch(WSCN_LIVE.format(channel=channel) + (f"&cursor={cursor}" if cursor else "")))
    data = payload.get("data") or {}
    items = data.get("items") or []
    rows = []
    for item in items:
        title = (item.get("title") or "").strip()
        content = re.sub(r"\s+", " ", (item.get("content_text") or item.get("content") or "")).strip()
        if not title:
            title = content[:44]
        if not title:
            continue
        rows.append({
            "title": title,
            "summary": content[:220],
            "time": int(item.get("display_time") or 0),
            "url": item.get("uri") or "",
            "channels": item.get("channels") or [],
            "score": float(item.get("score") or 0),
            "source": "华尔街见闻",
        })
    return rows, data.get("next_cursor")


def _sina():
    payload = json.loads(_fetch(SINA_ROLL, headers={**HEADERS, "Referer": "https://finance.sina.com.cn/"}))
    rows = []
    for item in ((payload.get("result") or {}).get("data")) or []:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        rows.append({
            "title": title,
            "summary": re.sub(r"\s+", " ", (item.get("intro") or item.get("summary") or "")).strip()[:220],
            "time": int(item.get("ctime") or 0),
            "url": item.get("url") or "",
            "channels": [],
            "score": 0.0,
            "source": item.get("media_name") or "新浪财经",
        })
    return rows


def _classify(row):
    text = f"{row['title']} {row['summary']}"
    tags, score, bulls, bears = [], float(row["score"] or 0), 0, 0
    for word, (tag, weight, tone) in KEYWORDS.items():
        if word in text:
            if tag not in tags:
                tags.append(tag)
            score += weight
            if tone == "bull":
                bulls += weight
            elif tone == "bear":
                bears += weight
    if "global-channel" in row["channels"] and any(w in text for w in SCOPE_GLOBAL):
        scope = "国际"
    elif any(w in text for w in SCOPE_DOMESTIC):
        scope = "国内"
    elif "global-channel" in row["channels"] and not any(w in text for w in SCOPE_DOMESTIC):
        scope = "国际"
    else:
        scope = "国内"
    is_micro = ("公告" in text or "回购" in text or "中标" in text) and len(row["summary"]) < 120
    if is_micro:
        score -= 3
    if bulls - bears >= 3:
        impact = "利好"
    elif bears - bulls >= 3:
        impact = "利空"
    else:
        impact = "中性"
    tags.sort(key=lambda t: TAG_ORDER.index(t) if t in TAG_ORDER else 99)
    level = 5 if score >= 9 else 4 if score >= 6 else 3 if score >= 3.5 else 2 if score >= 2 else 1
    upcoming = any(w in text for w in UPCOMING_WORDS)
    return {**row, "scope": scope, "tags": tags[:3], "impact": impact, "level": level,
            "upcoming": upcoming, "is_leader": any(w in text for w in LEADER_WORDS),
            "score": round(score, 1),
            "time_text": datetime.fromtimestamp(row["time"]).strftime("%m-%d %H:%M") if row["time"] else "-"}


# ---------------------------------------------------------------- 未来事件日历与预期热点
# 事件关键词 -> A股概念板块（参考东财财经日历与同花顺事件驱动口径）
EVENT_CONCEPTS = [
    (("半导体", "封装", "芯片", "晶圆", "集成电路", "存储"), ["半导体", "先进封装", "存储芯片"]),
    (("光通信", "全光", "F5G", "光模块", "数据中心", "算力", "AI", "人工智能", "大模型", "智能体"),
     ["算力", "光模块", "人工智能"]),
    (("机器人", "人形", "减速器", "具身"), ["机器人", "减速器"]),
    (("光伏", "储能", "锂电", "电池", "新能源", "充电"), ["光伏", "储能", "锂电池"]),
    (("医药", "医疗", "创新药", "疫苗", "生物", "医保"), ["创新药", "医疗器械"]),
    (("军工", "航天", "卫星", "航空", "导弹"), ["军工", "商业航天"]),
    (("汽车", "智驾", "自动驾驶", "车展"), ["智能驾驶", "汽车零部件"]),
    (("消费", "白酒", "零售", "食品", "家电"), ["大消费", "白酒"]),
    (("券商", "银行", "金融", "保险", "资本市场"), ["券商", "银行"]),
    (("地产", "基建", "建筑", "城市更新"), ["房地产", "基建"]),
    (("稀土", "有色", "黄金", "铜", "锂矿"), ["稀土", "有色金属"]),
    (("电力", "核电", "电网", "绿电", "风电"), ["电力", "核电"]),
    (("农业", "种业", "粮食", "生猪"), ["农业", "种业"]),
    (("低空", "无人机", "eVTOL", "通用航空"), ["低空经济", "无人机"]),
    (("量子", "6G", "卫星互联网", "通信"), ["量子科技", "6G"]),
    (("进博", "进口博览", "消博", "博览会"), ["大消费", "免税", "跨境电商"]),
    (("原油", "OPEC", "石油", "天然气", "油价", "沙特阿美"), ["石油石化", "油气"]),
    (("数据要素", "数字经济", "公共数据", "数据局"), ["数据要素", "数字经济"]),
    (("关税", "出口", "贸易", "外贸"), ["跨境电商", "出口贸易"]),
    (("煤", "电价", "电力现货", "用电", "电力", "电网", "绿电", "风电"), ["煤炭", "电力"]),
    (("高通", "AMD", "英伟达", "英特尔", "台积电", "美光", "ASML", "SK海力士", "三星电子"),
     ["半导体", "存储芯片"]),
    (("苹果", "小米", "消费电子", "手机", "折叠屏"), ["消费电子", "半导体"]),
    (("COP", "气候", "碳中和", "碳排放"), ["碳中和", "光伏"]),
]
IMPORTANT_EVENTS = ("CPI", "PPI", "PMI", "GDP", "非农", "FOMC", "议息", "LPR", "MLF", "社融",
                    "美联储", "政治局", "中央经济工作会议", "两会", "关税", "出口管制",
                    "降准", "降息", "中期选举", "OPEC", "进博会", "进口博览")
WEEK_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
COUNTRIES = ("美国", "中国", "欧元区", "欧盟", "日本", "英国", "德国", "法国", "意大利",
             "加拿大", "澳大利亚", "新西兰", "瑞士", "瑞典", "挪威", "巴西", "印度",
             "韩国", "俄罗斯", "中国香港", "中国台湾", "中国澳门", "全球", "财新")
# 经济数据中的高频库存/派生需求分项，对 A 股几乎没有映射价值，直接过滤
MACRO_NOISE = ("引申需求", "燃料乙醇", "车用汽油", "精炼油", "库欣", "商品贸易",
               "四周移动平均", "出口金额", "进口金额", "贸易差额", "库存量")
DAY_EVENT_CAP = 8


def _event_category(name: str, fe_type: str) -> str:
    text = f"{name} {fe_type}"
    if "经济数据" in fe_type:
        return "宏观数据"
    if any(w in name for w in ("逆回购", "MLF", "流动性", "国库现金", "中期借贷", "再融资")):
        return "资金面"
    if any(w in name for w in ("议息", "利率决议", "货币政策", "央行", "美联储", "听证", "选举",
                               "讲话", "演讲", "致辞", "问答", "主持")):
        return "海外"
    if any(w in name for w in ("新闻发布会", "国务院", "发改委", "商务部", "政治局", "国常会",
                               "证监会", "财政部", "政策", "座谈会")):
        return "政策"
    if any(w in text for w in ("行业会议", "高峰论坛", "会议", "论坛", "峰会", "博览")):
        return "产业会议"
    if "新股" in name:
        return "新股"
    if any(w in name for w in ("业绩", "财报", "年报", "季报")):
        return "财报"
    return "其他"


def _base_name(name: str) -> str:
    """经济数据常有多个分项（新订单/就业/库存等），折叠成一条便于阅读"""
    clean = re.sub(r"（[^）]*）|\([^)]*\)", "", name).strip()
    parts = [p.strip() for p in re.split(r"[:：]", clean) if p.strip()]
    if len(parts) >= 2 and parts[0] in COUNTRIES:
        key = parts[1]
        # 中国台湾：台湾:CPI:环比 -> 中国台湾:CPI
        if key in ("台湾", "香港", "澳门"):
            key = parts[2] if len(parts) >= 3 else ""
            if not key:
                return parts[0]
        return f"{parts[0]}:{key}"
    return clean[:48] or name[:48]


def _event_key(name: str) -> str:
    """去掉“将于/于 + 日期”等后缀，以及“某领导人将出席”前缀，避免同一会议重复计数"""
    clean = re.sub(r"(将于|于)[^，。；]*$", "", name)
    clean = re.sub(r"（[^）]*）|\([^)]*\)", "", clean).strip()
    if "出席" in clean:
        clean = clean.split("出席", 1)[1].strip()
    return clean[:24] or name[:24]


def _event_importance(name: str, category: str = "") -> str:
    if any(w in name for w in IMPORTANT_EVENTS):
        # 非主要经济体的 CPI/PPI 对 A 股影响有限，降为中等
        region = re.split(r"[:：]", name)[0]
        if category == "宏观数据" and region not in ("中国", "美国", "欧元区", "欧盟", "财新"):
            return "中"
        return "高"
    if any(w in name for w in ("会议", "论坛", "峰会", "公布", "发布", "听证", "表决")):
        return "中"
    return "低"


def _event_concepts(name: str, content: str, category: str = ""):
    # 海外数据/海外会议只保留与原油直接相关的映射，避免把美国贸易数据算到跨境电商头上
    if category == "海外" and not any(w in name for w in ("原油", "OPEC")):
        return []
    if category == "宏观数据" and not any(w in name for w in ("中国", "国内", "财新")):
        return []
    text = f"{name} {content}"
    out = []
    for words, concepts in EVENT_CONCEPTS:
        if any(w in text for w in words):
            for concept in concepts:
                if concept not in out:
                    out.append(concept)
    return out[:3]


def _day_label(date_text: str):
    try:
        day = datetime.strptime(date_text, "%Y-%m-%d").date()
    except ValueError:
        return "日期待定", None
    left = (day - datetime.now().date()).days
    prefix = "今天" if left == 0 else "明天" if left == 1 else f"{left}天后" if left > 1 else "已过"
    return f"{prefix} · {WEEK_LABELS[day.weekday()]}", left


def fetch_events(days: int = 21, limit: int = 120):
    """东财财经日历 + 概念映射，输出未来事件与预期热点"""
    today = datetime.now()
    start = today.strftime("%Y-%m-%d")
    end = (today + timedelta(days=days)).strftime("%Y-%m-%d")

    def producer():
        # 财经日历单次限量，按 7 天切片拉取，保证覆盖整个窗口而不是被前几天塞满
        rows, cursor = [], today.date()
        end_day = (today + timedelta(days=days)).date()
        while cursor <= end_day:
            chunk_end = min(cursor + timedelta(days=6), end_day)
            try:
                rows.extend(em.fetch_calendar(cursor.strftime("%Y-%m-%d"),
                                              chunk_end.strftime("%Y-%m-%d"), 500))
            except Exception:  # noqa: BLE001
                pass
            cursor = chunk_end + timedelta(days=1)

        events, seen = [], set()
        for row in rows:
            name = (row.get("name") or "").strip()
            if not name or "恒生指数" in name:
                continue
            category = _event_category(name, row.get("type") or "")
            if category == "宏观数据" and any(w in name for w in MACRO_NOISE):
                continue
            base = _base_name(name)
            key = (row.get("date"), base if category == "宏观数据" else _event_key(name))
            if key in seen:
                continue
            seen.add(key)
            label, left = _day_label(row.get("date") or "")
            if left is not None and left < 0:
                continue
            display = base if category == "宏观数据" else name
            events.append({
                "date": row.get("date"),
                "end_date": row.get("end_date"),
                "name": display,
                "type": row.get("type") or "其他",
                "category": category,
                "importance": _event_importance(base, category),
                "content": (row.get("content") or "")[:160],
                "city": row.get("city") or "",
                "sponsor": row.get("sponsor") or "",
                "related": _event_concepts(name, row.get("content") or "", category),
                "day_label": label,
                "days_left": left,
                "source": row.get("source") or "东方财富财经日历",
                "url": row.get("url"),
            })
        # 先按日期 + 重要性排序，再按每天上限截取，避免低价值数据把整个窗口挤掉
        events.sort(key=lambda e: (e["date"], {"高": 0, "中": 1, "低": 2}[e["importance"]]))
        capped, per_day = [], {}
        for event in events:
            day = event["date"]
            if per_day.get(day, 0) >= DAY_EVENT_CAP:
                continue
            per_day[day] = per_day.get(day, 0) + 1
            capped.append(event)
        events = capped[:limit]

        # 事件 -> 同花顺概念板块（名称映射，避免东财板块接口被限流）
        try:
            gn_list = ths.concept_boards()
        except Exception:  # noqa: BLE001
            gn_list = []
        weight = {"高": 3, "中": 2, "低": 1}
        agg = {}
        for event in events:
            for concept in event["related"]:
                slot = agg.setdefault(concept, {"keyword": concept, "name": concept,
                                                "board_code": None, "count": 0, "score": 0,
                                                "events": []})
                slot["count"] += 1
                slot["score"] += weight.get(event["importance"], 1)
                if len(slot["events"]) < 2:
                    slot["events"].append(event["name"][:32])
        for concept, slot in agg.items():
            hit = next((b for b in gn_list if concept in b["name"]), None)
            if hit:
                slot["name"] = hit["name"]
                slot["board_code"] = hit["code"]
            slot["strength"] = "强" if slot["score"] >= 6 else "中" if slot["score"] >= 3 else "观察"
        hotspots = sorted(agg.values(), key=lambda s: (-s["score"], -s["count"]))
        hotspots = [h for h in hotspots if h["score"] >= 2 or h["count"] >= 2][:10]
        return {
            "events": events,
            "hotspots": hotspots[:10],
            "window": f"{start} ~ {end}",
            "source": "东方财富财经日历",
        }

    return cached_call(f"events:v7:{start}:{days}:{limit}", 1800, producer)


# ---------------------------------------------------------------- 历史新闻归档
ARCHIVE_CHANNELS = ("a-stock-channel", "global-channel")
ARCHIVE_DAYS = 95          # 至少覆盖过去 3 个月
_archive_lock = threading.Lock()
_archive_state = {"running": False, "oldest": None, "error": "", "started": 0, "done_at": 0}


def _archive_row(item):
    ts = int(item.get("time") or 0)
    return {
        "id": hashlib.sha1(f"{item.get('url') or ''}|{item.get('title')}|{ts}"
                           .encode("utf-8")).hexdigest()[:20],
        "ts": ts,
        "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
        "title": item.get("title") or "",
        "summary": item.get("summary") or "",
        "url": item.get("url") or "",
        "source": item.get("source") or "",
        "scope": item.get("scope") or "国内",
        "impact": item.get("impact") or "中性",
        "level": item.get("level") or 1,
        "tags": item.get("tags") or [],
        "category": (item.get("tags") or [""])[0],
        "upcoming": item.get("upcoming"),
    }


def _crawl_channel(channel: str, stop_ts: float, max_pages: int = 800) -> int:
    """从最新快讯向历史翻页，写入归档直至覆盖 stop_ts"""
    cursor, pages, saved, oldest, fails = None, 0, 0, None, 0
    while pages < max_pages:
        try:
            rows, cursor = _wscn(channel, cursor)
            fails = 0
        except Exception:  # noqa: BLE001
            fails += 1
            if fails >= 3:
                break
            time.sleep(0.8 * fails)
            continue
        if not rows:
            break
        keep = []
        for row in rows:
            if row["time"] < stop_ts or not row["time"]:
                continue
            item = _classify(row)
            if item["level"] < 2:
                continue
            keep.append(_archive_row(item))
        saved += news_archive_put(keep)
        pages += 1
        page_oldest = min(r["time"] for r in rows)
        oldest = page_oldest if oldest is None else min(oldest, page_oldest)
        with _archive_lock:
            if oldest and (_archive_state["oldest"] is None or oldest < _archive_state["oldest"]):
                _archive_state["oldest"] = oldest
        if page_oldest < stop_ts or not cursor:
            break
        time.sleep(0.1)
    return saved


def _archive_worker(days: int):
    stop_ts = time.time() - days * 86400
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_crawl_channel, ch, stop_ts) for ch in ARCHIVE_CHANNELS]
            for future in futures:
                future.result()
    except Exception as exc:  # noqa: BLE001
        with _archive_lock:
            _archive_state["error"] = str(exc)
    finally:
        with _archive_lock:
            _archive_state["running"] = False
            _archive_state["done_at"] = time.time()


def ensure_archive(days: int = ARCHIVE_DAYS) -> bool:
    """归档覆盖不足时启动后台回溯（首次约数分钟），返回是否已启动"""
    with _archive_lock:
        if _archive_state["running"]:
            return False
        if _archive_state["done_at"] and time.time() - _archive_state["done_at"] < 600:
            return False
        span = news_archive_span()
        need_since = time.time() - (days - 1) * 86400
        if span["count"] and (span["min"] or 0) <= need_since:
            return False
        _archive_state.update(running=True, started=time.time(), error="",
                              oldest=span["min"], done_at=0)
    threading.Thread(target=_archive_worker, args=(days,), daemon=True).start()
    return True


def archive_meta():
    ensure_archive()
    span = news_archive_span()
    with _archive_lock:
        running = _archive_state["running"]
        oldest = _archive_state["oldest"]
        error = _archive_state["error"]
    fmt_day = lambda ts: datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""  # noqa: E731
    return {
        "running": running,
        "count": span["count"] or 0,
        "earliest": fmt_day(span["min"]),
        "latest": fmt_day(span["max"]),
        "crawled_to": fmt_day(oldest),
        "error": error,
    }


def archive_payload(days: int = 90, impact: str = "全部", scope: str = "全部",
                    per_day: int = 6, day: str = ""):
    """近 N 天归档：默认每天只返回最重要的 per_day 条，day 用于展开单日全量"""
    ensure_archive()
    end = time.time()
    start = end - days * 86400
    items = news_archive_query(
        int(start), int(end), impact, scope,
        limit=600, per_day=None if day else per_day, day=day or None)
    for item in items:
        item["time_text"] = datetime.fromtimestamp(item["ts"]).strftime("%m-%d %H:%M")
    stats = news_archive_stats(int(start), int(end))
    return {
        "items": items,
        "stats": stats,
        "days": days,
        "day": day,
        "archive": archive_meta(),
    }


def fetch_news(limit: int = 22):
    def producer():
        rows = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(_wscn, "global-channel"), pool.submit(_wscn, "a-stock-channel"),
                       pool.submit(_sina)]
        for future in futures:
            try:
                value = future.result()
            except Exception:  # noqa: BLE001
                continue
            rows.extend(value[0] if isinstance(value, tuple) else value)
        seen, unique = set(), []
        for row in sorted(rows, key=lambda r: r["time"], reverse=True):
            text = row["title"]
            if any(word in text for word in BLOCK_WORDS) and not any(w in text for w in LEADER_WORDS):
                continue
            key = re.sub(r"[^\w\u4e00-\u9fa5]", "", text)[:24]
            if key in seen:
                continue
            seen.add(key)
            unique.append(_classify(row))
        picked = [r for r in unique if r["level"] >= 2][:limit]
        if len(picked) < 8:
            picked = unique[:limit]
        groups = {"国内": [], "国际": []}
        for item in picked:
            groups[item["scope"]].append(item)
        upcoming = [i for i in picked if i["upcoming"]]
        past = [i for i in picked if not i["upcoming"]]
        try:
            news_archive_put([_archive_row(i) for i in picked])
        except Exception:  # noqa: BLE001
            pass
        ensure_archive()
        return {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "items": picked,
            "groups": groups,
            "upcoming": upcoming,
            "past": past,
            "counts": {**{k: len(v) for k, v in groups.items()},
                       "upcoming": len(upcoming), "past": len(past)},
        }

    return cached_call("news", NEWS_TTL, producer)
