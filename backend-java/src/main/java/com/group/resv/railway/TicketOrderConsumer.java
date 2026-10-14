package com.group.resv.railway;

import com.group.resv.railway.domain.TicketOrder;
import com.group.resv.railway.repo.TicketOrderRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.annotation.Order;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.redis.connection.stream.Consumer;
import org.springframework.data.redis.connection.stream.MapRecord;
import org.springframework.data.redis.connection.stream.RecordId;
import org.springframework.data.redis.connection.stream.ReadOffset;
import org.springframework.data.redis.connection.stream.StreamOffset;
import org.springframework.data.redis.connection.stream.StreamReadOptions;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.List;
import java.util.Map;

/** Redis Stream 消费者：把购票事件异步落库（削峰），订单先落为待支付。 */
@Component
@Order(21)
public class TicketOrderConsumer implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(TicketOrderConsumer.class);

    /** 对账任务判定投递失败、并已回补余票后写入的墓碑值。消费端见到它就丢弃迟到的事件。 */
    private static final String TOMBSTONE = "cancelled";

    private final StringRedisTemplate redis;
    private final TicketOrderRepository orderRepository;
    private final String streamKey = RailwayKeys.orderStream();
    private final String group;
    private final String consumerName;

    public TicketOrderConsumer(StringRedisTemplate redis, TicketOrderRepository orderRepository,
                               @Value("${app.stream.order-group:rv-order-g}") String group,
                               @Value("${app.stream.order-consumer:rv-order-c1}") String consumerName) {
        this.redis = redis;
        this.orderRepository = orderRepository;
        this.group = group;
        this.consumerName = consumerName;
    }

    @Override
    public void run(ApplicationArguments args) {
        ensureGroup();
        Thread worker = new Thread(this::consume, "rv-order-consumer");
        worker.setDaemon(true);
        worker.start();
    }

    private void ensureGroup() {
        try {
            RecordId marker = redis.opsForStream().add(streamKey, Map.of("init", "1"));
            try {
                redis.opsForStream().createGroup(streamKey, group);
            } catch (Exception ignored) {
                // 消费组已存在
            }
            if (marker != null) {
                redis.opsForStream().delete(streamKey, marker);
            }
        } catch (Exception e) {
            log.warn("初始化购票消费组失败：{}", e.getMessage());
        }
    }

    private void consume() {
        Consumer consumer = Consumer.from(group, consumerName);
        StreamReadOptions options = StreamReadOptions.empty().count(20).block(Duration.ofMillis(1500));
        while (!Thread.currentThread().isInterrupted()) {
            try {
                List<MapRecord<String, Object, Object>> records = redis.opsForStream()
                        .read(consumer, options,
                                StreamOffset.create(streamKey, ReadOffset.lastConsumed()));
                if (records != null) {
                    process(records, consumer);
                }
                // 自动认领重试：处理本消费者遗留的未 ack 消息（前轮落库失败的那些）
                List<MapRecord<String, Object, Object>> pending = redis.opsForStream()
                        .read(consumer,
                                StreamReadOptions.empty().count(20),
                                StreamOffset.create(streamKey, ReadOffset.from("0")));
                if (pending != null) {
                    process(pending, consumer);
                }
            } catch (Exception e) {
                log.error("购票订单消费异常（留 pending 待下轮认领）：{}", e.getMessage());
                sleepQuietly(1000);
            }
        }
    }

    /** 逐条处理：成功则落库+ack+删除；失败则记录并保留 pending（不 ack），由下轮认领重试。 */
    private void process(List<MapRecord<String, Object, Object>> records, Consumer consumer) {
        for (MapRecord<String, Object, Object> r : records) {
            RecordId id = r.getId();
            try {
                handle(r.getValue());
                redis.opsForStream().acknowledge(streamKey, group, id);
                redis.opsForStream().delete(streamKey, id);
            } catch (Exception e) {
                // 落库失败：不 ack、不删除，等认领重试；同时告警
                log.error("订单 {} 落库失败，已留 pending 待重试：{}",
                        r.getValue().get("requestId"), e.getMessage());
            }
        }
    }

    private void handle(Map<Object, Object> body) {
        String requestId = str(body.get("requestId"));
        if (requestId == null || requestId.isEmpty()) {
            return;
        }
        if (TOMBSTONE.equals(redis.opsForValue().get(RailwayKeys.inflight(requestId)))) {
            // 已被对账任务判定为投递失败，且余票已回补：丢弃这个迟到的事件。
            // 否则会出现「先补偿、后插单」——库存与订单对不上。
            log.warn("订单 {} 已被对账判定失败并回补余票，丢弃该事件", requestId);
            return;
        }
        if (orderRepository.findByRequestId(requestId).isPresent()) {
            cleanupInflight(requestId);
            return;
        }
        TicketOrder o = new TicketOrder();
        o.setRequestId(requestId);
        o.setOrderNo(str(body.get("orderNo")));
        o.setUserId(Long.valueOf(str(body.get("userId"))));
        o.setTripId(Long.valueOf(str(body.get("tripId"))));
        o.setSeatClass(str(body.get("seatClass")));
        o.setSeatNo(str(body.get("seatNo")));
        String type = str(body.get("ticketType"));
        o.setTicketType(type == null || type.isEmpty() ? "ADULT" : type);
        String age = str(body.get("passengerAge"));
        if (age != null && !age.isEmpty()) {
            o.setPassengerAge(Integer.valueOf(age));
        }
        o.setFromStation(str(body.get("from")));
        o.setToStation(str(body.get("to")));
        o.setPassengerName(str(body.get("passengerName")));
        o.setPassengerId(str(body.get("passengerId")));
        o.setPriceCents(Long.parseLong(str(body.get("priceCents"))));
        o.setStatus(TicketOrder.PENDING);
        // 用事件里的下单时刻，而不是消费时刻：否则 10 分钟支付窗口从"落库"起算，
        // 消费延迟越大留给用户的支付时间越短，与产品语义不符。
        String createdAtMs = str(body.get("createdAt"));
        o.setCreatedAt(createdAtMs == null || createdAtMs.isEmpty()
                ? LocalDateTime.now()
                : LocalDateTime.ofInstant(Instant.ofEpochMilli(Long.parseLong(createdAtMs)),
                        ZoneId.systemDefault()));
        try {
            orderRepository.save(o);
        } catch (DataIntegrityViolationException e) {
            // 重复落库，容忍
        }
        cleanupInflight(requestId);
    }

    /** 落库成功（或订单已存在）后销掉在途登记，对账任务就不会再把它当成失败去补偿。 */
    private void cleanupInflight(String requestId) {
        try {
            redis.delete(RailwayKeys.inflight(requestId));
            redis.opsForZSet().remove(RailwayKeys.inflightIndex(), requestId);
        } catch (Exception e) {
            log.warn("清理在途登记失败（对账任务会兜底）：{}", e.getMessage());
        }
    }

    private String str(Object o) {
        return o == null ? null : String.valueOf(o);
    }

    private void sleepQuietly(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
