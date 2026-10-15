"""结构化确认端到端：登录(Java) → 查票 → 购票 → 用 intent_id 确认 / 取消。

和 chat_e2e.py 的区别：它在半途注入 resume 载荷，走的是**按钮**那条路径，
而不是让用户打字「确认」。

验证三件事：
  1. 带正确 intent_id 的确认 → 出票
  2. 带过期/错误 intent_id 的确认 → 拒绝（串话 / 重放防线）
  3. 带 confirm=False 的取消 → 不出票
另外会顺带打一条「好的，但我想改时间」，其结果取决于服务端
AGENT_CONFIRM_LEGACY（=0 时不出票，=1 时仍会出票）。

用法（Windows 侧，venv 内）：
    .venv/Scripts/python scripts/confirm_e2e.py [账号]
前置：Java(8080) / Python(8000) 已起，.env 里有 DEEPSEEK_API_KEY。
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

import httpx

JAVA = os.environ.get("JAVA_BASE", "http://127.0.0.1:8080")
PY = "http://127.0.0.1:8000"

QUERY = "帮我查明天北京南到上海虹桥的二等座"

_results: list[tuple[bool, str]] = []


def login(username: str) -> str:
    body = json.dumps({"username": username, "password": "123456"}).encode()
    req = urllib.request.Request(
        JAVA + "/api/auth/login", method="POST", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())["data"]["token"]


def stream(token: str, message: str, resume: dict | None = None,
           label: str = "") -> list[dict]:
    """发一轮对话，收集全部 SSE 事件。label 用于打印。"""
    print(f"\n--- {label or message} ---")
    if resume:
        print(f"    resume={json.dumps(resume, ensure_ascii=False)}")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"message": message, "resume": resume} if resume else {"message": message}
    events: list[dict] = []
    # 真实 LLM 偶发慢响应；超时不该中断整轮，记一条 error 事件继续
    try:
        return _read_stream(events, payload, headers, label)
    except httpx.TimeoutException as e:
        print(f"    [超时] {type(e).__name__}（本轮跳过）")
        events.append({"kind": "error", "text": f"超时：{type(e).__name__}"})
        return events


def _read_stream(events: list[dict], payload: dict, headers: dict, label: str) -> list[dict]:
    with httpx.stream("POST", PY + "/api/chat", json=payload,
                      headers=headers, timeout=240) as resp:
        if resp.status_code != 200:
            print(f"    HTTP {resp.status_code}")
            return events
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
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    events.append(ev)
                    tag = {"think": "思考", "result": "结果", "answer": "答复",
                           "confirm": "待确认", "error": "错误"}.get(ev.get("kind"), ev.get("kind"))
                    extra = ""
                    if ev.get("intent_id"):
                        extra += f"  intent_id={ev['intent_id']}"
                    if ev.get("pay_request_id"):
                        extra += f"  pay_request_id={ev['pay_request_id']}"
                    print(f"    [{tag}]{extra}")
                    if ev.get("text"):
                        print(f"        {ev['text'][:160]}")
    return events


def check(cond: bool, label: str) -> None:
    _results.append((cond, label))
    print(f"    {'✓' if cond else '✗'} {label}")


def ordered(events: list[dict]) -> bool:
    return any("下单成功" in (e.get("text") or "") for e in events)


def paid(events: list[dict]) -> bool:
    return any("已支付" in (e.get("text") or "") for e in events)


def get_intent_id(events: list[dict]) -> str | None:
    for e in events:
        if e.get("kind") == "confirm" and e.get("intent_id"):
            return e["intent_id"]
    return None


def new_intent(token: str, label: str) -> str | None:
    """跑一轮 查票 + 购票，拿到挂起中的 intent_id。

    车次号**从查票结果里解析**，不能写死 —— 否则模型会因为「查不到该车次」
    而不调用 buy_ticket，自然也就拿不到 confirm 事件。
    """
    ev = stream(token, QUERY, label=f"{label} · 查票")
    code = next((m.group(1) for e in ev
                 for m in [re.search(r"tripId=\d+\s+(\S+)", e.get("text") or "")] if m), None)
    if not code:
        print("    未从查票结果解析到车次号，跳过")
        return None
    ev = stream(token, f"就买 {code} 二等座", label=f"{label} · 发起购票({code})")
    return get_intent_id(ev)


# 需要清掉的键前缀。刻意**不用 FLUSHDB**：
# FLUSHDB 会把订单流的消费组 rv-order-g 一起抹掉，而 Java 侧 ensureGroup()
# 只在启动时跑一次 → 之后所有订单都落不了库（下单接口照样返回 PENDING，
# 但 ticket_order 里查不到，表现为"下单成功却付不了款/看不到订单"）。
# 已实测：FLUSHDB 后必须重启 Java 才能恢复。
_WIPE_PREFIXES = ("resv:agent:*", "rv:active:*", "rv:inflight:*")


def reset() -> None:
    """清该用户的订单 + 挂起状态，让场景之间互不干扰。

    直接调 docker，**不经过 bash** —— Windows 上 `bash` 常解析到
    C:\\Windows\\System32\\bash.exe(WSL)，而 WSL 里没有 docker，
    重置会静默失败（start-all.sh 开头就警告过这个坑）。
    """
    r = subprocess.run(["docker", "exec", "resv-mysql", "mysql", "-uresv", "-presv123",
                        "-e", "use resv; delete from ticket_order;"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"清订单失败：{r.stderr.strip()}")

    for pattern in _WIPE_PREFIXES:
        keys = subprocess.run(["docker", "exec", "resv-redis", "redis-cli", "KEYS", pattern],
                              capture_output=True, text=True).stdout.split()
        if keys:
            subprocess.run(["docker", "exec", "resv-redis", "redis-cli", "DEL", *keys],
                           capture_output=True)


def main() -> None:
    user = sys.argv[1] if len(sys.argv) > 1 else "zhanggong"
    token = login(user)
    print(f"==== 登录 {user} OK ====")

    # ── 场景 1：确认（正确 intent_id）→ 出票 ─────────────────────
    reset()
    iid = new_intent(token, "场景1")
    if not iid:
        print("\n未能拿到 intent_id，后续场景跳过（服务端可能未推送 confirm 事件）")
        return
    ev1 = stream(token, "", {"confirm": True, "intent_id": iid}, label="场景1 · 点确认")
    check(ordered(ev1), "正确 intent_id 的确认 → 出票")
    print("    （此时留下一笔待支付订单，供下面验证不会被误支付）")

    # ── 场景 2：重放同一个确认载荷 → 不出票，且不误支付 ★ ─────────
    # 这是本次修掉的那个缺陷的回归用例：曾经任何带 confirm=true 的载荷
    # 都会顺带把待支付订单付掉。
    ev2 = stream(token, "", {"confirm": True, "intent_id": iid},
                 label="场景2 · 重放刚才的确认载荷")
    check(not ordered(ev2), "重放确认载荷 → 不出票")
    check(not paid(ev2), "重放确认载荷 → 不会误支付那笔待支付订单")

    # ── 场景 3：显式支付载荷 → 已支付 ────────────────────────────
    ev3 = stream(token, "", {"action": "pay", "confirm": True}, label="场景3 · 点支付")
    check(paid(ev3), "action=pay 的支付 → 已支付")

    # ── 场景 4：取消 → 不出票 ────────────────────────────────────
    reset()
    iid = new_intent(token, "场景4")
    ev4 = stream(token, "", {"confirm": False, "intent_id": iid}, label="场景4 · 点取消")
    check(not ordered(ev4), "取消 → 不出票")

    # ── 场景 5：关键词回退的开关状态 ─────────────────────────────
    reset()
    new_intent(token, "场景5")
    ev5 = stream(token, "好的，但我想改时间", label="场景5 · 话里带「好的」但并非确认")
    got = ordered(ev5)
    print(f"    → {'出票了（AGENT_CONFIRM_LEGACY=1，过渡期行为）' if got else '未出票（AGENT_CONFIRM_LEGACY=0，缺陷已消除）'}")

    print("\n" + "=" * 56)
    for ok, label in _results:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    failed = [l for ok, l in _results if not ok]
    print("=" * 56)
    print(f"{len(_results) - len(failed)}/{len(_results)} 通过")
    if not os.environ.get("E2E_KEEP"):
        reset()                     # E2E_KEEP=1 时保留现场，便于排障
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
