# 灵犀采购 · 后续开发任务清单（含文件映射）

> 更新时间：2026-09-05 · 依据：PRD 开发优先级 + 代码现状盘点 + 9/4 工作日志
> 当前基线：DB 层 ✅ · 主业务图全链路已跑通（数值模拟） · LLM 未接入 · 聚合输出 None · 问答/RAG/API/前端均为空壳
> 标记说明：✏️ 修改既有文件 · 🆕 新建/从零实现 · ⬜ 空壳文件（0 行，需实现）

---

## P0 主流程收口（最高优先级）

- [x] **1. 实现 orchestrator_node 最终聚合逻辑** ✅ 9/5 完成
  9/5 已定位聚合不生效的根因与新引入 bug 清单（聚合代码已写，但被 bug 卡死）：
  - `main_graph.py` L21-26：orchestrator 内 `pipeline` 列表 **"cost_calulate_node" 后漏逗号**（字符串拼接）且节点名拼错（实际是 `cost_calculate_node`）→ `next_node` 永远不为 None → **聚合分支永远不触发，final 恒为 None（根因）**
  - `main_graph.py` L43-63：`solution_line` / `solution_lines` 变量名不一致 → 聚合一跑就 NameError
  - `main_graph.py` L254-262：`risk_approval_node` 的注释写在 dict 字面量内部，`"""..."""` 会与后面的 `"approve_status"` **字符串拼接成一个超长 key** → `approve.get("approve_status")` 取不到值
  验收：`run_purchase_task.py` 跑完，打印的最终方案为完整 markdown 且各字段非 None

- [x] **2. 清理 main_graph.py 遗留占位与变量问题** ✅ 9/5 完成（谈判策略/供应商回复的「待LLM生成」占位保留，归 P0-3 LLM 接入时处理）
  9/5 精确定位的其余缺陷：
  - `market_intelligence_node`：**缺 import**（`SessionLocal`、`CommodityPrice`、`Inventory`、`Supplier` 均未导入）→ 查库必然 NameError 被 except 吞掉，情报结果为空
  - L88：`.filer` 拼写错误（应为 `.filter`）→ AttributeError 同样被吞
  - `cost_calculate_node` L144：读取键名 `market_price` 与市场节点写入的 `market_base_price` 不一致 → 基准价恒为 0，测算全废
  - `negotiation_node` L191：`estimate_tatal` 拼写问题；谈判策略/供应商回复仍是「待LLM生成」占位
  - 4 个业务节点均未套用 `harness.py` 外壳（P0-3 顺带处理）
  - `test_graph.py` 仍在同步调用 async 形态 `build_main_graph`，已过时需同步更新

- [x] **3. LLM 接入各业务节点** ✅ 9/5 完成（`src/utils/llm.py` 已有百炼客户端但零调用）
  依次接入：行情摘要研判 → 成本测算解读 → 谈判策略/多轮博弈话术 → 风控结论文本
  📁 `src/utils/llm.py` ✏️（可能需补流式/结构化输出封装）
  📁 `src/graph/main_graph.py` ✏️（4 个业务节点改造）
  📁 `src/utils/config.py` ⬜（LLM API Key、模型名等配置读取）

- [x] **4. 编写全部 Prompt 工程** ✅ 9/5 完成（4个业务prompt落地；orchestrator/qa按计划留空待P2）（6 个 prompt 文件全部 ⬜ 空壳）
  📁 `src/graph/prompts/orchestrator_prompt.py` 🆕（任务拆解 + 最终聚合输出格式）
  📁 `src/graph/prompts/market_agent_prompt.py` 🆕（行情研判/窗口期判断）
  📁 `src/graph/prompts/cost_agent_prompt.py` 🆕（多维成本测算解读）
  📁 `src/graph/prompts/negotiation_agent_prompt.py` 🆕（预算约束谈判策略）
  📁 `src/graph/prompts/risk_agent_prompt.py` 🆕（三重校验结论）
  📁 `src/graph/prompts/qa_agent_prompt.py` 🆕（P2 问答子图预留，可后置）
  > 输出格式建议统一 JSON Schema，便于节点解析与冲突校验

- [x] **5. 依赖修正** ✅ 9/5 完成
  📁 `requirements.txt` ✏️（补 `langchain_alibaba`；`redis==8.1.0` → `redis>=7,<8`，9/2 已踩坑）
  📁 `src/utils/config.py` ⬜ + `src/utils/logger.py` ⬜（配套环境变量/日志基建）

## P1 记忆体系与工程化加固

- [ ] **6. 结构化实体记忆落地**（PRD §3.1-3 硬性要求）
  从 state 提取并固化 `task_id / budget_limit / material_name / quantity`，防跨节点遗忘
  📁 `src/graph/state.py` ✏️（实体记忆字段与 reducer）
  📁 `src/graph/main_graph.py` ✏️（orchestrator 提取写入 + 各节点读取约束）
  > 【2026-09-08 决策】本项不单独开发：任务内实体固化已由 state + Redis Checkpoint 天然覆盖（budget_limit 防篡改断言已验证）；执行过程记忆（loop_round / loop_failed_nodes）**并入 E2**，跨任务实体经验**并入 E4**，验收标准拆入 E2 / E5。

- [x] **7. 补齐空壳可靠性模块** ✅ 9/7 完成（config/logger/retry/exception 四件套 + llm/harness 集成，重试+降级演练通过）
  📁 `src/core/retry.py` 🆕（指数退避重试装饰器/策略）
  📁 `src/core/exception_handler.py` 🆕（全局异常捕获与降级响应）
  📁 `src/utils/logger.py` 🆕（统一日志，节点执行留痕）
  📁 `src/utils/config.py` 🆕（集中配置：DB/Redis/LLM）
  📁 `src/core/harness.py` ✏️（与 retry/exception 打通）
  📁 `src/db/session.py` ✏️（如需会话级重试）

- [x] **8. 双分支验收自测** ✅ 9/5 完成（reject拦截分支新增；budget防篡改断言内置）
  合理预算跑「分支B 审批通过」；超低预算跑「分支A 超支拦截」；确认预算红线全程不丢失不篡改
  📁 `run_purchase_task.py` ✏️（支持传入不同预算参数）
  📁 `src/graph/main_graph.py` ✏️（如拦截分支路由有缺漏则修）
  📁 `docs/main_flow.mmd` 📖（对照流程图核对分支设计）
  📁 `init_db.py` / `src/data/generate_erp.py` ✏️（如需补超支场景种子数据）

## P2 问答子系统（RAG + NL2SQL + 独立子图）

- [x] **9. RAG 四件套** ✅ 9/11 全部完成（vector_store/hybrid_retriever 9/10；nl2sql 三道安全闸+agentic_retrieval 迭代检索 9/11，三问冒烟全过）
  📁 `src/rag/vector_store.py` ✅（9/10 完成：百炼 text-embedding-v3 1024维 + Chroma 持久化；修 chunck 参数名笔误；embedding 分批≤10条适配百炼限制）
  📁 `src/rag/hybrid_retriever.py` ✅（9/10 完成：jieba+BM25+RRF 融合；用户误将 build_kb 内容贴入导致自引用，已重写）
  📁 `src/rag/nl2sql.py` 🆕（表结构注入、SQL 生成与安全校验、只读限制）
  📁 `src/rag/agentic_retrieval.py` 🆕（迭代检索/自判断补检）
  📁 `src/db/models.py` 📖（KnowledgeDocument 模型对接）
  📁 `src/db/session.py` 📖（NL2SQL 查库复用 SessionLocal）

- [x] **10. 知识库语料准备与向量化** ✅ 9/10 完成（4 篇语料 17 片段双路入库，冒烟命中）
  📁 `seed_data/kb/` ✅（9/10 完成：4 篇演示制度语料——预算管理/供应商管理/采购流程/风险控制）
  📁 `src/rag/build_kb.py` ✅（9/10 完成：建库脚本，17 片段入库实测通过；修复语料文件名缺"大"字；9/11 迁入 src/rag 与检索层内聚，运行命令改为 python -m src.rag.build_kb）
  📁 `chroma_db/` ✅（Chroma 持久化 + chunks.jsonl 生成，双路冒烟测试命中 ✅）
  📁 `.gitignore` ✅（chroma_db/__pycache__/.env/logs 已排除）
  📁 `src/data/import_csv.py` ✏️（扩展知识库文档导入）
  📁 `src/rag/vector_store.py` ✏️（初始化建集合脚本入口）
  📁 `init_db.py` ✏️（串接语料导入步骤）

- [x] **11. 问答子图 `qa_graph.py`** ✅ 9/11 完成（分诊→条件路由→答题人；三问实测：rag 路/路nl2sql 全过，NL2SQL 回答含数据库真实订单号+SQL 溯源；修复：ask_json 异步 await×6、nl2sql logger 缺失、SCHEMA_TEXT 表结构按真实列名修正——history_order 用 commodity_name/order_num 而非 material_name/quantity）
  意图路由（知识库检索 / NL2SQL 查数 / 混合）→ 执行 → 带溯源的回答生成
  📁 `src/graph/qa_graph.py` ✅ 📁 `src/graph/qa_state.py` ✅ 📁 `src/graph/prompts/qa_agent_prompt.py` ✅
  📁 `src/rag/*` 📖（复用 9 号任务产出）
  📁 `test_qa.py` ✅（三问冒烟，EXIT=0）

## P3 FastAPI 接口层（4 个文件：main.py 有骨架、其余 ⬜ 空壳）

- [x] **12. task_api + SSE 进度** ✅ 9/14 完成（POST create 落库pending→后台astream→negotiation_log 4轮落库→终态落库；GET查单；SSE /api/sse/task/{id} 实时播报节点流转+done哨兵。修复：ulid→uuid、json未导入、b.add笔误、SSE路由缺斜杠、LangGraph推{节点:None}需防御）
  📁 `src/api/task_api.py` ✅ 📁 `src/api/sse_api.py` ✅

- [x] **13. qa_api** ✅ 9/14 完成（问答并入 sse_api.py 的 /api/sse/qa 频道：route/answer/sources 分条推送+end哨兵；`src/api/qa_api.py` 保留空壳不使用）
  📁 `src/api/sse_api.py` ✅ 📁 `src/graph/qa_graph.py` 📖

- [x] **14. 应用入口** ✅ 9/14 完成（路由注册、CORS、全局异常中间件5001、health探活含db/redis检查；Checkpoint 复用 Redis 6380）
  📁 `src/api/main.py` ✅
  📁 验收记录：uvicorn 8001 起服务，SSE 全程直播 8 节点流转+回环重谈，终态 manual 落库，qa 频道 route/answer/sources 推送正常

## P4 前端（frontend/ 已完成）

- [x] **15. Vue3 + TailwindCSS + ECharts 脚手架** ✅ 9/15 完成（package.json/vite/tailwind/postcss/index.html + main.js/style.css；依赖 vue/echarts/axios/vite/tailwindcss；注意 package.json 不允许注释）
  📁 `frontend/` ✅（前端地址 127.0.0.1:5174，5173 被 LegalEye nginx 占用；vite server.host:true 双栈）

- [x] **16. 主业务看板** ✅ 9/15 完成（任务创建含可读名称/进度条/直播日志、Agent 工作流时间线、行情四品类切换、供应商地区排行含溢价率、风险预警面板、成本节约真数据、任务管理列表+删除；行情/供应商已切后端实时接口 /api/market /api/suppliers）
  📁 `frontend/src/components/Dashboard.vue` ✅ 📁 `frontend/src/components/TaskConsole.vue` ✅

- [x] **17. 悬浮问答助手** ✅ 9/15 完成（灵犀仔=极光发光核：呼吸光圈+涟漪+眨眼+循环气泡；点击展开问答窗，SSE 打字机输出+溯源来源卡片+快捷指令）
  📁 `frontend/src/components/LingxiZai.vue` ✅（坑：scoped 会改 keyframes 名，keyframes 须放全局 style）

- [x] **18. 前后端联调** ✅ 9/15 完成（SSE 直播联动时间线、超支拦截预警面板、方案面板右侧展示+落库持久化；后端补 approve/stats/month/data 三组接口；LLM 切 qwen-plus 提速 5 倍，验证单 42 秒跑完）
  📁 `src/api/data_api.py` ✅ 📁 `src/api/task_api.py` ✏️（approve/stats/month/final_amount/task_name/delete）

## P5 总验收（对照 PRD §4 五类标准）

- [x] **19. 全流程验收自测** ✅ 9/16 完成（acceptance_test.py **19/19 全过**：§4.1 双分支+预算红线不篡改+成交额落库；§4.2 negotiation_log 4轮留痕+状态持久化+Redis Checkpoint 339键；§4.3 RAG 4条溯源+NL2SQL 真实订单号无幻觉；§4.4 五接口 200；§4.5 降级路径/重试/合规语料在位）
  📁 `acceptance_test.py` 🆕（验收脚本，项目根目录 python acceptance_test.py，全过退出码 0）
  📁 `PRD.md` 📖（逐条对照 §4.1~§4.5）
  > 遗留选做（不阻塞收官）：E4 跨任务经验沉淀、E5 换商回环全链路演示、对话框内 markdown 表格渲染

---

## P1.5 扩展篇：交易 Harness + Agent Loop + 自进化（9/5 新增）

> 前置依赖：P0 全部完成（LLM 已接入、聚合已收口）。`state.py` 第 31-36 行已预留全部字段（loop_round / max_loop_limit / loop_failed_nodes / self_evolve_trigger / task_final_outcome），本篇是把预留设计落成代码。与 P1/P2 可部分并行。

- [x] **E1. Harness 交易场景化升级** ✅ 9/8 完成（含异常低价校验的预算钳制豁免修正）
  给 AgentHarness 增加业务校验器注册机制：预算红线校验（报价≤budget_limit）、报价 vs 行情基准价偏差阈值、LLM 输出 JSON Schema 校验；校验失败原因写回 state（而非仅 `__agent_failed` 标志），供 Loop 与自进化消费
  📁 `src/core/harness.py` ✏️
  📁 `src/graph/main_graph.py` ✏️（各节点接入 validator）

- [x] **E2. Agent Loop 条件边（两条回环）** ✅ 9/8 完成（含同供应商还价归属修正）
  回环A：谈判↔供应商还价模拟，未成交则 `loop_round+1` 重谈；回环B：风控 reject（非超支原因）→ 注入失败原因回环重谈。超支/轮次耗尽 → 人工干预。注意 9/4 死循环教训：路由条件必须显式、完成标记字段名核对
  📁 `src/graph/main_graph.py` ✏️（add_conditional_edges + 路由函数）
  📁 `src/graph/state.py` ✏️（如需补 loop 上下文字段）
  📁 `docs/main_flow.mmd` ✏️（同步流程图）
  📁 `run_purchase_task.py` ✏️（验证多轮收敛 + 上限触发人工干预）

- [x] **E3. 任务内自进化：Reflector 反思节点** ✅ 9/9 完成（用户敲码 + 修复 `new_state.state` 笔误；实测三轮反思日志正常，策略随轮次递进，反思只写笔记不碰价格/路由）
  每轮回环后分析 `loop_failed_nodes` 失败原因，生成策略修正建议注入下一轮谈判 Prompt
  📁 `src/graph/main_graph.py` ✏️（新增 reflector_node）
  📁 `src/graph/prompts/reflector_prompt.py` 🆕（成败归因、策略修正输出格式）

- [ ] **E4. 跨任务经验沉淀与注入**
  任务完结后把 `task_final_outcome` + 成败结果提炼为经验案例入库；后续任务的谈判/市场 Agent 启动时检索相似历史案例作 few-shot。入库需过门禁：仅沉淀有客观结果验证（真实成交/明确拦截）的案例，防自进化幻觉自我强化
  📁 `src/db/models.py` ✏️（新增经验案例表 experience_case）
  📁 `src/db/init_sql/schema.sql` ✏️
  📁 `src/data/generate_erp.py` ✏️（可选：仿真初始经验数据）
  📁 `src/rag/vector_store.py` 📖（复用 P2 向量基建）
  📁 `src/graph/prompts/negotiation_agent_prompt.py` ✏️（增加历史案例 few-shot 槽位）
  📁 `src/graph/main_graph.py` ✏️（任务收尾触发沉淀）

- [ ] **E5. 扩展场景验收**
  多轮谈判收敛至预算内 · 轮次上限触发人工干预 · reject 回环后策略可见变化 · 经验注入前后同任务方案可对比
  📁 `run_purchase_task.py` / `test_graph.py` ✏️
  > 【9/9 进度】已验：多轮谈判收敛至预算内 ✅ · 轮次上限触发人工干预 ✅（exhausted→manual）· 全程留痕 ✅ · E3 反思注入后策略可见变化 ✅ · 换商回环B **单元级验证 PASS** ✅（触发踢标记/重派/计数闸/防二次触发全过；附带修复 risk 节点尾部无条件自我登记导致回环B失效的 bug）。剩：换商回环全链路演示（受 LLM 超时影响，可选）。

- [x] **E6. 政采网成交数据锚点 + Orchestrator 总裁总结** ✅ 9/9 完成（指南：`docs/代码指南_政采网爬取与LLM接入.md`）
  一次性低频爬取政采网成交公告（间隔≥3秒 / 上限200请求 / 限流退避60秒，合规守 PRD §3.3"仅允许一次性初始化导入"）→ `seed_data/ccgp_deals.csv`，作谈判参考价锚点与供应商背景佐证，**不写入 history_order**；Orchestrator 聚合新增"AI 总裁总结"（四节点事实喂 LLM，只总结不计算，失败降级纯模板）
  📁 `src/data/ccgp_crawler.py` ✅（9/9 修复 KEYWORD_MVP 不一致/seed_deta 拼写/requests_count/polite_get 签名，升级：完整浏览器头+Session+主页预热过指纹拦截、三模板正则、品类预过滤、URL去重）
  📁 `seed_data/ccgp_deals.csv` ✅（9/9 正式采集 200 请求跑满 → 12 条真实成交记录，4 品类覆盖、真实企业名/金额/可溯源 URL；已知限制：原材料类公告在政采网存量偏少，属客观数据分布）
  📁 `src/graph/prompts/orchestrator_prompt.py` ✅（9/9 完成，JSON 键与聚合代码核对一致）
  📁 `src/graph/main_graph.py` ✅（9/9 完成："AI 总裁结论"章节实测出现，数字与程序结果逐项一致）
  📁 `requirements.txt` ✅（requests / beautifulsoup4 已补，9/8）

## 明确不做（防烂尾，PRD §3.2/3.3）

多用户权限、合同PDF导出、周报月报、复杂可视化大盘、人工驳回回滚、商用API/OA对接、运行时爬虫、付费API —— MVP 阶段代码零开发。

## 建议推进顺序

1 → 2 → 3/4（并行）→ 5 → 8（P0 收口出可演示闭环）→ 6 → 7 → 9~11（问答）→ 12~14（API）→ 15~18（前端）→ 19（验收）
