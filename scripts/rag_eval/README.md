# RAG 检索评测

对知识库检索做**可复现的量化评估**：三路检索对比 + 生成层判分。
回答的是面试第一问——「你的 RAG 效果怎么量化」。

## 目录

| 文件 | 说明 |
|---|---|
| `golden_set.jsonl` | 评测集：36 条 `{id, question, answer, source, keywords}`。`source` 为标准答案所在的文档（判检索命中），`keywords` 为关键词组（判生成答案对错） |
| `rag_eval.py` | 评测脚本：检索层三路对比 + 生成层规则判分 + 可选 RAGAS |
| `result*.json` | 运行产物（已在 `.gitignore`，不入库） |

## 为什么用独立虚拟环境

评测需要 **RAGAS**，它会把整个 langchain 生态拉进来（约 90 个包）。装进 `backend-python/.venv` 会污染服务的依赖集，所以单独建环境。

```bash
# WSL 内（系统 Python 是 3.14，RAGAS 依赖链在 3.12 上更稳）
/home/cpluto/.local/bin/python3.12 -m venv ~/rag-eval-venv
~/rag-eval-venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    ragas "pymilvus[milvus_lite]" numpy httpx python-dotenv
```

### ⚠️ 一个必须知道的依赖坑

`ragas 0.4.3`（当前最新）在 `ragas/llms/base.py` 顶层无条件导入：

```python
from langchain_community.chat_models.vertexai import ChatVertexAI
```

而 `langchain-community 0.4.x` 已经把该模块移除 → **import ragas 直接 ModuleNotFoundError**。
pip 自动解析会装 0.4.2，于是装上就用不了。修法是把它压回 0.3 线：

```bash
~/rag-eval-venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "langchain-community<0.4"
```

这条命令只动 `langchain-community` 与 4 个小依赖，不会连带降级 `langchain-core` / `langchain`。

## 怎么跑

**前提**：22 篇语料已入库；且**已停掉 Python 主服务**——Milvus Lite 对 `data/rag.db` 持单写者锁，服务在跑时评测进程打不开。
嵌入服务（8001）**要保持运行**，向量检索要用它。

```bash
# 1) 停主服务（保留嵌入服务与容器）
wsl -e bash -lc 'pkill -f "uvicorn app[.]main"'

# 2) 检索层（快，零 API 调用）
wsl -e bash -lc 'cd /mnt/c/Users/24522/Desktop/铁路购票系统 && \
    ~/rag-eval-venv/bin/python scripts/rag_eval/rag_eval.py'

# 3) 加生成层（调 DeepSeek）
wsl -e bash -lc 'cd /mnt/c/Users/24522/Desktop/铁路购票系统 && \
    ~/rag-eval-venv/bin/python scripts/rag_eval/rag_eval.py --gen --ragas --ragas-limit 12'

# 4) 起回服务
bash scripts/start-all.sh
```

## 指标

**检索层**

| 指标 | 含义 |
|---|---|
| `Hit@K` | 标准答案所在文档是否进入前 K 条（召回） |
| `Hit@1` | 是否正确文档排在第一位 |
| `MRR` | 首个命中的倒数排名均值，综合反映排序质量 |

**生成层**

- **关键词规则判分**：每个关键词组内命中任一同义词即通过，全部组通过才算答对。确定性、零成本、可复现。
- **RAGAS**：`faithfulness`（忠实度）、`answer_relevancy`（切题度）、`context_precision`、`context_recall`。需要 LLM 当裁判。

## 已知局限

- 语料仅 22 篇 115 段，TopK=5 时三路都接近饱和（Hit@5 均 100%），**区分度主要体现在排序**而非召回。
- 评测集 36 条为手写，规模有限，不能替代线上真实分布的评测。
- RAGAS 的 LLM 裁判存在已知偏见（偏爱长答案、偏爱自家模型输出），因此能用规则判分的地方优先用规则。
