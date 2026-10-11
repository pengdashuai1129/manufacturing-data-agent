
SET NAMES utf8mb4;

CREATE DATABASE IF NOT EXISTS dw DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
GRANT ALL PRIVILEGES ON dw.* TO 'hohohoho'@'%';
USE dw;



DROP TABLE IF EXISTS dim_customer;
CREATE TABLE dim_customer
(
    customer_id     VARCHAR(20) PRIMARY KEY COMMENT '客户ID，主键',
    customer_name   VARCHAR(60) COMMENT '客户名称（海外经销商）',
    country         VARCHAR(30) COMMENT '所在国家/地区',
    continent       VARCHAR(20) COMMENT '所属洲：欧洲/北美洲/大洋洲/亚洲',
    trade_type      VARCHAR(10) COMMENT '内外销：内销 / 出口',
    customer_level  VARCHAR(10) COMMENT '客户等级 A/B/C，按订单量分层，量大的等级越高',
    customer_status VARCHAR(10) COMMENT '新客户 / 老客户'
) COMMENT '客户维度表：客户档案，全部为经销商，无个人客户';


DROP TABLE IF EXISTS dim_product;
CREATE TABLE dim_product
(
    product_id   VARCHAR(30) PRIMARY KEY COMMENT '品号，主键（真实格式如 R0222341T01）',
    product_name VARCHAR(50) COMMENT '品名（如 水热散热器）',
    product_type VARCHAR(20) COMMENT '产品类型：成品 / 配件',
    heating_type VARCHAR(10) COMMENT '加热方式：水热 / 电热（配件可空 = 不适用）',
    material     VARCHAR(20) COMMENT '材质：不锈钢 / 碳钢（按表面处理反推，非单据原字段）',
    spec         VARCHAR(50) COMMENT '规格（真实格式如 1400*360/中心距为320）',
    unit_weight  DECIMAL(8,2) COMMENT '只重（kg/件，来自单据明细栏）'
) COMMENT '产品维度表：散热器业务线的产品档案，33 个品号全部来自真实单据';


DROP TABLE IF EXISTS dim_employee;
CREATE TABLE dim_employee
(
    employee_id   VARCHAR(20) PRIMARY KEY COMMENT '员工ID，主键',
    employee_name VARCHAR(20) COMMENT '姓名',
    department    VARCHAR(20) COMMENT '部门：业务部/运营部/技术部/采购部/生产部/财务部',
    employee_role VARCHAR(20) COMMENT '角色：业务员/销售内勤/技术员/技术总监/采购员/生产质检/应收会计/总经理'
) COMMENT '员工维度表：全员档案，事实表只引用业务员';


DROP TABLE IF EXISTS dim_workshop;
CREATE TABLE dim_workshop
(
    workshop_id   VARCHAR(20) PRIMARY KEY COMMENT '车间ID，主键',
    workshop_name VARCHAR(20) COMMENT '车间名称：机加工/制管/焊接/抛光/喷塑/包装'
) COMMENT '车间维度表：散热器工艺路线上的各道工序所在车间';


DROP TABLE IF EXISTS dim_date;
CREATE TABLE dim_date
(
    date_id INT PRIMARY KEY COMMENT '日期ID，主键，格式 YYYYMMDD（INT，便于直接比较大小）',
    year    INT COMMENT '年份',
    quarter VARCHAR(2) COMMENT '季度：Q1~Q4',
    month   INT COMMENT '月份：1~12',
    day     INT COMMENT '日：1~31'
) COMMENT '日期维度表：由递归 CTE 生成，覆盖 2025-01-01 ~ 2026-12-31';



DROP TABLE IF EXISTS fact_quotation;
CREATE TABLE fact_quotation
(
    quotation_id   VARCHAR(40) PRIMARY KEY COMMENT '报价单号，主键。规则 BJ-<业务员首字母>-<序号>',
    customer_id    VARCHAR(20) COMMENT '客户ID，外键 → dim_customer',
    product_id     VARCHAR(30) COMMENT '品号，外键 → dim_product',
    employee_id    VARCHAR(20) COMMENT '报价业务员ID，外键 → dim_employee',
    quote_date_id  INT COMMENT '报价日期，外键 → dim_date',
    currency       VARCHAR(10) COMMENT '币种：USD / CNY',
    quote_quantity INT COMMENT '报价数量（件）',
    quote_amount   DECIMAL(14,2) COMMENT '报价金额（按 currency 计价）',
    quote_status   VARCHAR(20) COMMENT '报价状态：中标 / 落标 / 跟进中',
    lost_reason    VARCHAR(50) COMMENT '未成交原因，仅落标单有值',
    order_id       VARCHAR(40) COMMENT '中标后回填的合同号，外键 → fact_order',
    KEY idx_q_customer (customer_id),
    KEY idx_q_product  (product_id),
    KEY idx_q_employee (employee_id),
    KEY idx_q_date     (quote_date_id)
) COMMENT '报价单事实表：接单漏斗最前端，回答"为什么接不到订单"';


DROP TABLE IF EXISTS fact_sample;
CREATE TABLE fact_sample
(
    sample_id       VARCHAR(40) PRIMARY KEY COMMENT '样品单号，主键。规则 YP-<业务员首字母>-<序号>',
    customer_id     VARCHAR(20) COMMENT '客户ID，外键 → dim_customer',
    product_id      VARCHAR(30) COMMENT '品号，外键 → dim_product',
    employee_id     VARCHAR(20) COMMENT '负责业务员ID，外键 → dim_employee',
    sample_date_id  INT COMMENT '送样日期，外键 → dim_date',
    due_date_id     INT COMMENT '承诺交期，外键 → dim_date',
    done_date_id    INT COMMENT '实际寄出日期，外键 → dim_date（未寄出为 NULL）',
    sample_quantity INT COMMENT '样品数量（件）',
    sample_status   VARCHAR(20) COMMENT '样品状态：制作中 / 已寄出 / 已确认 / 已放弃',
    delay_days      INT COMMENT '延期天数 = 实际寄出 - 承诺交期（未寄出为 NULL，AVG 会自动跳过）',
    is_on_time      VARCHAR(10) COMMENT '是否准时：准时 / 延期（未寄出为 NULL）',
    delay_reason    VARCHAR(50) COMMENT '延期原因',
    order_id        VARCHAR(40) COMMENT '转成正式合同后回填的合同号，外键 → fact_order',
    KEY idx_s_customer (customer_id),
    KEY idx_s_product  (product_id),
    KEY idx_s_employee (employee_id),
    KEY idx_s_date     (sample_date_id)
) COMMENT '样品单事实表：样品环节的交付表现与转化情况';


DROP TABLE IF EXISTS fact_order;
CREATE TABLE fact_order
(
    order_id             VARCHAR(40) PRIMARY KEY COMMENT '销售合同号，主键。规则 2302-<业务员首字母>-<序号>',
    customer_id          VARCHAR(20) COMMENT '客户ID，外键 → dim_customer',
    employee_id          VARCHAR(20) COMMENT '负责业务员ID，外键 → dim_employee（注意：创建人多为销售内勤，不是业务员）',
    order_date_id        INT COMMENT '订单日期，外键 → dim_date',
    ship_date_id         INT COMMENT '发货日期 / 承诺交期，外键 → dim_date（对应单据上的"生产评审交期"）',
    actual_ship_date_id  INT COMMENT '实际发货日期，外键 → dim_date（未发货为 NULL）',
    currency             VARCHAR(10) COMMENT '币种：USD / CNY',
    contract_type        VARCHAR(20) COMMENT '合同性质：一般合同 / 大额合同（70 万人民币为界）',
    order_status         VARCHAR(20) COMMENT '合同状态：生产中 / 已完工 / 已发货 / 已取消',
    delay_days           INT COMMENT '延期天数 = 实际发货 - 承诺交期（未发货为 NULL）',
    is_on_time           VARCHAR(10) COMMENT '是否准时：准时 / 延期（未发货为 NULL）',
    delay_reason         VARCHAR(50) COMMENT '延期原因',
    KEY idx_o_customer (customer_id),
    KEY idx_o_employee (employee_id),
    KEY idx_o_date     (order_date_id)
) COMMENT '合同头事实表：接单结果 + 交付表现（合同级，一行一份合同）';


DROP TABLE IF EXISTS fact_order_item;
CREATE TABLE fact_order_item
(
    item_id      VARCHAR(50) PRIMARY KEY COMMENT '明细行号，主键。规则 <合同号>-<两位序号>',
    order_id     VARCHAR(40) COMMENT '合同号，外键 → fact_order',
    product_id   VARCHAR(30) COMMENT '品号，外键 → dim_product',
    quantity     INT COMMENT '数量（件）',
    total_weight DECIMAL(12,2) COMMENT '总重（kg）',
    amount       DECIMAL(14,2) COMMENT '金额（按合同头的 currency 计价）',
    KEY idx_oi_order   (order_id),
    KEY idx_oi_product (product_id)
) COMMENT '合同明细事实表：合同下的产品行，一行一个产品';


DROP TABLE IF EXISTS fact_production;
CREATE TABLE fact_production
(
    production_id       VARCHAR(50) PRIMARY KEY COMMENT '生产指令号，主键。规则 <合同号>-<两位序号>（真实单据沿用合同单号）',
    order_id            VARCHAR(40) COMMENT '合同号，外键 → fact_order',
    product_id          VARCHAR(30) COMMENT '品号，外键 → dim_product',
    plan_quantity       INT COMMENT '计划生产数量（件）',
    output_quantity     INT COMMENT '实际产量（件）',
    plan_done_date_id   INT COMMENT '计划完工日期，外键 → dim_date（对应单据上的"完工日期"栏）',
    actual_done_date_id INT COMMENT '实际完工日期，外键 → dim_date（未完工为 NULL）',
    delay_days          INT COMMENT '生产延期天数 = 实际完工 - 计划完工（未完工为 NULL）',
    is_on_time          VARCHAR(10) COMMENT '是否准时：准时 / 延期（未完工为 NULL）',
    delay_reason        VARCHAR(50) COMMENT '生产延期原因：车间排产满 / 工艺问题 / 原材料到货延迟 / 客户改单 / 客户确认慢 / 返工',
    KEY idx_p_order   (order_id),
    KEY idx_p_product (product_id)
) COMMENT '生产指令事实表（产品级）：计划与产量，公司总产量口径';


DROP TABLE IF EXISTS fact_production_process;
CREATE TABLE fact_production_process
(
    process_id           VARCHAR(60) PRIMARY KEY COMMENT '工序行号，主键。规则 <生产指令号>-P<工序号>',
    production_id        VARCHAR(50) COMMENT '生产指令号，外键 → fact_production',
    workshop_id          VARCHAR(20) COMMENT '车间ID，外键 → dim_workshop',
    process_seq          INT COMMENT '工序顺序：1 机加工 / 2 制管 / 3 焊接 / 4 抛光或喷塑 / 5 包装',
    process_plan_quantity INT COMMENT '该工序计划加工数（件）',
    process_output       INT COMMENT '该工序实际加工数（件）',
    defect_quantity      INT COMMENT '该工序不良数（件）',
    defect_type          VARCHAR(20) COMMENT '不良类型：生锈/划伤/毛刺/凹坑/麻点/电镀发黄/缝隙过大/螺丝凸出',
    process_done_date_id INT COMMENT '该工序完工日期，外键 → dim_date',
    KEY idx_pp_production (production_id),
    KEY idx_pp_workshop   (workshop_id)
) COMMENT '工序明细事实表：工艺路线上的每道工序，不良数据的所在层';



SET SESSION cte_max_recursion_depth = 1000;

INSERT INTO dim_date (date_id, year, quarter, month, day)
WITH RECURSIVE date_seq (d) AS (SELECT DATE('2025-01-01')
                                UNION ALL
                                SELECT DATE_ADD(d, INTERVAL 1 DAY)
                                FROM date_seq
                                WHERE d < '2026-12-31')
SELECT CAST(DATE_FORMAT(d, '%Y%m%d') AS UNSIGNED) AS date_id,
       YEAR(d)                                    AS year,
       CONCAT('Q', QUARTER(d))                    AS quarter,
       MONTH(d)                                   AS month,
       DAY(d)                                     AS day
FROM date_seq;
