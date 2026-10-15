"""模型用量与成本报告：聚合 backend-python/data/usage/llm.jsonl。

回答两个原先答不出的问题：
  「一次对话花多少 token / 多少钱」——按 trace id 聚合
  「哪个任务最贵 / 钱花在哪」——按 task 拆分

**账本里只有 token，金额在这里现算**。这样单价一变，历史数据可以重新定价；
把金额写进账本就会永久停在当时的价上。

单价默认**不配**（只报 token，不编造）。要报金额就设环境变量，形如：
    MODEL_PRICE_DEEPSEEK_V4_PRO=2.0/8.0     # 输入价/输出价，元每百万 token

用法：
    python scripts/cost_report.py                # 总览
    python scripts/cost_report.py --by-task      # 按任务
    python scripts/cost_report.py --top 10       # 最贵的 10 次对话
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USAGE = ROOT / "backend-python" / "data" / "usage" / "llm.jsonl"

# 单价表（元/百万 token）。留空 = 未配置。真实单价以服务商价格页为准，
# 不要在这里写死当事实；用环境变量按需覆盖即可。
PRICES: dict[str, tuple[float, float]] = {}


def price_of(model: str) -> tuple[float, float]:
    env = os.getenv(f"MODEL_PRICE_{model.upper().replace('-', '_')}")
    if env and "/" in env:
        try:
            a, b = env.split("/", 1)
            return float(a), float(b)
        except ValueError:
            print(f"  ⚠️ MODEL_PRICE_{model} 格式不对（应为 输入价/输出价）：{env}")
    return PRICES.get(model, (0.0, 0.0))


def load() -> list[dict]:
    if not USAGE.is_file():
        return []
    rows = []
    for line in USAGE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def cost_cents(rows: list[dict]) -> float:
    total = 0.0
    for r in rows:
        pin, pout = price_of(r.get("model", ""))
        total += (r["promptTokens"] * pin + r["completionTokens"] * pout) / 10000
    return total


def priced(rows: list[dict]) -> bool:
    return any(price_of(r.get("model", "")) != (0.0, 0.0) for r in rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--by-task", action="store_true", help="按任务拆分")
    ap.add_argument("--top", type=int, default=0, help="最贵的 N 次对话（按 trace id）")
    args = ap.parse_args()

    rows = load()
    if not rows:
        print(f"没有用量记录（{USAGE.relative_to(ROOT)} 不存在或为空）。")
        print('先跑几轮对话：python scripts/chat_e2e.py zhanggong "儿童票有什么规定"')
        return

    prompt = sum(r["promptTokens"] for r in rows)
    completion = sum(r["completionTokens"] for r in rows)
    total = sum(r["totalTokens"] for r in rows)
    has_price = priced(rows)
    cost = cost_cents(rows)
    traces = {r["traceId"] for r in rows if r.get("traceId")}

    print("=" * 64)
    print(f"模型调用次数      {len(rows)}")
    print(f"输入 tokens       {prompt:,}")
    print(f"输出 tokens       {completion:,}")
    print(f"合计 tokens       {total:,}")
    if prompt:
        print(f"输入占比          {prompt / total * 100:.1f}%   "
              f"← 成本主要在上下文注入，不在生成")
    print(f"成本              {f'¥{cost / 100:.4f}' if has_price else '未配置单价（只报 token）'}")
    if has_price and traces:
        print(f"每次对话平均      {total // len(traces):,} tokens / ¥{cost / 100 / len(traces):.4f}"
              f"（{len(traces)} 次对话）")
    if not has_price:
        print("                  设 MODEL_PRICE_<模型名>=输入价/输出价 可出金额")

    if args.by_task:
        print()
        print(f"{'task':<14}{'次数':>7}{'输入':>12}{'输出':>12}{'合计':>12}{'成本':>11}")
        print("-" * 64)
        agg: dict[str, list] = defaultdict(list)
        for r in rows:
            agg[r.get("task", "-")].append(r)
        for task in sorted(agg):
            g = agg[task]
            c = cost_cents(g)
            print(f"{task:<14}{len(g):>7}"
                  f"{sum(x['promptTokens'] for x in g):>12,}"
                  f"{sum(x['completionTokens'] for x in g):>12,}"
                  f"{sum(x['totalTokens'] for x in g):>12,}"
                  f"{(f'¥{c / 100:.4f}' if has_price else '-'):>11}")

    if args.top:
        print()
        print(f"最贵的 {args.top} 次对话（按 trace id 聚合）")
        print(f"{'traceId':<18}{'轮次':>6}{'tokens':>12}{'成本':>12}")
        print("-" * 64)
        by_trace: dict[str, list] = defaultdict(list)
        for r in rows:
            by_trace[r.get("traceId") or "(无)"].append(r)
        ranked = sorted(by_trace.items(),
                        key=lambda kv: -sum(x["totalTokens"] for x in kv[1]))
        for tid, g in ranked[:args.top]:
            c = cost_cents(g)
            print(f"{tid:<18}{len(g):>6}"
                  f"{sum(x['totalTokens'] for x in g):>12,}"
                  f"{(f'¥{c / 100:.4f}' if has_price else '-'):>12}")


if __name__ == "__main__":
    main()
