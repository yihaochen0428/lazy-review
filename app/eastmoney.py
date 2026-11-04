"""东方财富公开行情接口封装（仅做数据归一化，不含业务逻辑）"""
import json
import math
import random
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .db import cached_call
from .config import QUOTE_TTL, KLINE_TTL, CONCEPT_TTL

# 对数据源友好：全局最多 4 个并发，请求之间保持最小间隔
_SEMAPHORE = threading.Semaphore(4)
_PACE_LOCK = threading.Lock()
_LAST_REQUEST = [0.0]
MIN_INTERVAL = 0.08
# 东方财富整体不可用时熔断 5 分钟，避免每次请求都重试拖慢页面
_DOWN_UNTIL = [0.0]


def _is_down() -> bool:
    return time.time() < _DOWN_UNTIL[0]


def _mark_down():
    _DOWN_UNTIL[0] = time.time() + 300

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}
UT_QUOTE = "fa5fd1943c7b386f172d6893dbfba10b"
UT_CLIST = "bd1d9ddb04089700cf9c27f6f7426281"
UT_POOL = "7eea3edcaed734bea9cbfc24409ed989"

TENCENT_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Referer": "https://gu.qq.com/",
}

SINA_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Referer": "https://finance.sina.com.cn/",
}
SINA_COUNT = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
              "Market_Center.getHQNodeStockCount?node=hs_a")
SINA_LIST = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData?page={page}&num=100&sort=changepercent&asc=0"
             "&node=hs_a&symbol=&_s_r_a=page")
SINA_NODE_LIST = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                  "Market_Center.getHQNodeData?page={page}&num=100&sort=changepercent&asc=0"
                  "&node={node}&symbol=&_s_r_a=page")
SINA_INDUSTRY = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
SINA_CONCEPT = "https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php?param=class"
SINA_TOP = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData?page=1&num=100&sort=changepercent&asc=0&node=hs_a"
            "&symbol=&_s_r_a=page")

QUOTE = "https://push2.eastmoney.com/api/qt"
POOL = "https://push2ex.eastmoney.com"
HIS = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

ALL_A_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

INDICES = [
    {"code": "000001", "secid": "1.000001", "name": "上证指数", "short": "上证"},
    {"code": "399001", "secid": "0.399001", "name": "深证成指", "short": "深成指"},
    {"code": "399006", "secid": "0.399006", "name": "创业板指", "short": "创业板"},
    {"code": "000688", "secid": "1.000688", "name": "科创50", "short": "科创50"},
]


class DataError(RuntimeError):
    pass


def num(value, default=None):
    if value is None or value == "-" or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pace():
    with _PACE_LOCK:
        wait = MIN_INTERVAL - (time.time() - _LAST_REQUEST[0])
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST[0] = time.time()


def _get_json(url: str, timeout: int = 15, retries: int = 3):
    if _is_down():
        raise DataError("东方财富接口熔断中")
    last = None
    for attempt in range(retries + 1):
        try:
            _pace()
            with _SEMAPHORE:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.6 * (attempt + 1) + random.uniform(0, 0.3))
    _mark_down()
    raise DataError(f"请求失败: {last}")


def _get_text(url: str, timeout: int = 15, retries: int = 2, encoding: str = "gbk"):
    last = None
    for attempt in range(retries + 1):
        try:
            _pace()
            with _SEMAPHORE:
                req = urllib.request.Request(url, headers=TENCENT_HEADERS)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.read().decode(encoding, "ignore")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise DataError(f"备用数据源请求失败: {last}")


def _get_sina(url: str, timeout: int = 12, retries: int = 2, encoding: str = "utf-8"):
    last = None
    for attempt in range(retries + 1):
        try:
            _pace()
            with _SEMAPHORE:
                req = urllib.request.Request(url, headers=SINA_HEADERS)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.read().decode(encoding, "ignore")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise DataError(f"备用行情源请求失败: {last}")


def tencent_symbol(code: str) -> str:
    if code.startswith("920") or code.startswith("8") or code.startswith("4"):
        return f"bj{code}"
    return ("sh" if code[:1] in ("6", "9") else "sz") + code


def _tencent_quotes(symbols):
    """腾讯行情兜底：返回 symbol -> 字段数组"""
    url = "https://qt.gtimg.cn/q=" + ",".join(symbols)
    text = _get_text(url)
    result = {}
    for line in text.split(";"):
        line = line.strip()
        if not line.startswith("v_") or "=" not in line:
            continue
        symbol, payload = line.split("=", 1)
        fields = payload.strip().strip('"').split("~")
        if len(fields) > 38:
            result[symbol[2:]] = fields
    return result


def _tencent_indices():
    symbols = ["sh000001", "sz399001", "sz399006", "sh000688"]
    quotes = _tencent_quotes(symbols)
    result = []
    for symbol, item in zip(symbols, INDICES):
        fields = quotes.get(symbol)
        if not fields:
            continue
        result.append({
            "code": item["code"],
            "secid": item["secid"],
            "name": fields[1] or item["name"],
            "short": item["short"],
            "price": num(fields[3]),
            "change": num(fields[31]),
            "change_pct": num(fields[32]),
            "amount": (num(fields[37]) or 0) * 1e4,
            "up": None,
            "down": None,
        })
    if not result:
        raise DataError("指数备用数据源为空")
    return result


def _tencent_kline(secid: str, klt: int, limit: int):
    market, code = secid.split(".")
    symbol = ("sh" if market == "1" else "sz") + code
    period = {101: "day", 102: "week", 103: "month"}.get(klt, "day")
    url = f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},{period},,,{limit},qfq"
    payload = json.loads(_get_text(url, encoding="utf-8"))
    node = (payload.get("data") or {}).get(symbol) or {}
    rows = node.get(period) or node.get(f"qfq{period}") or []
    bars = []
    prev_close = None
    for row in rows:
        if len(row) < 6:
            continue
        date, open_, close, high, low, volume = row[0], num(row[1]), num(row[2]), num(row[3]), num(row[4]), num(row[5])
        change = round(close - prev_close, 2) if prev_close else 0
        pct = round(change / prev_close * 100, 2) if prev_close else 0
        bars.append({
            "date": date, "open": open_, "close": close, "high": high, "low": low,
            "volume": volume, "amount": None, "amplitude": round((high - low) / prev_close * 100, 2) if prev_close else 0,
            "change_pct": pct, "change": change, "turnover": None,
        })
        prev_close = close
    if not bars:
        raise DataError("K线备用数据源为空")
    return {"code": code, "name": "", "prev_close": prev_close, "bars": bars}


def _bucket_key(date: str, klt: int) -> str:
    if klt == 103:
        return date[:7]
    from datetime import datetime as _dt
    try:
        week = _dt.strptime(date, "%Y-%m-%d").isocalendar()
        return f"{week[0]}-W{week[1]:02d}"
    except ValueError:
        return date


def _sina_kline(secid: str, klt: int, limit: int):
    """新浪 K 线兜底：日线直接取，周/月由日线聚合"""
    market, code = secid.split(".")
    symbol = ("sh" if market == "1" else "sz") + code
    mult = {101: 1, 102: 5, 103: 21}.get(klt, 1)
    # 新浪日线单次最多可返回约 1800 根，月/周线由日线聚合，取够历史才能保证均线与缠论拟合
    want = min(limit * mult + 20, 1800)
    url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
           f"?symbol={symbol}&scale=240&ma=no&datalen={want}")
    rows = json.loads(_get_sina(url))
    raw = []
    for row in rows or []:
        close = num(row.get("close"))
        if close is None:
            continue
        raw.append({
            "date": row.get("day"),
            "open": num(row.get("open")) or close,
            "close": close,
            "high": num(row.get("high")) or close,
            "low": num(row.get("low")) or close,
            "volume": num(row.get("volume")) or 0,
        })
    if not raw:
        raise DataError("K线备用数据源为空")
    if mult > 1:
        grouped = []
        for row in raw:
            key = _bucket_key(row["date"], klt)
            if grouped and grouped[-1]["key"] == key:
                last = grouped[-1]
                last["high"] = max(last["high"], row["high"])
                last["low"] = min(last["low"], row["low"])
                last["close"] = row["close"]
                last["volume"] += row["volume"]
                last["date"] = row["date"]
            else:
                grouped.append({"key": key, "date": row["date"], "open": row["open"],
                                "close": row["close"], "high": row["high"], "low": row["low"],
                                "volume": row["volume"]})
        raw = grouped
    bars = []
    prev_close = None
    for row in raw[-limit:]:
        change = round(row["close"] - prev_close, 2) if prev_close else 0
        pct = round(change / prev_close * 100, 2) if prev_close else 0
        amplitude = round((row["high"] - row["low"]) / prev_close * 100, 2) if prev_close else 0
        bars.append({
            "date": row["date"], "open": row["open"], "close": row["close"],
            "high": row["high"], "low": row["low"], "volume": row["volume"],
            "amount": None, "amplitude": amplitude, "change_pct": pct,
            "change": change, "turnover": None,
        })
        prev_close = row["close"]
    return {"code": code, "name": "", "prev_close": prev_close, "bars": bars}


def _kline_fallback(secid: str, klt: int, limit: int):
    # 当前腾讯 K 线接口频繁 501，优先新浪，腾讯作为次选
    try:
        return _sina_kline(secid, klt, limit)
    except DataError:
        return _tencent_kline(secid, klt, limit)


def market_prefix(code: str) -> str:
    return "1" if code[:1] in ("6", "9") else "0"


# ---------------------------------------------------------------- 指数
def _fetch_indices_raw():
    secids = ",".join(item["secid"] for item in INDICES)
    url = (f"{QUOTE}/ulist.np/get?secids={secids}"
           f"&fields=f2,f3,f4,f6,f12,f14,f104,f105&fltt=2&invt=2&ut={UT_QUOTE}")
    data = _get_json(url).get("data") or {}
    return data.get("diff") or []


def fetch_indices():
    return cached_call("indices", QUOTE_TTL, _fetch_indices_normalized)


def _fetch_indices_normalized():
    try:
        rows = _fetch_indices_raw()
    except DataError:
        return _tencent_indices()
    by_code = {str(r.get("f12")): r for r in rows}
    result = []
    for item in INDICES:
        row = by_code.get(item["code"], {})
        result.append({
            "code": item["code"],
            "secid": item["secid"],
            "name": item["name"],
            "short": item["short"],
            "price": num(row.get("f2")),
            "change": num(row.get("f4")),
            "change_pct": num(row.get("f3")),
            "amount": num(row.get("f6")),
            "up": num(row.get("f104")),
            "down": num(row.get("f105")),
        })
    return result


# ---------------------------------------------------------------- 全市场涨跌家数
def _breadth_page(pn: int, pages: dict) -> dict:
    if pn in pages:
        return pages[pn]
    url = (f"{QUOTE}/clist/get?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3"
           f"&fs={ALL_A_FS}&fields=f12,f3&ut={UT_CLIST}")
    data = _get_json(url).get("data") or {}
    data.setdefault("diff", [])
    pages[pn] = data
    return data


def fetch_breadth():
    """用二分法在涨跌幅降序列表里定位正负分界页，精确统计涨/跌/平家数"""
    return cached_call("breadth", QUOTE_TTL, _fetch_breadth_raw)


def _fetch_breadth_raw():
    try:
        return _fetch_breadth_eastmoney()
    except DataError:
        return _fetch_breadth_sina()


def _fetch_breadth_eastmoney():
    pages_cache = {}
    first = _breadth_page(1, pages_cache)
    total = int(first.get("total") or 0)
    if not total:
        raise DataError("全市场行情为空")
    pages = math.ceil(total / 100)

    def first_positive(pn: int) -> bool:
        diff = _breadth_page(pn, pages_cache).get("diff") or []
        return bool(diff) and (num(diff[0].get("f3"), 0) or 0) > 0

    # 二分找最后一个「首页为正」的页
    lo, hi, boundary = 1, pages, pages + 1
    if not first_positive(1):
        boundary = 1
    else:
        while lo <= hi:
            mid = (lo + hi) // 2
            if first_positive(mid):
                boundary = mid
                lo = mid + 1
            else:
                hi = mid - 1
        boundary += 1

    up = 0
    down = 0
    if boundary <= pages:
        diff = _breadth_page(boundary, pages_cache).get("diff") or []
        up = (boundary - 1) * 100 + sum(1 for r in diff if (num(r.get("f3"), 0) or 0) > 0)
        down = (pages - boundary) * 100 + sum(1 for r in diff if (num(r.get("f3"), 0) or 0) < 0)
    else:
        up = total
    flat = max(total - up - down, 0)
    return {"up": up, "down": down, "flat": flat, "total": total}


def _fetch_breadth_sina():
    """东方财富不可用时的兜底：新浪 A 股列表同样按涨跌幅降序分页，二分统计"""
    pages_cache = {}
    total = int(num(_get_sina(SINA_COUNT).strip().strip('"'), 0) or 0)
    if not total:
        raise DataError("备用涨跌家数数据为空")
    pages = math.ceil(total / 100)

    def page_rows(pn: int):
        if pn not in pages_cache:
            try:
                rows = json.loads(_get_sina(SINA_LIST.format(page=pn)) or "[]")
            except ValueError:
                rows = []
            pages_cache[pn] = rows if isinstance(rows, list) else []
        return pages_cache[pn]

    def pct(row):
        return num(row.get("changepercent"), 0) or 0

    def first_positive(pn: int) -> bool:
        rows = page_rows(pn)
        return bool(rows) and pct(rows[0]) > 0

    lo, hi, boundary = 1, pages, pages + 1
    if not first_positive(1):
        boundary = 1
    else:
        while lo <= hi:
            mid = (lo + hi) // 2
            if first_positive(mid):
                boundary = mid
                lo = mid + 1
            else:
                hi = mid - 1
        boundary += 1

    if boundary <= pages:
        rows = page_rows(boundary)
        up = (boundary - 1) * 100 + sum(1 for r in rows if pct(r) > 0)
        down = (pages - boundary) * 100 + sum(1 for r in rows if pct(r) < 0)
    else:
        up = total
        down = 0
    flat = max(total - up - down, 0)
    return {"up": up, "down": down, "flat": flat, "total": total}


# ---------------------------------------------------------------- 涨停/跌停/炸板池
def _fmt_time(value):
    if not value:
        return "-"
    text = str(int(value)).zfill(6)
    return f"{text[:2]}:{text[2:4]}"


def _pool_url(kind: str, date: str, size: int) -> str:
    endpoint = {"zt": "getTopicZTPool", "dt": "getTopicDTPool", "zb": "getTopicZBPool"}[kind]
    sort = {"zt": "fbt:asc", "dt": "fund:asc", "zb": "fbt:asc"}[kind]
    return (f"{POOL}/{endpoint}?ut={UT_POOL}&dpt=wz.ztzt&Pageindex=0&pagesize={size}"
            f"&sort={urllib.parse.quote(sort)}&date={date}")


def _normalize_pool_item(kind: str, item: dict) -> dict:
    base = {
        "code": str(item.get("c") or ""),
        "market": int(item.get("m") or 0),
        "name": str(item.get("n") or "").strip(),
        "price": round((num(item.get("p")) or 0) / 1000, 2),
        "change_pct": round(num(item.get("zdp"), 0) or 0, 2),
        "amount": num(item.get("amount"), 0),
        "turnover": round(num(item.get("hs"), 0) or 0, 2),
        "float_cap": num(item.get("ltsz")),
        "industry": str(item.get("hybk") or ""),
    }
    if kind == "zt":
        base.update({
            "board": int(num(item.get("lbc"), 1) or 1),
            "seal_time": _fmt_time(item.get("fbt")),
            "last_seal_time": _fmt_time(item.get("lbt")),
            "break_times": int(num(item.get("zbc"), 0) or 0),
            "seal_fund": num(item.get("fund")),
            "stat_days": int((item.get("zttj") or {}).get("days") or 0),
            "stat_count": int((item.get("zttj") or {}).get("ct") or 0),
            "status": "sealed",
        })
    elif kind == "zb":
        base.update({
            "limit_price": round((num(item.get("ztp")) or 0) / 1000, 2),
            "break_times": int(num(item.get("zbc"), 0) or 1),
            "seal_time": _fmt_time(item.get("fbt")),
            "amplitude": round(num(item.get("zf"), 0) or 0, 2),
            "stat_days": int((item.get("zttj") or {}).get("days") or 0),
            "stat_count": int((item.get("zttj") or {}).get("ct") or 0),
            "status": "broken",
        })
    else:
        base.update({
            "seal_time": _fmt_time(item.get("lbt")),
            "break_times": int(num(item.get("oc"), 0) or 0),
            "seal_fund": num(item.get("fba")),
            "pe": num(item.get("pe")),
            "stat_days": int(num(item.get("days"), 0) or 0),
            "status": "limit_down",
        })
    return base


def fetch_pool(kind: str, date: str, ttl=None):
    """date 形如 20261104"""
    cache_key = f"pool:{kind}:{date}"
    if ttl is None:
        ttl = QUOTE_TTL

    def producer():
        data = _get_json(_pool_url(kind, date, 400)).get("data") or {}
        items = [_normalize_pool_item(kind, it) for it in (data.get("pool") or [])]
        if kind == "zt":
            items = [it for it in items if it["name"] and "ST" not in it["name"].upper()]
        return {"total": int(data.get("tc") or 0), "items": items, "date": date}

    return cached_call(cache_key, ttl, producer)


# ---------------------------------------------------------------- 板块
def _board_fs(board_type: str) -> str:
    return "m:90+t:3" if board_type == "concept" else "m:90+t:2"


def _sina_board_list(board_type: str):
    """东方财富板块接口不可用时的兜底：新浪行业/概念板块快照"""
    url = SINA_INDUSTRY if board_type == "industry" else SINA_CONCEPT
    text = _get_sina(url, encoding="gbk")
    start = text.find("{")
    if start < 0:
        raise DataError("备用板块数据为空")
    payload = json.loads(text[start:].strip())
    rows = []
    for key, raw in payload.items():
        parts = str(raw).split(",")
        if len(parts) < 12:
            continue
        symbol = parts[8] or ""
        rows.append({
            "code": key,
            "name": parts[1] or "",
            "price": num(parts[3]),
            "change_pct": round(num(parts[5], 0) or 0, 2),
            "amount": num(parts[7], 0),
            "turnover": None,
            "up": None,
            "down": None,
            "leader": parts[12] if len(parts) > 12 else "",
            "leader_code": symbol[2:] if symbol[:2] in ("sh", "sz", "bj") else symbol,
            "leader_pct": round(num(parts[9], 0) or 0, 2),
            "main_flow": None,
        })
    if not rows:
        raise DataError("备用板块数据为空")
    return rows


def fetch_boards(board_type: str, sort: str = "f3", limit: int = 30):
    cache_key = f"boards:{board_type}:{sort}:{limit}"

    def producer():
        try:
            url = (f"{QUOTE}/clist/get?pn=1&pz=100&po=1&np=1&fltt=2&invt=2&fid={sort}"
                   f"&fs={_board_fs(board_type)}"
                   f"&fields=f12,f14,f2,f3,f6,f8,f104,f105,f128,f140,f136,f62&ut={UT_CLIST}")
            diff = (_get_json(url).get("data") or {}).get("diff") or []
        except DataError:
            rows = _sina_board_list(board_type)
            key = {"f6": "amount", "f62": "main_flow"}.get(sort, "change_pct")
            if any(r.get(key) is not None for r in rows):
                rows.sort(key=lambda x: x.get(key) or -1e18, reverse=True)
            else:
                rows.sort(key=lambda x: x["change_pct"], reverse=True)
            return rows[:limit]
        items = []
        for r in diff:
            items.append({
                "code": str(r.get("f12")),
                "name": str(r.get("f14") or ""),
                "price": num(r.get("f2")),
                "change_pct": round(num(r.get("f3"), 0) or 0, 2),
                "amount": num(r.get("f6"), 0),
                "turnover": round(num(r.get("f8"), 0) or 0, 2),
                "up": int(num(r.get("f104"), 0) or 0),
                "down": int(num(r.get("f105"), 0) or 0),
                "leader": str(r.get("f128") or ""),
                "leader_code": str(r.get("f140") or ""),
                "leader_pct": round(num(r.get("f136"), 0) or 0, 2),
                "main_flow": num(r.get("f62"), 0),
            })
        items.sort(key=lambda x: x["change_pct"], reverse=True)
        return items[:limit]

    return cached_call(cache_key, QUOTE_TTL, producer)


def fetch_board_stocks(code: str, sort: str = "f3", limit: int = 100):
    cache_key = f"boardstocks:{code}:{sort}:{limit}"

    def producer():
        try:
            url = (f"{QUOTE}/clist/get?pn=1&pz=100&po=1&np=1&fltt=2&invt=2&fid={sort}"
                   f"&fs=b:{code}&fields=f12,f14,f2,f3,f6,f8,f10,f62,f20,f21&ut={UT_CLIST}")
            data = _get_json(url).get("data") or {}
        except DataError:
            rows = _sina_board_stocks(code)
            return {"total": len(rows), "items": rows[:limit]}
        items = []
        for r in data.get("diff") or []:
            items.append({
                "code": str(r.get("f12")),
                "name": str(r.get("f14") or ""),
                "price": num(r.get("f2")),
                "change_pct": round(num(r.get("f3"), 0) or 0, 2),
                "amount": num(r.get("f6"), 0),
                "turnover": round(num(r.get("f8"), 0) or 0, 2),
                "volume_ratio": round(num(r.get("f10"), 0) or 0, 2),
                "main_flow": num(r.get("f62"), 0),
                "total_cap": num(r.get("f20")),
                "float_cap": num(r.get("f21")),
            })
        return {"total": int(data.get("total") or len(items)), "items": items[:limit]}

    return cached_call(cache_key, QUOTE_TTL, producer)


def _sina_board_stocks(node: str, limit: int = 100):
    url = (f"{SINA_NODE_LIST.format(page=1, node=node)}")
    rows = json.loads(_get_sina(url) or "[]")
    items = []
    for r in rows if isinstance(rows, list) else []:
        items.append({
            "code": str(r.get("code") or ""),
            "name": str(r.get("name") or ""),
            "price": num(r.get("trade")),
            "change_pct": round(num(r.get("changepercent"), 0) or 0, 2),
            "amount": num(r.get("amount"), 0),
            "turnover": round(num(r.get("turnoverratio"), 0) or 0, 2),
            "volume_ratio": None,
            "main_flow": None,
            "total_cap": (num(r.get("mktcap"), 0) or 0) * 1e4,
            "float_cap": (num(r.get("nmc"), 0) or 0) * 1e4,
        })
    items.sort(key=lambda x: x["change_pct"], reverse=True)
    return items[:limit]


def fetch_board_meta(code: str):
    def producer():
        try:
            url = (f"{QUOTE}/stock/get?secid=90.{code}&fields=f57,f58,f43,f3,f4,f6,f8,f104,f105"
                   f"&fltt=2&invt=2&ut={UT_QUOTE}")
            data = _get_json(url).get("data") or {}
        except DataError:
            rows = _sina_board_list("concept") + _sina_board_list("industry")
            row = next((r for r in rows if r["code"] == code), None)
            if not row:
                raise
            return {k: row[k] for k in ("code", "name", "price", "change_pct", "amount", "turnover", "up", "down")}
        return {
            "code": code,
            "name": str(data.get("f58") or ""),
            "price": num(data.get("f43")),
            "change_pct": round(num(data.get("f3"), 0) or 0, 2),
            "amount": num(data.get("f6"), 0),
            "turnover": round(num(data.get("f8"), 0) or 0, 2),
            "up": int(num(data.get("f104"), 0) or 0),
            "down": int(num(data.get("f105"), 0) or 0),
        }

    return cached_call(f"boardmeta:{code}", QUOTE_TTL, producer)


# ---------------------------------------------------------------- K 线
def fetch_kline(secid: str, klt: int = 101, limit: int = 260, fqt: int = 1):
    cache_key = f"kline:{secid}:{klt}:{limit}:{fqt}"

    def producer():
        try:
            url = (f"{HIS}?secid={secid}&klt={klt}&fqt={fqt}&end=20500101&lmt={limit}"
                   f"&fields1=f1,f2,f3,f4,f5,f6"
                   f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&ut={UT_QUOTE}")
            data = _get_json(url).get("data") or {}
        except DataError:
            return _kline_fallback(secid, klt, limit)
        bars = []
        for line in data.get("klines") or []:
            parts = line.split(",")
            if len(parts) < 11:
                continue
            bars.append({
                "date": parts[0],
                "open": num(parts[1]),
                "close": num(parts[2]),
                "high": num(parts[3]),
                "low": num(parts[4]),
                "volume": num(parts[5]),
                "amount": num(parts[6]),
                "amplitude": num(parts[7]),
                "change_pct": num(parts[8]),
                "change": num(parts[9]),
                "turnover": num(parts[10]),
            })
        if not bars:
            return _kline_fallback(secid, klt, limit)
        return {
            "code": str(data.get("code") or ""),
            "name": str(data.get("name") or ""),
            "prev_close": num(data.get("preKPrice")),
            "bars": bars,
        }

    return cached_call(cache_key, KLINE_TTL, producer)


SINA_MINUTE = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
               "?symbol={symbol}&scale={scale}&ma=no&datalen={n}")


def fetch_minute_kline(secid: str, scale: int = 5, limit: int = 240, days: int = 1):
    """分时 K 线（新浪分钟线）：默认取最近一个交易日的 5 分钟线，附带日内均价"""
    cache_key = f"mink:{secid}:{scale}:{limit}:{days}"

    def producer():
        market, code = secid.split(".")
        symbol = ("sh" if market == "1" else "sz") + code
        rows = json.loads(_get_sina(SINA_MINUTE.format(symbol=symbol, scale=scale, n=limit)))
        raw = []
        for row in rows or []:
            close = num(row.get("close"))
            if close is None:
                continue
            raw.append({
                "date": str(row.get("day") or ""),
                "open": num(row.get("open")) or close,
                "close": close,
                "high": num(row.get("high")) or close,
                "low": num(row.get("low")) or close,
                "volume": num(row.get("volume")) or 0,
                "amount": num(row.get("amount")) or 0,
            })
        if not raw:
            raise DataError("分时数据为空")
        dates = []
        for row in raw:
            day = row["date"][:10]
            if day not in dates:
                dates.append(day)
        keep = dates[-days:]
        prev_close = None
        for row in raw:
            if row["date"][:10] == keep[0]:
                break
            prev_close = row["close"]
        bars = []
        base = prev_close
        for row in raw:
            if row["date"][:10] not in keep:
                continue
            pct = round((row["close"] / base - 1) * 100, 2) if base else 0
            bars.append({
                **row,
                "time": row["date"][11:16],
                "change_pct": pct,
                "change": round(row["close"] - base, 2) if base else 0,
            })
            base = row["close"]
        return {"bars": bars, "prev_close": prev_close, "days": keep}

    return cached_call(cache_key, 120, producer)


# ---------------------------------------------------------------- 财务与行业（F10）
F10_FINANCE = ("https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew"
               "?type=0&code={prefix}{code}")
DATACENTER_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
DATACENTER_HEADERS = {**HEADERS, "Referer": "https://data.eastmoney.com/"}
INDUSTRY_REPORT_API = "https://reportapi.eastmoney.com/report/list"


def _get_data_json(url: str, timeout: int = 15, retries: int = 2):
    """东财数据中心接口走独立 host，不受 push2 熔断影响"""
    last = None
    for attempt in range(retries + 1):
        try:
            _pace()
            with _SEMAPHORE:
                req = urllib.request.Request(url, headers=DATACENTER_HEADERS)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    text = resp.read().decode("utf-8", "ignore")
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return text
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.4 * (attempt + 1))
    raise DataError(f"数据中心接口请求失败: {last}")


def fetch_finance(code: str):
    """东财 F10 主要财务指标（最近 8 个报告期）"""
    def producer():
        prefix = "SH" if code[:1] in ("6", "9") else "SZ"
        payload = _get_f10(F10_FINANCE.format(prefix=prefix, code=code))
        rows = []
        for r in (payload.get("data") or [])[:8]:
            rows.append({
                "date": str(r.get("REPORT_DATE") or "")[:10],
                "name": str(r.get("REPORT_DATE_NAME") or "").strip(),
                "type": str(r.get("REPORT_TYPE") or "").strip(),
                "revenue": num(r.get("TOTALOPERATEREVE")),
                "revenue_yoy": num(r.get("TOTALOPERATEREVETZ")),
                "net_profit": num(r.get("PARENTNETPROFIT")),
                "net_profit_yoy": num(r.get("PARENTNETPROFITTZ")),
                "deduct_profit": num(r.get("KCFJCXSYJLR")),
                "deduct_yoy": num(r.get("KCFJCXSYJLRTZ")),
                "gross_margin": num(r.get("XSMLL")),
                "net_margin": num(r.get("XSJLL")),
                "roe": num(r.get("ROEJQ")),
                "debt_ratio": num(r.get("ZCFZL")),
                "eps": num(r.get("EPSJB")),
                "bps": num(r.get("BPS")),
                "cash_flow_ps": num(r.get("MGJYXJJE")),
            })
        return rows

    return cached_call(f"finance:{code}", 43200, producer)


def fetch_calendar(start: str, end: str, limit: int = 60):
    """东财财经日历：未来会议 / 经济数据 / 其他事件"""
    cache_key = f"calendar:{start}:{end}:{limit}"

    def producer():
        filter_str = f"(START_DATE>='{start}')(START_DATE<='{end}')"
        query = {
            "reportName": "RPT_CPH_FECALENDAR",
            "columns": "START_DATE,END_DATE,FE_CODE,FE_NAME,FE_TYPE,CONTENT,STD_TYPE_CODE,SPONSOR_NAME,CITY",
            "pageNumber": "1", "pageSize": str(limit),
            "sortColumns": "START_DATE", "sortTypes": "1",
            "filter": filter_str, "source": "WEB", "client": "WEB",
        }
        url = DATACENTER_API + "?" + urllib.parse.urlencode(query)
        payload = _get_data_json(url)
        rows = []
        for r in ((payload.get("result") or {}).get("data") or []):
            name = str(r.get("FE_NAME") or "").strip()
            if not name:
                continue
            rows.append({
                "date": str(r.get("START_DATE") or "")[:10],
                "end_date": str(r.get("END_DATE") or "")[:10],
                "name": name,
                "type": str(r.get("FE_TYPE") or "其他"),
                "content": str(r.get("CONTENT") or "").strip()[:200],
                "city": str(r.get("CITY") or ""),
                "sponsor": str(r.get("SPONSOR_NAME") or ""),
                "source": "东方财富财经日历",
                "url": "https://data.eastmoney.com/cjrl/",
            })
        return rows

    return cached_call(cache_key, 3600, producer)


UNUSUAL_COLUMNS = ("SECUCODE,IS_SYSDATE,SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,MARKET_CODE,"
                   "CHANGE_RATE,MAX_DAYS,DEVUATION_VALUE,CHANGE_RATE_TARGET,IS_HAPPEN,UNUSUAL_TYPE")


def fetch_unusual(filter_expr: str = "", limit: int = 100):
    """东财严重异动监控：10 日 / 30 日收盘价涨跌幅偏离值是否触发监管阈值"""
    cache_key = f"unusual:v1:{filter_expr}:{limit}"

    def producer():
        query = {
            "reportName": "RPT_WATCH_UNUSUAL_FLUCTUATE",
            "columns": UNUSUAL_COLUMNS,
            "quoteColumns": "",
            "filter": filter_expr,
            "pageNumber": "1", "pageSize": str(limit),
            "sortColumns": "TRADE_DATE", "sortTypes": "-1",
            "source": "SECURITIES", "client": "APP",
        }
        url = DATACENTER_API + "?" + urllib.parse.urlencode(query)
        payload = _get_data_json(url)
        rows = []
        for r in ((payload.get("result") or {}).get("data") or []):
            code = str(r.get("SECURITY_CODE") or "")
            if not code:
                continue
            rows.append({
                "code": code,
                "name": str(r.get("SECURITY_NAME_ABBR") or "").strip(),
                "date": str(r.get("TRADE_DATE") or "")[:10],
                "days": num(r.get("MAX_DAYS")),
                "dev": num(r.get("DEVUATION_VALUE")),
                "change_pct": num(r.get("CHANGE_RATE")),
                "target": num(r.get("CHANGE_RATE_TARGET")),
                "happened": str(r.get("IS_HAPPEN") or "0") == "1",
                "today": str(r.get("IS_SYSDATE") or "0") == "1",
                "rule": str(r.get("UNUSUAL_TYPE") or ""),
            })
        return rows

    return cached_call(cache_key, 300, producer)


def fetch_main_business(code: str):
    """东财 F10 主营构成：按产品拆分收入占比，用于判断公司真正的主营业务"""
    cache_key = f"mainbiz:{code}"

    def producer():
        prefix = "SH" if code[:1] in ("6", "9") else "BJ" if code[:1] in ("4", "8") else "SZ"
        url = ("https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax"
               f"?code={prefix}{code}")
        payload = _get_json(url)
        rows = payload.get("zygcfx") or []
        if not rows:
            return {"report_date": "", "products": [], "scope": ""}
        latest = str(rows[0].get("REPORT_DATE") or "")[:10]
        products = []
        for r in rows:
            if str(r.get("MAINOP_TYPE")) != "2":
                continue
            if str(r.get("REPORT_DATE") or "")[:10] != latest:
                continue
            name = str(r.get("ITEM_NAME") or "").strip()
            if not name or "补充" in name:
                continue
            ratio = num(r.get("MBI_RATIO"))
            products.append({
                "name": name,
                "ratio": round(ratio * 100, 1) if ratio is not None else None,
                "income": num(r.get("MAIN_BUSINESS_INCOME")),
            })
        products.sort(key=lambda p: -(p["income"] or 0))
        scope = ""
        for r in payload.get("zyfw") or []:
            scope = str(r.get("BUSINESS_SCOPE") or "").strip()
            if scope:
                break
        return {"report_date": latest, "products": products, "scope": scope}

    return cached_call(cache_key, 7 * 24 * 3600, producer)


def fetch_industry_reports(keyword: str, days: int = 120, limit: int = 8):
    """行业研报（东财研报中心 qType=1），按行业关键词过滤"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []

    def producer():
        from datetime import datetime as _dt, timedelta
        end = _dt.now()
        begin = end - timedelta(days=days)
        url = (f"{INDUSTRY_REPORT_API}?pageSize=40&pageNo=1&industryCode=*&qType=1"
               f"&beginTime={begin:%Y-%m-%d}&endTime={end:%Y-%m-%d}")
        payload = _get_data_json(url)
        if isinstance(payload, str):
            payload = json.loads(payload)
        rows = []
        for r in payload.get("data") or []:
            title = str(r.get("title") or "").strip()
            industry = str(r.get("industryName") or "").strip()
            if not title:
                continue
            if keyword not in industry and keyword not in title:
                continue
            info = str(r.get("infoCode") or "")
            rows.append({
                "title": title,
                "org": str(r.get("orgSName") or r.get("orgName") or ""),
                "date": str(r.get("publishDate") or "")[:10],
                "industry": industry,
                "url": f"https://data.eastmoney.com/report/zw_industry.jshtml?infocode={info}" if info else "",
            })
        rows.sort(key=lambda x: x["date"], reverse=True)
        return rows[:limit]

    return cached_call(f"indrep:{keyword}", 43200, producer)


# ---------------------------------------------------------------- 个股基础信息与概念
def fetch_stock_meta(code: str):
    def producer():
        try:
            url = (f"{QUOTE}/stock/get?secid={market_prefix(code)}.{code}"
                   f"&fields=f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f116,f117,f127,f128,f129,"
                   f"f162,f167,f168,f169,f170,f171&fltt=2&invt=2&ut={UT_QUOTE}")
            data = _get_json(url).get("data") or {}
        except DataError:
            return _tencent_stock_meta(code)
        if not data:
            return _tencent_stock_meta(code)
        concepts = [c for c in str(data.get("f129") or "").split(",") if c and c != "-"]
        return {
            "code": str(data.get("f57") or code),
            "name": str(data.get("f58") or ""),
            "price": num(data.get("f43")),
            "high": num(data.get("f44")),
            "low": num(data.get("f45")),
            "open": num(data.get("f46")),
            "prev_close": num(data.get("f60")),
            "volume": num(data.get("f47")),
            "amount": num(data.get("f48")),
            "volume_ratio": num(data.get("f50")),
            "total_cap": num(data.get("f116")),
            "float_cap": num(data.get("f117")),
            "industry": str(data.get("f127") or ""),
            "region": str(data.get("f128") or ""),
            "concepts": concepts,
            "pe": num(data.get("f162")),
            "pb": num(data.get("f167")),
            "turnover": num(data.get("f168")),
            "change": num(data.get("f169")),
            "change_pct": num(data.get("f170")),
            "amplitude": num(data.get("f171")),
        }

    return cached_call(f"meta:{code}", QUOTE_TTL, producer)


def _tencent_stock_meta(code: str):
    quotes = _tencent_quotes([tencent_symbol(code)])
    fields = next(iter(quotes.values()), None)
    if not fields:
        raise DataError("个股备用数据源为空")
    return {
        "code": code,
        "name": fields[1] or "",
        "price": num(fields[3]),
        "prev_close": num(fields[4]),
        "open": num(fields[5]),
        "volume": num(fields[6]),
        "change": num(fields[31]),
        "change_pct": num(fields[32]),
        "high": num(fields[33]),
        "low": num(fields[34]),
        "amount": (num(fields[37]) or 0) * 1e4,
        "turnover": num(fields[38]),
        "pe": num(fields[39]),
        "pb": num(fields[46]),
        "float_cap": (num(fields[44]) or 0) * 1e8,
        "total_cap": (num(fields[45]) or 0) * 1e8,
        "volume_ratio": None,
        "amplitude": None,
        "industry": "",
        "region": "",
        "concepts": [],
    }


F10_CONCEPTION = ("https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax"
                  "?code={prefix}{code}")
F10_HEADERS = {**HEADERS, "Referer": "https://emweb.securities.eastmoney.com/"}


def _get_f10(url: str, timeout: int = 12, retries: int = 2):
    """F10 接口走独立 host，不受 push2 熔断影响"""
    last = None
    for attempt in range(retries + 1):
        try:
            _pace()
            with _SEMAPHORE:
                req = urllib.request.Request(url, headers=F10_HEADERS)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8", "ignore"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.4 * (attempt + 1))
    raise DataError(f"F10 接口请求失败: {last}")


def fetch_stock_boards(code: str):
    """个股东财板块归属（F10 ssbk：行业板块 + 概念板块），缓存 7 天"""
    def producer():
        prefix = "SH" if code[:1] in ("6", "9") else "SZ"
        data = _get_f10(F10_CONCEPTION.format(prefix=prefix, code=code))
        rows = []
        for item in data.get("ssbk") or []:
            name = str(item.get("BOARD_NAME") or "").strip()
            if not name:
                continue
            rows.append({
                "code": str(item.get("BOARD_CODE") or ""),
                "name": name,
                "rank": int(num(item.get("BOARD_RANK"), 99) or 99),
            })
        return rows

    return cached_call(f"stkboards:{code}", CONCEPT_TTL, producer)


def fetch_board_members(code: str):
    """板块成分股（仅代码/名称，用于涨停股概念反查，缓存 7 天）"""

    def producer():
        try:
            url = (f"{QUOTE}/clist/get?pn=1&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3"
                   f"&fs=b:{code}&fields=f12,f14&ut={UT_CLIST}")
            data = _get_json(url).get("data") or {}
            return [{"code": str(r.get("f12")), "name": str(r.get("f14") or "")}
                    for r in (data.get("diff") or [])]
        except DataError:
            try:
                return [{"code": s["code"], "name": s["name"]} for s in _sina_board_stocks(code)]
            except DataError:
                return []

    return cached_call(f"boardmembers:{code}", CONCEPT_TTL, producer)


def fetch_concept_map(stock_codes, concept_boards, top_n=24):
    """通过热门概念板块成分股反查个股所属概念，显著减少逐个股票请求"""
    targets = set(stock_codes)
    mapping = {code: [] for code in targets}
    boards = [b for b in (concept_boards or []) if b.get("code")][:top_n]
    if not boards:
        return mapping

    def load(board):
        try:
            return board["name"], fetch_board_members(board["code"])
        except DataError:
            return board["name"], []

    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, members in pool.map(load, boards):
            for item in members:
                if item["code"] in targets:
                    mapping[item["code"]].append(name)
    return mapping


def fetch_gainers(limit: int = 80):
    """今日涨幅榜（新浪 A 股列表，按涨跌幅降序）"""
    def producer():
        rows = json.loads(_get_sina(SINA_TOP) or "[]")
        out = []
        for r in rows if isinstance(rows, list) else []:
            out.append({
                "code": str(r.get("code") or ""),
                "name": str(r.get("name") or ""),
                "price": num(r.get("trade")),
                "change_pct": round(num(r.get("changepercent"), 0) or 0, 2),
                "amount": num(r.get("amount"), 0),
                "turnover": round(num(r.get("turnoverratio"), 0) or 0, 2),
                "float_cap": (num(r.get("nmc"), 0) or 0) * 1e4,
            })
        return out[:limit]

    return cached_call(f"gainers:{limit}", QUOTE_TTL, producer)


def suggest_stocks(key: str, limit: int = 8):
    """新浪联想搜索：代码 / 名称 / 拼音首字母"""
    key = (key or "").strip()
    if not key:
        return []
    url = ("https://suggest3.sinajs.cn/suggest/type=11,12,13,15&key="
           + urllib.parse.quote(key))
    try:
        text = _get_text(url, encoding="gbk")
    except DataError:
        return []
    payload = text.split('"', 1)[1] if '"' in text else ""
    rows = []
    for part in payload.split(";"):
        fields = part.split(",")
        if len(fields) < 5:
            continue
        code = fields[2].strip()
        name = (fields[0] or fields[4] or "").strip()
        if len(code) == 6 and code.isdigit():
            rows.append({"code": code, "name": name, "market": fields[3][:2]})
    return rows[:limit]


def fetch_quotes(codes):
    """批量查询现价与涨跌幅（东财失败时切换腾讯行情）"""
    codes = [c for c in codes if c]
    if not codes:
        return {}
    out = {}
    failed = False
    for start in range(0, len(codes), 40):
        chunk = codes[start:start + 40]
        secids = ",".join(f"{market_prefix(c)}.{c}" for c in chunk)
        url = (f"{QUOTE}/ulist.np/get?secids={secids}&fields=f2,f3,f5,f6,f8,f12,f14,f18,f62,f184"
               f"&fltt=2&invt=2&ut={UT_QUOTE}")
        try:
            diff = (_get_json(url).get("data") or {}).get("diff") or []
        except DataError:
            failed = True
            continue
        for r in diff:
            out[str(r.get("f12"))] = {
                "price": num(r.get("f2")),
                "change_pct": num(r.get("f3")),
                "name": str(r.get("f14") or ""),
                "volume": num(r.get("f5")),
                "amount": num(r.get("f6")),
                "turnover": num(r.get("f8")),
                "prev_close": num(r.get("f18")),
                "main_flow": num(r.get("f62")),
                "main_flow_pct": num(r.get("f184")),
            }
    if failed or not out:
        try:
            quotes = _tencent_quotes([tencent_symbol(c) for c in codes])
            for code in codes:
                fields = quotes.get(tencent_symbol(code))
                if not fields:
                    continue
                out[code] = {
                    "price": num(fields[3]),
                    "change_pct": num(fields[32]),
                    "name": fields[1] or "",
                    "prev_close": num(fields[4]),
                    "volume": num(fields[6]),
                    # 腾讯 fields[37] 成交额(万元)、fields[38] 换手率(%)
                    "amount": (num(fields[37]) or 0) * 10000 or None,
                    "turnover": num(fields[38]),
                }
        except DataError:
            pass
    return out
