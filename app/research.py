"""研报聚合：东方财富研报中心公开接口，统计近 1/3/6 个月观点与目标价"""
import json
import urllib.request
from datetime import datetime, timedelta

from .db import cached_call
from .eastmoney import num

REPORT_API = "https://reportapi.eastmoney.com/report/list"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/",
}
TTL = 86400


def _get(url: str, timeout: int = 12):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def fetch_reports(code: str):
    """近 200 天个股研报明细"""
    def producer():
        end = datetime.now()
        begin = end - timedelta(days=200)
        url = (f"{REPORT_API}?pageSize=60&pageNo=1&code={code}&industryCode=*"
               f"&rating=&ratingChange=&beginTime={begin:%Y-%m-%d}&endTime={end:%Y-%m-%d}"
               f"&fields=&qType=0&orgCode=&rcode=")
        payload = json.loads(_get(url))
        rows = []
        for r in payload.get("data") or []:
            date = str(r.get("publishDate") or "")[:10]
            if not date:
                continue
            rows.append({
                "date": date,
                "org": r.get("orgSName") or r.get("orgName") or "",
                "rating": r.get("emRatingName") or r.get("sRatingName") or "",
                "last_rating": r.get("lastEmRatingName") or "",
                "rating_change": r.get("ratingChange"),
                "target": num(r.get("indvAimPriceT")) or num(r.get("orgAimPriceT")),
                "title": r.get("title") or "",
                "eps": num(r.get("predictThisYearEps")),
                "pe": num(r.get("predictThisYearPe")),
            })
        rows.sort(key=lambda x: x["date"], reverse=True)
        return rows

    return cached_call(f"reports:{code}", TTL, producer)


def _parse_date(text: str):
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def summarize_reports(code: str, price: float = 0):
    rows = fetch_reports(code)
    today = datetime.now().date()
    windows = {"1m": 30, "3m": 90, "6m": 180}
    buckets = {}
    for key, days in windows.items():
        subset = []
        for r in rows:
            parsed = _parse_date(r["date"])
            if not parsed:
                continue
            if (today - parsed).days <= days:
                subset.append(r)
        targets = [r["target"] for r in subset if r["target"] and r["target"] > 1]
        ratings = {}
        for r in subset:
            if r["rating"]:
                ratings[r["rating"]] = ratings.get(r["rating"], 0) + 1
        buckets[key] = {
            "count": len(subset),
            "target_avg": round(sum(targets) / len(targets), 2) if targets else None,
            "target_high": round(max(targets), 2) if targets else None,
            "target_low": round(min(targets), 2) if targets else None,
            "ratings": sorted(ratings.items(), key=lambda kv: -kv[1]),
        }

    recent = rows[:8]
    upgrades = sum(1 for r in rows if r["rating_change"] == 1)
    downgrades = sum(1 for r in rows if r["rating_change"] == 2)
    target = buckets["3m"]["target_avg"] or buckets["6m"]["target_avg"]
    upside = round((target / price - 1) * 100, 2) if target and price else None

    if buckets["1m"]["count"] >= buckets["3m"]["count"] and buckets["3m"]["count"] > 0:
        trend = "近一个月机构关注度集中，观点更新频繁"
    elif buckets["1m"]["count"] == 0:
        trend = "近一个月暂无新研报，观点以存量为主"
    else:
        trend = "机构观点覆盖平稳，未见明显转向"

    if upgrades > downgrades and upgrades >= 2:
        trend += "，评级上调家数多于下调，态度偏积极。"
    elif downgrades > upgrades and downgrades >= 2:
        trend += "，出现评级下调，需警惕预期修正。"
    else:
        trend += "，评级调整较少，分歧有限。"

    return {
        "available": bool(rows),
        "windows": buckets,
        "recent": recent,
        "target_avg": target,
        "upside_pct": upside,
        "trend": trend,
        "upgrades": upgrades,
        "downgrades": downgrades,
        "total": len(rows),
    }
