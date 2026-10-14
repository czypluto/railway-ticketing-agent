# backend-python

Python 智能链路（FastAPI，端口 8000）。三合一：JWT 校验中间件 + Agent 调度 + RAG 检索；模型路由见 `app/config.py`。

## 目录

```
app/
  config.py       环境与模型路由（铁律：模型名只在这里）
  middleware.py   全局 JWT 校验，失败 401，不进入 RAG 不调 Java
  security.py     JWT 解析（与 Java 共用 JWT_SECRET，HS256）
  llm.py          模型路由层：chat(task, ...)，统一 OpenAI 兼容协议
  java_client.py  REST 调 Java（只此一条执行通道）
  tools.py        Agent 工具 schema（与 Java 的 /api/ticket、/api/user、/api/rail 接口人工对齐）
  agent/          编排：每轮 RAG 注入 -> function-calling 循环 -> 工具调 Java
  rag/            Small-to-Big 检索 + 中文 BM25 + RRF（可选，需 pymilvus + 嵌入服务）
  embed/          本地嵌入服务（独立的 FastAPI 应用，端口 8001）
  routers/        chat（SSE）/ history（会话档案）/ kb（知识库管理）
  main.py         入口 + /api/health
```

## 运行

在 WSL2 内（建议 Python 3.11+）：

```bash
pip install -r requirements.txt
# 如需 RAG：pip install pymilvus numpy，并确保本地嵌入服务（8001）已起
uvicorn app.main:app --port 8000
```

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/chat | 对话（带 Token，SSE 流式返回 think/result/answer/confirm/error 事件） |
| GET  | /api/health | 健康 + 当前模型路由（观察 dev/prod 切换是否生效） |
| GET/POST/DELETE | /api/chat/history | 会话档案列表 / 详情 / 删除 / 新对话 |
| GET/POST/DELETE | /api/kb | 知识库上传 / 重建 / 删除 |

## 模型路由

- `ENV=dev`：`MODEL_AGENT/MODEL_MULTIMODAL/MODEL_GEN` 默认 `deepseekv4pro`。
- 上线后：把前两者设为 `deepseekv4flash` 即可，代码零改动；每个 SSE 事件带 `model` 字段。
- `MODEL_ID_*` 可在官方模型标识与别名不同时覆盖。
- `MODEL_MULTIMODAL` 当前尚无业务调用方（多模态走本地嵌入，不是多模态大模型）。

## 说明

- 写交易的放行由 Java 侧确定性规则层裁决（`backend-java` 的 `railway/TicketPolicy.java`），模型只做解释，不参与放行。
- RAG 依赖未就绪时自动降级，不影响购票主流程。

## RAG 使用（本地嵌入，已启用）

- 嵌入服务：`app/embed/server.py`（torch + transformers 加载 WeMM-Embedding-2B int8 GPU，或 bge-m3 CPU；端口 8001，OpenAI 兼容 `/v1/embeddings`）。模型目录由 `EMBED_MODEL_DIR` 指定（ModelScope 下载）。
- 向量库：Milvus Lite（`data/rag.db`）。注意 **单写者锁**：先灌库再起服务。
  ```bash
  # 1) 起嵌入服务（WSL）
  EMBED_MODEL_DIR=/home/cpluto/models/bge-m3 python3 -m uvicorn app.embed.server:app --port 8001
  # 2) 起主服务前先灌库（主服务持锁后不能再灌）
  python3 scripts/rag_test.py      # 或 python3 -m app.rag.ingest
  # 3) 起主服务
  uvicorn app.main:app --port 8000
  ```
- 检索冒烟：`python3 scripts/rag_query.py "<问题>" <部门>`（仅在主服务未持锁时可用）。
