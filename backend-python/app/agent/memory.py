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
import time

logger = logging.getLogger(__name__)

KEY_PREFIX = "mem:agent:"
TTL_SEC = 3600
RAW_LIMIT = 10               # 压缩后保留最近 ~5 轮原文（保证指代细节不丢）
SUMMARY_MAX = 1200           # 摘要上限（字）
# 高水位：涨到这个条数才压缩，压完裁回 RAW_LIMIT。
#
# 原来用 `> RAW_LIMIT` 触发 —— 压完是 10，下一轮 +2 立刻又超，于是**每轮都压一次**。
# 用量记账实测：~40 轮对话产生 34 次 gen 调用，**占掉 43% 的模型成本**。
# 加了水位后大约每 4 轮压一次（16→18 触发），压缩调用降到约 1/4。
#
# 代价：两轮压缩之间保留的原文更多（最多 18 条 ≈ 9 轮），每轮注入的上下文变大。
# 这是「少压几次」换「上下文略长」的取舍，靠 cost_report 的输入/输出占比来判断是否划算。
COMPRESS_AT = 16

_fallback: dict[int, dict] = {}

# 连接复用 + 失败冷却，与 pending.py 同一套做法（原来每次读写都新建连接并 ping）。
_CLIENT = None
_RETRY_AFTER = 0.0
COOLDOWN_SEC = 30.0


def _drop_client(reason: str) -> None:
    global _CLIENT, _RETRY_AFTER
    _CLIENT = None
    _RETRY_AFTER = time.monotonic() + COOLDOWN_SEC
    logger.warning("记忆 Redis 不可用（%s），%.0fs 内退回进程内存", reason, COOLDOWN_SEC)


try:
    import redis as _redis

    def _client():
        global _CLIENT
        if _CLIENT is not None:
            return _CLIENT
        if time.monotonic() < _RETRY_AFTER:
            return None
        try:
            host = os.getenv("MEMORY_REDIS_HOST", "127.0.0.1")
            port = int(os.getenv("MEMORY_REDIS_PORT", "6379"))
            c = _redis.Redis(host=host, port=port, decode_responses=True,
                             socket_timeout=1, socket_connect_timeout=1)
            c.ping()
            _CLIENT = c
            return c
        except Exception as e:  # noqa: BLE001
            _drop_client(str(e))
            return None
except Exception:  # noqa: BLE001
    _redis = None


def _key(user_id, session=""):
    # 记忆按会话隔离：mem:agent:{user}:{session}；缺省会话归入 default，兼容旧数据/单测
    return f"{KEY_PREFIX}{user_id}:{session or 'default'}"


def _read(user_id, session="") -> dict:
    empty = {"summary": "", "msgs": []}
    raw = None
    c = _client() if _redis else None
    if c:
        try:
            raw = c.get(_key(user_id, session))
        except Exception as e:  # noqa: BLE001
            _drop_client(str(e))
            c = None
    if not c:
        return _fallback.get((user_id, session or "default"), dict(empty))
    if not raw:
        return empty
    try:
        d = json.loads(raw)
        return {"summary": d.get("summary", ""), "msgs": d.get("msgs", [])}
    except Exception:  # noqa: BLE001
        return empty


def _write(user_id, session, state):
    c = _client() if _redis else None
    if c:
        try:
            c.setex(_key(user_id, session), TTL_SEC, json.dumps(state, ensure_ascii=False))
            return
        except Exception as e:  # noqa: BLE001
            _drop_client(str(e))
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
    if len(msgs) > COMPRESS_AT:
        evicted, keep = msgs[:-RAW_LIMIT], msgs[-RAW_LIMIT:]
        summary = await _compress(summary, evicted)
        msgs = keep

    _write(user_id, session, {"summary": summary, "msgs": msgs})


def clear(user_id, session=""):
    c = _client() if _redis else None
    if c:
        try:
            c.delete(_key(user_id, session))
        except Exception as e:  # noqa: BLE001
            _drop_client(str(e))
    _fallback.pop((user_id, session or "default"), None)
