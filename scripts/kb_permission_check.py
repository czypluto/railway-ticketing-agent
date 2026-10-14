"""验证 RAG 行级可见性：同一问题，不同部门检索到的来源应不同。

用法（WSL，嵌入服务 8001 已起、docs 已入库）：
    python3 scripts/kb_permission_check.py

预期：《值班与调度规程（综合管理部）》只出现在"综合管理部"的命中来源里，
"技术部"检索同一问题时不应命中它。命中来源以 [来源 ...] 前缀展示。
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend-python"))

from app.rag.store import RagStore  # noqa: E402

QUERY = "值班交接和应急响应是怎么规定的"
DEPARTMENTS = ["综合管理部", "技术部"]

_CITE = re.compile(r"\[来源 ([^\]]+)\]")


def sources(hits: list[str]) -> list[str]:
    out = []
    for h in hits:
        m = _CITE.search(h)
        out.append(m.group(1) if m else "?")
    return sorted(set(out))


async def main() -> None:
    store = RagStore()
    if not store.enabled:
        print("RAG 不可用：检查 pymilvus 是否安装、Milvus Lite 是否初始化成功")
        return
    print(f"问题：{QUERY}\n")
    for dept in DEPARTMENTS:
        hits = await store.retrieve(QUERY, dept, top_k=5)
        print(f"部门={dept!r}")
        print(f"  命中页数={len(hits)}")
        for s in sources(hits):
            print(f"  来源：{s}")
    print("\n预期：受限文档只在『综合管理部』下出现。")


if __name__ == "__main__":
    asyncio.run(main())
