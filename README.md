# 铁路购票系统

> 用自然语言完成查票、复述确认、下单、支付的铁路购票平台。
>
> **核心命题：模型负责理解和解释，能不能改数据由确定性代码说了算。**

用户在对话里说「明天北京南到上海虹桥，二等座」，系统完成查票、列出行程、**复述待确认**、用户点确认后下单、支付。购票政策（一人一票、儿童票、学生票、支付退票状态机）全部由 Java 侧的确定性规则层裁决，不交给模型判断。

---

## 它解决的两个问题

### 1. 高并发购票的一致性

不超卖、不重复扣票、座位不冲突。Redis + Lua 原子扣减、Redis Stream 异步落库、`@Version` 乐观锁、启动预热、对账补偿。

### 2. 把模型关进笼子

模型只负责理解意图和组织语言。真正的写操作前有四道确定性闸门：

| 机制 | 做法 | 防的是什么 |
|---|---|---|
| **tripId 溯源** | `buy_ticket` 的 tripId 必须命中上一轮查票结果 | 模型凭空编造车次 |
| **两阶段确认** | 模型只预填意向，用户点按钮回传 `intent_id` 后才执行 | 模型自作主张扣库存 |
| **订单号溯源** | 支付/退票的 requestId 只认「刚下单那一笔」或「刚 `my_orders` 列出过的」 | 模型编造参数发起写交易 |
| **政策硬校验** | `TicketPolicy.java` 集中裁决所有写操作的前置条件 | 政策判定被模型幻觉污染 |

---

## 架构

```
                    ┌──────────────────────────────┐
   浏览器 :5173 ────┤  Vue 3 + Element Plus         │
                    └───────────┬──────────────────┘
                    /java-api   │   /py-api
                                ▼
        ┌───────────────────────┴────────────────────────┐
        │                                                │
┌───────▼─────────┐                            ┌─────────▼──────────┐
│ Java  :8080     │  ◄── REST（透传 JWT）────── │ Python :8000       │
│ Spring Boot 3   │                            │ FastAPI            │
│                 │                            │                    │
│ 交易核心        │                            │ JWT 中间件         │
│ 确定性规则层    │                            │ Agent 编排         │
│ 铁路数据管理    │                            │ RAG 检索 / SSE     │
│ 审计            │                            │                    │
└───┬─────────┬───┘                            └────┬──────────┬────┘
    │         │                                     │          │
    ▼         ▼                                     ▼          ▼
┌────────┐ ┌────────┐                        ┌────────────┐ ┌──────────────┐
│ Redis  │ │ MySQL  │                        │ 嵌入服务    │ │ Milvus Lite  │
│ :6379  │ │ :3306  │                        │ :8001      │ │ data/rag.db  │
└────────┘ └────────┘                        └────────────┘ └──────────────┘
```

**为什么拆成两个后端**：交易需要确定性，对话天然是概率性的。分开之后，模型侧出问题不会影响交易链路；Python 也不直连业务库，只能经 REST 调 Java，保证订单只有一个写入口。

---

## 功能

**购票**
- 自然语言 / 表单两种入口，查票 → 选车次 → 选乘车人与票种 → 下单 → 支付
- 票种：成人票、儿童票（6–14 周岁半价）、学生票（学生身份 + 二等座 + 寒暑假 75%）
- 我的车票：支付、退票（余票与座位号一并回补）

**智能助手**
- 每轮 RAG 检索注入参考资料，function-calling 循环（最多 4 轮），12 个工具
- SSE 分阶段推送：`think / tool / result / answer / confirm / error`，每条事件带当前模型名
- 两阶段确认：前端按钮直接把 `intent_id` 带回，不靠关键词猜意图
- 会话档案：列表 / 回放 / 切换 / 删除；短期记忆两层（近期原文 + 滚动摘要）

**知识库（ADMIN）**
- 上传 / 删除 / 全量重建，支持 md / docx / pdf
- 混合检索：向量 + 中文 BM25 + RRF 融合
- 行级权限：文档 `dept_tags` 声明可见部门，检索时两路都过滤
- 操作审计：每次写操作与**被拒绝的越权尝试**都留痕

**铁路管理（ADMIN）**
- 车站 / 列车 / 经停序列 / 运行日与席别
- 删除类高危操作需一次性确认码（2 分钟 TTL，验过即废）

---

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | Vue 3、Vite、Element Plus |
| 交易服务 | Spring Boot 3.4、JDK 17、Spring Security、Spring Data JPA、Redisson |
| 智能服务 | FastAPI、httpx、python-jose |
| 向量检索 | Milvus Lite（嵌入式）、自实现中文 BM25 + RRF |
| 嵌入模型 | 本地 WeMM-Embedding-2B（int8 GPU，MRL 截 1024 维）或 bge-m3（CPU） |
| 模型接入 | DeepSeek 官网 API（OpenAI 兼容），按配置键路由 |
| 中间件 | Redis 7（AOF）、MySQL 8 |
| 运行环境 | WSL2 + Docker |

---

## 快速开始

### 前置

- Windows + WSL2（`.wslconfig` 建议 12GB 内存）
- Docker（WSL2 内）、JDK 17、Node 18+、Python 3.11+
- DeepSeek API key
- 本地嵌入需要使用 NVIDIA 显卡（支持 CUDA in WSL）；没有显卡可改用 bge-m3 走 CPU

### 配置

```bash
cp .env.example .env
# 填 DEEPSEEK_API_KEY；JWT_SECRET 需 >= 32 字节，Java / Python 共用同一个值
```

### 一键启动

```bash
bash scripts/start-all.sh
```

脚本按顺序拉起：Redis/MySQL 容器 → Java(8080) → 嵌入服务(8001) → Python(8000) → 前端(5173)，并会等待每个服务就绪。幂等，可重复执行。

停止：

```bash
bash scripts/stop-all.sh
```

### 访问

打开 <http://localhost:5173>，演示账号（密码均为 `123456`）：

| 账号 | 角色 | 可见功能 |
|---|---|---|
| `zhanggong` | EMPLOYEE | 在线购票、我的车票、智能助手 |
| `wangzong` | ADMIN | 以上 + 知识库管理、铁路管理 |
| `lizhu` | EMPLOYEE | 同上（学生身份，可演示学生票） |

### 手动启动（对应上面的顺序）

```bash
cd infra && docker compose up -d                  # Redis + MySQL
cd backend-java && mvn spring-boot:run            # :8080
cd backend-python && uvicorn app.embed.server:app --port 8001
cd backend-python && uvicorn app.main:app --port 8000
cd frontend-vue && npm run dev                    # :5173
```

---

## 项目结构

```
docs/            设计文档（总纲：docs/架构设计.md）
infra/           docker-compose、初始化 SQL
backend-java/    Spring Boot 交易服务（8080）
backend-python/  FastAPI 智能服务（8000）
  app/embed/     本地嵌入服务（8001，OpenAI 兼容 /v1/embeddings）
  app/agent/     Agent 编排、记忆、挂起状态、会话档案
  app/rag/       文档切分、Milvus 读写、BM25 页索引、RRF 检索
frontend-vue/    Vue 3 前端（5173）
scripts/         启动、联调、评测、容量测试、故障演练
```

各模块的详细说明见对应的 `README.md`。

---

## 关键实现

### 确定性交易链路

```
POST /api/ticket/buy
  → 令牌桶限流（Redis + Lua）
  → 幂等：Redisson 锁 + requestId 唯一索引
  → 车次级「一人一票」：Redis SETNX 原子准入
  → Lua 原子扣库存
  → 座位号分配：优先复用回收池，池空才递增
  → 在途登记 → 写 Redis Stream → 立即返回 PENDING
  → 后台消费者落库；失败不 ACK，下轮认领重试
```

**余票预热**：启动时按「总席位 − 在售订单数」回填 Redis，避免重启后把已售座位当成余票。

**座位号回收**：退票时把座位号还回空闲集合，下次优先复用。纯递增会让号只增不减，反复买退后涨过总席位。

**三层兜底**：在途登记（写在 XADD 之前）→ 消费端落库后销登记 → 每 60 秒对账任务回补超时未落库的登记。对账判定时先立「墓碑」，让迟到的事件被丢弃，避免"先补偿、后插单"。

**熔断**：Redis 是这条链路的强依赖，连续失败达阈值即快速失败 503，避免线程池被超时请求占满。库存扣减**不做降级**——没有原子扣减就可能超卖，宁可不卖。

### 智能对话链路

```
POST /api/chat（SSE）
  → JWT 校验（失败即 401，不进 RAG、不调 Java）
  → 并行取车站 + RAG 检索
  → 组装上下文（system + 摘要 + 近期原文 + 本轮参考）
  → function-calling 循环（最多 4 轮）
     模型返回 tool_calls → 执行 → 结果回注 → 下一轮
  → 工具经 java_client 单一 REST 通道调 Java（透传 JWT 与 trace id）
  → SSE 分阶段推前端
```

### 检索

- **Small-to-Big**：句级小段作为检索单元，所属整页作为生成单元
- **混合检索**：向量（语义）+ 中文 BM25（术语、编号）+ RRF 融合
- **为什么不用加权求和**：两路分数尺度不同（余弦 0–1，BM25 0–数十），RRF 只用排名，免疫尺度差异
- **行级权限**：向量路用 Milvus 标量过滤，BM25 路用 Python 过滤，两边语义必须一致

### 可观测

- **trace id 跨服务**：Python 每轮生成，随 `X-Trace-Id` 透传，Java 写入 MDC，两边日志可按同一 id grep
- **模型用量**：每次调用记 token 到 `data/usage/llm.jsonl`，`scripts/cost_report.py` 聚合为「一轮对话花多少 / 花在哪」

---

## 评测与实测

| 项目 | 结果 | 位置 |
|---|---|---|
| 单元测试 | Java 21 项、Python 58 项 | `backend-*/src/test/`、`backend-python/tests/` |
| RAG 检索评测 | 36 条 / 22 篇语料，Hit@1：向量 0.806、BM25 0.861、**混合 0.889**；三路 Hit@5 均 1.0 | `scripts/rag_eval/` |
| Agent 行为评测 | 23 条，实测 **22/23**；断言"订单数变化"的用例从未失败 | `scripts/agent_eval/` |
| 熔断演练 | 停 Redis 后前 5 个请求各等 3s 超时，之后 **21–49ms 返 503**，恢复自动闭合 | `scripts/circuit_breaker_check.py` |
| 交易链路冒烟 | 下单 → 异步落库 → 支付 → 退票 → 余票回补 | `scripts/rail_smoke.py` |
| 座位号回收 | 真实系统连续买退 6 轮，座位号复用为同一个号 | 手工实测 |

> **关于 22/23 的口径**：失败的是断言"模型工具轨迹"的用例（模型偶尔不按预期调工具），而断言"订单数变化"的硬用例一次没红——**安全属性由代码保证，模型轨迹是概率性的**。

---

## 主要接口

**Java（`:8080`）**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | 登录，返回 JWT |
| POST | `/api/auth/register` | 注册 |
| GET | `/api/ticket/query` | 余票查询 |
| GET | `/api/ticket/stations` | 车站只读列表 |
| GET | `/api/ticket/trains` | 列车只读列表 |
| GET | `/api/ticket/trips` | 开行车次只读列表 |
| POST | `/api/ticket/buy` | 下单（返回 requestId，异步落库） |
| POST | `/api/ticket/orders/{requestId}/pay` | 支付 |
| POST | `/api/ticket/orders/{requestId}/cancel` | 退票 |
| GET | `/api/ticket/orders/my` | 我的订单 |
| GET/POST/PUT/DELETE | `/api/user/**` | 资料与常用联系人 |
| * | `/api/rail/**` | 铁路数据管理（仅 ADMIN） |

**Python（`:8000`）**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/chat` | 对话（SSE） |
| GET | `/api/health` | 健康检查 + 当前模型路由 |
| GET/POST/DELETE | `/api/chat/history/**` | 会话档案 |
| * | `/api/admin/kb/**` | 知识库管理（仅 ADMIN） |

---

## 已知局限

- **Redis Stream 顶替消息队列**：单机演示够用，生产应换 Kafka / RocketMQ（可靠性、死信、监控）。
- **Agent 同一轮内的多个工具调用仍串行**：取车站与 RAG 检索已并行，工具分发循环未并行。
- **下单确认保留了过渡期关键词回退**（`AGENT_CONFIRM_LEGACY`，默认开）。前端按钮已支持回传 `intent_id`，**上线后应置 0**。
- **退票没有按钮级二次确认**：已限制在「刚列出过的订单」范围内，但仍是模型同轮执行。
- **RAG 页索引是全量内存倒排**：增删文档后第一个请求会阻塞在重建上；语料再大需改增量维护。
- **知识库为演示规模**（22 篇），评测集 36 条，不能替代线上分布。
- **模型用量的单价未配置**：`cost_report.py` 只报 token，不报金额。
- **Java 只读接口不打日志**：查票/订单查询的 trace id 在 Java 侧没有对应日志行。
- **座位号序列依赖 Redis 持久化**：Redis 被清空而订单仍在库时，序号会从头开始。
- **行为评测里有一类是概率性的**：断言模型轨迹的用例约 75–78% 通过（见上文口径说明）。

更完整的取舍与残余风险见 [`docs/架构设计.md`](docs/架构设计.md) §10。

---

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/架构设计.md`](docs/架构设计.md) | 架构、两条链路、对账补偿、RAG 设计、已知局限 |
| [`backend-java/README.md`](backend-java/README.md) | 交易服务的能力、接口、演示账号 |
| [`backend-python/README.md`](backend-python/README.md) | 智能服务的目录、接口、模型路由 |
| [`scripts/README.md`](scripts/README.md) | 启动脚本、联调、评测、故障演练 |

---

## 说明

- 服务默认监听本机；生产部署需要自行配置 HTTPS、密钥管理与访问控制。
- `.env` 不提交仓库；示例见 `.env.example`。
