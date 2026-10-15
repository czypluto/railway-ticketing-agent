"""倒排重构的等价性验证：新实现必须与旧实现**逐位一致**。

旧实现（每页存一个 tf dict、逐页算分）在本文件里冻结为参照，
用真实语料（data/docs 的 22 篇）跑一批 query × 部门组合，
断言两件事：
  1. 每页的 BM25 分数**精确相等**（不是近似）
  2. 排序后的 key 顺序**完全相同**

为什么分数能做到精确相等：
  两版都是按 `set(query_tokens)` 的顺序对同一页累加同样的项、同样的运算，
  累加顺序未变，所以浮点结果逐位相同。候选集不同不影响结果——
  旧实现里 score 恰为 0 的页本来就会被丢弃。

纯逻辑，不需要 Milvus。
"""
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.store import _count_tokens, build_page_index, visible  # noqa: E402

DOCS = Path(__file__).resolve().parent.parent / "data" / "docs"


# ── 冻结的旧实现（仅作对照，不要引用到生产路径）──────────────────────

def _legacy_build(rows):
    pages = {}
    for r in rows:
        pid = r.get("parent_id")
        if pid is None:
            continue
        src = r.get("source") or ""
        key = (src, pid)
        rec = pages.get(key)
        if rec is None:
            rec = {"text": r.get("parent_text") or "", "dept": set(), "source": src,
                   "page_no": r.get("page_no"), "heading": r.get("heading") or "",
                   "tf": {}, "dl": 0}
            pages[key] = rec
        rec["dept"].add(r.get("dept_tags") or "all")
    df = {}
    for rec in pages.values():
        rec["tf"] = _count_tokens(rec["text"])
        rec["dl"] = sum(rec["tf"].values())
        for t in rec["tf"]:
            df[t] = df.get(t, 0) + 1
    total = len(pages)
    avgdl = (sum(r["dl"] for r in pages.values()) / total) if total else 0.0
    return pages, df, total, avgdl


def _legacy_scores(pages, df, total, avgdl, query_tokens, department):
    k1, b = 1.5, 0.75
    n = max(total, 1)
    avgdl_ = max(avgdl, 1.0)
    scored = []
    for key, rec in pages.items():
        if not visible(rec["dept"], department):
            continue
        score = 0.0
        for t in set(query_tokens):
            f = rec["tf"].get(t, 0)
            if not f:
                continue
            d = df.get(t, 0) or 0
            idf = math.log(1.0 + (n - d + 0.5) / (d + 0.5))
            score += idf * (f * (k1 + 1.0)) / (f + k1 * (1.0 - b + b * rec["dl"] / avgdl_))
        if score > 0:
            scored.append((score, key))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored


# ── 语料与用例 ────────────────────────────────────────────────────────

def _corpus_rows():
    """把真实语料按 markdown 标题切页，构造与入库同形的 rows。"""
    rows = []
    for f in sorted(DOCS.glob("*.md")):
        text = re.sub(r"^---.*?---", "", f.read_text(encoding="utf-8"), flags=re.S)
        blocks = [b.strip() for b in re.split(r"\n(?=#{1,3} )", text)]
        is_internal = "综合管理部" in f.name                 # 复现受限文档
        for i, b in enumerate(blocks, start=1):
            if not b:
                continue
            rows.append({
                "parent_id": i,
                "source": f.name,
                "parent_text": b[:1200],
                "dept_tags": "综合管理部" if is_internal else "all",
                "page_no": i,
                "heading": b.splitlines()[0].lstrip("# ")[:40],
            })
    return rows


QUERIES = [
    "退票手续费多少", "儿童票怎么买", "学生票优惠", "行李超重",
    "开车前48小时退票", "实名制购票", "宠物能不能带上车", "危险品种类",
    "晚点怎么赔偿", "常旅客积分", "候车室在哪", "值班交接怎么规定",
    "票价如何计算", "团体购票", "遗失物品招领", "安检规定",
    "导盲犬", "电子客票", "投诉处理", "合同责任",
]
DEPTS = ["技术部", "综合管理部", "客运部", ""]


def test_new_index_matches_frozen_legacy_bit_for_bit():
    rows = _corpus_rows()
    idx = build_page_index(rows)
    l_pages, l_df, l_total, l_avgdl = _legacy_build(rows)

    # 索引规模必须一致
    assert idx.total == l_total
    assert idx.avgdl == l_avgdl
    assert idx.df == l_df, "df 表必须完全一致"

    checked = 0
    for q in QUERIES:
        for dept in DEPTS:
            qt = _tokens(q)
            if not qt:
                continue
            new = idx.rank(qt, dept, limit=len(rows))
            old = _legacy_scores(l_pages, l_df, l_total, l_avgdl, qt, dept)

            # 1) 分数逐位相等（不是近似）
            assert [k for k, _ in new] == [k for _, k in old], (
                f"排序不一致 q={q!r} dept={dept!r}\n"
                f"新: {[k for k, _ in new][:5]}\n旧: {[k for _, k in old][:5]}")
            assert [s for _, s in new] == [s for s, _ in old], (
                f"分数不是精确相等 q={q!r} dept={dept!r}")
            checked += 1

    assert checked >= 40, f"覆盖用例太少：{checked}"


def _tokens(q):
    from app.rag.store import _zh_tokens
    return _zh_tokens(q)
