"""RAG 冒烟：灌入 data/docs 并做一次带部门过滤的检索。

用法（WSL，嵌入服务 8001 已在跑）：
    python3 scripts/rag_test.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend-python"))

from app.rag.store import RagStore  # noqa: E402


async def main() -> None:
    store = RagStore()
    print("rag.enabled =", store.enabled)
    if not store.enabled:
        print("RAG 不可用：查看上方日志原因")
        return
    n = await store.ingest_dir()
    print("ingested paragraphs =", n)
    hits = await store.retrieve("学生票有什么优惠条件", "技术部", top_k=3)
    print("hits =", len(hits))
    for h in hits:
        print(" -", h[:80])


if __name__ == "__main__":
    asyncio.run(main())
