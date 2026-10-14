-- 令牌桶：KEYS[1]=桶 key；ARGV[1]=capacity，ARGV[2]=rate(每秒)，ARGV[3]=now(毫秒)，ARGV[4]=过期(毫秒)
-- 桶内值存为 "tokens:lastTs"
-- 返回 1=放行，0=限流
local v = redis.call('GET', KEYS[1])
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local tokens = capacity
local last = now
if v then
    local t = string.match(v, '^([%d%.]+):')
    local l = string.match(v, ':([%d]+)$')
    if t then tokens = tonumber(t) end
    if l then last = tonumber(l) end
end

local elapsed = now - last
if elapsed < 0 then elapsed = 0 end
-- now/last 是毫秒，rate 是「每秒」补充量：必须除以 1000，否则令牌补充快 1000 倍、限流失效
tokens = tokens + (elapsed / 1000) * rate
if tokens > capacity then tokens = capacity end

local allowed = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
end

redis.call('SET', KEYS[1], string.format('%.4f:%d', tokens, now), 'PX', ttl)
return allowed
