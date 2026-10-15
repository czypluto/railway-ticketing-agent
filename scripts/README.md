# scripts

## 一键脚本（Windows Git Bash / WSL2 通用）

| 脚本 | 作用 |
|---|---|
| `start-all.sh` | **一键启动全部服务**：Redis/MySQL → Java(8080) → WSL 嵌入服务(8001) → WSL Python 主服务(8000) → 前端(5173)。在 Windows Git Bash 里 `bash scripts/start-all.sh` 即可，幂等可重复跑 |
| `stop-all.sh` | 停全部（前端/Java + WSL Python/嵌入），保留容器 |
| `start-dev.sh` | 只拉起后端（Redis/MySQL + Java + Python），不启前端与嵌入；首次运行自动建 venv 装依赖 |
| `stop-dev.sh` | 停 Windows 侧 Java/Python（保留容器） |
| `reset-demo.sh` | 清订单 + 清 Redis，演示数据回到初始（无需重启 Java） |

## 联调脚本

| 脚本 | 作用 |
|---|---|
| `rail_smoke.py` | **Java 交易链路端到端冒烟**：登录 → 车站 → 余票查询 → 下单 → 异步落库 → 支付 → 退票 → 余票回补校验 |
| `chat_e2e.py` | **对话链路联调**：登录(Java) → Python `/api/chat`(SSE) → 真实 DeepSeek；打印每条 SSE 事件及其 `model` 字段。用法 `python chat_e2e.py <账号> "<话>"` |
| `rag_test.py` | RAG 冒烟：灌入 `data/docs` 并做一次带部门过滤的检索（需嵌入服务 8001 在跑） |
| `rag_query.py` | 只看检索命中、不灌库。用法 `python rag_query.py "<问题>" [部门]` |
| `ingest_pdf_cli.py` / `ingest_pdf_pages.py` | PDF 命令行入库（整页渲染成图走多模态嵌入） |
| `wemm_test.py` | 探测 WeMM-Embedding-2B 能否在本机以 int8/4bit 加载并产出 1024 维嵌入 |

示例：

```bash
bash scripts/start-dev.sh
python scripts/rail_smoke.py
python scripts/chat_e2e.py zhanggong "帮我查明天北京南到上海虹桥的二等座"
bash scripts/reset-demo.sh
```

## 模型用量与成本

| 脚本 | 作用 |
|---|---|
| `cost_report.py` | 聚合模型用量：token、按任务拆分、最贵的 N 次对话 |

账本在 `backend-python/data/usage/llm.jsonl`（由 `app/llm.py` 每次调用自动记，
不入库），**只存 token 不存金额**——单价变了历史数据还能重新定价。
单价默认不配，用 `MODEL_PRICE_<模型名>=输入价/输出价` 环境变量给。

```bash
python scripts/cost_report.py --by-task --top 5
MODEL_PRICE_DEEPSEEK_V4_PRO=2.0/8.0 python scripts/cost_report.py
```

**这套度量直接改掉了两处实现，两个发现都来自它：**

**① 记忆压缩原先每轮都触发。** `append` 用 `len(msgs) > RAW_LIMIT` 判断，
压完裁回 10、下一轮 +2 又超 —— 于是**每轮压一次**，~40 轮对话产生 34 次压缩调用。
改成高水位（`COMPRESS_AT=16`，约每 4 轮压一次）后降到 3 次/12 轮。
代码与取舍写在 `app/agent/memory.py` 的 `COMPRESS_AT` 注释里。

**② 推理 token 被当成普通输出。** `deepseek-v4-pro` 是推理模型，
`reasoning_tokens` 计入 `completion_tokens` 但**不产生可见输出**。
记忆压缩实测：完成 2,298 输出 token 里 **1,791 是推理（78%）**，
而摘要正文只有 ~500 字。agent 任务只有 9%。
→ 账本现在把 `reasoningTokens` 单独拆出来，否则「输出 token 高」
会被误读成「摘要写太长了」，优化方向就找错了。

**成本结构**（8 轮对话）：输入占 88.7%——**成本主要在上下文注入
（system + 车站表 + 参考资料 + 记忆 + 工具结果），不在生成**。
所以降成本该先削上下文，不是换更小的模型。

**待定的优化**（测量已就绪，决策未做）：记忆压缩是纯归并摘要任务，
却挂在 `gen` 上走最强推理模型 —— 切非推理/小模型应能降一个量级。
这是产品侧的取舍，没擅自改。

## 依赖故障演练

| 脚本 | 作用 |
|---|---|
| `circuit_breaker_check.py` | 停掉 Redis，验证熔断快速失败与自动恢复 |

Redis 是下单链路的强依赖。停掉它之后：前 5 个请求各等 3s 超时才失败（阈值），
之后熔断打开，**21–49 ms 就返回 503**（约快 70 倍），恢复后自动闭合。
实测数据见脚本头部注释。

```bash
python scripts/circuit_breaker_check.py      # 会 stop/start resv-redis
```

## Agent 行为层评测

| 脚本 | 作用 |
|---|---|
| `agent_eval/run.py` | 断言 Agent **行为**：工具轨迹、是否出票、是否拒绝（18 用例 / 6 类） |
| `agent_eval/cases.jsonl` | 用例集，加一行即可扩用例 |

和 `rag_eval/` 的分工：那边测检索**质量**（Hit@K / MRR / RAGAS），
这边测 Agent **行为**。「有没有乱下单」这类断言不能靠文案判断——
`order_delta` 直接数**数据库里的订单数**。

```bash
python scripts/agent_eval/run.py                 # 需 AGENT_CONFIRM_LEGACY=0
python scripts/agent_eval/run.py --cat F --repeat 3
```

结论、变异测试与尚未覆盖的部分见 `agent_eval/README.md`。

## 容量测试

| 脚本 | 作用 |
|---|---|
| `capacity/bench.py` | RAG 页索引的构建内存/耗时 + 查询延迟；`--impl old` 可对比重构前 |
| `capacity/candidate_ratio.py` | 倒排候选集占比——解释查询提速为何因查询而异 |

`build_page_index` 是纯函数，所以**不需要 Milvus、不需要停主服务**，
用 backend-python 的 venv 直接跑即可。结论与 caveat 见 `capacity/README.md`。

```bash
python scripts/capacity/bench.py --pages 100000 --impl new
python scripts/capacity/bench.py --pages 100000 --impl old
```

## 手动启动步骤（对应 `docs/架构设计.md` §9）

1. WSL2 就绪（`.wslconfig` 12GB，`wsl --shutdown` 后重启）
2. `cd infra && docker compose up -d`
3. `cd backend-java && mvn spring-boot:run`
4. `cd backend-python && uvicorn app.embed.server:app --port 8001`（嵌入服务）
5. `cd backend-python && uvicorn app.main:app --port 8000`
6. `cd frontend-vue && npm run dev`

## 说明

- Python 从根目录 `.env` 读配置（含 `DEEPSEEK_API_KEY` 与模型路由）。Java 用环境变量，默认值与 infra 一致即可。
- 模型路由：`/api/health` 会返回当前任务→模型映射。dev=pro，上线把 `MODEL_AGENT/MODEL_MULTIMODAL` 指到 `deepseekv4flash`。
- DeepSeek 官方 model id 带连字符（`deepseek-v4-pro` / `deepseek-v4-flash`），通过 `.env` 的 `MODEL_ID_*` 映射。
- 运行拓扑：Java 在 Windows（8080）；Python 主服务与嵌入服务(8001)在 WSL，`--host 0.0.0.0` 绑定，前端自动经 WSL IP 访问。停 WSL 里的 Python：`wsl -e pkill -f "uvicorn app.main"`（停嵌入：`pkill -f app.embed.server`）。`stop-dev.sh` 只停 Windows 侧端口。
