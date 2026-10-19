package com.group.resv.redis;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.io.ClassPathResource;
import org.springframework.data.redis.core.script.DefaultRedisScript;

@Configuration
public class LuaScripts {

    @Bean
    public DefaultRedisScript<Long> tokenBucketScript() {
        DefaultRedisScript<Long> s = new DefaultRedisScript<>();
        s.setLocation(new ClassPathResource("lua/token_bucket.lua"));
        s.setResultType(Long.class);
        return s;
    }

    @Bean
    public DefaultRedisScript<Long> stockDecrScript() {
        DefaultRedisScript<Long> s = new DefaultRedisScript<>();
        s.setLocation(new ClassPathResource("lua/stock_decr.lua"));
        s.setResultType(Long.class);
        return s;
    }

    @Bean
    public DefaultRedisScript<Long> seatAllocScript() {
        DefaultRedisScript<Long> s = new DefaultRedisScript<>();
        s.setLocation(new ClassPathResource("lua/seat_alloc.lua"));
        s.setResultType(Long.class);
        return s;
    }
}
