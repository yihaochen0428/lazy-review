"""离线校验日K量价信号：分等级/分类型/双口径回测 + 抽样个股信号明细

运行：
    $env:PYTHONPATH="<repo>"
    .\\.venv\\Scripts\\python.exe scripts\\validate_daily_signals.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import eastmoney as em
from app import ksig


def pct(value):
    return "-" if value is None else f"{value:.1f}%"


def show(stat, label):
    if not stat or not stat.get("samples"):
        print(f"  {label}: 样本 0")
        return
    print(f"  {label}: 样本 {stat['samples']} · 胜率 {pct(stat.get('win_rate'))} · 均值 {stat.get('avg_return')}%")


def main():
    result = ksig.backtest(days=60, force=True)
    print(f"== 买点信号回测（{result['days']} 日，样本池 {result['pool']} 只）")
    print(f"信号样本 {result['samples']}；跳过 {result['skipped']}")
    show(result["buy"]["baseline"], "基准 持有5日")

    for grade, stat in result["buy"]["by_grade"].items():
        print(f"\n[{grade}] 样本 {stat['samples']}")
        show(stat["t1"], "T+1")
        show(stat["t3"], "T+3")
        show(stat["t5"], "T+5")
        swing = stat["swing"]
        print(f"  波段: 样本 {swing['samples']} · 胜率 {pct(swing['win_rate'])} · 均值 {swing['avg_return']}%"
              f" · 平均 {swing['avg_days']} 日 · 盈亏比 {swing['payoff']} · {swing.get('exits')}")

    print("\n== 分类型")
    for row in result["buy"]["by_type"]:
        print(f"  {row['title']}: 样本 {row['samples']} · T+5 胜率 {pct(row['t5']['win_rate'])}"
              f" / 均值 {row['t5']['avg_return']}% · 波段胜率 {pct(row['swing']['win_rate'])}"
              f" / 均值 {row['swing']['avg_return']}%")

    print("\n== 卖点")
    sell = result["sell"]
    print(f"  样本 {sell['samples']} · 后 5 日下跌占比 {pct(sell['after5_down_ratio'])} · 均值 {sell['after5_avg']}%")
    for row in sell["by_type"]:
        print(f"  {row['title']}: 样本 {row['samples']} · 下跌占比 {pct(row['down_ratio'])} · 均值 {row['after5_avg']}%")

    print("\n== 个股抽样")
    os.makedirs("data", exist_ok=True)
    for code in ("002812", "300750", "600276"):
        secid = f"{em.market_prefix(code)}.{code}"
        bars = em.fetch_kline(secid, 101, 140)["bars"]
        res = ksig.detect(bars, env=ksig.env_score_live())
        print(f"\n{code} 共 {len(res['items'])} 条：{res['summary']}")
        for item in res["items"][-8:]:
            side = "买" if item["side"] == "buy" else "卖"
            print(f"  {item['date']} {side} {item['score']} 分 {item['grade']} · {item['type']} · {item['reason']}")
        with open(os.path.join("data", f"ksig_{code}.json"), "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
