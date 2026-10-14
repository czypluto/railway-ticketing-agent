"""知识库管理操作审计：append-only JSONL。

记录每一次知识库写操作与**被拒绝的越权尝试**。审计的意义在于事后可追责：
「谁、什么时候、对哪个文件、做了什么、成没成」。

设计取舍：Java 侧已有 `action_log` 表（`audit/` 包），但 Python 不直连业务库
（工程约定：只经 REST 调 Java）。为不让审计耦合主流程，这里落本地 JSONL。
若要与 Java 的审计合并成一份，正确做法是在 Java 加一个审计上报接口，由 Python 调用。

原则：审计写入失败只记 warning，绝不抛错——审计不能把主流程拖垮。
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config
from .security import UserCtx

logger = logging.getLogger(__name__)


def _path() -> Path:
    p = Path(config.AUDIT_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def record(user: Optional[UserCtx], action: str, target: str,
           ok: bool = True, detail: str = "") -> None:
    """记一条审计。action 形如 kb.upload / kb.delete / kb.reingest / kb.list。"""
    entry = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "userId": getattr(user, "user_id", None),
        "username": getattr(user, "username", "") or "",
        "name": getattr(user, "name", "") or "",
        "role": getattr(user, "role", "") or "",
        "department": getattr(user, "department", "") or "",
        "action": action,
        "target": target,
        "ok": ok,
        "detail": detail,
    }
    try:
        with _path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning("审计写入失败（不影响主流程）：%s", e)


def tail(limit: int = 200) -> list[dict]:
    """读最近 limit 条，倒序返回（最新在前）。"""
    p = _path()
    if not p.is_file():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # 半截行（写入中断）直接跳过
    return out[-limit:][::-1]
