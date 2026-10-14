package com.group.resv.railway.repo;

import com.group.resv.railway.domain.TicketOrder;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface TicketOrderRepository extends JpaRepository<TicketOrder, Long> {

    Optional<TicketOrder> findByRequestId(String requestId);

    List<TicketOrder> findByUserIdOrderByIdDesc(Long userId);

    List<TicketOrder> findByUserIdOrderByIdDesc(Long userId, org.springframework.data.domain.Pageable pageable);

    boolean existsByUserIdAndTripIdAndStatusIn(Long userId, Long tripId, Collection<String> statuses);

    List<TicketOrder> findByStatusAndCreatedAtBefore(String status, java.time.LocalDateTime before);

    long countByTripIdAndSeatClassAndStatusIn(Long tripId, String seatClass, Collection<String> statuses);
}
