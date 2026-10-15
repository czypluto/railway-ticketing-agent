"""模型用量 / 成本 / 时延报告：聚合 backend-python/data/usage/llm.jsonl。

账本里有两类记录，用 `kind` 区分：
  kind=llm   一次模型调用：模型、task、token 数
  kind=turn  一轮对话：耗时、事件数、成败      ← 由 app/routers/chat.py 写

两类都带 traceId，所以报告能把「这轮多慢」和「这轮多少钱」直接对上，
看得出慢是因为贵（上下文长）还是因为别的。

回答原先答不出的问题：
  「一次对话花多少 token / 多少钱 / 多久」——按 trace id 聚合
  「钱花在哪」——按 task 拆分

**账本里只有 token，金额在这里现算**。这样单价一变，历史数据可以重新定价；
把金额写进账本就会永久停在当时的价上。

单价默认**不配**（只报 token，不编造）。要报金额就设环境变量：
    MODEL_PRICE_DEEPSEEK_V4_PRO=2.0/8.0     # 输入价/输出价，元每百万 token

用法：
    python scripts/cost_report.py                # 总览（含时延 P50/P95/P99）
    python scripts/cost_report.py --by-task      # 按任务拆分
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


def load() -> tuple[list[dict], list[dict]]:
    """返回 (llm 调用记录, 对话轮次记录)。老条目没有 kind，按 llm 处理。"""
    if not USAGE.is_file():
        return [], []
    llm, turns = [], []
    for line in USAGE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        (turns if r.get("kind") == "turn" else llm).append(r)
    return llm, turns


def pct(values: list[int], p: float) -> int:
    """最近秩百分位。样本少时 P95 就是最大值——不插值，避免给出不存在的数。"""
    if not values:
        return 0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round(len(s) * p / 100 + 0.5)) - 1))
    return s[idx]


def cost_cents(rows: list[dict]) -> float:
    return sum((r["promptTokens"] * price_of(r.get("model", ""))[0]
                + r["completionTokens"] * price_of(r.get("model", ""))[1]) / 10000
               for r in rows)


def priced(rows: list[dict]) -> bool:
    return any(price_of(r.get("model", "")) != (0.0, 0.0) for r in rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--by-task", action="store_true", help="按任务拆分")
    ap.add_argument("--top", type=int, default=0, help="最贵的 N 次对话（按 trace id）")
    args = ap.parse_args()

    llm, turns = load()
    if not llm and not turns:
        print(f"没有用量记录（{USAGE.relative_to(ROOT)} 不存在或为空）。")
        print('先跑几轮对话：python scripts/chat_e2e.py zhanggong "儿童票有什么规定"')
        return

    prompt = sum(r["promptTokens"] for r in llm)
    completion = sum(r["completionTokens"] for r in llm)
    total = sum(r["totalTokens"] for r in llm)
    has_price = priced(llm)
    cost = cost_cents(llm)
    traces = {r["traceId"] for r in llm if r.get("traceId")}
    durations = [r["durationMs"] for r in turns]

    print("=" * 66)
    print(f"模型调用次数      {len(llm)}")
    print(f"输入 tokens       {prompt:,}")
    print(f"输出 tokens       {completion:,}")
    reasoning = sum(r.get("reasoningTokens", 0) for r in llm)
    if reasoning:
        print(f"  其中推理 token   {reasoning:,}   占输出 "
              f"{reasoning / completion * 100:.0f}%   ← 计费但不可见，见下")
    print(f"合计 tokens       {total:,}")
    if total:
        print(f"输入占比          {prompt / total * 100:.1f}%   "
              f"← 成本主要在上下文注入，不在生成")
    print(f"成本              {f'¥{cost / 100:.4f}' if has_price else '未配置单价（只报 token）'}")

    if durations:
        failed = sum(1 for r in turns if not r.get("ok", True))
        print(f"对话轮次          {len(turns)}   失败 {failed}")
        print(f"端到端时延 P50    {pct(durations, 50):,} ms")
        print(f"           P95    {pct(durations, 95):,} ms")
        print(f"           P99    {pct(durations, 99):,} ms")
        print(f"           max    {max(durations):,} ms")
    if traces:
        print(f"每次对话平均      {total // len(traces):,} tokens"
              + (f" / ¥{cost / 100 / len(traces):.4f}" if has_price else ""))
    if not has_price and llm:
        print("                  设 MODEL_PRICE_<模型名>=输入价/输出价 可出金额")
    if not durations:
        print("（没有对话轮次记录——chat.py 会在每轮结束写一条 kind=turn）")

    if args.by_task:
        print()
        print(f"{'task':<14}{'次数':>7}{'输入':>12}{'输出':>12}{'其中推理':>11}"
              f"{'合计':>12}{'成本':>11}")
        print("-" * 78)
        agg: dict[str, list] = defaultdict(list)
        for r in llm:
            agg[r.get("task", "-")].append(r)
        for task in sorted(agg):
            g = agg[task]
            c = cost_cents(g)
            reason = sum(x.get("reasoningTokens", 0) for x in g)
            print(f"{task:<14}{len(g):>7}"
                  f"{sum(x['promptTokens'] for x in g):>12,}"
                  f"{sum(x['completionTokens'] for x in g):>12,}"
                  f"{reason:>11,}"
                  f"{sum(x['totalTokens'] for x in g):>12,}"
                  f"{(f'¥{c / 100:.4f}' if has_price else '-'):>11}")

    if args.top:
        print()
        print(f"最贵的 {args.top} 次对话（按 trace id 聚合）")
        print(f"{'traceId':<18}{'调用':>5}{'tokens':>11}{'耗时ms':>9}{'成本':>11}")
        print("-" * 66)
        by_trace: dict[str, dict] = defaultdict(lambda: {"llm": [], "turn": []})
        for r in llm:
            by_trace[r.get("traceId") or "(无)"]["llm"].append(r)
        for r in turns:
            by_trace[r.get("traceId") or "(无)"]["turn"].append(r)
        ranked = sorted(by_trace.items(),
                        key=lambda kv: -sum(x["totalTokens"] for x in kv[1]["llm"]))
        for tid, g in ranked[:args.top]:
            c = cost_cents(g["llm"])
            ms = g["turn"][0]["durationMs"] if g["turn"] else 0
            print(f"{tid:<18}{len(g['llm']):>5}"
                  f"{sum(x['totalTokens'] for x in g['llm']):>11,}"
                  f"{ms:>9,}"
                  f"{(f'¥{c / 100:.4f}' if has_price else '-'):>11}")


if __name__ == "__main__":
    main()
