-- 铁路购票系统 - 基础表（MySQL 8）
-- 本文件只建系统用户表；铁路业务表见 002_ticket.sql，审计表见 003_audit.sql。
-- 数据由 Java 端 DataInitializer 在空表时播种演示数据。

CREATE TABLE IF NOT EXISTS sys_user (
    id         BIGINT       NOT NULL AUTO_INCREMENT,
    username   VARCHAR(64)  NOT NULL,
    password   VARCHAR(128) NOT NULL COMMENT 'BCrypt 密文',
    name       VARCHAR(64)  NOT NULL,
    department VARCHAR(64)  DEFAULT NULL,
    position   VARCHAR(64)  DEFAULT NULL COMMENT '职级：高管/部门正职/高级工程师/实习生等',
    role       VARCHAR(32)  DEFAULT NULL,
    created_at DATETIME     DEFAULT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_user_username (username)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '系统用户';
