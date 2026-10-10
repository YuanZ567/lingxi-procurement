-- 灵犀采购 项目建表脚本 MySQL8.0
-- 仅建表，无业务初始化数据
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- 1.大宗商品行情表 commodity_price
DROP TABLE IF EXISTS `commodity_price`;
CREATE TABLE `commodity_price` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `commodity_name` varchar(128) NOT NULL COMMENT '物料名称',
  `price` decimal(16,4) NOT NULL COMMENT '当日基准价格',
  `record_date` date NOT NULL COMMENT '行情日期',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_commodity_name` (`commodity_name`),
  KEY `idx_record_date` (`record_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大宗商品行情表(真实CSV导入)';

-- 2.供应商表 supplier
DROP TABLE IF EXISTS `supplier`;
CREATE TABLE `supplier` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `supplier_name` varchar(128) NOT NULL COMMENT '供应商名称',
  `credit_status` varchar(32) NOT NULL COMMENT '信用风险等级',
  `premium_rate` decimal(8,4) NOT NULL COMMENT '供应商溢价系数，用于仿真订单价格计算',
  `main_category` varchar(32) DEFAULT NULL COMMENT '主营品类(螺纹钢/铝材/铜材/塑料粒子)，与行情表物料名对齐【9/8新增】',
  `credit_code` varchar(32) DEFAULT NULL COMMENT '统一社会信用代码，18位唯一身份编码【9/8新增】',
  `legal_rep` varchar(64) DEFAULT NULL COMMENT '法定代表人姓名【9/8新增】',
  `reg_capital` varchar(64) DEFAULT NULL COMMENT '注册资本(带币种原文)【9/8新增】',
  `reg_status` varchar(64) DEFAULT NULL COMMENT '登记状态(存续/在营等)【9/8新增】',
  `found_date` date DEFAULT NULL COMMENT '成立日期【9/8新增】',
  `province` varchar(32) DEFAULT NULL COMMENT '注册省份【9/8新增】',
  `city` varchar(32) DEFAULT NULL COMMENT '注册城市【9/8新增】',
  `industry` varchar(64) DEFAULT NULL COMMENT '国标行业大类【9/8新增】',
  `address` varchar(255) DEFAULT NULL COMMENT '注册地址【9/8新增】',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_supplier_name` (`supplier_name`),
  KEY `idx_main_category` (`main_category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应商表(白牛商查API真实工商档案+业务仿真系数)';

-- 3.采购主任务表 procurement_task【核心表】
DROP TABLE IF EXISTS `procurement_task`;
CREATE TABLE `procurement_task` (
  `id` varchar(64) NOT NULL COMMENT 'task_id全局唯一主键',
  `material_name` varchar(128) NOT NULL COMMENT '物料',
  `purchase_quantity` decimal(16,4) NOT NULL COMMENT '采购数量',
  `expect_delivery_date` date DEFAULT NULL COMMENT '期望交付时间',
  `budget_limit` decimal(16,4) NOT NULL COMMENT '本次任务预算红线，全局刚性约束；程序禁止修改此字段，仅允许前端人工编辑',
  `task_status` varchar(32) NOT NULL DEFAULT 'pending' COMMENT '任务状态:pending/running/blocked/success/failed',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_task_status` (`task_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购主任务表';

-- 4.谈判轮次日志 negotiation_log
DROP TABLE IF EXISTS `negotiation_log`;
CREATE TABLE `negotiation_log` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `task_id` varchar(64) NOT NULL COMMENT '关联采购任务',
  `round_no` int NOT NULL COMMENT '谈判轮数',
  `agent_output` text COMMENT 'Agent输出策略、话术',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_task_id` (`task_id`),
  CONSTRAINT `fk_negotiation_task` FOREIGN KEY (`task_id`) REFERENCES `procurement_task` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='谈判轮次日志';

--5.成本测算方案表 cost_plan
DROP TABLE IF EXISTS `cost_plan`;
CREATE TABLE `cost_plan` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `task_id` varchar(64) NOT NULL COMMENT '关联任务',
  `plan_type` varchar(16) NOT NULL COMMENT 'once一次性 / batch分批采购',
  `total_cost` decimal(16,4) NOT NULL COMMENT '测算总成本',
  `material_cost` decimal(16,4) NOT NULL COMMENT '物料成本',
  `storage_cost` decimal(16,4) NOT NULL COMMENT '仓储成本',
  `capital_occupy_cost` decimal(16,4) NOT NULL COMMENT '资金占用成本',
  `surplus_or_over` decimal(16,4) NOT NULL COMMENT '预算结余/超支金额',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_task_id` (`task_id`),
  CONSTRAINT `fk_costplan_task` FOREIGN KEY (`task_id`) REFERENCES `procurement_task` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='成本测算方案表';

--6.历史订单表 history_order（仿真ERP）
DROP TABLE IF EXISTS `history_order`;
CREATE TABLE `history_order` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_no` varchar(64) NOT NULL COMMENT '订单编号',
  `commodity_name` varchar(128) NOT NULL COMMENT '物料名称',
  `supplier_id` bigint NOT NULL COMMENT '供应商ID',
  `order_num` decimal(16,4) NOT NULL COMMENT '采购数量',
  `unit_price` decimal(16,4) NOT NULL COMMENT '单价',
  `total_amount` decimal(16,4) NOT NULL COMMENT '订单总金额',
  `order_date` date NOT NULL COMMENT '下单日期',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_order_no` (`order_no`),
  KEY `idx_commodity_name` (`commodity_name`),
  KEY `idx_supplier_id` (`supplier_id`),
  CONSTRAINT `fk_order_supplier` FOREIGN KEY (`supplier_id`) REFERENCES `supplier` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='历史订单表(仿真ERP，generate_erp.py生成)';

--7.库存表 inventory（仿真ERP）
DROP TABLE IF EXISTS `inventory`;
CREATE TABLE `inventory` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `commodity_name` varchar(128) NOT NULL COMMENT '物料名称',
  `stock_quantity` decimal(16,4) NOT NULL COMMENT '现有库存数量',
  `update_time` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_commodity_name` (`commodity_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='库存表(仿真ERP)';

--8.RAG知识库元数据表 kb_document
DROP TABLE IF EXISTS `kb_document`;
CREATE TABLE `kb_document` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `doc_title` varchar(256) NOT NULL COMMENT '文档标题',
  `doc_source` varchar(256) DEFAULT NULL COMMENT '文档来源，用于问答溯源',
  `vector_doc_id` varchar(128) DEFAULT NULL COMMENT '对应Chroma向量文档id',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_vector_doc_id` (`vector_doc_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='RAG知识库元数据表';

SET FOREIGN_KEY_CHECKS = 1;