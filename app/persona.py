"""个股股性：日内节律画像 + 20 日滚动做T经验库

- profile()  ：哪些时段容易见日内高点/低点、是否早盘冲高午后回落，用于展示
- experience()：逐日重放该股最近 20 个交易日的做T信号，统计「策略 × 方向 × 时段 × 量能」
  的真实胜率，作为加权先验；每 20 个交易日重新总结一次，并把上一版结论按样本量融合进来
  （滚动压缩，下一周期在上一周期基础上判断）。
"""
from datetime import datetime

from . import eastmoney as em
from . import tscore
from .db import cached_call, cache_get_stale
from .indicators import macd
from .intraday import t_points
from .tenv import _by_date, _resolve, _series

DAYS = 20
MEM_TTL = DAYS * 86400  # 20 个自然日后重新总结（约 14 个交易日，宁可早于 20 个交易日）
MIN_DAYS = 5


def _dataset(secid, days=DAYS):
    """取最近 days 个交易日的 5 分钟线，并算出各时段的历史均量（量能基准）"""
    by_day, prev = _series(secid, days + 1)
    dates = sorted(by_day)[-days:]
    if not dates:
        return {"by_day": {}, "prev": {}, "base_vol": [None] * 8, "dates": []}
    buckets = [[] for _ in range(8)]
    for day in dates:
        for bar in by_day[day]:
            idx = tscore.bin_index(bar.get("time"))
            volume = bar.get("volume") or 0
            if idx is not None and volume > 0:
                buckets[idx].append(volume)
    base_vol = [round(sum(v) / len(v), 2) if v else None for v in buckets]
    return {
        "by_day": {d: by_day[d] for d in dates},
        "prev": {d: prev.get(d) for d in dates},
        "base_vol": base_vol,
        "dates": dates,
    }


def _day_features(day_bars, prev_close):
    if not day_bars:
        return None
    closes = [b.get("close") or 0 for b in day_bars]
    highs = [b.get("high") or b.get("close") or 0 for b in day_bars]
    lows = [b.get("low") or b.get("close") or 0 for b in day_bars]
    open_ = day_bars[0].get("open") or closes[0]
    close = closes[-1]
    high, low = max(highs), min(lows)
    if not open_ or not close or high <= 0:
        return None
    high_bar = day_bars[highs.index(high)]
    low_bar = day_bars[lows.index(low)]
    high_bin = tscore.bin_index(high_bar.get("time"))
    low_bin = tscore.bin_index(low_bar.get("time"))
    morning = [b for b in day_bars if (b.get("time") or "15:00") <= "11:30"]
    mid = (morning[-1].get("close") or close) if morning else close
    return {
        "open": open_, "close": close, "high": high, "low": low,
        "high_bin": high_bin, "low_bin": low_bin,
        "gap": (open_ / prev_close - 1) * 100 if prev_close else 0.0,
        "morning_ret": (mid / open_ - 1) * 100,
        "afternoon_ret": (close / mid - 1) * 100 if mid else 0.0,
        "amplitude": (high - low) / prev_close * 100 if prev_close else (high - low) / open_ * 100,
        # 早盘（10:30 前）见高点且收盘较高点回落 0.6% 以上
        "spike_fade": bool(high_bin is not None and high_bin <= 1 and close <= high * 0.994),
        "low_open_high_close": bool(prev_close and open_ < prev_close and close > open_),
    }


def _pct(count, total):
    return round(count / total * 100, 1) if total else 0.0


def _top_bins(counter, total, take=2):
    ranked = sorted(range(8), key=lambda i: -counter[i])[:take]
    return [{"label": tscore.BIN_LABELS[i], "prob": _pct(counter[i], total)} for i in ranked]


def profile_from(ds):
    by_day = ds.get("by_day") or {}
    feats = []
    for day in sorted(by_day):
        feat = _day_features(by_day[day], (ds.get("prev") or {}).get(day))
        if feat:
            feats.append(feat)
    n = len(feats)
    if n < MIN_DAYS:
        return {"ok": False, "sample_days": n, "reason": "分时历史不足，暂不生成股性画像"}
    high_cnt = [0] * 8
    low_cnt = [0] * 8
    for f in feats:
        if f["high_bin"] is not None:
            high_cnt[f["high_bin"]] += 1
        if f["low_bin"] is not None:
            low_cnt[f["low_bin"]] += 1
    morning_high = sum(high_cnt[:2])
    morning_low = sum(low_cnt[:2])
    spike_fade = sum(1 for f in feats if f["spike_fade"])
    afternoon_weak = sum(1 for f in feats if f["afternoon_ret"] < 0)
    avg_morning = sum(f["morning_ret"] for f in feats) / n
    avg_afternoon = sum(f["afternoon_ret"] for f in feats) / n
    avg_amplitude = sum(f["amplitude"] for f in feats) / n
    high_prob = [_pct(c, n) for c in high_cnt]
    low_prob = [_pct(c, n) for c in low_cnt]
    best_buy = _top_bins(low_cnt, n)
    best_sell = _top_bins(high_cnt, n)
    if _pct(spike_fade, n) >= 55:
        style = "早盘冲高回落型"
    elif high_prob[7] >= 40:
        style = "尾盘拉高型"
    elif _pct(morning_low, n) >= 55 and avg_afternoon > 0:
        style = "下午走强型"
    else:
        style = "全天震荡型"
    note = (f"近 {n} 个交易日，{_pct(morning_high, n)}% 的日子在 10:30 前见到当日高点，"
            f"其中 {_pct(spike_fade, n)}% 随后回落；低吸较优时段 {best_buy[0]['label']}，"
            f"高抛较优时段 {best_sell[0]['label']}。")
    if best_buy[0]["label"] == best_sell[0]["label"]:
        note = (f"近 {n} 个交易日，{_pct(morning_high, n)}% 的日子在 10:30 前见到当日高点，"
                f"其中 {_pct(spike_fade, n)}% 随后回落；"
                f"{best_buy[0]['label']} 既是低点也是高点多发区（波动最大），低吸与高抛都集中在该时段。")
    return {
        "ok": True,
        "sample_days": n,
        "style": style,
        "high_bin_prob": high_prob,
        "low_bin_prob": low_prob,
        "bins": list(tscore.BIN_LABELS),
        "morning_high_prob": _pct(morning_high, n),
        "morning_low_prob": _pct(morning_low, n),
        "spike_fade_prob": _pct(spike_fade, n),
        "afternoon_weak_prob": _pct(afternoon_weak, n),
        "avg_amplitude": round(avg_amplitude, 2),
        "avg_morning_ret": round(avg_morning, 2),
        "avg_afternoon_ret": round(avg_afternoon, 2),
        "best_buy_bins": best_buy,
        "best_sell_bins": best_sell,
        "note": note,
    }


def _fuse(cell, old, prior_weight):
    """把上一周期结论按样本量融合进本周期统计"""
    rate = _pct(cell["wins"], cell["samples"])
    avg = cell["ret"] / cell["samples"] if cell["samples"] else 0.0
    if old and old.get("win_rate") is not None:
        rate = round(rate * (1 - prior_weight) + old["win_rate"] * prior_weight, 1)
    if old and old.get("avg_return") is not None:
        avg = round(avg * (1 - prior_weight) + old["avg_return"] * prior_weight, 2)
    return {
        "samples": cell["samples"],
        "wins": cell["wins"],
        "win_rate": round(rate, 1),
        "avg_return": round(avg, 2),
    }


def _experience_raw(secid, code):
    ds = _dataset(secid)
    by_day = ds.get("by_day") or {}
    base_vol = ds.get("base_vol") or [None] * 8
    groups, coarse = {}, {}
    samples = 0
    used_days = 0
    for day in sorted(by_day):
        day_bars = by_day[day]
        prev_close = (ds.get("prev") or {}).get(day)
        if len(day_bars) < 6 or not prev_close:
            continue
        highs = [b.get("high") or b.get("close") or 0 for b in day_bars]
        lows = [b.get("low") or b.get("close") or 0 for b in day_bars]
        if prev_close and (max(highs) - min(lows)) / prev_close * 100 < 0.5:
            continue  # 振幅过小的死水日，信号无统计意义
        closes = [b.get("close") or 0 for b in day_bars]
        dif = macd(closes)["dif"]
        points = t_points(day_bars, prev_close, dif, max_points=40)["points"]
        if not points:
            continue
        used_days += 1
        for point in points:
            b = tscore.bin_index(point.get("time"))
            energy = tscore.energy_state(day_bars, point.get("index") or 0,
                                         base_vol[b] if b is not None else None)
            result, ret = _resolve(day_bars, point["index"], point)
            samples += 1
            fine = f"{point['strategy_key']}|{point['type']}|{b}|{energy['label']}"
            rough = f"{point['strategy_key']}|{point['type']}"
            for bucket, key in ((groups, fine), (coarse, rough)):
                cell = bucket.setdefault(key, {"samples": 0, "wins": 0, "ret": 0.0})
                cell["samples"] += 1
                cell["wins"] += 1 if result == "win" else 0
                cell["ret"] += ret
    if samples < 20:
        return None
    return groups, coarse, samples, used_days, sorted(by_day)


def _finalize(raw, prior):
    groups, coarse, samples, used_days, dates = raw
    prior_weight = 0.4 if (prior or {}).get("samples", 0) >= 50 else 0.25
    old_groups = (prior or {}).get("groups") or {}
    old_coarse = (prior or {}).get("coarse") or {}
    fine = {k: _fuse(v, old_groups.get(k), prior_weight) for k, v in groups.items()}
    rough = {k: _fuse(v, old_coarse.get(k), prior_weight) for k, v in coarse.items()}
    weights = {}
    for key in {k.split("|")[0] for k in rough}:
        cells = [v for k, v in coarse.items() if k.split("|")[0] == key]
        total = sum(c["samples"] for c in cells)
        wins = sum(c["wins"] for c in cells)
        if total < 10:
            weights[key] = 1.0
            continue
        rate = wins / total * 100
        weights[key] = round(max(0.8, min(1.3, 1 + (rate - 50) / 50 * 0.3)), 2)
    cycles = int((prior or {}).get("cycles") or 0) + 1
    start = (prior or {}).get("start") or dates[0]
    return {
        "ok": True,
        "version": 2,
        "start": start,
        "end": dates[-1],
        "days": used_days,
        "samples": samples,
        "groups": fine,
        "coarse": rough,
        "strategy_weights": weights,
        "prior_source": "carried" if prior else "fresh",
        "cycles": cycles,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def experience(code, secid):
    """20 日滚动经验库：每 20 个自然日重新总结，并与上一版按样本量融合"""
    key = f"tmem:v2:{code}"

    def producer():
        prior = cache_get_stale(key)
        raw = _experience_raw(secid, code)
        if not raw:
            return (prior or {}) if (prior or {}).get("ok") else {"ok": False, "samples": 0,
                                                                   "reason": "历史信号样本不足"}
        return _finalize(raw, prior)

    return cached_call(key, MEM_TTL, producer) or {"ok": False, "samples": 0, "reason": "经验库不可用"}


def prior_win_rate(exp, strategy_key, side, time_text, energy_label):
    """按「策略 × 方向 × 时段 × 量能」取历史同类胜率，样本不足时回落到粗粒度"""
    if not exp or not exp.get("ok"):
        return None, 0
    b = tscore.bin_index(time_text)
    candidates = (f"{strategy_key}|{side}|{b}|{energy_label}", f"{strategy_key}|{side}")
    for key in candidates:
        cell = (exp.get("groups") or {}).get(key) or (exp.get("coarse") or {}).get(key)
        if cell and cell.get("samples", 0) >= 3:
            return cell.get("win_rate"), cell.get("samples")
    return None, 0


def strategy_weight(exp, strategy_key):
    if not exp or not exp.get("ok"):
        return 1.0
    return (exp.get("strategy_weights") or {}).get(strategy_key, 1.0)


def snapshot(secid, code):
    """一次性取画像、经验库与时段量能基准，避免重复拉 20 日分时线"""
    ds = _dataset(secid)
    return {
        "profile": profile_from(ds),
        "experience": experience(code, secid),
        "base_vol": ds.get("base_vol") or [None] * 8,
    }
