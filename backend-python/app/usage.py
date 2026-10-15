"""模型用量记账：append-only JSONL，一行一次调用。

**为什么单独记**：模型用量是这套系统里最主要的可变成本。不记就答不出
「一次对话花多少钱」「哪个任务最贵」。这是面试会直接问、而项目原先完全没有的维度。

**为什么落 JSONL 而不是建表**：与 `audit.py` 同样的取舍 —— 工程约定是 Python
不直连业务库，只经 REST 调 Java。用量属于分析型数据，文本文件足够，
要按维度聚合时用 `scripts/cost_report.py`。

**只记事实，不记结论**：这里只写 token 数（接口直接返回，不会错）与模型名。
**成本在 `scripts/cost_report.py` 里按当时的单价现算** —— 如果把金额也写进账本，
单价一变历史数据就永久停在旧价上，想重新定价都做不到。
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config, trace

logger = logging.getLogger(__name__)


def _path() -> Path:
    p = Path(config.USAGE_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _append(entry: dict) -> None:
    try:
        with _path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning("用量记账失败（不影响主流程）：%s", e)


def record(task: str, model: str, usage: Optional[dict]) -> dict:
    """记一次模型调用的用量，返回归一化后的记录。

    失败只记 warning —— 记账不能把主流程拖垮（和 audit 一样的取舍）。
    """
    u = usage or {}
    prompt = int(u.get("prompt_tokens") or 0)
    completion = int(u.get("completion_tokens") or 0)
    total = int(u.get("total_tokens") or (prompt + completion))
    # 推理 token 单独拆出来。deepseek-v4-pro 是推理模型，reasoning_tokens 计入
    # completion_tokens 但**不产生可见输出** —— 记忆压缩实测每次 completion 约 3000，
    # 而摘要正文只有 ~500 字（≈800 token），差额全是推理。不拆开的话
    # "输出 token" 这个数会让人以为是摘要写长了，从而找错优化方向。
    detail = u.get("completion_tokens_details") or {}
    reasoning = int(detail.get("reasoning_tokens") or 0)
    entry = {
        "kind": "llm",
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "traceId": trace.current(),
        "task": task,
        "model": model,
        "promptTokens": prompt,
        "completionTokens": completion,
        "reasoningTokens": reasoning,
        "visibleTokens": max(0, completion - reasoning),
        "totalTokens": total,
    }
    _append(entry)
    return entry


def record_turn(trace_id: str, duration_ms: int, events: int, ok: bool = True) -> dict:
    """记一轮对话的耗时。

    刻意和 LLM 调用记在**同一个账本**里（用 `kind` 区分）——报告就能按 traceId
    把「这轮花了多少钱」和「这轮花了多少时间」直接对上，看得出慢是因为贵
    （上下文长）还是因为别的。分两个文件就得靠 join，容易对不齐。
    """
    entry = {
        "kind": "turn",
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "traceId": trace_id,
        "durationMs": int(duration_ms),
        "events": int(events),
        "ok": bool(ok),
    }
    _append(entry)
    return entry


def tail(limit: int = 5000) -> list[dict]:
    p = _path()
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out[-limit:]
