package com.group.resv.railway;

import java.time.Duration;

/**
 * 购票链路的时间常量集中处。
 *
 * <p>这些数字原先散在 {@link TicketService}、{@link TicketExpireJob}、
 * {@link TicketInflightReconcileJob} 三个文件里。它们**彼此有约束关系**，
 * 分开写等于靠注释维系，改一个很容易忘另一个——而且错了不会立刻报错，
 * 只会在某个边界上表现为"余票少一张"或"占位提前泄漏"这类难查的症状。
 *
 * <p>约束关系（由 {@code TicketTimingTest} 钉住）：
 * <ul>
 *   <li>{@link #ACTIVE_LEASE} ≥ {@link #PAY_WINDOW}：占位租约不能短于支付窗口，
 *       否则订单还在待支付、一人一票的占位就已经自己过期了。</li>
 *   <li>{@link #INFLIGHT_TTL} ≥ {@link #INFLIGHT_GRACE}：在途登记不能先于对账宽限期过期，
 *       否则对账任务扫到索引时详情已经没了，只能摘索引、无法回补余票。</li>
 *   <li>{@link #INFLIGHT_GRACE} 远大于正常消费延迟（毫秒级）：否则把"正在处理中"误判成投递失败。</li>
 *   <li>{@link #TOMBSTONE_KEEP} ≥ 消费端可能的最大迟到时间：墓碑要活到迟到事件出现为止，
 *       否则事件绕过墓碑插进来，就成了"先补偿、后插单"。</li>
 * </ul>
 */
public final class TicketTiming {

    private TicketTiming() {
    }

    /** 待支付时限：从**下单时刻**起算（事件里带的下单时间戳），不是从落库时刻起算。 */
    public static final Duration PAY_WINDOW = Duration.ofMinutes(10);

    /** 一人一票活跃占位的兜底租约。正常路径由 cancel / expire 显式释放，这里只是防漏。 */
    public static final Duration ACTIVE_LEASE = Duration.ofMinutes(30);

    /** 在途购票登记 TTL（`rv:inflight:{requestId}`）。 */
    public static final Duration INFLIGHT_TTL = Duration.ofMinutes(15);

    /** 对账宽限期：超过它仍未销登记，才判定投递失败。 */
    public static final Duration INFLIGHT_GRACE = Duration.ofMinutes(3);

    /** 墓碑保留时间（对账判定失败后把登记改写成 cancelled，供消费端丢弃迟到事件）。 */
    public static final Duration TOMBSTONE_KEEP = Duration.ofHours(1);
}
