package com.group.resv.railway;

import com.group.resv.common.BizException;
import com.group.resv.railway.domain.TicketOrder;
import com.group.resv.railway.repo.StationRepository;
import com.group.resv.railway.repo.TicketOrderRepository;
import com.group.resv.railway.repo.TrainRepository;
import com.group.resv.railway.repo.TrainStopRepository;
import com.group.resv.railway.repo.TripClassRepository;
import com.group.resv.railway.repo.TripRepository;
import com.group.resv.redis.DependencyCircuitBreaker;
import com.group.resv.redis.RateLimiter;
import com.group.resv.repo.ContactRepository;
import com.group.resv.repo.UserRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.Spy;
import org.mockito.junit.jupiter.MockitoExtension;
import org.redisson.api.RedissonClient;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.time.LocalDateTime;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** TicketService 核心业务单测：支付流转、退票回补、越权拒绝。 */
@ExtendWith(MockitoExtension.class)
class TicketServiceTest {

    @Mock TripRepository tripRepository;
    @Mock TrainRepository trainRepository;
    @Mock TrainStopRepository trainStopRepository;
    @Mock StationRepository stationRepository;
    @Mock TripClassRepository tripClassRepository;
    @Mock TicketOrderRepository orderRepository;
    @Mock ContactRepository contactRepository;
    @Mock UserRepository userRepository;
    @Mock RailwayStockService stockService;
    @Mock StringRedisTemplate redis;
    @Mock RedissonClient redisson;
    @Mock RateLimiter rateLimiter;
    @Mock TicketPolicy policy;
    /** 用真实实例而不是 @Mock：熔断器是无依赖的纯逻辑，
     *  被 mock 掉后 call() 会直接返回 null、根本不执行被包装的动作。 */
    @Spy DependencyCircuitBreaker breaker = new DependencyCircuitBreaker(5, 60_000);

    @InjectMocks TicketService ticketService;

    private TicketOrder order(String requestId, String status, Long owner) {
        TicketOrder o = new TicketOrder();
        o.setRequestId(requestId);
        o.setOrderNo("T" + requestId);
        o.setUserId(owner);
        o.setTripId(2L);
        o.setSeatClass("二等座");
        o.setFromStation("北京南");
        o.setToStation("上海虹桥");
        o.setPassengerName("王建国");
        o.setPriceCents(55300);
        o.setStatus(status);
        o.setCreatedAt(LocalDateTime.now());
        return o;
    }

    @Test
    void pay_pending_to_paid() {
        TicketOrder o = order("r-pay", TicketOrder.PENDING, 7L);
        when(orderRepository.findByRequestId("r-pay")).thenReturn(Optional.of(o));

        Map<String, Object> res = ticketService.pay("r-pay", 7L);

        assertEquals(TicketOrder.PAID, res.get("status"));
        assertNotNull(o.getPaidAt());
        verify(orderRepository).save(o);
    }

    @Test
    void cancel_paid_releases_stock() {
        TicketOrder o = order("r-cancel", TicketOrder.PAID, 7L);
        when(orderRepository.findByRequestId("r-cancel")).thenReturn(Optional.of(o));

        Map<String, Object> res = ticketService.cancel("r-cancel", 7L);

        assertEquals(TicketOrder.CANCELLED, res.get("status"));
        verify(stockService).release(2L, "二等座");
        verify(orderRepository).save(o);
    }

    @Test
    void cancel_of_other_user_rejected() {
        TicketOrder other = order("r-other", TicketOrder.PAID, 99L);
        when(orderRepository.findByRequestId("r-other")).thenReturn(Optional.of(other));

        assertThrows(BizException.class, () -> ticketService.cancel("r-other", 7L));
    }
}
