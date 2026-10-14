package com.group.resv.railway;

import com.group.resv.railway.domain.TicketOrder;
import com.group.resv.railway.repo.TicketOrderRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 超时订单过期的事务边界。
 *
 * 单独拆成一个 bean，是因为 {@code @Transactional} 靠代理生效——把带注解的方法写在
 * {@code @Scheduled} 所在的类里、由本类自己调用，注解会被静默忽略。
 *
 * 「读出来 → 确认仍是 PENDING → 改成 EXPIRED」收在同一个事务里，并与支付/退票
 * 通过 {@code @Version} 乐观锁互斥：并发的两条路径只有一条能提交成功。
 * 调用方**只有拿到非 null 返回值（即本次确实由我改成了 EXPIRED）才回补余票**，
 * 避免"订单已被支付/退票，却又被过期任务回补一张"的重复回补。
 */
@Service
public class TicketExpireService {

    private final TicketOrderRepository orderRepository;

    public TicketExpireService(TicketOrderRepository orderRepository) {
        this.orderRepository = orderRepository;
    }

    /** 超过 <before> 仍未支付的订单 id。只读，不做事务要求。 */
    public List<Long> findStaleIds(LocalDateTime before) {
        return orderRepository.findByStatusAndCreatedAtBefore(TicketOrder.PENDING, before)
                .stream().map(TicketOrder::getId).toList();
    }

    /**
     * 尝试把订单置为 EXPIRED。
     *
     * @return 本次确实改成 EXPIRED 时返回该订单；状态已被支付/退票改走则返回 null
     */
    @Transactional
    public TicketOrder tryExpire(Long orderId) {
        return orderRepository.findById(orderId)
                .filter(o -> TicketOrder.PENDING.equals(o.getStatus()))
                .map(o -> {
                    o.setStatus(TicketOrder.EXPIRED);
                    return orderRepository.save(o);
                })
                .orElse(null);
    }
}
