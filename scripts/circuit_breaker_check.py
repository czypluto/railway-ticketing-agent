"""Redis 故障注入：验证熔断器的快速失败与自动恢复。

做三件事：
  1. 正常时下单成功（基线）
  2. **停掉 Redis**，连打 N 次下单——前几次慢（每个等 Lettuce 超时 3s），
     达阈值后熔断打开，后续请求**立刻** 503（不再等超时）
  3. **恢复 Redis**，冷却后半开试探成功、熔断闭合，下单恢复正常

为什么用「延迟」而不是「状态码」判定：故障期间本来就该失败，
区别在于**失败得多快**——熔断的价值是把线程及时让出来，避免级联故障。

实测（默认配置：阈值 5 次 / 冷却 10s，Lettuce 命令超时 3s）：

    ① 基线（Redis 正常）        334 ms  HTTP 200  ok
    ② 停掉 Redis
       #1–#5   3018–3046 ms  HTTP 500  系统内部错误   ← 每个都干等 3s 超时
       #6–#8      21–49 ms   HTTP 503  服务繁忙       ← 熔断打开，快约 70 倍
    ③ 恢复 Redis                179 ms  HTTP 200  ok   ← 自动闭合

用法（前置：Java/Python 已起）
    python scripts/circuit_breaker_check.py [账号]
需要能操作 docker（会 stop/start resv-redis 容器）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote

JAVA = "http://127.0.0.1:8080"
REDIS_CONTAINER = "resv-redis"
PROBES = 8                      # 故障期间打多少次
FAST_MS = 300                   # 判定「快速失败」的阈值


def docker(*args, **kw) -> str:
    out = subprocess.run(["docker", *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", **kw)
    return (out.stdout or "").strip()


def login(user: str) -> str:
    body = json.dumps({"username": user, "password": "123456"}).encode()
    req = urllib.request.Request(JAVA + "/api/auth/login", method="POST", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())["data"]["token"]


FROM_STATION = "北京南"
TO_STATION = "上海虹桥"
TRAVEL_DATE = "2026-10-16"
SEAT_CLASS = "二等座"


def buy(token: str, body: dict, request_id: str) -> tuple[int, int, str]:
    """返回 (耗时ms, http状态, 消息)。"""
    payload = {**body, "requestId": request_id}
    req = urllib.request.Request(JAVA + "/api/ticket/buy",
                                 method="POST", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
            ms = (time.perf_counter() - t0) * 1000
            return int(ms), r.status, data.get("msg", "")
    except urllib.error.HTTPError as e:
        ms = (time.perf_counter() - t0) * 1000
        try:
            msg = json.loads(e.read().decode()).get("msg", "")
        except Exception:  # noqa: BLE001
            msg = ""
        return int(ms), e.code, msg


def get(token: str, path: str):
    req = urllib.request.Request(JAVA + path,
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())["data"]


def station_id(token: str, name: str) -> int:
    rows = get(token, f"/api/ticket/stations?kw={quote(name)}")
    return rows[0]["id"]


def buy_body(token: str) -> dict:
    """构造一个能真正走到 Redis 的下单请求体。

    注意 fromStationId/toStationId 必填——缺了会在 @Valid 层就 400，
    根本到不了 Redis，也就测不到熔断。
    """
    from_id = station_id(token, FROM_STATION)
    to_id = station_id(token, TO_STATION)
    rows = get(token, f"/api/ticket/query?from={from_id}&to={to_id}"
                      f"&date={TRAVEL_DATE}&seatClass={quote(SEAT_CLASS)}")
    trip = rows[0]
    return {"tripId": trip["tripId"], "seatClass": trip["seatClass"],
            "fromStationId": from_id, "toStationId": to_id,
            "ticketType": "ADULT"}


def reset(user: str) -> None:
    """清该用户订单 + 一人一票占位 + 余票/座位号，让每一阶段都能真正下单。

    余票与座位号必须一起清：删订单不会把已扣的库存还回去，只删订单的话
    余票会只减不增，跑几次就漂移。删掉 stock 键后，下次下单会走
    decr→-2→preheat 重新按「总席位 − 在售订单」算出正确值。

    只在 Redis 活着时调用（故障阶段容器是停的）。
    """
    subprocess.run(["docker", "exec", "resv-mysql", "mysql", "-uresv", "-presv123",
                    "-e", f"use resv; delete o from ticket_order o join sys_user u "
                          f"on o.user_id=u.id where u.username='{user}';"],
                   capture_output=True)
    for pattern in ("rv:active:*", "rv:stock:*", "rv:seat:*"):
        keys = docker("exec", REDIS_CONTAINER, "redis-cli", "KEYS", pattern).split()
        if keys:
            docker("exec", REDIS_CONTAINER, "redis-cli", "DEL", *keys)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("user", nargs="?", default="zhanggong")
    args = ap.parse_args()

    token = login(args.user)
    body = buy_body(token)
    rid = lambda i: f"circuit-probe-{int(time.time())}-{i}"  # noqa: E731

    print("=" * 70)
    print("① 基线（Redis 正常）")
    reset(args.user)
    ms, code, msg = buy(token, body, rid("base"))
    print(f"   下单 {ms} ms  HTTP {code}  {msg}")
    baseline_ok = code == 200
    reset(args.user)                      # 清掉基线那单，避免后面撞「一人一票」

    print()
    print("② 停掉 Redis，连续打请求")
    docker("stop", "-t", "1", REDIS_CONTAINER)
    rows = []
    for i in range(PROBES):
        ms, code, msg = buy(token, body, rid(f"down-{i}"))
        rows.append((i + 1, ms, code, msg))
        print(f"   #{i + 1}  {ms:>5} ms  HTTP {code}  {msg}")

    slow = [r for r in rows if r[1] > FAST_MS]
    fast = [r for r in rows if r[1] <= FAST_MS]
    print(f"\n   慢（等超时）{len(slow)} 次 / 快（熔断快速失败）{len(fast)} 次")

    print()
    print("③ 恢复 Redis，等冷却后半开试探")
    docker("start", REDIS_CONTAINER)
    time.sleep(15)                       # 冷却 + 客户端重连
    ms, code, msg = buy(token, body, rid("recover"))
    print(f"   下单 {ms} ms  HTTP {code}  {msg}")

    print()
    print("=" * 70)
    checks = [
        (baseline_ok, "基线：Redis 正常时下单成功"),
        (len(fast) > 0, f"故障期间出现快速失败（{len(fast)} 次 ≤ {FAST_MS}ms）"),
        (not fast or fast[0][0] > 1, "快速失败发生在若干次慢失败**之后**（阈值生效）"),
        (code == 200, "恢复后自动闭合，下单成功"),
    ]
    for ok, label in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    sys.exit(0 if all(c[0] for c in checks) else 1)


if __name__ == "__main__":
    main()
