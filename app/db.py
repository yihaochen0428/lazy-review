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
