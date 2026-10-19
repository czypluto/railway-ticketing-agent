package com.group.resv.railway;

/** 购票业务 Redis key 规约。 */
public final class RailwayKeys {

    private RailwayKeys() {
    }

    /** 余票：某车次某席别剩余 */
    public static String stock(Long tripId, String seatClass) {
        return "rv:stock:" + tripId + ":" + seatClass;
    }

    public static String orderStream() {
        return "rv:stream:orders";
    }

    /** 一人一票活跃占位（车次级：每车次每人一票，不限席别）。取消/过期时删除。 */
    public static String active(Long userId, Long tripId) {
        return "rv:active:" + userId + ":" + tripId;
    }

    /** 座位号递增序列（每车次每席别独立；空闲集合为空时才取新号）。 */
    public static String seatSeq(Long tripId, String seatClass) {
        return "rv:seat:" + tripId + ":" + seatClass;
    }

    /**
     * 已释放、可复用的座位号集合（每车次每席别一个）。
     * 退票/过期/对账补偿时把号还回来，下次分配优先取用，
     * 使在用的座位号始终落在 [1, 总席位] 内（否则序列只增不减，会一路涨过总席位）。
     */
    public static String seatFree(Long tripId, String seatClass) {
        return "rv:seatfree:" + tripId + ":" + seatClass;
    }

    /**
     * 在途购票登记索引（ZSET：member=requestId，score=下单毫秒时间戳）。
     * 用于对账任务按"超时未落库"扫描，避免异步落库失败造成余票永久泄漏。
     */
    public static String inflightIndex() {
        return "rv:inflight:idx";
    }

    /**
     * 单笔在途购票详情：`tripId|seatClass|userId|下单毫秒|座位号`。
     * 消费端落库成功后删除；对账任务判定投递失败后改写为 `cancelled` 墓碑，
     * 消费端据此丢弃迟到的事件，防止"补偿完又被插进来"。
     *
     * <p>座位号是后加的第五个字段：对账补偿时要连座位号一起还回空闲集合。
     * 老登记（4 段）仍能被解析，只是座位号视为缺失——解析保持向后兼容。
     */
    public static String inflight(String requestId) {
        return "rv:inflight:" + requestId;
    }
}
