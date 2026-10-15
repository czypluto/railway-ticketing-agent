"""页级索引单测（回归）。

背景：`parent_id` 是**每个文档各自从 1 开始编号**的。早期实现把页索引的 key
写成 `parent_id`，导致不同文档的同号页塌进同一条记录——正文互相覆盖（后入库的
文档内容整段丢失）、部门标签串味（受限文档继承了公开文档的 all，对所有人可见）。
实测表现：问"退票手续费多少"也返回值班规程的正文。

纯逻辑，不需要 Milvus。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.store import build_page_index, visible  # noqa: E402


def _row(src, pid, text, dept, page_no, heading=""):
    return {"parent_id": pid, "source": src, "parent_text": text,
            "dept_tags": dept, "page_no": page_no, "heading": heading}


def test_same_page_number_in_different_docs_does_not_collide():
    rows = [
        _row("公开.md", 1, "公开文档的第一页", "all", 1),
        _row("受限.md", 1, "受限文档的第一页", "综合管理部", 1),
        _row("受限.md", 2, "受限文档的第二页", "综合管理部", 2),
    ]
    idx = build_page_index(rows)

    assert idx.total == 3, "不同文档的同号页不能被合并"
    assert idx.pages[("公开.md", 1)]["text"] == "公开文档的第一页"
    assert idx.pages[("受限.md", 1)]["text"] == "受限文档的第一页"
    assert idx.pages[("受限.md", 2)]["text"] == "受限文档的第二页"


def test_department_tags_do_not_leak_across_documents():
    """受限文档不能因为与公开文档同号页而继承 all，否则对所有人可见。"""
    rows = [
        _row("公开.md", 1, "公开内容", "all", 1),
        _row("受限.md", 1, "受限内容", "综合管理部", 1),
    ]
    idx = build_page_index(rows)

    assert idx.pages[("受限.md", 1)]["dept"] == {"综合管理部"}
    assert visible(idx.pages[("受限.md", 1)]["dept"], "技术部") is False
    assert visible(idx.pages[("受限.md", 1)]["dept"], "综合管理部") is True
    assert visible(idx.pages[("公开.md", 1)]["dept"], "技术部") is True


def test_multiple_tags_on_one_page_are_merged():
    rows = [
        _row("A.md", 1, "同一页的内容", "技术部", 1),
        _row("A.md", 1, "同一页的内容", "人事部", 1),
    ]
    idx = build_page_index(rows)

    assert len(idx.pages) == 1
    assert idx.pages[("A.md", 1)]["dept"] == {"技术部", "人事部"}


def test_bm25_stats_cover_all_pages():
    rows = [
        _row("A.md", 1, "退票手续费", "all", 1),
        _row("B.md", 1, "学生票优惠", "all", 1),
    ]
    idx = build_page_index(rows)

    assert idx.total == 2
    assert idx.avgdl > 0
    # _count_tokens 用 CJK 重叠 bigram 切词，词元是双字，不是单字
    assert idx.df.get("退票") == 1
    assert idx.df.get("退") is None


# ── 倒排结构（替代「每页一个 tf dict」）─────────────────────────────

def test_postings_are_flat_int_arrays():
    """倒排用扁平 array('i')，不是每页一个 dict —— 这是内存优化的落点。"""
    from array import array

    rows = [
        _row("A.md", 1, "退票手续费", "all", 1),
        _row("B.md", 1, "退票规则", "all", 1),
    ]
    idx = build_page_index(rows)

    post = idx.postings["退票"]
    assert isinstance(post, array), type(post)
    assert len(post) % 2 == 0, "必须是 [idx, cnt, idx, cnt, ...] 的扁平结构"
    assert idx.df["退票"] == 2, "df 由 postings 长度推出"
    # 页记录里不再持有 tf dict
    assert "tf" not in idx.pages[("A.md", 1)]


def test_rank_filters_by_department():
    """权限过滤必须作用在打分阶段：受限页对无权限部门不可见。"""
    rows = [
        _row("公开.md", 1, "退票手续费规定", "all", 1),
        _row("受限.md", 1, "退票手续费内部细则", "综合管理部", 1),
    ]
    idx = build_page_index(rows)

    tech = [k for k, _ in idx.rank(["退票"], "技术部", limit=10)]
    mgmt = [k for k, _ in idx.rank(["退票"], "综合管理部", limit=10)]

    assert ("受限.md", 1) not in tech, "技术部不该检索到综合管理部的页"
    assert ("受限.md", 1) in mgmt
    assert ("公开.md", 1) in tech


def test_rank_ignores_pages_without_query_tokens():
    """只返回 score > 0 的页 —— 这是「只扫命中页」与旧实现等价的前提。"""
    rows = [
        _row("A.md", 1, "退票手续费", "all", 1),
        _row("B.md", 1, "学生票优惠", "all", 1),
    ]
    idx = build_page_index(rows)

    hits = [k for k, _ in idx.rank(["退票"], "技术部", limit=10)]
    assert ("A.md", 1) in hits
    assert ("B.md", 1) not in hits


def test_rank_is_stable_across_runs():
    """并列时按 (来源, 页) 定序，同一输入必须给出同样顺序。"""
    rows = [_row("A.md", 1, "退票手续费", "all", 1),
            _row("B.md", 1, "退票手续费", "all", 1)]
    idx = build_page_index(rows)
    runs = [[k for k, _ in idx.rank(["退票"], "技术部", limit=10)] for _ in range(5)]
    assert all(r == runs[0] for r in runs), runs
