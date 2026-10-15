package com.group.resv.redis;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * Redis + Lua 令牌桶，全局限流。
 *
 * 走熔断器：Redis 不可用时不在这里决定放不放行（放行等于把流量送给一个已经挂了的
 * 依赖），而是让异常冒出去由熔断器快速失败成 503。
 */
@Component
public class RateLimiter {

    private final StringRedisTemplate redis;
    private final DefaultRedisScript<Long> script;
    private final DependencyCircuitBreaker breaker;
    private final long capacity;
    private final long rate;
    private final long ttlMillis;

    public RateLimiter(StringRedisTemplate redis,
                       DefaultRedisScript<Long> tokenBucketScript,
                       DependencyCircuitBreaker breaker,
                       @Value("${app.seckill.qps:1000}") long qps,
                       @Value("${app.seckill.burst:2000}") long burst) {
        this.redis = redis;
        this.script = tokenBucketScript;
        this.breaker = breaker;
        // 令牌按秒补充，rate=qps；ttl 留 5 分钟，无流量自动清桶
        this.rate = qps;
        this.capacity = Math.max(burst, qps);
        this.ttlMillis = 5 * 60_000L;
    }

    /** true=放行；false=触发限流。name 用于区分不同接口的桶。 */
    public boolean allow(String name) {
        String key = "resv:rate:" + name;
        Long r = breaker.call("redis", () -> redis.execute(script, List.of(key),
                String.valueOf(capacity), String.valueOf(rate),
                String.valueOf(System.currentTimeMillis()), String.valueOf(ttlMillis)));
        return r != null && r == 1L;
    }
}
