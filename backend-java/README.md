# backend-java

购票交易服务（Spring Boot 3，端口 8080）。

## 能力

- 登录/注册签发 JWT（HS256，与 Python 共用 `JWT_SECRET`）
- 购票交易：令牌桶限流 → 幂等 → Redis + Lua 原子扣库存 → Redis Stream 异步落库
- 确定性购票规则层（`railway/TicketPolicy.java`）：过期车次、一人一票、儿童票年龄、学生票资格、支付/退票状态机幂等
- 余票预热（按「总席位 − 在售订单」回填，防重启超卖）、座次原子分配（Redis 递增）
- 铁路数据管理（车站/列车/经停/车次/席别），仅 `ADMIN` 可操作，高危写操作需一次性确认码
- 操作审计日志（`audit/`）

## 关键接口

**认证**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | 登录，返回 JWT |
| POST | `/api/auth/register` | 注册 |

**购票**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/ticket/query` | 余票查询（Python/Agent 查票调用） |
| POST | `/api/ticket/buy` | 下单（返回 requestId，异步落库） |
| POST | `/api/ticket/orders/{requestId}/pay` | 支付 |
| POST | `/api/ticket/orders/{requestId}/cancel` | 退票 |
| GET  | `/api/ticket/orders/my` | 我的订单 |
| GET  | `/api/ticket/orders/request/{requestId}` | 按 requestId 查订单 |

**用户资料**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/PUT | `/api/user/profile` | 资料读写 |
| GET/POST | `/api/user/contacts` | 乘车人列表/新增 |
| PUT/DELETE | `/api/user/contacts/{id}` | 修改/删除乘车人 |

**铁路数据管理**（`@PreAuthorize("hasRole('ADMIN')")`）

| 方法 | 路径 |
|---|---|
| GET/POST | `/api/rail/stations` |
| GET/POST | `/api/rail/trains`　PUT/DELETE `/api/rail/trains/{id}` |
| POST | `/api/rail/trains/{id}/stops` |
| GET/POST | `/api/rail/trips`　GET/DELETE `/api/rail/trips/{id}` |
| POST | `/api/rail/confirm/request`（高危操作换取一次性确认码） |

## 运行

前置：Redis + MySQL 已起（见 `../infra/`）。

```bash
mvn spring-boot:run
```

演示账号（`DataInitializer` 初始化，密码均 `123456`）：

| 账号 | 姓名 | 角色 | 说明 |
|---|---|---|---|
| wangzong | 王建国 | ADMIN | 可访问 `/api/rail/**` 管理接口 |
| zhanggong | 张伟 | EMPLOYEE | 仅业务接口，访问管理接口返回 403 |
| lizhu | 李琳 | EMPLOYEE | 同上 |
