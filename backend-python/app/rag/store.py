"""RAG 存储与检索（Small-to-Big，带来源元数据）。

- 格式：md / docx / pdf，按 页+语义切片 入库（见 documents.py）
- 每条记录保留 source / page_no / heading，检索可给出处引用
- 嵌入经本地 /v1/embeddings（WeMM-Embedding-2B int8 GPU 或 bge-m3 CPU），向量 1024 维
- 注意：本文件 schema 若升级，需删除 data/rag.db 目录后重灌
"""
import logging
import math
import re
from array import array
from pathlib import Path
from typing import Optional

import httpx

from .. import config
from .documents import _split_markdown, chunk_sentences, load_pages, read_frontmatter

logger = logging.getLogger(__name__)

_available: Optional[bool] = None
_DIM = 1024
_PARENT_MAX = 20000


def rag_available() -> bool:
    global _available
    if _available is None:
        try:
            import pymilvus  # noqa: F401
            _available = True
        except ImportError:
            _available = False
    return _available


_singleton: Optional["RagStore"] = None


def get_store() -> "RagStore":
    """进程内共享的 RagStore 单例。

    Milvus Lite 对同一数据目录持有文件锁，同进程再开实例会冲突（服务与知识库管理接口须共用同一实例）。
    """
    global _singleton
    if _singleton is None:
        _singleton = RagStore()
    return _singleton


async def embed_texts(texts: list[str]) -> list[list[float]]:
    url = f"{config.EMBED_BASE}/v1/embeddings"
    payload = {"model": config.EMBED_MODEL, "input": texts[:64]}
    async with httpx.AsyncClient(timeout=240) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return [item["embedding"] for item in data["data"]]


async def embed_texts_images(images: list[str]) -> list[list[float]]:
    """图片嵌入（WeMM 视觉，需服务端支持 images）。"""
    url = f"{config.EMBED_BASE}/v1/embeddings"
    payload = {"model": config.EMBED_MODEL, "images": images}
    async with httpx.AsyncClient(timeout=240) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return [item["embedding"] for item in data["data"]]


def _clean(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')[: _PARENT_MAX]


def dept_filter_expr(department: str) -> str:
    """向量检索路的 Milvus 标量过滤：可见 dept_tags 为 all 或本部门。

    用户没有部门时退化为只听 all——即"没有部门的人只能看公开文档"。
    """
    return 'dept_tags in ["all", "%s"]' % _clean(department or "")


def visible(dept_tags, department: str) -> bool:
    """BM25 路过滤，语义必须与 dept_filter_expr 保持一致。

    记录未声明部门时入库为 all（全员可见）；用户没有部门时只能看到 all。
    """
    d = (department or "").strip()
    return ("all" in dept_tags) or (bool(d) and d in dept_tags)


class PageIndex:
    """页级关键词索引（BM25 用）。

    为什么不是「每页存一个 tf dict」：
      那是 N 个独立的 Python dict 对象。实测 8000 页时 tf 占 95% 内存
      （23 KB/页，而一页正文只有 0.9 KB）——25 倍开销。10 万页约 2.5 GB，
      而 `_ensure_page_index` 的 limit 是 200000（外推约 4.9 GB）。

    改成单个倒排 + 紧凑整型数组（`array('i')`）后：
      - 内存约为原来的 1/11
      - 打分只扫「含查询词元的页」，查询复杂度从 O(总页数) 降到 O(命中页)

    槽位：idx 0..total-1，`keys[idx]` 还原为 (来源, parent_id)。
    """

    __slots__ = ("keys", "pages", "depts", "dls", "postings", "df", "total", "avgdl")

    def __init__(self) -> None:
        self.keys: list[tuple[str, int]] = []
        self.pages: dict = {}          # key -> {text, dept, source, page_no, heading}
        self.depts: list[set] = []     # idx -> dept 集合（按引用共享，不额外占内存）
        self.dls: list[int] = []       # idx -> 页长度（tf 计数之和）
        self.postings: dict = {}       # token -> array('i')，扁平 [idx, cnt, idx, cnt, ...]
        self.df: dict = {}             # token -> 含该词的页数
        self.total = 0
        self.avgdl = 0.0

    def rank(self, query_tokens: list[str], department: str,
             limit: int) -> list[tuple[tuple[str, int], float]]:
        """BM25 打分排序，返回 [(key, score)]，只含 score > 0 的页。

        与旧的「逐页取 tf dict 算分」**逐位等价**：
        - 候选集 = 至少含一个查询词元的页；旧实现里 score 恰为 0 的页本来也会被丢掉
        - 每页的累加顺序仍是 `set(query_tokens)` 的迭代顺序，与旧实现相同
        - 公式、k1/b、idf、dl、avgdl 全部照旧
        """
        if not self.total:
            return []
        k1, b = 1.5, 0.75
        n = max(self.total, 1)
        avgdl = max(self.avgdl, 1.0)

        cand: dict[int, float] = {}
        for t in set(query_tokens):
            post = self.postings.get(t)
            if not post:
                continue
            df = self.df.get(t, 0)
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            for i in range(0, len(post), 2):
                pi = post[i]
                # 权限过滤只对候选页判定，比旧实现「先遍历全部页过滤」更省
                if not visible(self.depts[pi], department):
                    continue
                f = post[i + 1]
                dl = self.dls[pi] or 1
                cand[pi] = cand.get(pi, 0.0) + idf * (f * (k1 + 1.0)) / (
                    f + k1 * (1.0 - b + b * dl / avgdl))

        scored = [(s, self.keys[pi]) for pi, s in cand.items() if s > 0]
        # 并列时按 (来源, 页) 定序，保证可复现
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [(key, s) for s, key in scored[:limit]]


def build_page_index(rows: list[dict]) -> PageIndex:
    """由 Milvus 行构造「页」级索引。

    **key 必须是 (来源, parent_id)**：parent_id 是每个文档各自从 1 开始编号的，
    只用 parent_id 会让不同文档的同号页互相覆盖——正文丢失、部门标签串味、
    检索结果张冠李戴。抽成纯函数是为了能被单测钉住（见 tests/test_rag_index.py）。
    """
    idx = PageIndex()
    for r in rows:
        pid = r.get("parent_id")
        if pid is None:
            continue
        src = r.get("source") or ""
        key = (src, pid)
        rec = idx.pages.get(key)
        if rec is None:
            rec = {
                "text": r.get("parent_text") or "",
                "dept": set(),
                "source": src,
                "page_no": r.get("page_no"),
                "heading": r.get("heading") or "",
            }
            idx.pages[key] = rec
            idx.keys.append(key)
        rec["dept"].add(r.get("dept_tags") or "all")

    # 单倒排：token -> 扁平 [idx, cnt, ...]；df 由 postings 长度推出，不单独维护。
    postings: dict[str, array] = {}
    for i, key in enumerate(idx.keys):
        rec = idx.pages[key]
        tf = _count_tokens(rec["text"])
        dl = sum(tf.values())
        idx.dls.append(dl)
        idx.depts.append(rec["dept"])
        for t, c in tf.items():
            postings.setdefault(t, array("i")).extend((i, c))

    idx.postings = postings
    idx.df = {t: len(p) // 2 for t, p in postings.items()}
    idx.total = len(idx.keys)
    idx.avgdl = (sum(idx.dls) / idx.total) if idx.total else 0.0
    return idx


# ---- 中文词面检索：CJK 用重叠 bigram，英文/数字整词；无需分词依赖，规则/条款术语命中稳定 ----
_CJK_RUN = re.compile(r"[一-鿿]+|[a-z0-9]+")


def _zh_tokens(text: str) -> list[str]:
    out: list[str] = []
    for m in _CJK_RUN.finditer(text.lower()):
        s = m.group()
        if "一" <= s[0] <= "鿿":
            if len(s) >= 2:
                out.extend(s[i:i + 2] for i in range(len(s) - 1))
            else:
                out.append(s)
        else:
            out.append(s)
    return out


def _count_tokens(text: str) -> dict:
    counts: dict[str, int] = {}
    for t in _zh_tokens(text):
        counts[t] = counts.get(t, 0) + 1
    return counts


class RagStore:
    def __init__(self) -> None:
        self.enabled = rag_available()
        self._client = None
        self._collection = None
        # 页级关键词索引（懒加载，供 BM25 检索路提升召回；增删文档后失效重建）
        self._page_index: Optional[PageIndex] = None
        if not self.enabled:
            logger.warning("未安装 pymilvus，RAG 关闭")
            return
        try:
            from pymilvus import MilvusClient

            config.RAG_DB.parent.mkdir(parents=True, exist_ok=True)
            self._client = MilvusClient(str(config.RAG_DB))
            self._ensure_collection()
        except Exception as e:  # noqa: BLE001
            logger.warning("RAG 初始化失败，已关闭：%s", e)
            self.enabled = False

    def _ensure_collection(self) -> None:
        from pymilvus import DataType

        name = "small"
        if self._client.has_collection(name):
            self._client.load_collection(name)
            self._collection = name
            return
        schema = self._client.create_schema(auto_id=True, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("parent_id", DataType.INT64)
        schema.add_field("small_text", DataType.VARCHAR, max_length=512)
        schema.add_field("parent_text", DataType.VARCHAR, max_length=_PARENT_MAX)
        schema.add_field("dept_tags", DataType.VARCHAR, max_length=256, default_value="all")
        schema.add_field("source", DataType.VARCHAR, max_length=256)
        schema.add_field("page_no", DataType.INT64)
        schema.add_field("heading", DataType.VARCHAR, max_length=512)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=_DIM)
        index_params = self._client.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        self._client.create_collection(name, schema=schema, index_params=index_params)
        self._client.load_collection(name)
        self._collection = name

    async def ingest_dir(self) -> int:
        if not self.enabled:
            return 0
        if not config.DOCS_DIR.is_dir():
            return 0
        count = 0
        for path in sorted(config.DOCS_DIR.iterdir()):
            if path.suffix.lower() in (".md", ".docx", ".doc", ".pdf"):
                count += await self.ingest_file(path)
        return count

    def _query_ids_by_source(self, source: str) -> list:
        if not self.enabled or not self._collection:
            return []
        try:
            esc = source.replace('"', '\\"')
            res = self._client.query(
                collection_name=self._collection,
                filter=f'source == "{esc}"',
                output_fields=["id"],
                limit=16384)
            return [r["id"] for r in res]
        except Exception as e:  # noqa: BLE001
            logger.warning("按 source 查询失败 %s：%s", source, e)
            return []

    def delete_by_source(self, source: str) -> None:
        """删除某文件的全部向量（上传/替换/删除时用，避免改文档后新旧重复堆积）。"""
        ids = self._query_ids_by_source(source)
        if ids:
            try:
                self._client.delete(collection_name=self._collection, ids=ids)
            except Exception as e:  # noqa: BLE001
                logger.warning("按 source 删除失败 %s：%s", source, e)
        self._page_index = None  # 数据变了，关键词索引下次懒重建

    def count_by_source(self, source: str) -> int:
        return len(self._query_ids_by_source(source))

    def distinct_sources(self) -> set:
        """库中出现的全部 source 名（含历史残留）。"""
        if not (self.enabled and self._collection):
            return set()
        try:
            rows = self._client.query(
                collection_name=self._collection, output_fields=["source"], limit=200000, filter="")
        except Exception as e:  # noqa: BLE001
            logger.warning("枚举 source 失败：%s", e)
            return set()
        return {r.get("source") for r in rows if r.get("source")}

    def purge_missing(self, keep: set) -> int:
        """清除源文件已不在 docs 目录里的残留向量（历史遗留/误传留下的垃圾会抢占召回）。"""
        removed = 0
        for src in self.distinct_sources():
            if src not in keep:
                self.delete_by_source(src)
                removed += 1
        return removed

    async def ingest_file(self, path: Path, tags: Optional[list[str]] = None) -> int:
        """入库单个文件；按 source 先删旧再插入，可安全替换。

        md 支持顶部元数据声明可见性（dept_tags: 综合管理部）并在入库时剥离；docx/pdf 默认 all。
        """
        if not self.enabled or not self._collection:
            return 0
        self.delete_by_source(path.name)  # 先删同名旧向量，支持替换/去重

        pages = []
        meta: list[str] = tags or []
        if path.suffix.lower() == ".md":
            raw = path.read_text(encoding="utf-8", errors="replace")
            meta, body = read_frontmatter(raw)
            pages = _split_markdown(body)
        else:
            pages = load_pages(path)
        if not pages:
            return 0
        if not meta:
            meta = ["all"]

        rows, pid = [], 0
        for page in pages:
            pid += 1
            smalls = chunk_sentences(page.text)
            if not smalls:
                continue
            vecs = await embed_texts(smalls)
            parent = _clean(page.text)
            for tag in meta:
                for small, vec in zip(smalls, vecs):
                    rows.append({
                        "parent_id": pid,
                        "small_text": small,
                        "parent_text": parent,
                        "dept_tags": tag,
                        "source": path.name,
                        "page_no": page.page_no,
                        "heading": (page.heading or "")[:500],
                        "vector": vec,
                    })
        if rows:
            self._client.insert(collection_name=self._collection, data=rows)
        self._page_index = None  # 新内容入库，关键词索引下次懒重建
        return len(rows)

    async def ingest_images(self, files: list[Path]) -> int:
        """图片多模态入库：WeMM 图像嵌入；同目录同名 .txt 作说明文字一并入库。"""
        if not self.enabled or not files:
            return 0
        rows, pid_base = [], self.count()
        for i, f in enumerate(files):
            if f.suffix.lower() not in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
                continue
            caption = ""
            cap = f.with_suffix(".txt")
            if cap.exists():
                caption = cap.read_text(encoding="utf-8").strip()
            vec = (await embed_texts_images([str(f)]))[0]
            rows.append({
                "parent_id": pid_base + i + 1,
                "small_text": ("[图] " + caption)[:500] or "[图]",
                "parent_text": _clean(caption + (" " if caption else "") + f"[文件 {f.name}]"),
                "dept_tags": "all",
                "source": f.name,
                "page_no": 0,
                "heading": "[图片]" + (f" {caption[:60]}" if caption else ""),
                "vector": vec,
            })
        if rows:
            self._client.insert(collection_name=self._collection, data=rows)
        self._page_index = None  # 新内容入库，关键词索引下次懒重建
        return len(rows)

    def count(self) -> int:
        """当前集合记录数（灌库/校验用）。"""
        if not self.enabled:
            return 0
        try:
            rows = self._client.query(collection_name=self._collection,
                                      output_fields=["count(*)"], filter="")
            if rows:
                return int(rows[0].get("count(*)", 0))
        except Exception as e:  # noqa: BLE001
            logger.warning("count 失败：%s", e)
        return 0

    # ---------------- 混合检索：向量 + 中文 BM25，RRF 融合 ----------------
    def _ensure_page_index(self) -> None:
        """懒加载“页”级关键词索引（Big 粒度），key 为 (来源, parent_id)。

        增删文档后调用方把 _page_index 置 None 即触发重建。

        ⚠️ 全量拉取：一次 query 取回整个 collection（含每页正文）。
        容量实测（22 篇语料重组成页）：构建约 0.21 s / 1000 页，
        重建发生在请求路径里，期间的请求会被阻塞；到 limit 上限
        200000 页时外推约 42 s。语料再大需要改增量维护，
        或把倒排挪到独立检索组件。
        """
        if self._page_index is not None or not (self.enabled and self._collection):
            return
        try:
            rows = self._client.query(
                collection_name=self._collection,
                output_fields=["parent_id", "parent_text", "dept_tags", "source", "page_no", "heading"],
                limit=200000,
                filter="")
        except Exception as e:  # noqa: BLE001
            logger.warning("加载关键词索引失败：%s", e)
            rows = []
        self._page_index = build_page_index(rows)

    async def retrieve(self, query: str, department: str, top_k: int = 5,
                       mode: str = "hybrid") -> list[str]:
        """混合召回提升命中率：
        1) 向量命中小段 → 按页去重（语义定位，保留部门过滤）；
        2) 中文 BM25 词面命中页（补回向量没打中的术语页）；
        3) RRF 融合两路排序，取 top_k 页整文返回。

        ``mode`` 是给评测做消融用的显式开关，生产链路一律走默认值：
        - ``hybrid``：向量 + BM25，RRF 融合（默认，即线上行为）
        - ``vector``：只用向量路
        - ``bm25``：只用关键词路
        """
        if not self.enabled:
            return []

        # 1) 向量路（键为 (来源, 页)，与页索引对齐）
        vec_rank: list[tuple[str, int]] = []
        if mode in ("hybrid", "vector"):
            try:
                qv = (await embed_texts([query]))[0]
                expr = dept_filter_expr(department)
                res = self._client.search(
                    collection_name=self._collection,
                    data=[qv],
                    limit=top_k * 4,
                    output_fields=["parent_id", "source"],
                    filter=expr,
                    search_params={"metric_type": "COSINE"},
                )
                seen: set[tuple[str, int]] = set()
                for hit in res[0]:
                    ent = hit.get("entity") or {}
                    pid = ent.get("parent_id")
                    if pid is None:
                        continue
                    key = (ent.get("source") or "", pid)
                    if key in seen:
                        continue
                    seen.add(key)
                    vec_rank.append(key)
            except Exception as e:  # noqa: BLE001
                logger.warning("向量检索失败：%s", e)

        # 页索引在三种模式下都要建：向量路命中的键是 (来源, 页)，
        # 最终取正文与出处也要靠它。只在 BM25 分支里建会让 mode="vector" 取不到正文、结果为空。
        self._ensure_page_index()

        # 2) BM25 关键词路（补召回）
        # 倒排打分只扫「含查询词元的页」，不再遍历全部页；并列时按 (来源, 页) 定序，
        # 保证同一输入两次跑出同样顺序（评测可复现）。
        kw_rank: list[tuple[str, int]] = []
        if mode in ("hybrid", "bm25") and self._page_index:
            qt = _zh_tokens(query)
            if qt:
                kw_rank = [key for key, _ in
                           self._page_index.rank(qt, department, limit=max(top_k * 2, 8))]

        # 3) RRF 融合
        k = 60

        def _rrf(key: tuple[str, int]) -> float:
            score = 0.0
            for lst in (vec_rank, kw_rank):
                try:
                    rank = lst.index(key) + 1
                except ValueError:
                    continue
                score += 1.0 / (k + rank)
            return score

        # 并列时按 key 定序：set 的迭代顺序受解释器哈希随机化影响，
        # 直接 reverse=True 会让同一输入两次跑出不同顺序（评测不可复现）。
        order = sorted(set(vec_rank) | set(kw_rank), key=lambda k: (-_rrf(k), k))
        out: list[str] = []
        for key in order[:top_k]:
            rec = (self._page_index.pages if self._page_index else {}).get(key)
            if not rec:
                continue
            text = (rec.get("text") or "").strip()
            if not text:
                continue
            cite = rec.get("source") or ""
            if rec.get("page_no"):
                cite += f" 第{rec['page_no']}页"
            if rec.get("heading"):
                cite += f" · {rec['heading']}"
            out.append(f"[来源 {cite}]\n{text}")
        return out
