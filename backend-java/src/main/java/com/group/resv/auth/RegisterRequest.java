package com.group.resv.auth;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record RegisterRequest(
        @NotBlank(message = "不能为空")
        @Size(min = 3, max = 32, message = "长度需在 3-32 之间") String username,
        @NotBlank(message = "不能为空")
        @Size(min = 6, max = 64, message = "长度需在 6-64 之间") String password,
        @NotBlank(message = "不能为空") String name) {
}
