package com.group.resv.redis;

import com.group.resv.common.BizException;
import org.junit.jupiter.api.Test;

import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

/** 熔断器状态机单测：打开 / 快速失败 / 半开探测 / 恢复。 */
class DependencyCircuitBreakerTest {

    /** 依赖故障：模拟 Redis 连接异常。 */
    private static RuntimeException boom() {
        return new IllegalStateException("connection refused");
    }

    private static void trip(DependencyCircuitBreaker cb, int times) {
        for (int i = 0; i < times; i++) {
            assertThrows(IllegalStateException.class,
                    () -> cb.call("redis", () -> {
                        throw boom();
                    }));
        }
    }

    @Test
    void opens_after_threshold_then_fails_fast() {
        DependencyCircuitBreaker cb = new DependencyCircuitBreaker(3, 60_000);
        assertEquals("CLOSED", cb.state());

        trip(cb, 3);
        assertEquals("OPEN", cb.state(), "连续 3 次失败后应打开");

        // 打开期间：立刻 503，且**根本不去碰依赖**
        AtomicInteger invoked = new AtomicInteger();
        BizException e = assertThrows(BizException.class, () -> cb.call("redis", () -> {
            invoked.incrementAndGet();
            return "ok";
        }));
        assertEquals(503, e.getCode());
        assertEquals(0, invoked.get(), "熔断打开时不该再真的调依赖");
        assertEquals(1, cb.rejectedCount());
    }

    @Test
    void business_exception_does_not_count_as_failure() {
        // 这条是熔断器最关键的边界：余票不足 409、请勿重复提交 409 都说明 Redis 是通的。
        // 若把它们计为失败，一波正常抢票就能把熔断打开。
        DependencyCircuitBreaker cb = new DependencyCircuitBreaker(3, 60_000);
        for (int i = 0; i < 10; i++) {
            assertThrows(BizException.class, () -> cb.call("redis", () -> {
                throw new BizException(409, "余票不足");
            }));
        }
        assertEquals("CLOSED", cb.state(), "业务异常不该触发熔断");
    }

    @Test
    void success_resets_consecutive_count() {
        DependencyCircuitBreaker cb = new DependencyCircuitBreaker(3, 60_000);
        trip(cb, 2);
        cb.call("redis", () -> "ok");   // 中间成功一次
        trip(cb, 2);
        assertEquals("CLOSED", cb.state(), "中间成功应清零连续失败计数");
    }

    @Test
    void half_open_allows_single_probe_then_closes_on_success() throws Exception {
        DependencyCircuitBreaker cb = new DependencyCircuitBreaker(2, 50);
        trip(cb, 2);
        assertEquals("OPEN", cb.state());

        Thread.sleep(80);   // 等过冷却
        assertEquals("ok", cb.call("redis", () -> "ok"), "冷却后应放行试探请求");
        assertEquals("CLOSED", cb.state(), "试探成功应闭合");
    }

    @Test
    void half_open_reopens_if_probe_fails() throws Exception {
        DependencyCircuitBreaker cb = new DependencyCircuitBreaker(2, 50);
        trip(cb, 2);
        Thread.sleep(80);

        assertThrows(IllegalStateException.class, () -> cb.call("redis", () -> {
            throw boom();
        }));
        assertEquals("OPEN", cb.state(), "试探失败应立刻重新打开");
        assertEquals(1, cb.trippedCount());
    }

    @Test
    void only_outermost_call_is_counted() {
        // RateLimiter / RailwayStockService 各自包了一层，TicketService 又包了一层。
        // 若嵌套时每层都计数，一次故障会被放大成 N 次，熔断提前触发。
        DependencyCircuitBreaker cb = new DependencyCircuitBreaker(2, 60_000);

        // 第 1 次嵌套失败：只该计 1 次，达不到阈值 2（若内层也计，这里就会打开）
        assertThrows(IllegalStateException.class, () -> cb.call("redis", () ->
                cb.call("redis", () -> {
                    throw boom();
                })));
        assertEquals("CLOSED", cb.state(), "一次嵌套失败只该计 1 次");

        // 第 2 次嵌套失败：累计到 2，才打开
        assertThrows(IllegalStateException.class, () -> cb.call("redis", () ->
                cb.call("redis", () -> {
                    throw boom();
                })));
        assertEquals("OPEN", cb.state());
        assertEquals(1, cb.trippedCount(), "只该打开一次");
    }
}
