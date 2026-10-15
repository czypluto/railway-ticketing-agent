"""RAG 页索引容量基准：构建内存/耗时 + 查询延迟。

语料从「演示规模」长大时，用它量索引还能撑多少。
`build_page_index` 是纯函数，所以**不需要 Milvus、不需要嵌入服务**，
只依赖服务本身的 Python 环境（pymilvus 都不用装）。

用法（在 backend-python 的 venv 里跑）：

    python scripts/capacity/bench.py --pages 100000 --impl new
    python scripts/capacity/bench.py --pages 100000 --impl old    # 对比重构前

    --pages N        页数
    --impl new|old   新实现（单倒排 + array('i')）/ 重构前（每页一个 tf dict）
    --mode real|random
                     real   用 data/docs 真实语料重组成页（bigram 分布接近真实）
                     random 随机汉字（词表上界，只能小规模跑）
    --queries K      每个查询重复次数（默认 5，0 表示只测构建）
    --breakdown      额外打印内存构成明细
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend-python"
DOCS = BACKEND / "data" / "docs"
sys.path.insert(0, str(BACKEND))

from app.rag.store import _count_tokens, _zh_tokens, build_page_index, visible  # noqa: E402

PAGE_CHARS = 600
CJK = [chr(c) for c in range(0x4E00, 0x4E00 + 3000)]

# 取词面覆盖不同类型的查询：术语型 / 常识型 / 冷门型
QUERIES = [
    "退票手续费多少", "儿童票怎么买", "学生票优惠", "行李超重怎么办",
    "开车前48小时退票", "实名制购票规定", "宠物能不能带上车", "危险品有哪些",
    "列车晚点怎么赔偿", "常旅客积分怎么算", "候车室在哪", "值班交接怎么规定",
]


# ── 重构前的参照实现（仅用于容量对比，生产路径不要引用）──────────────

def legacy_build(rows):
    """每页存一个 tf dict —— 重构前的做法。"""
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


def legacy_query(pages, df, total, avgdl, query, department, limit):
    """重构前的查询：先按部门滤出全部页，再逐页算 BM25。"""
    qt = _zh_tokens(query)
    allowed = [k for k, r in pages.items() if visible(r["dept"], department)]
    k1, b = 1.5, 0.75
    n = max(total, 1)
    avgdl_ = max(avgdl, 1.0)
    scored = []
    for key in allowed:
        rec = pages[key]
        score = 0.0
        for t in set(qt):
            f = rec["tf"].get(t, 0)
            if not f:
                continue
            d = df.get(t, 0) or 0
            idf = math.log(1.0 + (n - d + 0.5) / (d + 0.5))
            score += idf * (f * (k1 + 1.0)) / (f + k1 * (1.0 - b + b * rec["dl"] / avgdl_))
        if score > 0:
            scored.append((score, key))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [k for _, k in scored[:limit]]


# ── 语料合成 ──────────────────────────────────────────────────────────

def load_sentences() -> list[str]:
    out: list[str] = []
    for p in sorted(DOCS.glob("*.md")):
        t = re.sub(r"^---.*?---", "", p.read_text(encoding="utf-8"), flags=re.S)
        t = re.sub(r"[#>*`\-|]", "", t)                       # 去 markdown 记号
        out += [s for s in re.split(r"[。！？；\n]", t) if len(s) >= 12]
    return out


def make_rows(n: int, sents: list[str], mode: str) -> list[dict]:
    rows = []
    need = max(1, PAGE_CHARS // 40)
    for i in range(n):
        text = ("".join(random.choices(sents, k=need)) if mode == "real"
                else "".join(random.choices(CJK, k=PAGE_CHARS)))
        rows.append({"parent_id": i + 1, "source": f"doc{i % 200}.md",
                     "parent_text": text, "dept_tags": "all",
                     "page_no": i + 1, "heading": "容量测试章节"})
    return rows


def rss_mb() -> float:
    try:
        for line in open("/proc/self/status", encoding="utf-8"):
            if line.startswith("VmRSS"):
                return int(line.split()[1]) / 1024
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def deep(obj, seen=None) -> int:
    """递归算对象真实占用（sys.getsizeof 不递归）。"""
    seen = seen if seen is not None else set()
    if id(obj) in seen:
        return 0
    seen.add(id(obj))
    size = sys.getsizeof(obj)
    if isinstance(obj, dict):
        for k, v in obj.items():
            size += deep(k, seen) + deep(v, seen)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            size += deep(v, seen)
    return size


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=10000)
    ap.add_argument("--impl", choices=["new", "old"], default="new")
    ap.add_argument("--mode", choices=["real", "random"], default="real")
    ap.add_argument("--queries", type=int, default=5)
    ap.add_argument("--breakdown", action="store_true")
    args = ap.parse_args()

    random.seed(42)
    base = rss_mb()
    rows = make_rows(args.pages, load_sentences(), args.mode)
    rss_rows = rss_mb()

    t0 = time.perf_counter()
    if args.impl == "old":
        pages, df, total, avgdl = legacy_build(rows)
    else:
        idx = build_page_index(rows)
    build_sec = time.perf_counter() - t0
    rss_after = rss_mb()

    out = {
        "impl": args.impl, "pages": args.pages, "mode": args.mode,
        "build_sec": round(build_sec, 2),
        "index_rss_mb": round(rss_after - rss_rows, 1),
        "total_rss_mb": round(rss_after - base, 1),
        "df_distinct": len(df) if args.impl == "old" else len(idx.df),
    }

    if args.impl == "new":
        out["postings_buffer_mb"] = round(
            sum(len(a) * 4 for a in idx.postings.values()) / 1024 / 1024, 1)

    if args.queries:
        times = []
        for _ in range(args.queries):
            for q in QUERIES:
                t0 = time.perf_counter()
                if args.impl == "old":
                    legacy_query(pages, df, total, avgdl, q, "技术部", 10)
                else:
                    idx.rank(_zh_tokens(q), "技术部", limit=10)
                times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        out |= {
            "query_p50_ms": round(times[len(times) // 2], 2),
            "query_p95_ms": round(times[int(len(times) * 0.95)], 2),
            "query_max_ms": round(times[-1], 2),
        }

    if args.breakdown and args.impl == "new":
        text_sz = sum(deep(r["text"]) for r in idx.pages.values())
        dept_sz = sum(deep(r["dept"]) for r in idx.pages.values())
        post_sz = sum(deep(a) for a in idx.postings.values())
        tot = text_sz + dept_sz + post_sz + deep(idx.df) + deep(idx.keys)
        kb = 1024
        out["breakdown"] = {
            "text_kb_per_page": round(text_sz / max(idx.total, 1) / kb, 1),
            "dept_kb_per_page": round(dept_sz / max(idx.total, 1) / kb, 1),
            "postings_kb_per_page": round(post_sz / max(idx.total, 1) / kb, 1),
            "postings_share_pct": round(post_sz / max(tot, 1) * 100),
        }

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
