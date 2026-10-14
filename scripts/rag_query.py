"""只看检索命中，不灌库。用法：python3 scripts/rag_query.py "<问题>" [部门]"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend-python"))

from app.rag.store import RagStore  # noqa: E402


async def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "开车前3天退票手续费收多少"
    dept = sys.argv[2] if len(sys.argv) > 2 else "技术部"
    store = RagStore()
    print("rag.enabled =", store.enabled)
    hits = await store.retrieve(query, dept, top_k=5)
    print(f"query={query!r} dept={dept} hits={len(hits)}")
    for h in hits:
        print(" -", h[:90])


if __name__ == "__main__":
    asyncio.run(main())
