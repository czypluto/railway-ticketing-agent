package com.group.resv.railway;

import com.group.resv.common.BizException;
import com.group.resv.domain.Contact;
import com.group.resv.domain.User;
import com.group.resv.railway.domain.Station;
import com.group.resv.railway.domain.TicketOrder;
import com.group.resv.railway.domain.Train;
import com.group.resv.railway.domain.TrainStop;
import com.group.resv.railway.domain.Trip;
import com.group.resv.railway.domain.TripClass;
import com.group.resv.railway.repo.StationRepository;
import com.group.resv.railway.repo.TicketOrderRepository;
import com.group.resv.railway.repo.TrainRepository;
import com.group.resv.railway.repo.TrainStopRepository;
import com.group.resv.railway.repo.TripClassRepository;
import com.group.resv.railway.repo.TripRepository;
import com.group.resv.redis.DependencyCircuitBreaker;
import com.group.resv.redis.RateLimiter;
import com.group.resv.repo.ContactRepository;
import com.group.resv.repo.UserRepository;
import com.group.resv.security.AuthUser;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;

/**
 * 购票主流程：余票查询 -> 下单(限流/防重/原子扣减) -> Stream 异步落库 -> 支付/退票。
 * 高并发亮点全部在这里：Redis Lua 扣减 + Redisson 防重 + Redis Stream 削峰。
 */
@Service
public class TicketService {

    private static final Logger log = LoggerFactory.getLogger(TicketService.class);

    private final TripRepository tripRepository;
    private final TrainRepository trainRepository;
    private final TrainStopRepository trainStopRepository;
    private final StationRepository stationRepository;
    private final TripClassRepository tripClassRepository;
    private final TicketOrderRepository orderRepository;
    private final ContactRepository contactRepository;
    private final UserRepository userRepository;
    private final RailwayStockService stockService;
    private final StringRedisTemplate redis;
    private final RedissonClient redisson;
    private final RateLimiter rateLimiter;
    private final DependencyCircuitBreaker breaker;
    private final TicketPolicy policy;

    public TicketService(TripRepository tripRepository,
                         TrainRepository trainRepository,
                         TrainStopRepository trainStopRepository,
                         StationRepository stationRepository,
                         TripClassRepository tripClassRepository,
                         TicketOrderRepository orderRepository,
                         ContactRepository contactRepository,
                         UserRepository userRepository,
                         RailwayStockService stockService,
                         StringRedisTemplate redis,
                         RedissonClient redisson,
                         RateLimiter rateLimiter,
                         DependencyCircuitBreaker breaker,
                         TicketPolicy policy) {
        this.tripRepository = tripRepository;
        this.trainRepository = trainRepository;
        this.trainStopRepository = trainStopRepository;
        this.stationRepository = stationRepository;
        this.tripClassRepository = tripClassRepository;
        this.orderRepository = orderRepository;
        this.contactRepository = contactRepository;
        this.userRepository = userRepository;
        this.stockService = stockService;
        this.redis = redis;
        this.redisson = redisson;
        this.rateLimiter = rateLimiter;
        this.breaker = breaker;
        this.policy = policy;
    }

    // ---------- 余票查询 ----------

    public List<Map<String, Object>> query(Long fromId, Long toId, LocalDate date, String seatClass) {
        if (fromId == null || toId == null || date == null) {
            throw new BizException(400, "起止站与日期必填");
        }
        List<Map<String, Object>> out = new java.util.ArrayList<>();
        for (Trip trip : tripRepository.findByTravelDateOrderByTravelDateAsc(date)) {
            if (!"OPEN".equals(trip.getStatus())) continue;
            Train train = trainRepository.findById(trip.getTrainId()).orElse(null);
            List<TrainStop> stops = trainStopRepository.findByTrainIdOrderBySeqAsc(trip.getTrainId());
            int fromIdx = indexOfStation(stops, fromId);
            int toIdx = indexOfStation(stops, toId);
            if (fromIdx < 0 || toIdx <= fromIdx) continue;

            List<TripClass> classes = seatClass == null || seatClass.isBlank()
                    ? tripClassRepository.findByTripIdOrderByIdAsc(trip.getId())
                    : tripClassRepository.findByTripIdAndSeatClass(trip.getId(), seatClass).stream().toList();
            for (TripClass tc : classes) {
                Map<String, Object> m = new HashMap<>();
                m.put("tripId", trip.getId());
                m.put("trainCode", train == null ? null : train.getCode());
                m.put("travelDate", trip.getTravelDate());
                m.put("from", name(stops.get(fromIdx).getStationId()));
                m.put("to", name(stops.get(toIdx).getStationId()));
                m.put("departTime", stops.get(fromIdx).getDepartTime());
                m.put("arriveTime", stops.get(toIdx).getArriveTime());
                m.put("seatClass", tc.getSeatClass());
                m.put("priceCents", tc.getPriceCents());
                m.put("remaining", stockService.remaining(trip.getId(), tc.getSeatClass()));
                out.add(m);
            }
        }
        return out;
    }

    /**
     * 车站只读列表：购票页与 Agent 都需要把「站名」解析成 stationId。
     * 管理端的 /api/rail/stations 是 ADMIN-only，普通用户拿不到；这里只暴露只读查询。
     */
    public List<Map<String, Object>> stations(String kw) {
        List<Station> list = kw == null || kw.isBlank()
                ? stationRepository.findAll()
                : stationRepository.findByNameContainingOrderByIdAsc(kw);
        return list.stream()
                .map(s -> Map.<String, Object>of("id", s.getId(), "code", s.getCode(), "name", s.getName()))
                .toList();
    }

    // ---------- 下单 ----------

    public record BuyRequest(
            @NotNull(message = "车次不能为空") Long tripId,
            @NotBlank(message = "席别不能为空") String seatClass,
            @NotNull(message = "出发站不能为空") Long fromStationId,
            @NotNull(message = "到达站不能为空") Long toStationId,
            Long contactId,
            String requestId,
            String ticketType) {
    }

    public Map<String, Object> buy(AuthUser user, BuyRequest req) {
        if (!rateLimiter.allow("ticket-buy")) {
            throw new BizException(429, "系统繁忙，请稍后重试");
        }
        String requestId = req.requestId() == null || req.requestId().isBlank()
                ? java.util.UUID.randomUUID().toString().replace("-", "")
                : req.requestId();

        // 幂等：已存在的 requestId 直接返回首次结果
        Optional<TicketOrder> existed = orderRepository.findByRequestId(requestId);
        if (existed.isPresent()) {
            return orderView(existed.get());
        }

        Trip trip = tripRepository.findById(req.tripId())
                .orElseThrow(() -> new BizException(404, "车次不存在"));
        if (!"OPEN".equals(trip.getStatus())) {
            throw new BizException(400, "该车次已停售");
        }
        policy.ensureNotPast(trip);
        Train train = trainRepository.findById(trip.getTrainId()).orElse(null);
        List<TrainStop> stops = trainStopRepository.findByTrainIdOrderBySeqAsc(trip.getTrainId());
        int fromIdx = indexOfStation(stops, req.fromStationId());
        int toIdx = indexOfStation(stops, req.toStationId());
        if (fromIdx < 0 || toIdx <= fromIdx) {
            throw new BizException(400, "起止站不在该车次停靠顺序内");
        }
        TripClass tc = tripClassRepository.findByTripIdAndSeatClass(trip.getId(), req.seatClass())
                .orElseThrow(() -> new BizException(404, "该席别不存在"));

        // 确定性规则审核（独立于模型）：每车次一人一票
        policy.ensureOneTicket(user.userId(), trip.getId());

        // 乘车人：优先常用联系人，否则本人
        Contact c = req.contactId() == null ? null
                : contactRepository.findByIdAndUserId(req.contactId(), user.userId()).orElse(null);
        String passengerName;
        String passengerId;
        Integer passengerAge;
        if (c != null) {
            passengerName = c.getName();
            passengerId = c.getIdNo();
            passengerAge = c.getAge();
        } else {
            User u = userRepository.findById(user.userId())
                    .orElseThrow(() -> new BizException(404, "用户不存在"));
            passengerName = u.getName();
            passengerId = u.getIdNo();
            passengerAge = u.getAge();
        }

        // 票种前置规则：儿童票 6-14 周岁半价；学生票 学生+二等座+寒暑假 75%价；默认成人票
        String ticketType = req.ticketType() == null || req.ticketType().isBlank()
                ? "ADULT" : req.ticketType().toUpperCase();
        long priceCents = tc.getPriceCents();
        if ("CHILD".equals(ticketType)) {
            policy.ensureChildEligible(passengerAge);
            priceCents = tc.getPriceCents() / 2;
        } else if ("STUDENT".equals(ticketType)) {
            Boolean isStudent = userRepository.findById(user.userId())
                    .map(User::getStudent).orElse(false);
            policy.ensureStudentEligible(isStudent, trip.getTravelDate(), tc.getSeatClass());
            priceCents = tc.getPriceCents() * 75 / 100;
        }

        String orderNo = genOrderNo();

        // Redisson 锁：同 requestId 并发只进一个
        RLock lock = redisson.getLock("rv:req:" + requestId);
        boolean locked = false;
        try {
            locked = breaker.call("redis", () -> {
                try {
                    return lock.tryLock(0, 5, TimeUnit.SECONDS);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    throw new BizException(503, "请求被中断，请重试");
                }
            });
            if (!locked) {
                // 拿不到锁是业务结果（并发重复提交），不是依赖故障——不该计入熔断
                throw new BizException(409, "请勿重复提交");
            }
            // 二次查重（锁内）
            existed = orderRepository.findByRequestId(requestId);
            if (existed.isPresent()) {
                return orderView(existed.get());
            }

            // 一人一票活跃占位（车次级 SETNX 原子准入，防并发“先查后写”重复下单）
            String activeKey = RailwayKeys.active(user.userId(), trip.getId());
            Boolean acquired = breaker.call("redis", () -> redis.opsForValue().setIfAbsent(
                    activeKey, "1", Duration.ofMinutes(30))); // 兜底租约；正常走 cancel/expire 显式释放
            if (!Boolean.TRUE.equals(acquired)) {
                throw new BizException(409, "您已购买该车次车票，一人一票，请勿重复购买");
            }

            // Lua 原子扣减；未预热先预热一次
            long left = stockService.decr(trip.getId(), tc.getSeatClass());
            if (left == -2) {
                stockService.preheat(trip.getId(), tc.getSeatClass());
                left = stockService.decr(trip.getId(), tc.getSeatClass());
            }
            if (left < 0) {
                redis.delete(activeKey); // 没买到就释放占位
                throw new BizException(409, "余票不足");
            }

            // 座位分配：原子递增取号，已分配的座不会再给第二个用户
            long seatNo = breaker.call("redis", () -> redis.opsForValue().increment(
                    RailwayKeys.seatSeq(trip.getId(), tc.getSeatClass())));

            String from = name(stops.get(fromIdx).getStationId());
            String to = name(stops.get(toIdx).getStationId());
            // 写 Stream，异步落库（削峰）；携带票种与年龄供消费端与审计使用
            Map<String, Object> event = new HashMap<>();
            event.put("requestId", requestId);
            event.put("orderNo", orderNo);
            event.put("userId", String.valueOf(user.userId()));
            event.put("tripId", String.valueOf(trip.getId()));
            event.put("seatClass", tc.getSeatClass());
            event.put("ticketType", ticketType);
            event.put("from", from);
            event.put("to", to);
            event.put("passengerName", passengerName);
            event.put("passengerId", passengerId == null ? "" : passengerId);
            event.put("passengerAge", passengerAge == null ? "" : String.valueOf(passengerAge));
            event.put("seatNo", String.valueOf(seatNo));
            event.put("priceCents", String.valueOf(priceCents));
            // 下单时刻随事件落库：支付窗口应从"下单"起算，而不是从"落库"起算
            event.put("createdAt", String.valueOf(System.currentTimeMillis()));
            // 在途登记：库存已扣、订单尚未落库。消费端落库成功后销掉；若长期未销，
            // 由 TicketInflightReconcileJob 回补余票——否则异步落库失败会让余票永久泄漏。
            // 必须写在 XADD 之前，这样 XADD 失败时也能立刻回补。
            String inflightKey = RailwayKeys.inflight(requestId);
            long nowMs = System.currentTimeMillis();
            breaker.run("redis", () -> {
                redis.opsForValue().set(inflightKey,
                        trip.getId() + "|" + tc.getSeatClass() + "|" + user.userId() + "|" + nowMs,
                        Duration.ofMinutes(15));
                redis.opsForZSet().add(RailwayKeys.inflightIndex(), requestId, nowMs);
            });
            try {
                breaker.run("redis", () ->
                        redis.opsForStream().add(RailwayKeys.orderStream(), event));
            } catch (RuntimeException e) {
                // 写 Stream 失败 = 本次购买作废：占位、在途登记、**以及已扣的库存**都要还回去。
                // （此前只释放了占位，扣掉的那张票会永久泄漏）
                //
                // 补偿是尽力而为：Redis 已经不可用时这些 delete 大概率同样失败。
                // 不能让补偿的异常盖掉原始异常——那会把"为什么失败"这条线索弄丢。
                // 真正的兜底是 TicketInflightReconcileJob：在途登记没被销掉，它会把余票补回来。
                try {
                    redis.delete(activeKey);
                    redis.delete(inflightKey);
                    redis.opsForZSet().remove(RailwayKeys.inflightIndex(), requestId);
                    stockService.release(trip.getId(), tc.getSeatClass());
                } catch (RuntimeException comp) {
                    log.warn("购票失败后的补偿未全部完成，交由在途对账兜底 requestId={}：{}",
                            requestId, comp.toString());
                }
                throw e;
            }

            log.info("购票受理 requestId={} trip={} class={} type={} {}->{} user={}",
                    requestId, trip.getId(), tc.getSeatClass(), ticketType, from, to, user.userId());

            Map<String, Object> m = new HashMap<>();
            m.put("orderNo", orderNo);
            m.put("requestId", requestId);
            m.put("status", TicketOrder.PENDING);
            m.put("message", "座位 " + seatNo + " 已锁定，请及时支付");
            m.put("seatNo", seatNo);
            if (train != null) {
                m.put("trainCode", train.getCode());
            }
            m.put("seatClass", tc.getSeatClass());
            m.put("ticketType", ticketType);
            m.put("priceCents", priceCents);
            m.put("from", from);
            m.put("to", to);
            return m;
        } finally {
            if (locked && lock.isHeldByCurrentThread()) {
                lock.unlock();
            }
        }
    }

    // ---------- 支付（简化，模拟支付回调） ----------

    @Transactional
    public Map<String, Object> pay(String requestId, Long userId) {
        TicketOrder order = owned(requestId, userId);
        policy.ensurePayable(order);
        if (!TicketOrder.PAID.equals(order.getStatus())) {
            order.setStatus(TicketOrder.PAID);
            order.setPaidAt(LocalDateTime.now());
            orderRepository.save(order);
        }
        return orderView(order);
    }

    // ---------- 退票 ----------

    @Transactional
    public Map<String, Object> cancel(String requestId, Long userId) {
        TicketOrder order = owned(requestId, userId);
        if (TicketOrder.CANCELLED.equals(order.getStatus())) {
            return orderView(order); // 幂等
        }
        policy.ensureCancellable(order); // EXPIRED 已回补过，禁止二次退票回补
        order.setStatus(TicketOrder.CANCELLED);
        order.setCancelledAt(LocalDateTime.now());
        orderRepository.save(order);
        stockService.release(order.getTripId(), order.getSeatClass());
        // 释放一人一票活跃占位，允许该用户之后重新购买同车次同席别
        redis.delete(RailwayKeys.active(order.getUserId(), order.getTripId()));
        log.info("退票 requestId={} 余票已回补", requestId);
        return orderView(order);
    }

    @Transactional(readOnly = true)
    public List<Map<String, Object>> myOrders(Long userId, int size) {
        int limit = Math.max(1, Math.min(size, 100));
        return orderRepository.findByUserIdOrderByIdDesc(userId, PageRequest.of(0, limit))
                .stream().map(this::orderView).toList();
    }

    @Transactional(readOnly = true)
    public Map<String, Object> getByRequest(String requestId, Long userId) {
        return orderView(owned(requestId, userId));
    }

    private TicketOrder owned(String requestId, Long userId) {
        return orderRepository.findByRequestId(requestId)
                .filter(o -> o.getUserId().equals(userId))
                .orElseThrow(() -> new BizException(404, "订单不存在"));
    }

    private Map<String, Object> orderView(TicketOrder o) {
        Map<String, Object> m = new HashMap<>();
        m.put("id", o.getId());
        m.put("orderNo", o.getOrderNo());
        m.put("requestId", o.getRequestId());
        m.put("tripId", o.getTripId());
        m.put("seatClass", o.getSeatClass());
        m.put("ticketType", o.getTicketType());
        m.put("passengerAge", o.getPassengerAge());
        m.put("from", o.getFromStation());
        m.put("to", o.getToStation());
        m.put("passengerName", o.getPassengerName());
        m.put("seatNo", o.getSeatNo());
        m.put("priceCents", o.getPriceCents());
        m.put("status", o.getStatus());
        m.put("createdAt", o.getCreatedAt());
        m.put("paidAt", o.getPaidAt());
        return m;
    }

    private int indexOfStation(List<TrainStop> stops, Long stationId) {
        for (int i = 0; i < stops.size(); i++) {
            if (stops.get(i).getStationId().equals(stationId)) {
                return i;
            }
        }
        return -1;
    }

    private String name(Long stationId) {
        return stationRepository.findById(stationId).map(Station::getName).orElse(String.valueOf(stationId));
    }

    private String genOrderNo() {
        return "T" + LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMddHHmmss"))
                + (1000 + ThreadLocalRandom.current().nextInt(9000));
    }
}
