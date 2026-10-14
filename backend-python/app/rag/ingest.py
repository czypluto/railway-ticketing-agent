"""把 data/docs/*.md 灌入向量库。用法：python -m app.rag.ingest"""
import asyncio
import logging

from .. import config
from .store import get_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    if not config.DOCS_DIR.is_dir():
        logger.info("没有 data/docs 目录，跳过灌库")
        return
    store = get_store()
    if not store.enabled:
        logger.warning("RAG 不可用（缺 pymilvus 或初始化失败），灌库中止")
        return
    n = await store.ingest_dir()
    logger.info("灌库完成：%s 条段落", n)


if __name__ == "__main__":
    asyncio.run(main())
