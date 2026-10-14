"""对话链路联调：登录(Java) -> Python /api/chat(SSE) -> 真实 DeepSeek 意图识别。

用法：.venv/Scripts/python ../scripts/chat_e2e.py [账号] [话]  （前置：Java/Python 已起、.env 有 key）
"""
import json
import os
import sys
import urllib.error
import urllib.request

import httpx

# JAVA_BASE 可用环境变量覆盖（如 Python 跑在 WSL 时指向 Windows 网关）
JAVA = os.environ.get("JAVA_BASE", "http://127.0.0.1:8080")
PY = "http://127.0.0.1:8000"

# 后端实际推送的 SSE 事件类型（见 app/agent/orchestrator.py 的 _event 调用）
EVENT_MARK = {
    "think": "[思考]",
    "result": "[结果]",
    "answer": "[答复]",
    "confirm": "[待确认]",
    "error": "[错误]",
}


def login(username):
    body = json.dumps({"username": username, "password": "123456"}).encode()
    req = urllib.request.Request(
        JAVA + "/api/auth/login", method="POST", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())["data"]
    print(f"\n==== 用户 {username}（{data['name']}）登录 OK ====")
    return data["token"]


def chat(token, message):
    print(f"\n--- 请求：{message} ---")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with httpx.stream("POST", PY + "/api/chat", json={"message": message},
                      headers=headers, timeout=90) as resp:
        if resp.status_code != 200:
            print(f"HTTP {resp.status_code}")
            return
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                for line in block.splitlines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        return
                    try:
                        ev = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    kind = ev.get("kind")
                    mark = EVENT_MARK.get(kind, f"[{kind}]")
                    print(f"  {mark} model={ev.get('model','-')}")
                    if ev.get("text"):
                        print(f"       {ev['text']}")


def main():
    user = sys.argv[1] if len(sys.argv) > 1 else "zhanggong"
    msg = sys.argv[2] if len(sys.argv) > 2 else "帮我查明天北京南到上海虹桥的二等座"
    token = login(user)
    chat(token, msg)


if __name__ == "__main__":
    main()
