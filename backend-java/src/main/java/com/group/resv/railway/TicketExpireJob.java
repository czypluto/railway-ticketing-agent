package com.group.resv.railway;

import com.group.resv.railway.domain.TicketOrder;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;

/**
 * 超时订单治理：把超过 10 分钟仍待支付的订单置为 EXPIRED 并回补余票。
 *
 * 状态变更与并发互斥放在 {@link TicketExpireService}（有自己的事务边界）；
 * 本类只负责调度与"确认过期后"的副作用（回补余票、释放一人一票占位）。
 */
@Component
public class TicketExpireJob {

    private static final Logger log = LoggerFactory.getLogger(TicketExpireJob.class);

    private final TicketExpireService expireService;
    private final RailwayStockService stockService;
    private final StringRedisTemplate redis;

    public TicketExpireJob(TicketExpireService expireService, RailwayStockService stockService,
                           StringRedisTemplate redis) {
        this.expireService = expireService;
        this.stockService = stockService;
        this.redis = redis;
    }

    @Scheduled(fixedDelay = 60_000)
    public void expirePending() {
        for (Long orderId : expireService.findStaleIds(
                LocalDateTime.now().minus(TicketTiming.PAY_WINDOW))) {
            try {
                // 只有本次确实改成 EXPIRED 才回补；被支付/退票抢先则返回 null，不能回补
                TicketOrder expired = expireService.tryExpire(orderId);
                if (expired == null) {
                    continue;
                }
                stockService.release(expired.getTripId(), expired.getSeatClass(),
                        RailwayStockService.parseSeatNo(expired.getSeatNo()));
                redis.delete(RailwayKeys.active(expired.getUserId(), expired.getTripId()));
                log.info("订单 {} 超时未支付，已过期并回补余票、释放占位", expired.getRequestId());
            } catch (Exception e) {
                log.warn("订单 {} 过期处理失败（可能正被支付），跳过本轮：{}", orderId, e.getMessage());
            }
        }
    }
}
