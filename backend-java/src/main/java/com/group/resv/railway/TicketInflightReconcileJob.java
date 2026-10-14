package com.group.resv.railway;

import com.group.resv.railway.repo.TicketOrderRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.Set;

/**
 * 在途购票对账：兜住「Redis 已扣余票、订单却始终没落库」这种异步失败。
 *
 * 下单时会在 {@link RailwayKeys#inflightIndex()} 登记 requestId，消费端落库成功后销掉。
 * 本任务每分钟扫一次超时仍未销的登记：
 * <ul>
 *   <li>DB 里已有该订单 → 只是消费端清理慢了，摘掉登记即可；</li>
 *   <li>DB 里没有该订单 → 判定投递失败：回补余票、释放一人一票占位，
 *       并把登记改写成 {@code cancelled} 墓碑，让迟到的事件被消费端丢弃。</li>
 * </ul>
 *
 * 没有这层对账时的后果：消费者持续失败 → 订单永不入库 → {@link TicketExpireJob}
 * 只扫 DB 扫不到 → 扣掉的余票永不回补；且占位 TTL（30 分钟）到期后用户还能再买同一车次，
 * 同一张票被扣两次库存，且 DB 与日志里毫无痕迹。
 */
@Component
public class TicketInflightReconcileJob {

    private static final Logger log = LoggerFactory.getLogger(TicketInflightReconcileJob.class);

    /** 宽限期：远大于正常消费延迟（毫秒级），避免把"正在处理中"误判成失败。 */
    private static final Duration GRACE = Duration.ofMinutes(3);
    private static final Duration KEEP = Duration.ofHours(1);
    private static final String TOMBSTONE = "cancelled";

    private final StringRedisTemplate redis;
    private final TicketOrderRepository orderRepository;
    private final RailwayStockService stockService;

    public TicketInflightReconcileJob(StringRedisTemplate redis, TicketOrderRepository orderRepository,
                                      RailwayStockService stockService) {
        this.redis = redis;
        this.orderRepository = orderRepository;
        this.stockService = stockService;
    }

    @Scheduled(fixedDelay = 60_000)
    public void reconcile() {
        long deadline = System.currentTimeMillis() - GRACE.toMillis();
        Set<String> stale = redis.opsForZSet().rangeByScore(RailwayKeys.inflightIndex(), 0, deadline);
        if (stale == null || stale.isEmpty()) {
            return;
        }
        for (String requestId : stale) {
            try {
                reconcileOne(requestId);
            } catch (Exception e) {
                log.warn("在途购票 {} 对账失败，下轮重试：{}", requestId, e.getMessage());
            }
        }
    }

    private void reconcileOne(String requestId) {
        String key = RailwayKeys.inflight(requestId);
        String detail = redis.opsForValue().get(key);
        if (detail == null || TOMBSTONE.equals(detail)) {
            redis.opsForZSet().remove(RailwayKeys.inflightIndex(), requestId);
            return;
        }

        // 解析与校验必须全部前置：墓碑一旦落下，消费端就会丢弃这笔订单。
        // 若此时才解析失败，就成了「有墓碑、没回补」——票凭空消失，比不做对账更糟。
        String[] p = detail.split("\\|");
        Long tripId;
        Long userId;
        try {
            if (p.length < 4) {
                throw new IllegalArgumentException("字段数不足：" + detail);
            }
            tripId = Long.valueOf(p[0]);
            userId = Long.valueOf(p[2]);
        } catch (RuntimeException e) {
            log.warn("在途购票 {} 登记内容不合法（{}），摘除索引且不回补：{}", requestId, detail, e.getMessage());
            redis.opsForZSet().remove(RailwayKeys.inflightIndex(), requestId);
            return;
        }
        String seatClass = p[1];

        // 立墓碑先行：把"消费端即将插入订单"这条路先关上，缩小"刚判完就插进来"的竞态窗口
        redis.opsForValue().set(key, TOMBSTONE, KEEP);
        try {
            if (orderRepository.findByRequestId(requestId).isPresent()) {
                // 订单其实落库了（消费端只是没来得及清理登记）：撤销墓碑，摘掉索引即可
                redis.opsForValue().set(key, detail, KEEP);
                redis.opsForZSet().remove(RailwayKeys.inflightIndex(), requestId);
                return;
            }
            stockService.release(tripId, seatClass);
            redis.delete(RailwayKeys.active(userId, tripId));
            redis.opsForZSet().remove(RailwayKeys.inflightIndex(), requestId);
            log.error("在途购票 {} 超过 {} 分钟仍未落库，已回补余票并释放占位（异步落库失败补偿）",
                    requestId, GRACE.toMinutes());
        } catch (Exception e) {
            // 补偿失败：回滚墓碑，让下一轮重试。否则会留下"有墓碑、没回补"的隐形泄漏。
            redis.opsForValue().set(key, detail, KEEP);
            throw e;
        }
    }
}
