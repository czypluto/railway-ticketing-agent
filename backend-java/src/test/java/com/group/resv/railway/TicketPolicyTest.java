package com.group.resv.railway;

import com.group.resv.common.BizException;
import com.group.resv.railway.domain.TicketOrder;
import com.group.resv.railway.domain.Trip;
import com.group.resv.railway.repo.TicketOrderRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDate;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;

/**
 * TicketPolicy 确定性规则单测。
 *
 * 这一层是「模型决策 + 确定性执行」里"确定性"的那一半，也是合规与权限的核心，
 * 之前只有 TicketServiceTest 的 3 个用例间接覆盖，规则本身没有直接测试。
 */
@ExtendWith(MockitoExtension.class)
class TicketPolicyTest {

    @Mock TicketOrderRepository orderRepository;
    @InjectMocks TicketPolicy policy;

    private static Trip tripOn(LocalDate date) {
        Trip t = new Trip();
        t.setTravelDate(date);
        return t;
    }

    private static TicketOrder orderWith(String status) {
        TicketOrder o = new TicketOrder();
        o.setStatus(status);
        return o;
    }

    // ---------- 车次日期 ----------

    @Test
    void reject_past_trip() {
        BizException e = assertThrows(BizException.class,
                () -> policy.ensureNotPast(tripOn(LocalDate.now().minusDays(1))));
        assertEquals(400, e.getCode());
    }

    @Test
    void allow_today_and_future_trip() {
        assertDoesNotThrow(() -> policy.ensureNotPast(tripOn(LocalDate.now())));
        assertDoesNotThrow(() -> policy.ensureNotPast(tripOn(LocalDate.now().plusDays(5))));
    }

    // ---------- 一人一票（车次级） ----------

    @Test
    void reject_when_same_trip_already_has_active_order() {
        when(orderRepository.existsByUserIdAndTripIdAndStatusIn(eq(1L), eq(2L), any()))
                .thenReturn(true);
        BizException e = assertThrows(BizException.class, () -> policy.ensureOneTicket(1L, 2L));
        assertEquals(409, e.getCode());
    }

    @Test
    void allow_when_no_active_order_on_that_trip() {
        when(orderRepository.existsByUserIdAndTripIdAndStatusIn(eq(1L), eq(2L), any()))
                .thenReturn(false);
        assertDoesNotThrow(() -> policy.ensureOneTicket(1L, 2L));
    }

    // ---------- 儿童票：6 周岁（含）到 14 周岁（不含） ----------

    @Test
    void child_ticket_age_boundary() {
        assertDoesNotThrow(() -> policy.ensureChildEligible(6));
        assertDoesNotThrow(() -> policy.ensureChildEligible(13));
        for (Integer age : new Integer[]{null, 5, 14, 30}) {
            assertThrows(BizException.class, () -> policy.ensureChildEligible(age), "age=" + age);
        }
    }

    // ---------- 学生票：学生身份 + 二等座 + 寒暑假窗口 ----------

    @Test
    void student_ticket_requires_all_three_conditions() {
        int y = LocalDate.now().getYear();
        LocalDate summer = LocalDate.of(y, 7, 15);
        LocalDate winter = LocalDate.of(y, 2, 10);
        LocalDate outsideWindow = LocalDate.of(y, 10, 14);

        // 三个条件都满足才放行
        assertDoesNotThrow(() -> policy.ensureStudentEligible(true, summer, "二等座"));
        assertDoesNotThrow(() -> policy.ensureStudentEligible(true, winter, "二等座"));

        assertThrows(BizException.class, () -> policy.ensureStudentEligible(false, summer, "二等座"));
        assertThrows(BizException.class, () -> policy.ensureStudentEligible(null, summer, "二等座"));
        assertThrows(BizException.class, () -> policy.ensureStudentEligible(true, summer, "一等座"));
        assertThrows(BizException.class, () -> policy.ensureStudentEligible(true, outsideWindow, "二等座"));
    }

    // ---------- 支付状态机：只允许从 PENDING 发生，PAID 幂等 ----------

    @Test
    void pay_only_from_pending_paid_is_idempotent() {
        assertDoesNotThrow(() -> policy.ensurePayable(orderWith(TicketOrder.PENDING)));
        assertDoesNotThrow(() -> policy.ensurePayable(orderWith(TicketOrder.PAID)));
        assertThrows(BizException.class, () -> policy.ensurePayable(orderWith(TicketOrder.CANCELLED)));
        assertThrows(BizException.class, () -> policy.ensurePayable(orderWith(TicketOrder.EXPIRED)));
    }

    // ---------- 退票状态机：PENDING/PAID 可退，CANCELLED 幂等，EXPIRED 已回补不能再退 ----------

    @Test
    void cancel_from_pending_or_paid_cancelled_is_idempotent() {
        assertDoesNotThrow(() -> policy.ensureCancellable(orderWith(TicketOrder.PENDING)));
        assertDoesNotThrow(() -> policy.ensureCancellable(orderWith(TicketOrder.PAID)));
        assertDoesNotThrow(() -> policy.ensureCancellable(orderWith(TicketOrder.CANCELLED)));
        assertThrows(BizException.class, () -> policy.ensureCancellable(orderWith(TicketOrder.EXPIRED)));
    }
}
