package com.group.resv.railway;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 把 {@link TicketTiming} 里的约束关系钉成断言。
 *
 * <p>这几条关系原先只写在注释里，而且散在三个文件。改错的表现不是编译失败，
 * 而是某个边界上的"余票少一张"、"占位提前泄漏"这类难查症状——所以用测试兜住。
 */
class TicketTimingTest {

    @Test
    void active_lease_covers_the_pay_window() {
        // 占位租约短于支付窗口 → 订单还在待支付，一人一票的占位就先过期了
        assertTrue(TicketTiming.ACTIVE_LEASE.compareTo(TicketTiming.PAY_WINDOW) >= 0,
                "占位租约必须不短于支付窗口");
    }

    @Test
    void inflight_ttl_covers_the_grace_window() {
        // 在途登记先于宽限期过期 → 对账扫到索引时详情已不在，只能摘索引、无法回补余票
        assertTrue(TicketTiming.INFLIGHT_TTL.compareTo(TicketTiming.INFLIGHT_GRACE) >= 0,
                "在途登记 TTL 必须不短于对账宽限期");
    }

    @Test
    void grace_is_orders_of_magnitude_above_normal_consumption() {
        // 正常消费是毫秒级；宽限期至少留到分钟量级，否则把"正在处理中"误判成投递失败
        assertTrue(TicketTiming.INFLIGHT_GRACE.toMinutes() >= 1,
                "对账宽限期至少 1 分钟");
    }

    @Test
    void tombstone_outlives_the_grace_window() {
        // 墓碑要活到迟到事件出现为止；比宽限期短就等于没立
        assertTrue(TicketTiming.TOMBSTONE_KEEP.compareTo(TicketTiming.INFLIGHT_GRACE) > 0,
                "墓碑保留时间必须长于对账宽限期");
    }
}
