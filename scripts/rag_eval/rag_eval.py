"""RAG 检索评测：三路检索对比 + 生成层评估。

先停掉 Python 主服务（Milvus Lite 是单写者锁），再在 WSL 里用独立评测环境跑：

    ~/rag-eval-venv/bin/python scripts/rag_eval/rag_eval.py            # 只跑检索层（快，零 API 调用）
    ~/rag-eval-venv/bin/python scripts/rag_eval/rag_eval.py --gen      # 追加生成层（会调 DeepSeek）
    ~/rag-eval-venv/bin/python scripts/rag_eval/rag_eval.py --gen --ragas   # 再追加 RAGAS 指标

指标：
  检索层 —— Hit@K（正确答案所在文档是否进入前 K）、MRR（首个命中的倒数排名）
  生成层 —— 关键词规则判分（确定性、可复现）+ 可选 RAGAS（忠实度/相关性/上下文精确率与召回率）
"""
import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend-python"
sys.path.insert(0, str(BACKEND))

GOLDEN = Path(__file__).resolve().parent / "golden_set.jsonl"

_CITE = re.compile(r"^\[来源 (.+?)\]")

GEN_SYSTEM = (
    "你是铁路客服助手。严格依据下面的【参考资料】回答用户问题；"
    "资料中没有的内容不要编造，直接说“资料中未提及”。"
)


def load_env():
    """从项目根 .env 读 DeepSeek key（不依赖 python-dotenv）。"""
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def load_golden() -> list[dict]:
    out = []
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def source_of(hit: str, known: list[str]) -> str | None:
    """从 `[来源 文件名.md 第N页 · 标题]` 里还原来源文件名。"""
    m = _CITE.match(hit)
    if not m:
        return None
    cite = m.group(1)
    for name in known:
        if cite.startswith(name):
            return name
    return None


async def retrieval_eval(store, cases: list[dict], known: list[str],
                         dept: str, top_k: int) -> dict:
    """对每个检索模式跑一遍，算 Hit@1 / Hit@K / MRR。"""
    modes = ["vector", "bm25", "hybrid"]
    report: dict = {"topK": top_k, "department": dept, "modes": {}}
    for mode in modes:
        hits_at_1 = 0
        hits_at_k = 0
        rr_sum = 0.0
        misses = []
        for c in cases:
            got = await store.retrieve(c["question"], dept, top_k=top_k, mode=mode)
            srcs = [s for s in (source_of(h, known) for h in got) if s]
            rank = srcs.index(c["source"]) + 1 if c["source"] in srcs else None
            if rank == 1:
                hits_at_1 += 1
            if rank:
                hits_at_k += 1
                rr_sum += 1.0 / rank
            else:
                misses.append({"id": c["id"], "q": c["question"],
                               "want": c["source"], "got": srcs[:top_k]})
        n = len(cases)
        report["modes"][mode] = {
            "hit@1": round(hits_at_1 / n, 4),
            "hit@K": round(hits_at_k / n, 4),
            "mrr": round(rr_sum / n, 4),
            "misses": misses,
        }
    return report


async def generate(question: str, contexts: list[str], model: str) -> str:
    import httpx

    base = os.getenv("DEEPSEEK_BASE", "https://api.deepseek.com")
    key = os.getenv("DEEPSEEK_API_KEY", "")
    ctx = "\n\n".join(contexts)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": GEN_SYSTEM},
            {"role": "user", "content": f"【参考资料】\n{ctx}\n\n【问题】{question}"},
        ],
        "temperature": 0,
    }
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{base}/chat/completions", json=payload,
                         headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def _norm(s: str) -> str:
    """去掉所有空白与常见标点差异，避免「50 千克」与「50千克」被判成不同答案。"""
    return re.sub(r"[\s　]", "", s or "")


def rule_grade(answer: str, keyword_groups: list[list[str]]) -> bool:
    """关键词规则判分：每个关键词组内命中任意一个即算该组通过，全部组通过才算答对。

    局限：只能验「关键信息有没有出现」，判不出「结论有没有被曲解」——
    后者要靠 RAGAS 的忠实度指标。所以两条路都跑，不互相替代。
    """
    a = _norm(answer)
    return all(any(_norm(alt) in a for alt in group) for group in keyword_groups)


async def generation_eval(store, cases: list[dict], known: list[str],
                          dept: str, top_k: int, model: str) -> dict:
    rows = []
    for c in cases:
        hits = await store.retrieve(c["question"], dept, top_k=top_k, mode="hybrid")
        try:
            ans = await generate(c["question"], hits, model)
        except Exception as e:  # noqa: BLE001
            ans = f"<生成失败：{e}>"
        ok = rule_grade(ans, c.get("keywords", []))
        rows.append({
            "id": c["id"], "question": c["question"], "reference": c["answer"],
            "response": ans, "contexts": hits, "rule_pass": ok,
        })
        mark = "✅" if ok else "❌"
        print(f"  {mark} {c['id']} {c['question'][:34]}")
    n = len(rows)
    return {
        "rule_accuracy": round(sum(r["rule_pass"] for r in rows) / n, 4),
        "samples": rows,
    }


def try_ragas(samples: list[dict], model: str) -> dict | None:
    """RAGAS 指标。装不上或 API 不兼容时返回 None，不影响其它结果。"""
    try:
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
        from ragas import EvaluationDataset, SingleTurnSample, evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        # 必须用 ragas.metrics 的实例：它们是 Metric 子类，evaluate() 接受。
        # ragas.metrics.collections 是新体系的类，实例化后不是 Metric，会报
        # "All metrics must be initialised metric objects"。
        from ragas.metrics import (answer_relevancy, context_precision,
                                   context_recall, faithfulness)
    except Exception as e:  # noqa: BLE001
        print(f"  [RAGAS 不可用] {e}")
        return None

    base = os.getenv("DEEPSEEK_BASE", "https://api.deepseek.com")
    key = os.getenv("DEEPSEEK_API_KEY", "")
    embed_base = os.getenv("EMBED_BASE", "http://127.0.0.1:8001")
    try:
        judge = LangchainLLMWrapper(
            ChatOpenAI(model=model, base_url=base, api_key=key, temperature=0))
        # check_embedding_ctx_length=False 必须加：默认 True 时 langchain 会先用 tiktoken
        # 把文本编码成 token id 数组再发给服务，而本地嵌入服务只接受字符串 → 422、
        # 指标算出来是 nan。
        emb = LangchainEmbeddingsWrapper(
            OpenAIEmbeddings(base_url=f"{embed_base}/v1", api_key="local",
                             model=os.getenv("EMBED_MODEL", "bge-m3"),
                             check_embedding_ctx_length=False))
        ds = EvaluationDataset(samples=[
            SingleTurnSample(
                user_input=s["question"], response=s["response"],
                retrieved_contexts=s["contexts"], reference=s["reference"])
            for s in samples])
        res = evaluate(ds, metrics=[faithfulness, answer_relevancy,
                                    context_precision, context_recall],
                       llm=judge, embeddings=emb)
        return {k: round(float(v), 4) for k, v in res.to_pandas().mean(numeric_only=True).items()}
    except Exception as e:  # noqa: BLE001
        print(f"  [RAGAS 执行失败] {type(e).__name__}: {e}")
        return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dept", default="技术部", help="检索时使用的部门（受限文档对其不可见）")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--gen", action="store_true", help="跑生成层（会调 DeepSeek）")
    ap.add_argument("--ragas", action="store_true", help="生成层追加 RAGAS 指标")
    ap.add_argument("--ragas-limit", type=int, default=0,
                    help="RAGAS 只评前 N 条（0=全部）。每个指标对每个样本都要调 LLM，全量慢且费钱")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "result.json"))
    args = ap.parse_args()

    load_env()
    from app import config
    from app.rag.store import RagStore

    cases = load_golden()
    store = RagStore()
    if not store.enabled:
        print("RAG 不可用：确认 pymilvus 已装、且已停掉占用 milvus 锁的 Python 主服务")
        return
    known = sorted(p.name for p in config.DOCS_DIR.glob("*.md"))
    print(f"评测集 {len(cases)} 条 · 语料 {len(known)} 篇 · 部门={args.dept} · TopK={args.top_k}\n")

    result = {"cases": len(cases), "docs": len(known), "startedAt": time.strftime("%Y-%m-%d %H:%M:%S")}

    print("=== 检索层：三路对比 ===")
    ret = await retrieval_eval(store, cases, known, args.dept, args.top_k)
    result["retrieval"] = ret
    print(f"{'模式':<10}{'Hit@1':>9}{'Hit@' + str(args.top_k):>9}{'MRR':>9}")
    for mode, m in ret["modes"].items():
        print(f"{mode:<10}{m['hit@1']:>9.1%}{m['hit@K']:>9.1%}{m['mrr']:>9.3f}")

    if args.gen:
        print("\n=== 生成层：关键词规则判分 ===")
        model = config.resolve_model("agent")
        gen = await generation_eval(store, cases, known, args.dept, args.top_k, model)
        result["generation"] = {"model": model, "rule_accuracy": gen["rule_accuracy"]}
        print(f"  规则判分准确率：{gen['rule_accuracy']:.1%}")
        if args.ragas:
            print("\n=== 生成层：RAGAS ===")
            rgs = gen["samples"] if args.ragas_limit <= 0 else gen["samples"][:args.ragas_limit]
            print(f"  （RAGAS 评估 {len(rgs)}/{len(gen['samples'])} 条）")
            rg = try_ragas(rgs, model)
            result["generation"]["ragas"] = rg
            if rg:
                for k, v in rg.items():
                    print(f"  {k}: {v}")
        result["generation"]["samples"] = gen["samples"]

    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
