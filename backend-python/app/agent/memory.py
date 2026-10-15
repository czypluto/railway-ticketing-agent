"""Agent 当前对话记忆（两层：近期原文 + 早期滚动摘要/上下文压缩）。

- 近期：保留最近 RAW_LIMIT 条原文，保证指代用的近期细节不丢；
- 超出即触发一次“上下文压缩”：把被淘汰的更早轮次用 LLM 压进一条摘要并滚动继承；
  LLM 不可用时退回朴素拼接截断，绝不因此抛错。
- 存储：Redis（MEMORY_REDIS_HOST）优先，连不上退回进程内存。
- append 是**协程**（压缩要调 LLM）；load_state / clear 是同步的。
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

KEY_PREFIX = "mem:agent:"
TTL_SEC = 3600
RAW_LIMIT = 10               # 保留最近 ~5 轮原文
SUMMARY_MAX = 1200           # 摘要上限（字）
_STATE = ("summary", "msgs")

_fallback: dict[int, dict] = {}

try:
    import redis as _redis

    def _client():
        try:
            host = os.getenv("MEMORY_REDIS_HOST", "127.0.0.1")
            port = int(os.getenv("MEMORY_REDIS_PORT", "6379"))
            c = _redis.Redis(host=host, port=port, decode_responses=True, socket_timeout=1)
            c.ping()
            return c
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis 不可用，记忆退回进程内存：%s", e)
            return None
except Exception:  # noqa: BLE001
    _redis = None


def _key(user_id, session=""):
    # 记忆按会话隔离：mem:agent:{user}:{session}；缺省会话归入 default，兼容旧数据/单测
    return f"{KEY_PREFIX}{user_id}:{session or 'default'}"


def _read(user_id, session="") -> dict:
    empty = {"summary": "", "msgs": []}
    c = _client() if _redis else None
    if c:
        raw = c.get(_key(user_id, session))
        if raw:
            try:
                d = json.loads(raw)
                return {"summary": d.get("summary", ""), "msgs": d.get("msgs", [])}
            except Exception:  # noqa: BLE001
                return empty
        return empty
    return _fallback.get((user_id, session or "default"), dict(empty))


def _write(user_id, session, state):
    c = _client() if _redis else None
    if c:
        c.setex(_key(user_id, session), TTL_SEC, json.dumps(state, ensure_ascii=False))
    else:
        _fallback[(user_id, session or "default")] = state


def load_state(user_id, session="") -> dict:
    """返回 {summary, msgs}：msgs 为清洗后可直接注入的对话原文（按会话隔离）。"""
    st = _read(user_id, session)
    msgs = _clean(st.get("msgs", []))
    return {"summary": st.get("summary", ""), "msgs": msgs}


def _clean(raw):
    out, prev = [], None
    for m in raw:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role not in ("user", "assistant") or not content or role == prev:
            continue
        out.append({"role": role, "content": content})
        prev = role
    if out and out[0]["role"] != "user":
        out = out[1:]
    if out and out[-1]["role"] == "user":
        out = out[:-1]
    return out


async def _compress(summary_old: str, evicted: list[dict]) -> str:
    """把更早轮次压进摘要。优先 LLM，失败退朴素拼接。

    注意 llm.chat 是协程 —— 这里必须 await。曾经漏了 await，
    拿到的是 coroutine 对象，取 .content 直接 AttributeError，
    被 except 吞掉后退回朴素拼接：**LLM 摘要从未真正生效过**，
    日志里只留一条 warning，很容易被当成噪音忽略。
    """
    turns = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in evicted)
    try:
        from .. import llm

        base = f"这是已有的对话摘要：\n{summary_old}\n" if summary_old else ""
        out = await llm.chat(
            task="gen",
            messages=[
                {"role": "system",
                 "content": "你是会话记忆压缩器。把下面的旧对话合并进已有摘要，产出不超过300字的中文要点，"
                            "保留：出发/到达站、车次、席别、日期、已下单/退票的订单、用户偏好；不要客套。"},
                {"role": "user", "content": base + f"\n新增旧对话：\n{turns}"},
            ],
            temperature=0.0,
        )
        if out and out.content and out.content.strip():
            return out.content.strip()[:SUMMARY_MAX]
    except Exception as e:  # noqa: BLE001
        logger.warning("摘要压缩失败，退朴素拼接：%s", e)
    # 朴素回退：只取被淘汰轮次里的用户话，截到上限
    user_only = "；".join(m.get("content", "") for m in evicted if m.get("role") == "user")
    merged = ((summary_old + " " + user_only) if summary_old else user_only).strip()
    return merged[:SUMMARY_MAX]


async def append(user_id, user_text, reply, session=""):
    """记一轮 (user, assistant) 到指定会话；超窗触发上下文压缩并保留近期原文。

    协程 —— 压缩要走 LLM，调用方需 await。
    """
    if not user_text or not reply:
        return
    st = _read(user_id, session)
    msgs = st.get("msgs", [])
    if msgs and msgs[-1].get("role") == "user":
        msgs.pop()
    msgs.append({"role": "user", "content": user_text[:800]})
    msgs.append({"role": "assistant", "content": reply[:1500]})

    summary = st.get("summary", "")
    if len(msgs) > RAW_LIMIT:
        evicted, keep = msgs[:-RAW_LIMIT], msgs[-RAW_LIMIT:]
        summary = await _compress(summary, evicted)
        msgs = keep

    _write(user_id, session, {"summary": summary, "msgs": msgs})


def clear(user_id, session=""):
    c = _client() if _redis else None
    if c:
        c.delete(_key(user_id, session))
    _fallback.pop((user_id, session or "default"), None)
