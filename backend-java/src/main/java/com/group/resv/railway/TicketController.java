package com.group.resv.railway;

import com.group.resv.audit.AuditService;
import com.group.resv.common.ApiResult;
import com.group.resv.common.BizException;
import com.group.resv.railway.domain.TicketOrder;
import com.group.resv.security.AuthUser;
import com.group.resv.security.SecurityUtil;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

/**
 * 购票对外接口：余票查询、下单、支付、退票、我的订单。
 */
@RestController
@RequestMapping("/api/ticket")
@Tag(name = "车票", description = "余票查询 / 购票 / 支付 / 退票 / 我的订单")
public class TicketController {

    private final TicketService ticketService;
    private final AuditService auditService;

    public TicketController(TicketService ticketService, AuditService auditService) {
        this.ticketService = ticketService;
        this.auditService = auditService;
    }

    private AuthUser who() {
        return SecurityUtil.current();
    }

    private Map<String, Object> p(Object... kv) {
        Map<String, Object> m = new java.util.HashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            m.put(String.valueOf(kv[i]), kv[i + 1]);
        }
        return m;
    }

    @Operation(summary = "余票查询", description = "按起止站与日期查可用车次及席别余票")
    @GetMapping("/query")
    public ApiResult<List<Map<String, Object>>> query(
            @RequestParam Long from,
            @RequestParam Long to,
            @RequestParam @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate date,
            @RequestParam(required = false) String seatClass) {
        return ApiResult.ok(ticketService.query(from, to, date, seatClass));
    }

    @Operation(summary = "车站列表", description = "只读；供购票页与 Agent 把站名解析为 stationId（鉴权即可访问，不限 ADMIN）")
    @GetMapping("/stations")
    public ApiResult<List<Map<String, Object>>> stations(@RequestParam(required = false) String kw) {
        return ApiResult.ok(ticketService.stations(kw));
    }

    @Operation(summary = "购票", description = "锁定库存并生成待支付订单（幂等，同人同车次同席别防重复）")
    @PostMapping("/buy")
    public ApiResult<Map<String, Object>> buy(@Valid @RequestBody TicketService.BuyRequest req) {
        AuthUser u = who();
        Map<String, Object> params = p("tripId", req.tripId(), "seatClass", req.seatClass(),
                "fromStationId", req.fromStationId(), "toStationId", req.toStationId());
        try {
            Map<String, Object> r = ticketService.buy(u, req);
            auditService.success(u.userId(), "ticket", "buy", String.valueOf(r.get("requestId")),
                    p("tripId", req.tripId(), "seatClass", req.seatClass(), "orderNo", r.get("orderNo"), "status", r.get("status")));
            return ApiResult.ok(r);
        } catch (BizException e) {
            auditService.fail(u.userId(), "ticket", "buy", String.valueOf(params.get("tripId")), params, e.getMessage());
            throw e;
        }
    }

    @PostMapping("/orders/{requestId}/pay")
    public ApiResult<Map<String, Object>> pay(@PathVariable String requestId) {
        AuthUser u = who();
        try {
            Map<String, Object> r = ticketService.pay(requestId, u.userId());
            auditService.success(u.userId(), "ticket", "pay", requestId, p("status", r.get("status"), "orderNo", r.get("orderNo")));
            return ApiResult.ok(r);
        } catch (BizException e) {
            auditService.fail(u.userId(), "ticket", "pay", requestId, p(), e.getMessage());
            throw e;
        }
    }

    @PostMapping("/orders/{requestId}/cancel")
    public ApiResult<Map<String, Object>> cancel(@PathVariable String requestId) {
        AuthUser u = who();
        try {
            Map<String, Object> r = ticketService.cancel(requestId, u.userId());
            auditService.success(u.userId(), "ticket", "cancel", requestId, p("status", r.get("status"), "orderNo", r.get("orderNo")));
            return ApiResult.ok(r);
        } catch (BizException e) {
            auditService.fail(u.userId(), "ticket", "cancel", requestId, p(), e.getMessage());
            throw e;
        }
    }

    @GetMapping("/orders/my")
    public ApiResult<List<Map<String, Object>>> my(@RequestParam(defaultValue = "50") int size) {
        return ApiResult.ok(ticketService.myOrders(SecurityUtil.current().userId(), size));
    }

    @GetMapping("/orders/request/{requestId}")
    public ApiResult<Map<String, Object>> byRequest(@PathVariable String requestId) {
        return ApiResult.ok(ticketService.getByRequest(requestId, SecurityUtil.current().userId()));
    }
}
