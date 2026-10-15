"""倒排的候选集占比：新实现「只扫命中页」相对旧实现（扫全部页）省了多少。

占比越低，查询优势越大。**这也是解释 bench.py 查询提速为什么因查询而异的关键**：
本脚本用 22 篇真实语料**重组**成页，常见 bigram 几乎出现在每一页，
所以算出的占比偏**高**；真实异构语料下占比会低得多，新实现优势更大。
=> bench.py 给出的查询提速是**下界**。

用法：
    python scripts/capacity/candidate_ratio.py --pages 100000
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend-python"))

from app.rag.store import _zh_tokens, build_page_index  # noqa: E402

_src = (Path(__file__).with_name("bench.py")).read_text(encoding="utf-8")
exec(_src.split("def main()")[0])          # 复用 load_sentences / make_rows

QUERIES = ["退票手续费多少", "宠物能不能带上车", "值班交接怎么规定",
           "常旅客积分怎么算", "危险品有哪些", "学生票优惠"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=100000)
    args = ap.parse_args()

    random.seed(42)
    idx = build_page_index(make_rows(args.pages, load_sentences(), "real"))

    print(f"总页数 {idx.total:,}   不同 bigram {len(idx.df):,}")
    print(f"旧实现每次查询扫描量 = 全部 {idx.total:,} 页")
    print()
    print(f"{'查询':<18}{'候选页':>10}{'占比':>9}")
    print("-" * 40)
    for q in QUERIES:
        cand = set()
        for t in set(_zh_tokens(q)):
            post = idx.postings.get(t)
            if post:
                cand.update(post[i] for i in range(0, len(post), 2))
        print(f"{q:<18}{len(cand):>10,}{len(cand) / idx.total * 100:>8.1f}%")


if __name__ == "__main__":
    main()
