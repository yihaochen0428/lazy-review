"""SQLite 缓存层：行情缓存 + 每日快照"""
import json
import sqlite3
import threading
import time

from .config import DB_PATH, DATA_DIR

_lock = threading.Lock()
_initialized = False


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    global _initialized
    with _lock:
        if _initialized:
            return
        with connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache ("
                " key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS snapshots ("
                " date TEXT PRIMARY KEY, built_at REAL NOT NULL, payload TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS news_archive ("
                " id TEXT PRIMARY KEY, ts INTEGER NOT NULL, date TEXT NOT NULL,"
                " title TEXT, summary TEXT, url TEXT, source TEXT, scope TEXT,"
                " impact TEXT, level INTEGER, tags TEXT, category TEXT, upcoming INTEGER)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_news_archive_ts ON news_archive(ts)")
        _initialized = True


def cache_get(key: str, ttl: float):
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT value, updated_at FROM cache WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    value, updated = json.loads(row[0]), row[1]
    if ttl is not None and time.time() - updated > ttl:
        return None
    return value


def cache_get_stale(key: str):
    """忽略过期时间，用于网络失败时的兜底"""
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def cache_set(key: str, value) -> None:
    init_db()
    payload = json.dumps(value, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            "INSERT INTO cache(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, payload, time.time()),
        )


def cached_call(key: str, ttl: float, producer):
    """读缓存，未命中则调用 producer 并写回；失败时用过期数据兜底"""
    value = cache_get(key, ttl)
    if value is not None:
        return value
    try:
        value = producer()
        cache_set(key, value)
        return value
    except Exception:
        stale = cache_get_stale(key)
        if stale is not None:
            return stale
        raise


def snapshot_get(date: str):
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT payload, built_at FROM snapshots WHERE date = ?", (date,)).fetchone()
    if not row:
        return None
    return {"payload": json.loads(row[0]), "built_at": row[1]}


def snapshot_put(date: str, payload) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO snapshots(date, built_at, payload) VALUES(?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET built_at = excluded.built_at, payload = excluded.payload",
            (date, time.time(), json.dumps(payload, ensure_ascii=False)),
        )


def snapshot_dates():
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT date, built_at FROM snapshots ORDER BY date DESC").fetchall()
    return [{"date": r[0], "built_at": r[1]} for r in rows]


# ---------------------------------------------------------------- 新闻归档
def news_archive_put(rows) -> int:
    """批量写入历史新闻（按 id 去重），返回新增条数"""
    if not rows:
        return 0
    init_db()
    payload = [
        (r["id"], int(r["ts"]), r["date"], r.get("title") or "", r.get("summary") or "",
         r.get("url") or "", r.get("source") or "", r.get("scope") or "国内",
         r.get("impact") or "中性", int(r.get("level") or 1),
         json.dumps(r.get("tags") or [], ensure_ascii=False), r.get("category") or "",
         1 if r.get("upcoming") else 0)
        for r in rows
    ]
    with connect() as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO news_archive VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
        return conn.total_changes - before


def news_archive_query(start_ts: int, end_ts: int, impact: str = None, scope: str = None,
                       limit: int = 800, per_day: int = None, day: str = None):
    """查询归档新闻；per_day 表示每天只取最重要的 N 条（按 level、时间倒序）"""
    init_db()
    cols = "ts,date,title,summary,url,source,scope,impact,level,tags,category,upcoming"
    where = ["ts BETWEEN ? AND ?"]
    params = [int(start_ts), int(end_ts)]
    if impact and impact != "全部":
        where.append("impact = ?")
        params.append(impact)
    if scope and scope != "全部":
        where.append("scope = ?")
        params.append(scope)
    if day:
        where.append("date = ?")
        params.append(day)
    cond = " AND ".join(where)
    if per_day:
        sql = (f"SELECT {cols} FROM (SELECT {cols},"
               " ROW_NUMBER() OVER (PARTITION BY date ORDER BY level DESC, ts DESC) AS rn"
               f" FROM news_archive WHERE {cond}) WHERE rn <= ? ORDER BY ts DESC")
        params.append(int(per_day))
    else:
        sql = f"SELECT {cols} FROM news_archive WHERE {cond} ORDER BY ts DESC LIMIT ?"
        params.append(int(limit))
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    keys = ("ts", "date", "title", "summary", "url", "source", "scope", "impact", "level",
            "tags", "category", "upcoming")
    items = []
    for row in rows:
        item = dict(zip(keys, row))
        item["tags"] = json.loads(item["tags"] or "[]")
        item["upcoming"] = bool(item["upcoming"])
        items.append(item)
    return items


def news_archive_stats(start_ts: int, end_ts: int):
    init_db()
    with connect() as conn:
        impacts = conn.execute(
            "SELECT impact, COUNT(*) FROM news_archive WHERE ts BETWEEN ? AND ? GROUP BY impact",
            (int(start_ts), int(end_ts))).fetchall()
        days = conn.execute(
            "SELECT date, COUNT(*) FROM news_archive WHERE ts BETWEEN ? AND ?"
            " GROUP BY date ORDER BY date DESC", (int(start_ts), int(end_ts))).fetchall()
    return {"impacts": {k: v for k, v in impacts},
            "days": [{"date": d, "count": c} for d, c in days],
            "total": sum(v for _, v in impacts)}


def news_archive_span():
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM news_archive").fetchone()
    return {"min": row[0], "max": row[1], "count": row[2]}
