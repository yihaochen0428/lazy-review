"""同花顺板块指数：任意个股的板块归属 + 板块日线

东方财富板块接口（push2/push2his）经常限流或被网络屏蔽，而原有板块联动依赖硬编码的
板块成分表，新加入的个股经常匹配不到。这里改用同花顺概念板块页 + 板块指数日线接口，
让任意个股都能自动挂上板块，无需为每只新股票补配置。
"""
import json
import re
import threading
import time
import urllib.request

from .db import cached_call

CONCEPT_URL = "http://basic.10jqka.com.cn/{code}/concept.html"
CONCEPT_LIST_URL = "http://q.10jqka.com.cn/gn/"
LIST_TTL = 6 * 3600
LINE_URL = "http://d.10jqka.com.cn/v6/line/48_{clid}/01/last.js"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "http://basic.10jqka.com.cn/",
}
BOARD_TTL = 7 * 24 * 3600
KLINE_TTL = 600
BOARD_LIMIT = 3

# 同花顺概念里混入的业绩/风格/资金类标签，与板块联动无关
_SKIP_WORDS = (
    "预增", "预减", "预亏", "扭亏", "摘帽", "脱帽", "贬值", "在线教育", "跨境电商",
    "融资融券", "送转", "增持", "回购", "举牌", "破净", "低价", "高价", "次新", "新股",
    "参股", "创投", "员工持股", "股权转让", "定增", "可转债", "中报", "年报", "季报",
    "业绩", "社保", "基金", "QFII", "机构", "百元", "小盘", "大盘", "风格", "MSCI",
    "富时", "标普", "高股息", "破发", "涨停", "跌停", "ST", "st", "同花顺",
)

_LOCK = threading.Lock()
_LAST = [0.0]


class ThsError(RuntimeError):
    pass


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pace(interval: float = 0.25) -> None:
    with _LOCK:
        wait = interval - (time.time() - _LAST[0])
        if wait > 0:
            time.sleep(wait)
        _LAST[0] = time.time()


def _get(url: str, encoding: str = "gbk", timeout: int = 12, retries: int = 2) -> str:
    last = None
    for attempt in range(retries + 1):
        try:
            _pace()
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode(encoding, "ignore")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.4 * (attempt + 1))
    raise ThsError(f"同花顺接口请求失败: {last}")


def concept_boards(limit: int = 400):
    """同花顺概念板块全量列表（名称 + clid），用于把未来事件映射到板块"""
    def producer():
        raw = _get(CONCEPT_LIST_URL, encoding="gbk")
        rows, seen = [], set()
        for code, name in re.findall(r'/gn/detail/code/(\d+)/"[^>]*>([^<]{1,20})</a>', raw):
            name = name.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            rows.append({"code": code, "name": name})
        return rows[:limit]

    return cached_call(f"thsgn:{limit}", LIST_TTL, producer)


def stock_boards(code: str, limit: int = BOARD_LIMIT):
    """个股所属概念板块（同花顺 clid + 板块名），任意个股通用"""
    def producer():
        text = _get(CONCEPT_URL.format(code=code))
        rows = []
        seen = set()
        for match in re.finditer(r'clid="(\d+)"[^>]*>\s*([^<]{1,30})', text):
            clid, name = match.group(1), match.group(2).strip()
            if not name or clid in seen or any(word in name for word in _SKIP_WORDS):
                continue
            seen.add(clid)
            rows.append({"code": clid, "name": name, "kind": "concept"})
        return rows[:limit]

    return cached_call(f"thsboards:{code}", BOARD_TTL, producer)


def board_kline(clid: str, limit: int = 60):
    """板块指数日线（含 OHLC），用于迷你 K 线与 0 轴判断"""
    def producer():
        text = _get(LINE_URL.format(clid=clid), encoding="utf-8")
        start, end = text.find("("), text.rfind(")")
        if start < 0 or end <= start:
            raise ThsError("板块 K 线返回格式异常")
        payload = json.loads(text[start + 1:end])
        bars = []
        for row in str(payload.get("data") or "").split(";"):
            parts = row.split(",")
            if len(parts) < 7 or len(parts[0]) != 8:
                continue
            open_, high, low, close = (_num(parts[1]), _num(parts[2]),
                                       _num(parts[3]), _num(parts[4]))
            if None in (open_, high, low, close) or not close:
                continue
            bars.append({
                "date": f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:]}",
                "open": open_, "high": high, "low": low, "close": close,
                "volume": _num(parts[5]), "amount": _num(parts[6]),
            })
        bars = bars[-limit:]
        for i, bar in enumerate(bars):
            base = bars[i - 1]["close"] if i else None
            bar["change_pct"] = round((bar["close"] / base - 1) * 100, 2) if base else 0.0
        if not bars:
            raise ThsError("板块 K 线为空")
        return {"code": clid, "name": str(payload.get("name") or ""), "bars": bars}

    return cached_call(f"thskline:{clid}:{limit}", KLINE_TTL, producer)
