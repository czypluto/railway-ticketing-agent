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
    private final DefaultRedisScript<Long> seatAllocScript;
    private final TripClassRepository tripClassRepository;
    private final TicketOrderRepository orderRepository;
    private final DependencyCircuitBreaker breaker;

    public RailwayStockService(StringRedisTemplate redis,
                               DefaultRedisScript<Long> stockDecrScript,
                               DefaultRedisScript<Long> seatAllocScript,
                               TripClassRepository tripClassRepository,
                               TicketOrderRepository orderRepository,
                               DependencyCircuitBreaker breaker) {
        this.redis = redis;
        this.stockDecrScript = stockDecrScript;
        this.seatAllocScript = seatAllocScript;
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

    /**
     * 座位号分配：优先复用已释放的号，空闲集合为空时才取新号（见 lua/seat_alloc.lua）。
     * 返回 0 表示分配失败（Redis 不可用），调用方按"取不到号"处理。
     */
    public long allocSeat(Long tripId, String seatClass) {
        Long n = breaker.call("redis", () -> redis.execute(seatAllocScript,
                List.of(RailwayKeys.seatFree(tripId, seatClass), RailwayKeys.seatSeq(tripId, seatClass))));
        return n == null ? 0L : n;
    }

    /**
     * 退票/过期/补偿：回补一张余票，并把座位号还回空闲集合供复用。不超总席位。
     *
     * <p>余票与座位号一起释放，是为了不让调用方有"只还了余票、忘还座位号"的机会——
     * 漏还不会报错，只会让座位号慢慢涨过总席位。
     */
    public void release(Long tripId, String seatClass, Long seatNo) {
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
        releaseSeat(tripId, seatClass, seatNo);
    }

    /** 座位号入空闲池。用 SADD（幂等）：重复释放不会把同一个号放进去两次，去重也就不会一号两用。 */
    public void releaseSeat(Long tripId, String seatClass, Long seatNo) {
        if (seatNo == null) {
            return;
        }
        breaker.run("redis", () -> redis.opsForSet().add(
                RailwayKeys.seatFree(tripId, seatClass), String.valueOf(seatNo)));
    }

    /**
     * 撤销一次释放：对账任务判定后又发现订单其实落库成功时，
     * 把余票扣回去、座位号也从空闲集合收回，否则会变成"订单在、余票多一张、号也可能被复用"。
     */
    public void reclaim(Long tripId, String seatClass, Long seatNo) {
        decr(tripId, seatClass);
        if (seatNo == null) {
            return;
        }
        breaker.run("redis", () -> redis.opsForSet().remove(
                RailwayKeys.seatFree(tripId, seatClass), String.valueOf(seatNo)));
    }

    /** 座位号在库里是字符串（历史原因）。解析失败按"未分配"处理，不阻断退票主流程。 */
    public static Long parseSeatNo(String seatNo) {
        if (seatNo == null || seatNo.isBlank()) {
            return null;
        }
        try {
            return Long.parseLong(seatNo.trim());
        } catch (NumberFormatException e) {
            return null;
        }
    }
}
