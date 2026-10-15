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
