"""挂起状态单测：状态出进程内存、按用户隔离、intent_id 生成、Redis 真实落盘。

原来 _rows/_intents/_orders 是 orchestrator 里的三个进程内 dict ——
多副本部署会串号、重启会丢。这里覆盖替换后的行为。
"""
import pytest

from app.agent import pending


@pytest.fixture
def offline():
    """强制走进程内存降级路径。

    必须保存/恢复 —— 直接改 pending._redis 会永久污染同会话的其它用例，
    导致「验证真实落盘」的那条被静默跳过（踩过一次）。
    """
    original = pending._redis
    pending._redis = None
    pending._fallback.clear()
    yield
    pending._redis = original
    pending._fallback.clear()


def test_set_get_pop_drop(offline):
    pending.set(1, "intent", {"intent_id": "x", "tripId": 2})
    assert pending.get(1, "intent")["tripId"] == 2
    # pop = 取出即消费：确认后立刻清掉，避免同一次意向被重复执行
    got = pending.pop(1, "intent")
    assert got["intent_id"] == "x"
    assert pending.get(1, "intent") is None


def test_missing_field_returns_none(offline):
    assert pending.get(42, "intent") is None
    assert pending.get(42, "order") is None
    assert pending.get(42, "rows") is None


def test_state_is_isolated_per_user(offline):
    """关键回归：状态按用户隔离。

    原实现靠 dict 的 key 是 user_id；现在靠 Redis key 前缀里的 user_id。
    两种方式都必须保证 A 用户的挂起意向不会被 B 用户读到。
    """
    pending.set(1, "intent", {"intent_id": "u1"})
    pending.set(2, "intent", {"intent_id": "u2"})
    assert pending.get(1, "intent")["intent_id"] == "u1"
    assert pending.get(2, "intent")["intent_id"] == "u2"

    pending.clear(1)
    assert pending.get(1, "intent") is None
    assert pending.get(2, "intent") is not None, "清一个用户不该影响另一个"


def test_fields_do_not_collide(offline):
    """rows / intent / order 三个字段互不覆盖。"""
    pending.set(3, "rows", [{"tripId": 1}])
    pending.set(3, "intent", {"intent_id": "i"})
    pending.set(3, "order", "REQ1001")
    assert pending.get(3, "rows") == [{"tripId": 1}]
    assert pending.get(3, "intent")["intent_id"] == "i"
    assert pending.get(3, "order") == "REQ1001"


def test_intent_id_is_unique():
    ids = {pending.new_intent_id() for _ in range(500)}
    assert len(ids) == 500, "intent_id 必须唯一，否则串话防线失效"


def test_redis_really_persists_when_available():
    """Redis 在跑时，验证状态真的落到了 Redis（而不是进程内存），且带 TTL。

    注意：这个用例**不能**用 offline fixture —— 它要验的就是 Redis 路径本身。
    """
    if pending._redis is None:
        pytest.skip("当前环境未安装 redis 客户端")
    client = pending._client()
    if client is None:
        pytest.skip("Redis 不可达，跳过真实落盘验证")

    key = pending._key(777, "intent")
    try:
        pending.set(777, "intent", {"intent_id": "redis-check"})
        assert client.get(key) is not None, "应当写进了 Redis，而不是进程内存"
        ttl = client.ttl(key)
        assert 0 < ttl <= pending.TTL_SEC, f"TTL 异常：{ttl}"
        # 模块级 _fallback 不应被用到
        assert (777, "intent") not in pending._fallback
    finally:
        pending.clear(777)
    assert client.get(key) is None, "clear 应当把 Redis 里的键删掉"


def test_connection_failure_degrades_and_cools_down(monkeypatch):
    """连接中途坏掉：丢弃连接、进入冷却期，期间直接走进程内存，绝不抛错。

    连接复用（原实现每次读写都新建连接并 ping）换来的新风险是"连接坏了会一直坏"。
    这个用例钉住兜底：坏连接被丢弃后，读要退回进程内存，写要落进程内存，
    且在冷却期内不再尝试重连 —— Redis 真挂的时候，反复去等连接超时才是最贵的。
    """
    class Boom:
        def get(self, *a, **k):
            raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(pending, "_CLIENT", Boom())
    monkeypatch.setattr(pending, "_RETRY_AFTER", 0.0)
    pending._fallback.clear()

    assert pending.get(900, "intent") is None, "读失败不该抛出去"
    assert pending._CLIENT is None, "坏连接必须被丢弃"
    assert pending._RETRY_AFTER > 0, "必须进入冷却期"

    pending.set(900, "intent", {"intent_id": "x"})
    assert pending.get(900, "intent")["intent_id"] == "x", "冷却期内应落进程内存"
    pending._fallback.clear()
