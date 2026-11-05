"""懒得复盘 - 全局配置"""
import os
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "lazyreview.db"
STATIC_DIR = BASE_DIR / "static"

# 缓存时间（秒）
QUOTE_TTL = int(os.getenv("LAZY_QUOTE_TTL", "300"))       # 行情类
KLINE_TTL = int(os.getenv("LAZY_KLINE_TTL", "600"))       # K线
CONCEPT_TTL = int(os.getenv("LAZY_CONCEPT_TTL", "604800"))  # 个股概念（7天）
SNAPSHOT_TTL = int(os.getenv("LAZY_SNAPSHOT_TTL", "300"))  # 当日快照
HISTORY_SNAPSHOT_TTL = 86400

# 可选大模型（OpenAI 兼容接口）。未配置时使用内置规则引擎生成复盘文案
LLM_BASE_URL = os.getenv("LAZY_LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY = os.getenv("LAZY_LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LAZY_LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT = int(os.getenv("LAZY_LLM_TIMEOUT", "60"))


def is_trading_time(now=None) -> bool:
    """是否处于 A股连续竞价时段（不判节假日，仅用于决定行情缓存时长）"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return (9 * 60 + 15 <= minutes <= 11 * 60 + 30) or (13 * 60 <= minutes <= 15 * 60 + 5)
