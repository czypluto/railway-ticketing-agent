#!/usr/bin/env bash
# 重置演示数据：清空车票订单 + 清 Redis 业务键（库存/座位号回到初始）。
# 无需重启 Java：下次抢票时服务会对缺库存的资源自动预热。
# 用法：bash scripts/reset-demo.sh
#
# ⚠️ 这里**不能用 FLUSHDB**。
#    FLUSHDB 会把订单流 rv:stream:orders 的消费组 rv-order-g 一并删掉，
#    而 Java 的 TicketOrderConsumer.ensureGroup() 只在应用启动时执行一次，
#    之后消费者每轮都抛 NOGROUP，所有订单都落不了库 ——
#    症状很隐蔽：/api/ticket/buy 照样返回 PENDING + requestId，
#    但 ticket_order 里查不到，用户「我的订单」为空、也付不了款。
#    实测必须重启 Java 才能恢复，所以这条脚本原先"无需重启 Java"的说法不成立。
set -euo pipefail

if ! docker info >/dev/null 2>&1; then
  echo "Docker 引擎未运行，无法重置。"
  exit 1
fi

# 车票订单在 ticket_order —— 这是「一人一票」的事实源。
# 原来删的 reservation_order 是资源预约（字段 resource_id/seat_no），属另一条业务线，
# 重置完用户仍"已购该车次"，再买就被一人一票规则拦下。
docker exec resv-mysql mysql -uresv -presv123 -e "use resv; delete from ticket_order;" 2>/dev/null

# 按前缀清业务键。刻意保留 rv:stream:orders 与其消费组。
docker exec resv-redis redis-cli EVAL "
  local n = 0
  for _, p in ipairs(ARGV) do
    for _, k in ipairs(redis.call('KEYS', p)) do
      n = n + redis.call('DEL', k)
    end
  end
  return n
" 0 'rv:stock:*' 'rv:seat:*' 'rv:active:*' 'rv:inflight:*' \
    'resv:rate:*' 'mem:agent:*' 'resv:agent:*' >/dev/null

# 清掉流里积压的事件，但保留流与消费组（XTRIM 不动 group）
docker exec resv-redis redis-cli XTRIM rv:stream:orders MAXLEN 0 >/dev/null

echo "演示数据已重置（车票订单与业务键已清，订单消费组保留）"
