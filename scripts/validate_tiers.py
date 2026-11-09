"""做T分层加权交叉验证：10 大盘 / 10 中盘 / 10 小盘

验证思路（无未来函数）：
1. 候选池 60 只，按流通市值排序，取前 10 / 中间 10 / 后 10 作为三档样本；
2. 每只股票取最近 25 个交易日的 5 分钟线，前 20 日用来算股性经验库与时段均量，
   最后 5 日作为验证窗口（股性先验只来自验证窗口之前的数据）；
3. 验证窗口内每日重放做T信号，用三种权重口径各取「强度最高的 3 个」信号，
   按动态目标价撮合，比较胜率与平均收益：
     base    基准：不做加权（只看策略基础胜率 + 多策略共振）
     uniform 统一权重：指数/量能/股性各 1/3，不分市值
     tiered  分层权重：大盘偏指数、小盘偏量能与股性（app/tscore.py TIERS）
4. 环境分用上证指数同期 5 分钟线还原（无板块代理，属于保守近似）。

用法（在项目根目录）：
    .\\.venv\\Scripts\\python.exe scripts\\validate_tiers.py
输出：控制台表格 + data/t_validation.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import eastmoney as em  # noqa: E402
from app import persona, tenv, tscore  # noqa: E402
from app.indicators import macd  # noqa: E402
from app.intraday import t_points  # noqa: E402

DAYS = 25       # 前 20 日训练 + 后 5 日验证
EVAL_DAYS = 5
TOP_N = 3

POOL = [
    "600519", "601318", "600036", "601398", "601288", "600900", "601899", "300750",
    "002594", "600030", "601012", "000858", "600276", "601888", "000333", "600887",
    "002415", "601601", "601628", "600028", "601088", "600188", "601166", "000001",
    "600000", "601169", "601328", "002460", "300124", "002241", "300274", "002050",
    "600809", "603288", "300059", "002714", "600438", "601138", "000725", "002027",
    "002812", "300496", "603596", "300896", "002472", "300627", "603986", "688981",
    "688111", "300454", "002439", "300679", "603501", "002371", "300308", "300394",
    "002156", "600584", "603160", "002138",
    # 小盘补充（流通市值多在 20~90 亿）
    "300700", "002903", "603178", "002863", "300885", "002896", "603331", "300819",
    "002971", "300906", "300843", "002988", "003008", "300961", "301004",
]

SCHEMES = {
    "base": {"env": 0.0, "vol": 0.0, "prior": 0.0},
    "uniform": {"env": 0.34, "vol": 0.33, "prior": 0.33},
}


def _exp_from(ds, days):
    """用训练日的信号构造经验库（逻辑同 persona._experience_raw，但不读缓存）"""
    by_day = ds.get("by_day") or {}
    base_vol = ds.get("base_vol") or [None] * 8
    groups, coarse, samples = {}, {}, 0
    for day in days:
        day_bars = by_day.get(day) or []
        prev_close = (ds.get("prev") or {}).get(day)
        if len(day_bars) < 6 or not prev_close:
            continue
        highs = [b.get("high") or b.get("close") or 0 for b in day_bars]
        lows = [b.get("low") or b.get("close") or 0 for b in day_bars]
        if (max(highs) - min(lows)) / prev_close * 100 < 0.5:
            continue
        closes = [b.get("close") or 0 for b in day_bars]
        dif = macd(closes)["dif"]
        for point in t_points(day_bars, prev_close, dif, max_points=40)["points"]:
            b = tscore.bin_index(point.get("time"))
            energy = tscore.energy_state(day_bars, point.get("index") or 0,
                                         base_vol[b] if b is not None else None)
            result, ret = tenv._resolve(day_bars, point["index"], point)
            samples += 1
            for bucket, key in ((groups, f"{point['strategy_key']}|{point['type']}|{b}|{energy['label']}"),
                                (coarse, f"{point['strategy_key']}|{point['type']}")):
                cell = bucket.setdefault(key, {"samples": 0, "wins": 0, "ret": 0.0})
                cell["samples"] += 1
                cell["wins"] += 1 if result == "win" else 0
                cell["ret"] += ret
    if samples < 20:
        return {"ok": False}
    out = {}
    for name, bucket in (("groups", groups), ("coarse", coarse)):
        out[name] = {
            k: {
                "samples": v["samples"],
                "wins": v["wins"],
                "win_rate": round(v["wins"] / v["samples"] * 100, 1),
                "avg_return": round(v["ret"] / v["samples"], 2),
            } for k, v in bucket.items()
        }
    weights = {}
    for key in {k.split("|")[0] for k in coarse}:
        cells = [v for k, v in coarse.items() if k.split("|")[0] == key]
        total = sum(c["samples"] for c in cells)
        wins = sum(c["wins"] for c in cells)
        weights[key] = 1.0 if total < 10 else round(
            max(0.8, min(1.3, 1 + (wins / total * 100 - 50) / 50 * 0.3)), 2)
    out.update({"ok": True, "strategy_weights": weights, "samples": samples})
    return out


def _index_env(day_bars, prev_close):
    state = tenv._state(day_bars, prev_close) if day_bars else None
    return (state or {}).get("score") or 0.0


def _collect(code, tier, weights):
    secid = f"{em.market_prefix(code)}.{code}"
    ds = persona._dataset(secid, DAYS)
    dates = ds.get("dates") or []
    if len(dates) <= EVAL_DAYS:
        return None
    train, evals = dates[:-EVAL_DAYS], dates[-EVAL_DAYS:]
    exp = _exp_from(ds, train)
    if not exp.get("ok"):
        return None
    idx_by_day, idx_prev = tenv._series("1.000001", DAYS + 1)
    by_day = ds.get("by_day") or {}
    base_vol = ds.get("base_vol") or [None] * 8
    rows = []
    for day in evals:
        day_bars = by_day.get(day) or []
        prev_close = (ds.get("prev") or {}).get(day)
        if len(day_bars) < 6 or not prev_close:
            continue
        env_score = _index_env(idx_by_day.get(day), idx_prev.get(day))
        closes = [b.get("close") or 0 for b in day_bars]
        dif = macd(closes)["dif"]
        points = t_points(day_bars, prev_close, dif, max_points=40)["points"]
        if not points:
            continue
        outcomes = {}
        for i, point in enumerate(points):
            b = tscore.bin_index(point.get("time"))
            energy = tscore.energy_state(day_bars, point.get("index") or 0,
                                         base_vol[b] if b is not None else None)
            prior_wr, prior_n = persona.prior_win_rate(
                exp, point.get("strategy_key"), point.get("type"), point.get("time"),
                energy.get("label"))
            ctx = {
                "env_score": env_score,
                "energy": energy,
                "bars": day_bars,
                "prior_wr": prior_wr,
                "prior_samples": prior_n,
                "strategy_weight": persona.strategy_weight(exp, point.get("strategy_key")),
            }
            for name, w in weights.items():
                scored = tscore.score_point(point, dict(ctx, weights=w))
                _, ret = tenv._resolve(day_bars, scored["index"], scored)
                outcomes[(name, i)] = (
                    "win" if ret > 0 else "loss", ret, scored["strength"], scored["type"])
        for name in weights:
            ranked = sorted(
                ((i, outcomes[(name, i)]) for i in range(len(points)) if (name, i) in outcomes),
                key=lambda item: -item[1][2])
            for rank, (_, (result, ret, strength, side)) in enumerate(ranked):
                rows.append({"code": code, "date": day, "scheme": name, "side": side,
                             "result": result, "return": ret, "strength": strength,
                             "rank": rank, "total": len(ranked)})
    return {"code": code, "tier": tier, "rows": rows}


def _summarize(rows):
    if not rows:
        return {"samples": 0, "win_rate": 0.0, "avg_return": 0.0}
    wins = sum(1 for r in rows if r["result"] == "win")
    return {
        "samples": len(rows),
        "win_rate": round(wins / len(rows) * 100, 1),
        "avg_return": round(sum(r["return"] for r in rows) / len(rows), 2),
    }


def main():
    metas = []
    for code in POOL:
        try:
            meta = em.fetch_stock_meta(code)
        except Exception:  # noqa: BLE001
            continue
        cap = meta.get("float_cap") or 0
        if cap:
            metas.append((code, cap))
    metas.sort(key=lambda item: -item[1])
    if len(metas) < 30:
        print(f"有效候选不足：{len(metas)}")
        return
    picks = [(code, cap, "大盘") for code, cap in metas[:10]]
    mid = len(metas) // 2
    picks += [(code, cap, "中盘") for code, cap in metas[mid - 5:mid + 5]]
    picks += [(code, cap, "小盘") for code, cap in metas[-10:]]
    print(f"候选 {len(metas)} 只，分层样本 {len(picks)} 只")

    results = {"generated_at": None, "tiers": {}, "stocks": []}
    from datetime import datetime
    results["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    for code, cap, tier in picks:
        tier_w = tscore.tier_of(cap)[1]
        weights = dict(SCHEMES, tiered=tier_w)
        try:
            got = _collect(code, tier, weights)
        except Exception as exc:  # noqa: BLE001
            print(f"{code} 跳过：{exc}")
            continue
        if not got:
            print(f"{code} 样本不足，跳过")
            continue
        print(f"{code} {tier} 完成，{len(got['rows'])} 条")
        results["stocks"].append({"code": code, "tier": tier, "float_cap": cap,
                                  "rows": len(got["rows"])})
        bucket = results["tiers"].setdefault(tier, {})
        for row in got["rows"]:
            bucket.setdefault(row["scheme"], []).append(row)

    def top3(rows):
        return [r for r in rows if r["rank"] < TOP_N]

    def first(rows):
        return [r for r in rows if r["rank"] == 0]

    def last(rows):
        return [r for r in rows if r["rank"] == r["total"] - 1]

    def scheme_rows(name, tiers=None):
        return [r for tier, schemes in results["tiers"].items()
                if tiers is None or tier in tiers for r in schemes.get(name, [])]

    summary = {}
    for tier, schemes in results["tiers"].items():
        base_rows = schemes.get("tiered") or []
        summary[tier] = {
            "signals": len(base_rows),
            "all": _summarize(base_rows),
            "top3": {n: _summarize(top3(schemes.get(n) or []))
                     for n in ("base", "uniform", "tiered")},
            "first": _summarize(first(base_rows)),
            "last": _summarize(last(base_rows)),
        }
    results["summary"] = summary

    def line(tier, label, cell):
        print("{:<8}{:<16}{:>8}{:>10}{:>12}".format(
            tier, label, cell.get("samples", 0),
            f"{cell.get('win_rate', 0)}%", f"{cell.get('avg_return', 0)}%"))

    print("\n【强度是否区分好坏：分层口径】")
    print("{:<8}{:<16}{:>8}{:>10}{:>12}".format("层级", "取样", "样本", "胜率", "平均收益"))
    for tier, cell in summary.items():
        line(tier, "全部信号", cell["all"])
        line(tier, f"最强 1 个", cell["first"])
        line(tier, f"最弱 1 个", cell["last"])
        line(tier, f"前 {TOP_N} 个", cell["top3"]["tiered"])
    line("合计", "全部信号", _summarize(scheme_rows("tiered")))
    line("合计", "最强 1 个", _summarize(first(scheme_rows("tiered"))))
    line("合计", "最弱 1 个", _summarize(last(scheme_rows("tiered"))))
    line("合计", f"前 {TOP_N} 个", _summarize(top3(scheme_rows("tiered"))))

    print(f"\n【三种权重口径：每日前 {TOP_N} 个信号】")
    print("{:<8}{:<16}{:>8}{:>10}{:>12}".format("层级", "口径", "样本", "胜率", "平均收益"))
    for tier, cell in summary.items():
        for name in ("base", "uniform", "tiered"):
            line(tier, name, cell["top3"][name])
    total = {}
    for name in ("base", "uniform", "tiered"):
        total[name] = _summarize(top3(scheme_rows(name)))
        line("合计", name, total[name])
    results["total_top3"] = total

    out = ROOT / "data" / "t_validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细已写入 {out}")


if __name__ == "__main__":
    main()
