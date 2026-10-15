package com.group.resv.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * 把调用方传来的 {@code X-Trace-Id} 放进 MDC，于是 Java 日志里也会带上这个 id。
 *
 * <h3>为什么需要</h3>
 * 一次「下单」横跨 Python 编排层与 Java 交易层：Python 侧生成 trace id 并放在请求头里，
 * Java 侧写进 MDC。出问题时把同一个 id 拿去两边 grep，就能拼出完整链路——
 * 在此之前只能靠时间戳猜，跨服务根本对不上号。
 *
 * <h3>必须在 finally 里 remove</h3>
 * Servlet 容器**复用线程**。不清理的话，上一个请求的 trace id 会残留在 MDC 里，
 * 串到下一个请求的日志上——那比没有 trace id 更糟：会把人引向错误的链路。
 *
 * <h3>顺序</h3>
 * {@link Ordered#HIGHEST_PRECEDENCE}：要排在 Spring Security 之前，
 * 否则鉴权失败的日志里没有 trace id。
 *
 * 缺省时（如健康检查、直连的 curl）MDC 为空，日志格式里用 {@code %X{traceId:-}} 兜底。
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class TraceIdFilter extends OncePerRequestFilter {

    public static final String HEADER = "X-Trace-Id";
    public static final String MDC_KEY = "traceId";

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        String tid = request.getHeader(HEADER);
        if (tid != null && !tid.isBlank()) {
            MDC.put(MDC_KEY, tid);
        }
        try {
            chain.doFilter(request, response);
        } finally {
            MDC.remove(MDC_KEY);
        }
    }
}
