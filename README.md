# 铁路购票系统

一个 AI 驱动的铁路购票平台。用户在对话里直接说「明天北京南到上海虹桥，二等座」，系统完成查票、复述确认、下单、支付的完整流程；购票政策（儿童票、学生票、一人一票等）由 Java 侧的确定性规则层裁决，不交给模型判断。

## 结构

| 目录 | 说明 |
|---|---|
| `docs/` | 设计文档 |
| `backend-java/` | Spring Boot 3（8080）：JWT 认证、购票交易核心、铁路数据管理 |
| `backend-python/` | FastAPI（8000）：JWT 中间件、模型路由、Agent 编排、RAG、SSE |
| `backend-python/app/embed/` | 本地嵌入服务（8001，OpenAI 兼容 `/v1/embeddings`） |
| `frontend-vue/` | Vue 3 + Element Plus 前端（5173） |
| `infra/` | Redis/MySQL 编排与初始化 SQL |
| `scripts/` | 启动与联调脚本 |

## 两条链路

**确定性交易链路（Java）**
令牌桶限流 → 幂等控制 → Redis + Lua 原子扣库存 → Redis Stream 异步落库（失败不 ack，下轮认领重试）。余票在启动时按「总席位 − 在售订单」预热，防重启超卖；座位号用 Redis 原子递增分配；订单带 `@Version` 乐观锁。

Redis 是这条链路的强依赖（限流、一人一票占位、扣库存、座位号、幂等锁、订单流都要它），
所以有**熔断**兜底：连续失败达阈值即打开，后续请求快速失败 503，不再逐个干等超时——
否则并发一上来线程池被打满，连不碰 Redis 的接口都会被拖垮。
实测（`scripts/circuit_breaker_check.py`）：停掉 Redis 后前 5 个请求各等 3s 超时，
之后 **21–49 ms 就返回 503（约快 70 倍）**，恢复后自动闭合。
注意库存扣减**不做降级**——没有原子扣减就可能超卖，宁可不卖。

**智能对话链路（Python）**
JWT 校验 → 每轮 RAG 检索注入参考资料 → function-calling Agent 循环（最多 4 轮）→ 12 个工具经一条 REST 通道调 Java → 结果以 SSE 分阶段推给前端。

**关键设计：模型决策与确定性执行分离。**
模型只负责理解意图和组织语言，真正改数据的动作交给确定性代码——`buy_ticket` 的 `tripId` 必须命中上一轮查票结果，否则拒绝；下单走两阶段确认，前端按钮带回 `intent_id` 后才由确定性代码真正扣库存；支付直接取记忆中的订单号。政策校验集中在 `TicketPolicy.java`。

挂起状态（待确认意向 / 待支付订单 / 最近查票结果）存在 Redis（`app/agent/pending.py`），
不再放进程内存——多副本部署不会串号，重启不丢。Redis 已开 AOF。

## 环境要求

- Windows + WSL2（`C:\Users\<user>\.wslconfig` 给 12GB 内存）
- Docker（WSL2 内）、JDK 17、Node 18+、Python 3.11+
- NVIDIA 显卡 + 支持 CUDA in WSL 的驱动（本地嵌入模型用）
- DeepSeek API key（环境变量 `DEEPSEEK_API_KEY`）

启动顺序见 `scripts/README.md`。

## 已知局限

- 余票扣减用 Redis Stream 顶替消息队列，单机演示够用，生产应换 Kafka/RocketMQ。
- Agent **同一轮内的多个工具调用仍是串行**。取车站与 RAG 检索已改为并行（每轮固定收益），
  但工具分发循环未并行 —— 模型通常一轮只发一个工具调用，且其中的写操作本就不可并行
  （会撞库存乐观锁），并行收益有限。跨轮的 ReAct 循环本身必须串行。
- 下单确认仍保留一条**过渡期**的关键词回退（`AGENT_CONFIRM_LEGACY`，默认开），
  用于兼容尚未改造的前端。前端按钮已支持回传 `intent_id`，**上线后应把该开关置 0**，
  届时「好的，但我想改时间」才不再触发出票。
- RAG 页索引是**全量内存倒排**：`_ensure_page_index` 一次取回整个 collection
  （`limit=200000`），且重建发生在 `retrieve()` 的请求路径里 —— 增删文档后
  第一个请求会阻塞在重建上。容量实测（22 篇语料重组成页）：约 0.19 s / 1000 页，
  到 limit 上限外推约 38 s。倒排结构已从「每页一个 tf dict」改为单倒排 +
  `array('i')`（10 万页 2.3 GB → 0.33 GB，约 1/7；查询只扫命中页）。
  **语料再大需要改增量维护，或把倒排挪到独立检索组件。**
- RAG 知识库为演示规模（22 篇）；检索评测集 36 条在 `scripts/rag_eval/`。
- 模型用量（token）未做统计与成本计量。
