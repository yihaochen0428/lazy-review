"""懒得复盘 - FastAPI 服务入口"""
import threading
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import market
from . import eastmoney as em
from .analysis import NOISE_CONCEPTS
from .config import STATIC_DIR, LLM_API_KEY, LLM_MODEL
from .db import init_db


def _warmup():
    try:
        market.build_dashboard()
    except Exception:  # noqa: BLE001
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    threading.Thread(target=_warmup, daemon=True).start()
    yield


app = FastAPI(title="懒得复盘 API", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def no_cache_static(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def fail(message):
    return JSONResponse({"error": str(message)}, status_code=502)


@app.get("/")
def index_page():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "llm": bool(LLM_API_KEY)}


@app.get("/api/meta")
def meta():
    return {
        "name": "懒得复盘",
        "indices": em.INDICES,
        "llm": {"enabled": bool(LLM_API_KEY), "model": LLM_MODEL if LLM_API_KEY else "rule-engine"},
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.get("/api/dashboard")
def dashboard(date: str = Query(default=None), force: bool = Query(default=False)):
    try:
        return market.build_dashboard(date=date, force=force)
    except Exception as exc:  # noqa: BLE001
        return fail(f"复盘数据生成失败：{exc}")


@app.post("/api/refresh")
def refresh(date: str = Query(default=None)):
    try:
        payload = market.build_dashboard(date=date, force=True)
        return {"ok": True, "date": payload["date"], "updated_at": payload["updated_at"]}
    except Exception as exc:  # noqa: BLE001
        return fail(f"刷新失败：{exc}")


@app.get("/api/sectors")
def sectors(type: str = Query(default="industry"), sort: str = Query(default="f3"),
            limit: int = Query(default=30, ge=5, le=100)):
    try:
        return market.sectors(type, sort, limit)
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


@app.get("/api/sector/{code}")
def sector_detail(code: str, sort: str = Query(default="f3")):
    try:
        return market.sector_detail(code, sort)
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


@app.get("/api/stock/{code}")
def stock(code: str, klt: int = Query(default=101)):
    try:
        return market.stock_detail(code, klt)
    except Exception as exc:  # noqa: BLE001
        return fail(f"个股数据获取失败：{exc}")


@app.get("/api/index/{code}")
def index_analysis(code: str, klt: int = Query(default=101)):
    try:
        return market.index_detail(code, klt)
    except Exception as exc:  # noqa: BLE001
        return fail(f"指数分析失败：{exc}")


@app.get("/api/news")
def news_page():
    try:
        return market.news_page()
    except Exception as exc:  # noqa: BLE001
        return fail(f"新闻获取失败：{exc}")


@app.post("/api/news/analyze")
def news_analyze(item: dict):
    try:
        return market.news_analyze(item)
    except Exception as exc:  # noqa: BLE001
        return fail(f"新闻分析失败：{exc}")


@app.get("/api/abnormal")
def abnormal():
    try:
        return market.abnormal_list()
    except Exception as exc:  # noqa: BLE001
        return fail(f"异动监控数据获取失败：{exc}")


@app.get("/api/search")
def search(q: str = Query(default="")):
    try:
        return market.search_stocks(q)
    except Exception as exc:  # noqa: BLE001
        return fail(f"搜索失败：{exc}")


@app.get("/api/quotes")
def quotes(codes: str = Query(default="")):
    try:
        return market.watch_quotes([c for c in codes.split(",") if c])
    except Exception as exc:  # noqa: BLE001
        return fail(f"行情获取失败：{exc}")


@app.get("/api/history")
def history():
    try:
        return market.history()
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))
