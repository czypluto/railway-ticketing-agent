"""Agent 行为层评测：断言「该调的调了、不该调的没调、该拒的拒了」。

和 rag_eval 的分工：
  rag_eval      → 检索**质量**（Hit@K / MRR / RAGAS）
  agent_eval    → Agent **行为**（工具调用轨迹 / 是否出票 / 是否拒绝）

为什么需要它：项目在「模型输出不直接执行」上做了很多（参数重建、grounding、
两阶段确认、一人一票），但过去只能口头描述，拿不出数据。这里把它变成一张表。

断言口径全部是**可观测结果**，不看内部实现：
  expect_tools    这些工具必须被调到（依赖编排层的 tool 事件）
  forbid_tools    这些工具一次都不能调
  order_delta     本轮结束后该用户订单数的净变化
  text_any/none   答复文案必须/不得包含
  last_text_any   只看最后一轮答复
  expect_kind     必须出现某类事件（如 confirm）

用法（Windows 侧 venv，前置：Java/Python 已起）：
    python scripts/agent_eval/run.py
    python scripts/agent_eval/run.py --cat F --repeat 3
    python scripts/agent_eval/run.py --json result_agent.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import httpx

JAVA = os.environ.get("JAVA_BASE", "http://127.0.0.1:8080")
PY = os.environ.get("PY_BASE", "http://127.0.0.1:8000")
CASES = Path(__file__).with_name("cases.jsonl")


# ── 环境操作 ──────────────────────────────────────────────────────────

def login(username: str) -> str:
    body = json.dumps({"username": username, "password": "123456"}).encode()
    req = urllib.request.Request(JAVA + "/api/auth/login", method="POST", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())["data"]["token"]


def _mysql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "resv-mysql", "mysql", "-uresv", "-presv123", "-N", "-e", sql],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (out.stdout or "").strip()


def reset(username: str) -> None:
    """清该用户的订单 + 挂起状态 + 余票/座位号，让用例之间互不干扰。

    刻意**不用 FLUSHDB**：那会删掉订单流 rv:stream:orders 的消费组，
    之后所有订单都落不了库（详见 scripts/README 与 reset-demo.sh 的说明）。

    余票与座位号要一起清：删订单不会把已扣的库存还回去，只删订单的话
    余票会只减不增，跑几十个用例后余票耗尽、A 类用例会假失败。
    删掉 stock 键后，下次下单走 decr→-2→preheat 重新算正确值。
    """
    _mysql(f"use resv; delete o from ticket_order o "
           f"join sys_user u on o.user_id=u.id where u.username='{username}';")
    for pattern in ("resv:agent:*", "rv:active:*", "rv:inflight:*",
                    "rv:stock:*", "rv:seat:*", "rv:seatfree:*"):
        # 必须显式 utf-8：席别是中文，key 形如 rv:stock:38:二等座，
        # Windows 默认按 GBK 解码会抛 UnicodeDecodeError，stdout 变 None。
        keys = subprocess.run(["docker", "exec", "resv-redis", "redis-cli", "KEYS", pattern],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout.split()
        if keys:
            subprocess.run(["docker", "exec", "resv-redis", "redis-cli", "DEL", *keys],
                           capture_output=True)


def order_count(username: str) -> int:
    r = _mysql(f"use resv; select count(*) from ticket_order o "
               f"join sys_user u on o.user_id=u.id where u.username='{username}';")
    return int(r or 0)


# ── 对话 ──────────────────────────────────────────────────────────────

def chat(token: str, message: str, resume: dict | None = None) -> list[dict]:
    payload = {"message": message, "resume": resume} if resume else {"message": message}
    events: list[dict] = []
    try:
        with httpx.stream("POST", PY + "/api/chat", json=payload, timeout=180,
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json"}) as resp:
            buf = ""
            for chunk in resp.iter_text():
                buf += chunk
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    for line in block.splitlines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:].strip()
                        if raw == "[DONE]":
                            return events
                        try:
                            events.append(json.loads(raw))
                        except json.JSONDecodeError:
                            pass
    except httpx.TimeoutException:
        events.append({"kind": "error", "text": "客户端超时"})
    return events


def run_turn(token: str, turn, state: dict) -> list[dict]:
    if isinstance(turn, str):
        return chat(token, turn)
    if "confirm" in turn:                       # 点「确认下单 / 取消」
        return chat(token, "", {"confirm": bool(turn["confirm"]),
                                "intent_id": state.get("intent_id") or ""})
    if turn.get("pay"):                         # 点「支付」
        return chat(token, "", {"action": "pay", "confirm": True})
    raise ValueError(f"未知的 turn 形态：{turn!r}")


# ── 断言 ──────────────────────────────────────────────────────────────

def check(case: dict, turns_events: list[list[dict]], before: int, after: int) -> list[str]:
    """返回失败原因列表；空列表 = 通过。"""
    tools = [e.get("text") for evs in turns_events for e in evs if e.get("kind") == "tool"]
    kinds = {e.get("kind") for evs in turns_events for e in evs}
    all_text = "\n".join(e.get("text") or "" for evs in turns_events for e in evs)
    last_text = "\n".join(e.get("text") or "" for e in turns_events[-1]) if turns_events else ""
    fails: list[str] = []

    for t in case.get("expect_tools", []):
        if t not in tools:
            fails.append(f"未调用 {t}（实际调了 {tools or '无'}）")
    for t in case.get("forbid_tools", []):
        if t in tools:
            fails.append(f"不该调用 {t}")
    if "order_delta" in case:
        delta = after - before
        if delta != case["order_delta"]:
            fails.append(f"订单数变化 {delta}，期望 {case['order_delta']}")
    if case.get("expect_kind") and case["expect_kind"] not in kinds:
        fails.append(f"未出现 {case['expect_kind']} 事件（实际 {sorted(kinds)}）")
    if case.get("text_any") and not any(w in all_text for w in case["text_any"]):
        fails.append(f"答复未包含 {case['text_any']} 任一")
    if case.get("text_none"):
        hit = [w for w in case["text_none"] if w in all_text]
        if hit:
            fails.append(f"答复不该包含 {hit}")
    if case.get("last_text_any") and not any(w in last_text for w in case["last_text_any"]):
        fails.append(f"末轮答复未包含 {case['last_text_any']} 任一")
    return fails


# ── 主流程 ────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat", help="只跑某类（如 A / F）")
    ap.add_argument("--id", help="只跑某个用例")
    ap.add_argument("--repeat", type=int, default=1, help="每个用例重复次数（看稳定性）")
    ap.add_argument("--json", help="把结果写到 JSON")
    args = ap.parse_args()

    cases = [json.loads(l) for l in CASES.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.cat:
        cases = [c for c in cases if c["cat"].startswith(args.cat)]
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]

    tokens: dict[str, str] = {}
    rows: list[dict] = []

    for case in cases:
        user = case.get("as", "zhanggong")
        if user not in tokens:
            tokens[user] = login(user)
        token = tokens[user]

        ok_runs, fails_all = 0, []
        for _ in range(args.repeat):
            reset(user)
            state: dict = {}
            for t in case.get("setup", []):          # 前置轮次：跑但不判定
                for e in chat(token, t):
                    if e.get("kind") == "confirm" and e.get("intent_id"):
                        state["intent_id"] = e["intent_id"]

            before = order_count(user)
            turns_events = []
            for turn in case["turns"]:
                evs = run_turn(token, turn, state)
                turns_events.append(evs)
                for e in evs:
                    if e.get("kind") == "confirm" and e.get("intent_id"):
                        state["intent_id"] = e["intent_id"]
            after = order_count(user)

            fails = check(case, turns_events, before, after)
            if fails:
                fails_all.append(fails)
            else:
                ok_runs += 1

        rows.append({
            "id": case["id"], "cat": case["cat"], "desc": case["desc"],
            "passed": ok_runs, "runs": args.repeat,
            "fails": fails_all[:1],
        })
        mark = "PASS" if ok_runs == args.repeat else "FAIL"
        # flush：用例要调真实 LLM，整轮可能跑十几分钟；不实时刷会被误判成卡住
        print(f"{mark}  {case['id']:<4} {case['desc']}", flush=True)
        if fails_all:
            for f in fails_all[0]:
                # 只用 ASCII：此前这里打的是 U+2717(✗)，在 GBK 控制台下 UnicodeEncodeError
                # 直接崩掉——而且**恰好只在有用例失败时崩**，把失败原因一起吞了。
                print(f"        x {f}", flush=True)

    # 汇总：按类统计通过率（这就是「任务完成率」的口径）
    print("\n" + "=" * 62)
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cat[r["cat"]].append(r)
    print(f"{'类别':<22}{'通过/总计':>12}{'完成率':>10}")
    print("-" * 62)
    total_ok = total_all = 0
    for cat in sorted(by_cat):
        ok = sum(r["passed"] for r in by_cat[cat])
        n = sum(r["runs"] for r in by_cat[cat])
        total_ok += ok
        total_all += n
        print(f"{cat:<22}{ok:>6}/{n:<6}{ok / n * 100:>9.1f}%")
    print("-" * 62)
    print(f"{'合计':<22}{total_ok:>6}/{total_all:<6}{total_ok / total_all * 100:>9.1f}%")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"summary": {"passed": total_ok, "total": total_all},
                        "cases": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写出 {args.json}")

    sys.exit(0 if total_ok == total_all else 1)


if __name__ == "__main__":
    main()
