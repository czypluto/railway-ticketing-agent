"""RAG 行级可见性单测：front matter 解析 + 可见性判定 + 过滤表达式。

纯逻辑，不需要 Milvus 与嵌入服务。
配置了 dept_tags 的文档只有对应部门可见，其余部门检索不到——这是"检索层权限"的基础。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.documents import read_frontmatter  # noqa: E402
from app.rag.store import dept_filter_expr, visible  # noqa: E402


# ---------- front matter 解析 ----------

def test_frontmatter_block_is_parsed_and_stripped():
    tags, body = read_frontmatter("---\ndept_tags: 综合管理部\n---\n正文内容")
    assert tags == ["综合管理部"]
    assert body == "正文内容"


def test_frontmatter_ignores_other_keys_and_multi_values():
    tags, body = read_frontmatter("---\ntitle: x\ndept_tags: 技术部, 人事部\n---\n正文")
    assert tags == ["技术部", "人事部"]
    assert body == "正文"


def test_frontmatter_bare_line_without_block():
    tags, body = read_frontmatter("dept_tags: 技术部\n正文")
    assert tags == ["技术部"]
    assert body == "正文"


def test_frontmatter_defaults_to_all_when_absent():
    tags, body = read_frontmatter("没有元数据的正文")
    assert tags == ["all"]
    assert body == "没有元数据的正文"


def test_frontmatter_all_wins_over_specific_departments():
    tags, _ = read_frontmatter("dept_tags: 技术部,all\n正文")
    assert tags == ["all"]


# ---------- 可见性判定 ----------

def test_visible_rules():
    assert visible({"all"}, "技术部") is True
    assert visible({"all"}, "") is True
    assert visible({"综合管理部"}, "综合管理部") is True
    assert visible({"综合管理部"}, "技术部") is False
    # 用户没有部门时只能看到公开文档
    assert visible({"综合管理部"}, "") is False
    assert visible({"综合管理部"}, "  ") is False


def test_dept_filter_expr_matches_visible_semantics():
    # 向量路的表达式与 BM25 路的 visible() 必须同义
    assert dept_filter_expr("技术部") == 'dept_tags in ["all", "技术部"]'
    assert dept_filter_expr("") == 'dept_tags in ["all", ""]'


def test_dept_filter_expr_escapes_quotes():
    # 部门名含引号时不能破坏 Milvus 表达式
    assert dept_filter_expr('技术"部') == 'dept_tags in ["all", "技术\\"部"]'
