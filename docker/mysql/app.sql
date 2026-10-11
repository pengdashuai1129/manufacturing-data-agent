
SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS app DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
GRANT ALL PRIVILEGES ON app.* TO 'hohohoho'@'%';

USE app;


CREATE TABLE IF NOT EXISTS sys_user
(
    user_id       VARCHAR(32) PRIMARY KEY              COMMENT '账号编号',
    username      VARCHAR(64) NOT NULL                 COMMENT '登录名',
    password_hash VARCHAR(255) NOT NULL                COMMENT '口令散列(PBKDF2，格式见 auth_service)',
    display_name  VARCHAR(64)                          COMMENT '显示名称',
    role          VARCHAR(32) NOT NULL                 COMMENT '角色：admin/boss/sales_manager/salesman',
    employee_id   VARCHAR(20)                          COMMENT '绑定的员工编号，对应 dw.dim_employee.employee_id；不绑具体员工的角色可为空',
    status        VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT '状态：active/disabled',
    created_at    DATETIME NOT NULL                    COMMENT '创建时间',
    UNIQUE KEY uk_sys_user_username (username)
) COMMENT '登录账号';


CREATE TABLE IF NOT EXISTS sys_session
(
    token      VARCHAR(64) PRIMARY KEY COMMENT '会话令牌(随机串，写在 HttpOnly Cookie 里)',
    user_id    VARCHAR(32) NOT NULL    COMMENT '账号编号',
    created_at DATETIME NOT NULL       COMMENT '登录时间',
    expires_at DATETIME NOT NULL       COMMENT '过期时间',
    KEY idx_sys_session_user (user_id)
) COMMENT '登录会话';


CREATE TABLE IF NOT EXISTS query_log
(
    id         BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增编号',
    user_id    VARCHAR(32)      COMMENT '提问的账号',
    question   TEXT             COMMENT '用户的原始问题',
    final_sql  TEXT             COMMENT '最终执行的 SQL(已应用行级权限)',
    status     VARCHAR(16)      COMMENT '结果：ok/rejected/error',
    row_count  INT              COMMENT '返回行数',
    elapsed_ms INT              COMMENT '耗时(毫秒)',
    request_id VARCHAR(32)      COMMENT '请求编号，用来和日志文件对上',
    created_at DATETIME NOT NULL COMMENT '提问时间',
    KEY idx_query_log_user_time (user_id, created_at)
) COMMENT '问数查询日志';


CREATE TABLE IF NOT EXISTS data_change_log
(
    id          BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增编号',
    table_name  VARCHAR(64) NOT NULL COMMENT '被改的表',
    pk_value    VARCHAR(64) NOT NULL COMMENT '被改行的主键值',
    action      VARCHAR(16) NOT NULL COMMENT '动作：insert/update/delete',
    before_json JSON                 COMMENT '改前整行快照(新增时为空)',
    after_json  JSON                 COMMENT '改后整行快照(删除时为空)',
    user_id     VARCHAR(32)          COMMENT '操作人账号',
    created_at  DATETIME NOT NULL    COMMENT '操作时间',
    KEY idx_change_log_table_pk (table_name, pk_value)
) COMMENT '数据变更审计日志';
