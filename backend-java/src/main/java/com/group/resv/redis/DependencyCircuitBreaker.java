package com.group.resv.redis;

import com.group.resv.common.BizException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Supplier;

/**
 * 外部依赖熔断器。
 *
 * <h3>为什么需要</h3>
 * Redis 是本项目下单链路的强依赖：限流、一人一票占位、Lua 原子扣库存、座位号、
 * 幂等锁、订单流，几乎每一步都要它。Redis 不可用时，每个请求都要**阻塞到命令超时**
 * （Lettuce 配的是 3s）才失败，并发一上来连接池与线程池被打满，
 * 连健康检查和不碰 Redis 的接口都会被拖垮——这正是级联故障。
 *
 * <h3>为什么不是「降级成另一种扣库存方式」</h3>
 * **库存扣减无法降级**：没有 Lua 原子扣减就可能超卖。宁可明确失败，也不能降级成
 * 「没扣库存也出票」。所以这里做的是**快速失败**——连续失败达阈值即打开，
 * 后续请求立刻返回 503，把线程让出来；冷却后进入半开，放一个请求试探，成功则闭合。
 *
 * <h3>只统计依赖故障，不统计业务错误</h3>
 * `BizException`（余票不足 409、请勿重复提交 409…）说明 Redis **是通的**，
 * 一律按成功处理。否则一波正常抢票就能把熔断打开。
 * `decr` 返回 -1/-2 是返回值不是异常，同样不计入。
 *
 * <h3>为什么手写而不是引入 Resilience4j</h3>
 * 只需要「连续失败计数 + 打开/半开 + 快速失败」这三件事；本项目的限流也是手写 Lua
 * （{@link RateLimiter}），风格一致。少一个依赖，行为也更可读可测。
 */
@Component
public class DependencyCircuitBreaker {

    private static final Logger log = LoggerFactory.getLogger(DependencyCircuitBreaker.class);

    public enum State { CLOSED, OPEN, HALF_OPEN }

    /** 嵌套调用只让最外层计数：叶子组件（RateLimiter/StockService）也各自包了一层。 */
    private static final ThreadLocal<Integer> DEPTH = ThreadLocal.withInitial(() -> 0);

    private final int failureThreshold;
    private final long openMillis;

    private State state = State.CLOSED;
    private int consecutiveFailures = 0;
    private long openedAt = 0L;
    private int halfOpenProbes = 0;

    private final AtomicLong rejected = new AtomicLong();   // 被快速失败的请求数
    private final AtomicInteger tripped = new AtomicInteger(); // 打开次数

    public DependencyCircuitBreaker(
            @Value("${app.circuit.failure-threshold:5}") int failureThreshold,
            @Value("${app.circuit.open-millis:10000}") long openMillis) {
        this.failureThreshold = Math.max(1, failureThreshold);
        this.openMillis = Math.max(0L, openMillis);
    }

    /** 包一层有返回值的依赖调用。 */
    public <T> T call(String dep, Supplier<T> action) {
        boolean outermost = DEPTH.get() == 0;
        if (outermost) {
            beforeCall(dep);
        }
        DEPTH.set(DEPTH.get() + 1);
        try {
            T r = action.get();
            if (outermost) {
                onSuccess();
            }
            return r;
        } catch (BizException e) {
            // 业务异常 = 依赖是通的，不算故障
            if (outermost) {
                onSuccess();
            }
            throw e;
        } catch (RuntimeException e) {
            if (outermost) {
                onFailure(dep, e);
            }
            throw e;
        } finally {
            int d = DEPTH.get() - 1;
            if (d <= 0) {
                DEPTH.remove();
            } else {
                DEPTH.set(d);
            }
        }
    }

    /** 包一层无返回值的依赖调用。 */
    public void run(String dep, Runnable action) {
        call(dep, () -> {
            action.run();
            return null;
        });
    }

    public String state() {
        return state.name();
    }

    public long rejectedCount() {
        return rejected.get();
    }

    public int trippedCount() {
        return tripped.get();
    }

    // ---- 状态迁移：频率极低（只在打开/半开期间），用 synchronized 足够且更好读 ----

    private synchronized void beforeCall(String dep) {
        if (state == State.OPEN && System.currentTimeMillis() - openedAt >= openMillis) {
            state = State.HALF_OPEN;
            halfOpenProbes = 1;
            log.warn("依赖 {} 冷却结束，熔断转半开，放行 {} 个试探请求", dep, halfOpenProbes);
        }
        if (state == State.OPEN) {
            rejected.incrementAndGet();
            throw new BizException(503, "服务繁忙，请稍后重试");
        }
        if (state == State.HALF_OPEN) {
            if (halfOpenProbes <= 0) {
                rejected.incrementAndGet();
                throw new BizException(503, "服务繁忙，请稍后重试");
            }
            halfOpenProbes--;
        }
    }

    private synchronized void onSuccess() {
        if (state != State.CLOSED) {
            log.info("依赖恢复，熔断关闭（累计快速失败 {} 次）", rejected.get());
        }
        state = State.CLOSED;
        consecutiveFailures = 0;
        halfOpenProbes = 0;
    }

    private synchronized void onFailure(String dep, Throwable e) {
        if (state == State.HALF_OPEN) {
            // 试探失败：立刻重新打开并重新计时，不放行后续请求
            state = State.OPEN;
            openedAt = System.currentTimeMillis();
            log.warn("依赖 {} 半开试探失败，重新熔断：{}", dep, e.toString());
            return;
        }
        consecutiveFailures++;
        if (consecutiveFailures >= failureThreshold && state == State.CLOSED) {
            state = State.OPEN;
            openedAt = System.currentTimeMillis();
            tripped.incrementAndGet();
            log.error("依赖 {} 连续失败 {} 次，熔断打开 {} ms（期间请求快速失败）",
                    dep, consecutiveFailures, openMillis, e);
        }
    }
}
