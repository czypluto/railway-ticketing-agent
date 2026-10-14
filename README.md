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

**智能对话链路（Python）**
JWT 校验 → 每轮 RAG 检索注入参考资料 → function-calling Agent 循环（最多 4 轮）→ 12 个工具经一条 REST 通道调 Java → 结果以 SSE 分阶段推给前端。

**关键设计：模型决策与确定性执行分离。**
模型只负责理解意图和组织语言，真正改数据的动作交给确定性代码——`buy_ticket` 的 `tripId` 必须命中上一轮查票结果，否则拒绝；下单走两阶段确认，用户回「确认」后才由确定性代码真正扣库存；支付直接取记忆中的订单号。政策校验集中在 `TicketPolicy.java`。

## 环境要求

- Windows + WSL2（`C:\Users\<user>\.wslconfig` 给 12GB 内存）
- Docker（WSL2 内）、JDK 17、Node 18+、Python 3.11+
- NVIDIA 显卡 + 支持 CUDA in WSL 的驱动（本地嵌入模型用）
- DeepSeek API key（环境变量 `DEEPSEEK_API_KEY`）

启动顺序见 `scripts/README.md`。

## 已知局限

- 余票扣减用 Redis Stream 顶替消息队列，单机演示够用，生产应换 Kafka/RocketMQ。
- Agent 工具调用为串行，未做并行、工具级超时与重试。
- RAG 知识库为演示规模，暂无检索评测集。
- 模型用量（token）未做统计与成本计量。
