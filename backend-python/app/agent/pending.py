"""Agent 挂起状态（待确认下单意向 / 待支付订单 / 最近查票结果）—— 落 Redis。

替代 orchestrator.py 里原来的三个进程内 dict：

    self._rows     -> get/set(uid, "rows")      最近查票结果（grounding 用）
    self._intents  -> get/set(uid, "intent")    待确认的下单意向
    self._orders   -> get/set(uid, "order")     刚下单的待支付 requestId

为什么必须出进程内存：
  1. 多副本部署时，原来的 key 只是进程内的 user_id —— 用户的「挂起」落在
     副本 A 的内存里，下一轮请求被负载均衡到副本 B 就读不到了，表现是
     「刚说确认，系统说没有待确认的单」。
  2. 进程重启（发版、OOM）会把所有人的挂起状态清零。

存储选型：Redis 而不是新起 Postgres —— 见 infra/docker-compose.yml，
Redis 已在栈内，且已开 AOF（`--appendonly yes`），重启不丢。

降级：沿用 memory.py 的策略，Redis 不可用时退回进程内 dict，绝不因此抛错。
      注意 —— 降级期间上面的两个问题会重新出现，所以日志是 warning 级。
"""
import json
import logging
import os
import uuid

logger = logging.getLogger(__name__)

KEY_PREFIX = "resv:agent:"
TTL_SEC = 900                # 15 分钟：用户超过这么久没回来，意向自动作废并释放
FIELDS = ("rows", "intent", "order")

_fallback: dict[tuple, str] = {}

try:
    import redis as _redis

    def _client():
        try:
            host = os.getenv("MEMORY_REDIS_HOST", "127.0.0.1")
            port = int(os.getenv("MEMORY_REDIS_PORT", "6379"))
            c = _redis.Redis(host=host, port=port, decode_responses=True,
                             socket_timeout=1)
            c.ping()
            return c
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis 不可用，挂起状态退回进程内存：%s", e)
            return None
except Exception:  # noqa: BLE001
    _redis = None


def _key(user_id, field: str) -> str:
    return f"{KEY_PREFIX}{user_id}:{field}"


def new_intent_id() -> str:
    """生成一次下单意向的标识。

    前端在收到 confirm 事件时保存它，用户点「确认」时原样带回 ——
    后端比对成功才执行。这样「确认」这个动作就离开了自然语言，
    不再需要从「好的」这样的词里去猜用户意图。
    """
    return uuid.uuid4().hex[:12]


def get(user_id, field: str):
    """读一个字段；不存在返回 None。"""
    c = _client() if _redis else None
    if c:
        raw = c.get(_key(user_id, field))
    else:
        raw = _fallback.get((user_id, field))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        logger.warning("挂起状态反序列化失败 user=%s field=%s", user_id, field)
        return None


def set(user_id, field: str, value) -> None:
    """写一个字段并续期 TTL。"""
    raw = json.dumps(value, ensure_ascii=False)
    c = _client() if _redis else None
    if c:
        # 用 set(ex=) 而不是 setex —— redis-py 8 起 setex 已标记 deprecated
        c.set(_key(user_id, field), raw, ex=TTL_SEC)
    else:
        _fallback[(user_id, field)] = raw


def pop(user_id, field: str):
    """取出并删除 —— 用于「确认后即消费」的意向，避免重复执行。"""
    value = get(user_id, field)
    drop(user_id, field)
    return value


def drop(user_id, field: str) -> None:
    c = _client() if _redis else None
    if c:
        c.delete(_key(user_id, field))
    _fallback.pop((user_id, field), None)


def clear(user_id) -> None:
    """清掉该用户的全部挂起状态（切会话/登出时用）。"""
    for f in FIELDS:
        drop(user_id, f)
