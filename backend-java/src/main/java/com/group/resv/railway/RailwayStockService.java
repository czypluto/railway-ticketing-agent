package com.group.resv.railway;

import com.group.resv.railway.domain.TicketOrder;
import com.group.resv.railway.domain.TripClass;
import com.group.resv.railway.repo.TicketOrderRepository;
import com.group.resv.railway.repo.TripClassRepository;
import com.group.resv.redis.DependencyCircuitBreaker;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * 余票读写。规则：DB trip_class.totalSeats 是总席位，运行期余票只在 Redis 维护。
 * 预热按「总席位 - 未取消在售订单」回填，杜绝重启后把已售座位当成余票造成超卖；
 * 扣减用 Lua 保证原子，退票按余量回补。
 */
@Service
public class RailwayStockService {

    private final StringRedisTemplate redis;
    private final DefaultRedisScript<Long> stockDecrScript;
    private final TripClassRepository tripClassRepository;
    private final TicketOrderRepository orderRepository;
    private final DependencyCircuitBreaker breaker;

    public RailwayStockService(StringRedisTemplate redis,
                               DefaultRedisScript<Long> stockDecrScript,
                               TripClassRepository tripClassRepository,
                               TicketOrderRepository orderRepository,
                               DependencyCircuitBreaker breaker) {
        this.redis = redis;
        this.stockDecrScript = stockDecrScript;
        this.tripClassRepository = tripClassRepository;
        this.orderRepository = orderRepository;
        this.breaker = breaker;
    }

    /**
     * 预热/回填余票：remaining = totalSeats - 在售订单数(PAID+PENDING)，且不小于 0。
     * 每次应用启动、或 key 缺失时调用，保证与已成交订单一致。
     */
    public boolean preheat(Long tripId, String seatClass) {
        Optional<TripClass> tc = tripClassRepository.findByTripIdAndSeatClass(tripId, seatClass);
        if (tc.isEmpty()) {
            return false;
        }
        try {
            long sold = orderRepository.countByTripIdAndSeatClassAndStatusIn(
                    tripId, seatClass, Set.of(TicketOrder.PAID, TicketOrder.PENDING));
            long remaining = Math.max(0L, tc.get().getTotalSeats() - sold);
            redis.opsForValue().set(RailwayKeys.stock(tripId, seatClass), String.valueOf(remaining));
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    /** 当前余票；未预热返回总席位。 */
    public long remaining(Long tripId, String seatClass) {
        String v = breaker.call("redis", () ->
                redis.opsForValue().get(RailwayKeys.stock(tripId, seatClass)));
        if (v != null) {
            return Long.parseLong(v);
        }
        Optional<TripClass> tc = tripClassRepository.findByTripIdAndSeatClass(tripId, seatClass);
        return tc.map(t -> (long) t.getTotalSeats()).orElse(0L);
    }

    /**
     * 原子扣减一张。返回 0 及以上=剩余；-1=无票；-2=未预热（需先 preheat 再扣）。
     *
     * -1/-2 是**返回值**不是异常，所以不会被熔断计为故障。
     * Redis 真的不可用时这里会抛出去 → 熔断器计数 → 达阈值后快速失败。
     */
    public long decr(Long tripId, String seatClass) {
        Long r = breaker.call("redis", () ->
                redis.execute(stockDecrScript, List.of(RailwayKeys.stock(tripId, seatClass))));
        return r == null ? -2 : r;
    }

    /** 退票回补一张，不超总席位。 */
    public void release(Long tripId, String seatClass) {
        String key = RailwayKeys.stock(tripId, seatClass);
        breaker.run("redis", () -> {
            String v = redis.opsForValue().get(key);
            if (v == null) {
                return;
            }
            long cur = Long.parseLong(v);
            Optional<TripClass> tc = tripClassRepository.findByTripIdAndSeatClass(tripId, seatClass);
            long cap = tc.map(t -> (long) t.getTotalSeats()).orElse(cur);
            if (cur < cap) {
                redis.opsForValue().increment(key);
            }
        });
    }
}
