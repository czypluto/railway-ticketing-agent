"""记忆模块单测：超窗触发压缩、近期原文保留、可引用、LLM 压缩真的被 await。"""
import asyncio

import pytest

from app.agent import memory as mem


@pytest.fixture
def offline():
    """强制走进程内存降级路径，并在用例结束后恢复。

    直接改 mem._redis 会永久污染同会话的其它用例（踩过一次），
    所以这里用 fixture 保存/恢复。
    """
    original = mem._redis
    mem._redis = None
    mem._fallback.clear()
    yield
    mem._redis = original
    mem._fallback.clear()


def test_compress_window_and_keep_recent(offline, monkeypatch):
    async def fake(summary_old, evicted):
        return "摘要占位"

    monkeypatch.setattr(mem, "_compress", fake)

    async def run():
        for i in range(9):  # 9 对 > RAW_LIMIT(10 条=5 对) 会触发多次压缩
            await mem.append(123, f"问题{i}", f"回答{i}")
        return mem.load_state(123)

    st = asyncio.run(run())
    assert len(st["msgs"]) <= mem.RAW_LIMIT, len(st["msgs"])
    assert st["summary"] == "摘要占位"
    # 近期原文仍保留最新几条，保证指代可用
    assert st["msgs"][-1]["content"] == "回答8"
    mem.clear(123)


def test_llm_compress_is_actually_awaited(offline, monkeypatch):
    """★ 回归用例。

    `_compress` 曾经漏了 await —— llm.chat 是协程，不 await 拿到的是 coroutine 对象，
    取 .content 抛 AttributeError，被 except 吞掉后静默退回朴素拼接。
    后果是 **LLM 摘要从未生效过**，日志里只留一条 warning，很容易被当噪音忽略。
    """
    called = {"n": 0}

    class FakeOut:
        content = "LLM 摘要"

    async def fake_chat(**kwargs):
        called["n"] += 1
        return FakeOut()

    from app import llm

    monkeypatch.setattr(llm, "chat", fake_chat)

    summary = asyncio.run(
        mem._compress("旧摘要", [{"role": "user", "content": "问题"}]))

    assert called["n"] == 1, "应当真的调用了一次 llm.chat"
    assert summary == "LLM 摘要", "应当采用 LLM 产出的摘要，而不是回退拼接"


def test_memory_still_recorded_after_window_fills(offline, monkeypatch):
    """★ 回归用例（修之前是红的）。

    `_compress` 的朴素回退分支里把 `SUMMARY_MAX` 写成了 `_SUMMARY_MAX`，
    NameError 又**在 try/except 之外**，于是从 `append` 抛出去、
    被 chat.py 的 except 吞掉，**`_write` 根本没执行**。

    净效果：对话超过 RAW_LIMIT（10 条 ≈ 5 轮）之后，
    每一轮都写不进记忆，且完全静默——日志只有一条"记忆写入失败"。
    """
    from app import llm

    async def boom(**kwargs):
        raise RuntimeError("LLM 挂了")      # 强制走回退分支

    monkeypatch.setattr(llm, "chat", boom)

    async def run():
        for i in range(9):                  # 9 对 > RAW_LIMIT，必然触发压缩
            await mem.append(456, f"问题{i}", f"回答{i}")
        return mem.load_state(456)

    st = asyncio.run(run())
    assert st["msgs"], "窗口填满后仍应写入记忆（曾经整轮丢失）"
    assert st["msgs"][-1]["content"] == "回答8"
    mem.clear(456)


def test_compress_falls_back_when_llm_fails(offline, monkeypatch):
    """LLM 不可用时有兜底：退朴素拼接，不抛错。"""
    from app import llm

    async def boom(**kwargs):
        raise RuntimeError("LLM 挂了")

    monkeypatch.setattr(llm, "chat", boom)
    out = asyncio.run(mem._compress("", [{"role": "user", "content": "开车前3天退票"}]))

    assert "开车前3天退票" in out, out
