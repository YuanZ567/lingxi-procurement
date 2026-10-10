# TECH_SPEC.md 灵犀采购｜技术规格说明书
> 对应 PRD.md MVP 版本，产品需求已经冻结，本文档将产品需求转化为技术实现方案；仅实现MVP范围，迭代功能不在本规格内。
> 职责：数据库设计、LangGraph双Graph定义、项目目录、接口概要、异常策略。

## 0 技术栈总览
- 后端框架：FastAPI
- 智能编排：LangGraph
- ORM：SQLAlchemy 2.0
- 结构化存储：MySQL 8.0
- 缓存&任务状态：Redis
- 向量库：ChromaDB
- 检索：Hybrid‑RAG(BM25+向量)+RRF重排、Agentic‑RAG、增强NL2SQL
- 可靠性：自定义Harness组件（重试、超时、降级、异常捕获）
- 前端：Vue3 + TailwindCSS；SSE流式推送
- 记忆：LangGraph Checkpoint + 结构化实体记忆
- 模型：兼容阿里云百炼 / OpenAI兼容接口

## 1 数据库 Schema 设计
### 1.1 核心数据表说明
1. `commodity_price` 大宗商品行情表（akshare期货主力合约真实日线2603行，2024-01～2026-09-08【2026-09-08更新】）
2. `supplier` 供应商表（白牛商查API真实工商档案80家+固定种子模拟的信用/溢价业务层【2026-09-08更新】；新增main_category/credit_code/legal_rep/reg_capital/reg_status/found_date/province/city/industry/address 10列）
3. `procurement_task` 采购主任务表 **核心，保存budget_limit预算红线**
4. `negotiation_log` 谈判轮次日志
5. `cost_plan` 成本测算方案表（一次性/分批两套方案）
6. `history_order` 历史订单表（仿真ERP，基于真实行情生成）
7. `inventory` 库存表（仿真ERP）
8. `kb_document` RAG知识库元数据表
9. `ccgp_deals.csv` 政采网真实成交记录种子文件【E扩展篇新增，2026-09-08】
    - 由 `src/data/ccgp_crawler.py` 一次性低频采集生成（手动运行，非运行时爬虫，符合 PRD §3.3）
    - 字段：category, supplier_name, material_kw, amount_raw, buyer, publish_date, title, url
    - 用途：谈判参考价锚点、供应商背景佐证；不写入 history_order（口径不一致，需人工核对）

> 重要约束：`procurement_task.budget_limit` 预算红线一旦创建不允许程序修改；仅允许人工前端修改。

### 1.2 关键字段摘要
**commodity_price**
|字段|说明|
|----|----|
|id|主键|
|commodity_name|物料名称|
|price|当日基准价格|
|record_date|行情日期|

**supplier**
|字段|说明|
|----|----|
|id|主键|
|supplier_name|供应商名称|
|credit_status|信用风险等级|
|premium_rate|供应商溢价系数，用于仿真订单价格计算|

**procurement_task**
|字段|说明|
|----|----|
|id|主键task_id，全局唯一|
|material_name|物料|
|purchase_quantity|采购数量|
|expect_delivery_date|期望交付时间|
|budget_limit|**本次任务预算红线，全局刚性约束**|
|task_status|任务状态：pending/running/blocked/success/failed|
|create_time|创建时间|

**negotiation_log**
|字段|说明|
|----|----|
|id|主键|
|task_id|关联采购任务|
|round_no|谈判轮数|
|agent_output|Agent输出策略、话术|
|create_time|记录时间|

**cost_plan**
|字段|说明|
|----|----|
|id|主键|
|task_id|关联任务|
|plan_type|once一次性 / batch分批采购|
|total_cost|测算总成本|
|material_cost|物料成本|
|storage_cost|仓储成本|
|capital_occupy_cost|资金占用成本|
|surplus_or_over|预算结余/超支金额|

**history_order / inventory / kb_document**
> history_order：由脚本`generate_erp.py`生成，订单单价=订单日期当天真实期货收盘价×供应商溢价系数（380条已核验）；inventory 库存数量为仿真；
> kb_document：记录RAG文档元数据、文档来源溯源，用于问答溯源卡片。

## 2 LangGraph 双 Graph 设计
### 2.1 AgentState TypedDict 【主业务Graph状态】
> 全部业务流转依赖此状态；**budget_limit 预算红线全程保留，不允许节点随意改写**
```python
class AgentState(TypedDict):
    # 用户原始输入
    task_id: str
    material_name: str
    purchase_quantity: float
    expect_delivery_date: str
    budget_limit: float       # 刚性预算红线，核心字段

    # 市场情报Agent输出
    market_base_price: float | None
    price_trend: str | None
    suggest_window: str | None
    supplier_list: list[dict] | None

    # 成本核算Agent输出
    cost_plans: list[dict] | None
    is_over_budget: bool | None
    over_amount: float | None

    # 谈判Agent输出
    negotiation_rounds: list[dict] | None

    # 风控审批Agent输出
    risk_check_result: dict | None
    task_final_result: dict | None

    # 流程控制字段
    current_node: str
    error_msg: str | None
    human_intervene_flag: bool
```

> 【E扩展篇新增，2026-09-08】实际实现 `state.py` 在上表基础上补充以下字段：
> - 执行记忆：`loop_round`（还价轮数）、`max_loop_limit`（轮数上限，默认3）、`loop_failed_nodes`（校验失败节点）、`self_evolve_trigger`、`task_final_outcome`（自进化预留）
> - 回环字段：`supplier_counter_result`（还价结果）、`tried_supplier_ids`（已谈供应商）、`risk_retry_count`（换商重谈次数）、`human_intervene_flag`（落实上表原生设计）
> - 约束不变：`budget_limit` 依然全程只读，任何回环轮次均不得改写

### 2.2 主业务 Graph 节点列表
1. `orchestrator_node` Orchestrator主编排Agent
    - 提取业务实体，锁定budget_limit；下发子任务；收集各节点输出；做结果聚合校验。
2. `market_intelligence_node` 市场情报Agent
3. `cost_calc_node` 成本核算Agent
4. `negotiation_node` 谈判博弈Agent
5. `risk_approve_node` 风控审批Agent

6. `supplier_counter_node` 供应商还价Agent【E扩展篇新增，2026-09-08】
    - 对抗模拟角色：扮演虚拟供应商，对谈判报价做接受/还价判定，支撑"多轮博弈预演"
    - 底价模型（程序确定性计算，LLM无判定权）：底价单价 = market_base_price × 目标供应商premium_rate × 0.98
    - 三分支判定：报价≥底价→成交放行；轮次耗尽(loop_round≥max_loop_limit默认3)→强制跳出交风控裁决并置human_intervene_flag；未成交→踢除negotiation完成标记，交Orchestrator重派重谈
    - LLM仅通过supplier_counter_prompt生成还价/接受话术文本

7. `reflector_node` 元认知反思Agent【E扩展篇新增，2026-09-08】
    - 角色定位：复盘教练（元认知层），不参与业务决策——不改价格、不改路由、不改finished_agent_list
    - 触发条件：仅当supplier_counter判定为"拒绝还价"时触发LLM复盘；成交/轮次耗尽直接放行并清空reflection_note
    - 产出：失败归因+下一轮策略修正建议，写入state.reflection_note，注入下一轮negotiation Prompt（单轮覆盖式，防上下文膨胀）
    - 连线：supplier_counter_node → reflector_node → orchestrator_node（两条无条件普通边，全部节点仍汇回主编排）

> 【E扩展篇新增，2026-09-08】orchestrator_node 聚合输出新增"AI 总裁总结"环节：四节点事实喂给 LLM 生成执行摘要/关键风险/下一步建议；LLM 仅总结不计算（严禁编造数字），失败自动降级为纯模板报告。

### 2.3 条件分支 edges（业务分支）
条件判断：`state["is_over_budget"]`
- True → 走向 `blocked`：任务拦截终止，输出预警；
- False → 走向 `complete`：输出完整采购方案。

### 2.3.1 回环设计【E扩展篇新增，2026-09-08】
- **回环A（谈判回环）**：negotiation_node → supplier_counter_node → orchestrator_node；未成交时由 Orchestrator 按未完成清单重派 negotiation（按还价底价重谈）
- **回环B（换商回环）**：风控 manual 且供应商高风险时，将 negotiation/risk 从 finished_agent_list 移除并重派，换未谈过的供应商；计数闸 risk_retry_count（最多1次）
- **统一模式**：回环一律"踢 finished_agent_list 完成标记 + 回 Orchestrator 重派"，禁止节点间直连跳转（符合 §2.6.1 状态机控制、PROJECT.md §6.3 主 Agent 统一调度）
- **硬约束**：超预算 reject 永不回环，直接终止（对应 PRD §10 分支A）
- **反思站【E扩展补充，2026-09-08】**：回环路径插入 reflector_node——supplier_counter_node → reflector_node → orchestrator_node（两条无条件普通边）；仅在拒绝还价时触发 LLM 复盘，产出 reflection_note 注入下一轮谈判 Prompt

### 2.4 记忆方案
1. Checkpoint持久化：使用Redis保存graph checkpoint；任务中断、页面刷新可恢复状态。
2. 结构化实体记忆：从state提取`task_id、budget_limit、material_name`关键实体，避免LLM遗忘业务参数。

3.【E扩展篇新增，2026-09-08】任务内反思记忆（E3）：state.reflection_note 单轮覆盖式保存最近一次复盘结论（失败归因+策略修正），注入下一轮 negotiation Prompt；仅回环失败时写入，成交/耗尽时清空，避免多轮累加导致上下文膨胀。

> MVP暂不实现高级摘要压缩记忆，留到后续迭代。

### 2.5 问答子Graph（独立graph，不和主业务流程耦合）
子Graph节点：
1. `query_analyze_node`：用户问题解析，判断走 Hybrid‑RAG 还是 NL2SQL
2. `hybrid_retrieve_node`：向量+BM25+RRF混合检索，Agentic‑RAG迭代检索
3. `nl2sql_node`：结构化业务库查询
4. `answer_generate_node`：结果融合生成回答，附带溯源来源

> 子Graph独立编译，拥有独立Checkpoint；不会修改采购任务业务数据，只做查询。

### 2.6 工具调用策略说明
> 本项目区分【LLM自主Function‑Calling】与【代码确定性工具执行】两种模式
1. **主业务Graph（采购流水线）**
不启用LLM自主Function‑Calling。
行情查询、供应商查询、成本原始数据读取，全部由节点内部代码确定性调用数据库完成，查询结果注入LLM上下文。
业务流转由LangGraph状态机和条件分支控制，避免大模型自由选择流程带来业务不可控风险，保障预算红线刚性约束不会被工具异常破坏。

2. **问答子Graph**
启用LLM Function‑Calling工具调用。
可用工具列表：
- `hybrid_retrieve`：Hybrid‑RAG混合检索知识库文档
- `nl2sql_run`：执行NL2SQL查询业务结构化数据

大模型根据用户问题自主选择工具、组合工具完成查询，仅做数据查询，不修改业务库。

## 3 项目目录结构设计
```plaintext
lingxi_procurement/
├── PROJECT.md               # 项目总介绍
├── PRD.md                   # 产品需求文档
├── TECH_SPEC.md             # 本技术规格文档
├── .env.example             # 环境变量样例
├── requirements.txt
├── docs/                    # 图表、mermaid流程图
├── src/
│   ├── graph/               # LangGraph编排
│   │   ├── state.py         # AgentState定义
│   │   ├── main_graph.py    # 主业务graph：节点、edges、编译
│   │   ├── qa_graph.py      # 问答子graph
│   │   └── prompts/         # 所有Agent prompt模板
│   ├── db/
│   │   ├── models.py        # SQLAlchemy ORM模型
│   │   ├── session.py       # 数据库会话
│   │   └── init_sql/        # 建表sql
│   ├── data/                # 数据初始化脚本
│   │   ├── import_csv.py    # CSV真实行情、供应商导入（含工商档案回填）
│   │   ├── market_provider.py # 实时行情provider：Redis缓存→akshare实时→降级读库【2026-09-08新增】
│   │   └── generate_erp.py  # 仿真ERP订单库存生成（订单价锚定当天真实行情×溢价系数）
│   ├── rag/
│   │   ├── hybrid_retriever.py
│   │   ├── agentic_retrieval.py
│   │   ├── nl2sql.py
│   │   ├── build_kb.py      # 知识库建库脚本：切分语料→向量入库→写BM25 jsonl【2026-09-11 自 src/data 迁入，与检索层内聚】
│   │   └── vector_store.py
│   ├── core/                # Harness可靠性组件
│   │   ├── retry.py
│   │   ├── exception_handler.py
│   │   ├── harness.py       【E扩展】Agent可靠性外壳v2：校验器二元组返回+结构化失败写回+wrap包装
│   │   └── validators.py    【E扩展】交易业务校验器：预算红线/单价合法/价格合理性
│   ├── api/                 # FastAPI接口
│   │   ├── main.py
│   │   ├── task_api.py      # 采购任务提交、查询
│   │   ├── sse_api.py       # SSE流式推送任务进度
│   │   └── qa_api.py        # 悬浮问答助手接口
│   └── utils/
└── frontend/                # Vue3极简前端 MVP
```

## 4 接口概要设计
### 4.1 REST接口
1. `POST /api/task/create`
入参：`material_name, purchase_quantity, expect_delivery_date, budget_limit`
返回：`task_id`

2. `GET /api/task/{task_id}`
返回：任务完整状态，各Agent输出、谈判记录、成本方案、风控结果

### 4.2 SSE流式接口（重点）
1. `/api/sse/task/{task_id}`
> 流式推送主业务Graph执行节点流转信息，前端实时展示Agent运行状态。

2. `/api/sse/qa`
> 悬浮问答助手SSE流式输出，返回回答+溯源来源信息。

### 4.3 错误码约定
- `4001`：预算参数非法
- `5001`：Agent执行异常
- `5002`：外部工具调用失败（检索/NL2SQL）

## 5 异常 & 降级策略（Harness）
1. Agent节点调用LLM失败：配置重试次数；重试失败标记task_status=failed，写入error_msg。
2. RAG检索异常：降级返回提示，禁止编造答案。
3. NL2SQL生成错误SQL：捕获异常，不执行错误SQL，返回友好提示。
4. 数据库读写异常：任务标记失败，不破坏状态一致性。
5. 全程禁止运行时网络爬虫；所有外部数据提前导入本地。
6.【E扩展篇新增】业务校验失败（如谈判输出超预算红线）：由 Harness 校验器拦截，结构化写入 task_error_records / loop_failed_nodes 后重试；重试耗尽降级放行并留 WARNING 日志，保障流水线不中断。

## 6 MVP开发约束（严格遵守）
1. 不在本阶段实现：权限系统、合同导出、周报、复杂大盘、人工驳回回滚。
2. 所有业务逻辑围绕state，**budget_limit预算红线不可被Agent节点修改**。
3. 问答子Graph只做查询，不修改业务数据。
4. 完成后对照 PRD.md 里的MVP验收标准逐项验证。
