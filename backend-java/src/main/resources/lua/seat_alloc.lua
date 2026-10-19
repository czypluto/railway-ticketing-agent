-- 座位号分配：KEYS[1]=空闲座位集合，KEYS[2]=递增序列
-- 返回：分配到的座位号（正整数）
--
-- 优先复用已释放的号（SPOP），空闲集合为空时才递增取新号。
-- 为什么不是单纯 INCR：退票只回补余票、不回退序列，反复买退会让号一路涨到
-- 超过总席位（600 座的二等座出现"座位 700"）。用空闲集合回收后，
-- 在用的座位号始终落在 [1, totalSeats] 内。
--
-- 不会给出重复号的理由：
--   1) 库存扣减保证「在用座位数 <= totalSeats」，故空闲集合为空时序列必 < totalSeats；
--   2) INCR 单调递增，新号大于历史上所有已分配的号，不可能撞上某个仍在用的号；
--   3) 释放用 SADD（幂等），重复释放不会把一个号放进去两次，去重也就不会一号两用。
local free = redis.call('SPOP', KEYS[1])
if free then
    return tonumber(free)
end
return redis.call('INCR', KEYS[2])
