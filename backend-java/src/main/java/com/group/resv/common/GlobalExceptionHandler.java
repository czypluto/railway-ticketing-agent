package com.group.resv.common;

import jakarta.validation.ConstraintViolationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.orm.ObjectOptimisticLockingFailureException;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

import java.util.stream.Collectors;

/**
 * 统一异常出口。原则：**客户端错误返回 4xx + 可读原因，不要一律兜成 500**。
 * 早期实现只覆盖了 BizException / 权限 / 乐观锁，导致 @Valid 失败、缺参、JSON 畸形
 * 这些客户端错误全部落到 Exception 分支变成 500，调用方拿不到有效信息。
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(BizException.class)
    public ResponseEntity<ApiResult<Void>> handleBiz(BizException e) {
        int code = e.getCode();
        int status = code >= 400 && code <= 599 ? code : 500;
        return ResponseEntity.status(status).body(ApiResult.fail(code, e.getMessage()));
    }

    @ExceptionHandler(AccessDeniedException.class)
    public ResponseEntity<ApiResult<Void>> handleAccessDenied(AccessDeniedException e) {
        return ResponseEntity.status(403).body(ApiResult.fail(403, "无权限执行该操作"));
    }

    @ExceptionHandler(ObjectOptimisticLockingFailureException.class)
    public ResponseEntity<ApiResult<Void>> handleOptimisticLock(ObjectOptimisticLockingFailureException e) {
        // 过期任务/支付/退票并发改写同一订单：后到者感知冲突，提示刷新
        return ResponseEntity.status(409).body(ApiResult.fail(409, "订单状态刚被更新，请刷新后重试"));
    }

    /** @Valid 校验失败：逐字段拼成一句可读提示。 */
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiResult<Void>> handleInvalidBody(MethodArgumentNotValidException e) {
        String msg = e.getBindingResult().getFieldErrors().stream()
                .map(f -> describe(f))
                .collect(Collectors.joining("；"));
        return ResponseEntity.status(400)
                .body(ApiResult.fail(400, msg.isBlank() ? "请求参数校验失败" : msg));
    }

    /** 方法参数上的约束校验失败（如 @RequestParam 上的 @NotNull）。 */
    @ExceptionHandler(ConstraintViolationException.class)
    public ResponseEntity<ApiResult<Void>> handleConstraint(ConstraintViolationException e) {
        String msg = e.getConstraintViolations().stream()
                .map(v -> v.getPropertyPath() + " " + v.getMessage())
                .collect(Collectors.joining("；"));
        return ResponseEntity.status(400)
                .body(ApiResult.fail(400, msg.isBlank() ? "请求参数校验失败" : msg));
    }

    /** JSON 畸形：请求体不是合法 JSON。 */
    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ApiResult<Void>> handleUnreadable(HttpMessageNotReadableException e) {
        log.warn("请求体解析失败：{}", e.getMessage());
        return ResponseEntity.status(400).body(ApiResult.fail(400, "请求体不是合法的 JSON"));
    }

    /** 缺少必填查询参数。 */
    @ExceptionHandler(MissingServletRequestParameterException.class)
    public ResponseEntity<ApiResult<Void>> handleMissingParam(MissingServletRequestParameterException e) {
        return ResponseEntity.status(400).body(ApiResult.fail(400, "缺少必填参数：" + e.getParameterName()));
    }

    /** 参数类型不符（例如日期格式不对）。 */
    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public ResponseEntity<ApiResult<Void>> handleTypeMismatch(MethodArgumentTypeMismatchException e) {
        return ResponseEntity.status(400).body(ApiResult.fail(400, "参数格式不正确：" + e.getName()));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiResult<Void>> handleOther(Exception e) {
        log.error("未捕获异常", e);
        return ResponseEntity.status(500).body(ApiResult.fail(500, "系统内部错误"));
    }

    private static String describe(FieldError f) {
        String m = f.getDefaultMessage();
        return m == null || m.isBlank() ? f.getField() + " 不合法" : f.getField() + " " + m;
    }
}
