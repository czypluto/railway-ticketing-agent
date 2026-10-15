package com.group.resv.config;

import org.redisson.Redisson;
import org.redisson.api.RedissonClient;
import org.redisson.config.Config;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Redisson 分布式锁客户端。只用于加锁，不接管 Redis 连接池；
 * 缓存/Lua/Stream 仍走 Spring Data Redis（Lettuce），二者互不干扰。
 */
@Configuration
public class RedissonConfig {

    @Bean(destroyMethod = "shutdown")
    public RedissonClient redissonClient(
            @Value("${spring.data.redis.host:127.0.0.1}") String host,
            @Value("${spring.data.redis.port:6379}") int port) {
        Config c = new Config();
        // 超时压到与 Lettuce 同量级：Redisson 默认 timeout 3s × retryAttempts 3
        // + retryInterval 1.5s ≈ 12s，一次失败十几秒才冒出来，熔断来不及起作用。
        c.useSingleServer()
                .setAddress("redis://" + host + ":" + port)
                .setTimeout(3000)
                .setRetryAttempts(2)
                .setRetryInterval(300);
        return Redisson.create(c);
    }
}
