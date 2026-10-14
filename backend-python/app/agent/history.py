"""对话档案：全量记录持久化，用户可回溯。

- 每个"会话"= 一个 JSON 文件（data/chat_hist/{user}_{session}.json），内含 startedAt + messages
- 每次对话把 user 原话 + assistant 完整文本(含各[结果/答复]行)追加进"当前会话"
- 提供 列表 / 详情 / 开新会话；开新会话同时清空当前对话记忆
- 持久性不依赖 Redis（普通文件即可，避免 1h 过期丢档）
"""
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from . import memory as mem

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "chat_hist"
_CURRENT = ROOT / "_current.json"

# 会话档案是「整份读出 → 改 → 整份写回」的 JSON 文件，同一用户并发对话会互相覆盖，
# 表现为丢轮次。单实例 uvicorn 下用进程内锁串行化即可；
# 若将来多实例部署，要换成文件锁，或干脆改成追加式存储（每轮一行 JSONL）。
_LOCK = threading.Lock()


def _atomic_write(p: Path, text: str) -> None:
    """先写临时文件再原子替换，避免进程在写到一半时中断留下半截 JSON。"""
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def _path(user_id, session):
    return ROOT / f"{user_id}_{session}.json"


def _ensure_dir():
    ROOT.mkdir(parents=True, exist_ok=True)


def _read_current(user_id):
    _ensure_dir()
    try:
        d = json.loads(_CURRENT.read_text(encoding="utf-8"))
        return d.get(str(user_id))
    except Exception:  # noqa: BLE001
        return None


def _write_current(user_id, session):
    _ensure_dir()
    with _LOCK:
        d = {}
        try:
            d = json.loads(_CURRENT.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
        d[str(user_id)] = session
        _atomic_write(_CURRENT, json.dumps(d, ensure_ascii=False))


def current_session(user_id) -> str:
    s = _read_current(user_id)
    if not s:
        s = new_session(user_id)
    return s


def new_session(user_id) -> str:
    _ensure_dir()
    s = f"s{uuid.uuid4().hex[:10]}"
    _atomic_write(ROOT / f"{user_id}_{s}.json",
                  json.dumps({"startedAt": time.time(), "messages": []}, ensure_ascii=False))
    _write_current(user_id, s)
    # 记忆已按会话隔离：新会话自带空桶，这里不再清整用户记忆（旧会话切回仍可继续）
    return s


def activate(user_id, session) -> bool:
    """把某历史会话设为“当前”，之后续聊（档案+记忆）都落回该会话。"""
    if not (ROOT / f"{user_id}_{session}.json").exists():
        return False
    _write_current(user_id, session)
    return True


def append_turn(user_id, user_text: str, assistant_text: str):
    """追加一轮到当前会话（assistant_text 为多行完整文本，含各结果/答复）。"""
    if not user_text:
        return
    _ensure_dir()
    session = current_session(user_id)
    p = _path(user_id, session)
    with _LOCK:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            doc = {"startedAt": time.time(), "messages": []}
        doc["messages"].append({"role": "user", "text": user_text, "ts": time.time()})
        if assistant_text:
            doc["messages"].append({"role": "assistant", "text": assistant_text, "ts": time.time()})
        _atomic_write(p, json.dumps(doc, ensure_ascii=False))


def list_sessions(user_id) -> list[dict]:
    """按时间倒序返回会话元信息。"""
    _ensure_dir()
    out = []
    for f in ROOT.glob(f"{user_id}_s*.json"):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            msgs = doc.get("messages", [])
            first_user = next((m.get("text", "") for m in msgs if m.get("role") == "user"), "")
            out.append({
                "id": f.name[len(f"{user_id}_"):].replace(".json", ""),
                "startedAt": doc.get("startedAt", 0),
                "count": len(msgs) // 2,
                "preview": first_user[:60],
            })
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda x: x["startedAt"], reverse=True)
    return out


def delete_session(user_id, session):
    """删除单个历史会话文件与对应会话记忆；若删的是当前会话，同时清当前指向。"""
    p = _path(user_id, session)
    if p.exists():
        p.unlink()
    mem.clear(user_id, session)  # 会话级记忆一并清掉，避免孤儿桶
    if _read_current(user_id) == session:
        with _LOCK:
            d = {}
            try:
                d = json.loads(_CURRENT.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
            d.pop(str(user_id), None)
            _atomic_write(_CURRENT, json.dumps(d, ensure_ascii=False))


def get_session(user_id, session) -> list[dict]:
    p = _path(user_id, session)
    if not p.exists():
        return []
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc.get("messages", [])
    except Exception:  # noqa: BLE001
        return []
