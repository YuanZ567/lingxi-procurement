<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'                            // HTTP 客户端（创建任务用）

const API_BASE = 'http://localhost:8001'            // 后端基地址

// 表单字段，初值给个示例方便测试
const form = ref({
  task_name: '',                                    // 任务名称（可留空，后端自动生成可读名）
  material_name: '螺纹钢',                           // 采购物料（与行情 CSV 品种名一致）
  purchase_quantity: 1000,                          // 数量（吨）
  expect_delivery_date: '2026-10-15',               // 期望交付
  budget_limit: 4200000                            // 预算红线（元）
})
const status = ref('idle')                          // idle / running / completed / blocked / manual / failed
const log = ref([])                                // 直播日志：每收到一条事件 push 一行
let es = null                                      // 任务 SSE 连接
const nodeCount = {}                               // 节点出现次数（谈判类节点第几次出现=第几轮）
const doneSteps = ref(0)                           // 已推进节点数（进度条用）

// ===== 任务列表（任务管理功能）：拉最近20单，点任意单查看其结果 =====
const tasks = ref([])
async function fetchTasks() {
  const { data } = await axios.get(`${API_BASE}/api/task/list`)
  tasks.value = data
}
function viewTask(t) {                             // 点列表某单：把它的终态与方案装回当前面板
  lastTaskId.value = t.task_id
  localStorage.setItem('lingxi_last_task', t.task_id)
  log.value = [`已切换到任务：${t.task_name}（${t.create_time}）`]
  window.dispatchEvent(new CustomEvent('agent-risk', { detail: '' }))  // 熄灭上一单的预警
  loadSolution(t.task_id)
}
async function delTask(t) {                        // 删除任务：后端删库记录（谈判日志级联）+ 刷新列表
  if (!confirm(`确定删除任务「${t.task_name}」吗？删除后不可恢复`)) return
  await axios.delete(`${API_BASE}/api/task/${t.task_id}`)
  if (lastTaskId.value === t.task_id) {            // 删的是当前正看的那单：清空结果面板
    lastTaskId.value = ''
    localStorage.removeItem('lingxi_last_task')
    solution.value = ''
  }
  fetchTasks()                                     // 刷新列表
}
const STATUS_ZH = { idle: '待启动', pending: '排队中', running: '进行中', completed: '已完成', blocked: '已拦截', manual: '待人工', failed: '失败' }
function statusZh(s) { return STATUS_ZH[s] || s }
function statusColor(s) {                          // 状态徽标配色
  return { completed: 'bg-green-100 text-green-700', running: 'bg-blue-100 text-blue-700',
           blocked: 'bg-red-100 text-red-700', manual: 'bg-amber-100 text-amber-700',
           failed: 'bg-red-100 text-red-700' }[s] || 'bg-black/5 text-muted'
}

// 创建任务并开直播
async function start() {
  // 1) 调后端创建任务，拿到 task_id
  const { data } = await axios.post(`${API_BASE}/api/task/create`, form.value)
  const taskId = data.task_id
  lastTaskId.value = taskId                                   // 记住这单，刷新页面后也能查回结果
  localStorage.setItem('lingxi_last_task', taskId)
  status.value = 'running'
  doneSteps.value = 0                                        // 新单进度清零
  log.value = [`已创建任务：${form.task_name || '(自动命名)'}`]

  // 2) 开 SSE 长连接订阅进度
  if (es) es.close()
  es = new EventSource(`${API_BASE}/api/sse/task/${taskId}`)

  es.onmessage = (e) => {
    const evt = JSON.parse(e.data)                 // {event, data}
    if (evt.event === 'status') {
      log.value.push(`状态：${evt.data}`)           // running
    } else if (evt.event === 'node') {
      // 节点名翻译成中文 + 谈判类节点标注轮次，日志才读得出"谈判进行到哪"
      const NODE_ZH = { orchestrator_node: '主编排', market_intelligence_node: '市场情报', cost_calculate_node: '成本核算',
                        negotiation_node: '谈判报价', supplier_counter_node: '供应商还价', reflector_node: '反思复盘', risk_approval_node: '风控审批' }
      const zh = NODE_ZH[evt.data] || evt.data                       // 中文名，查不到就显示原名
      nodeCount[evt.data] = (nodeCount[evt.data] || 0) + 1           // 计数
      const tag = evt.data.startsWith('negotiation') || evt.data.startsWith('supplier_counter') || evt.data.startsWith('reflector')
                  ? `第${nodeCount[evt.data]}轮 ` : ''  // 每轮固定走 谈判→还价→反思 三节点，各自计数=轮数
      log.value.push(`✅ ${tag}${zh}完成`)
      doneSteps.value++                              // 进度 +1
      // 用 window 自定义事件通知 Dashboard 点亮工作流时间线（跨组件解耦，不用传 ref）
      window.dispatchEvent(new CustomEvent('agent-node', { detail: evt.data }))
    } else if (evt.event === 'done') {
      status.value = evt.data                       // completed / blocked / manual
      log.value.push(`🏁 终态：${statusZh(evt.data)}`)
      fetchTasks()                                   // 终态后刷新任务列表（状态徽标同步）
      window.dispatchEvent(new CustomEvent('agent-risk', { detail: evt.data })) // 通知风险面板
      loadSolution(taskId)                          // 终态到了：拉取最终谈判方案展示
    } else if (evt.event === 'error') {
      log.value.push(`❌ 出错：${evt.data}`)
    } else if (evt.event === 'end') {
      es.close(); es = null                         // 收工
    }
  }
}

// 人工审批：真调后端接口（manual 单确认后改 completed，方案已生成可直接执行）
async function approve() {
  if (!lastTaskId.value) return                        // 没有当前单就不动
  await axios.post(`${API_BASE}/api/task/${lastTaskId.value}/approve`)
  status.value = 'completed'                           // 本地状态同步
  log.value.push('✅ 人工审批通过，任务状态已改为 completed')
  fetchTasks()                                         // 刷新任务列表徽标
  window.dispatchEvent(new CustomEvent('agent-risk', { detail: 'completed' }))  // 熄灭预警+刷新统计
}
function rerun()    { log.value.push('（提示）调整参数后点「启动任务并直播」即可重跑') }

// 拉取最终谈判方案：任务结束后从查单接口拿 solution（后端聚好的 markdown 长文本）
const solution = ref('')
const lastTaskId = ref(localStorage.getItem('lingxi_last_task') || '')  // 记住最近一单，刷新后可恢复
async function loadSolution(id) {
  const { data } = await axios.get(`${API_BASE}/api/task/${id}`)
  solution.value = data.solution || '（后端未返回方案文本）'
  // 方案面板已移到右侧 Dashboard（Agent 工作流下方），用事件把文本传过去
  window.dispatchEvent(new CustomEvent('agent-solution', { detail: solution.value }))
  if (data.status && data.status !== 'running') {
    status.value = data.status                        // 恢复时同步终态（人工审批按钮也会出现）
    log.value.push(`📋 已恢复历史任务 ${id}（终态：${data.status}）`)
  }
}
// 刷新页面后：SSE 直播不可回放，但结果在后端存着——自动把上一单的方案拉回来；顺便拉任务列表
onMounted(() => { fetchTasks(); if (lastTaskId.value) loadSolution(lastTaskId.value) })
</script>

<template>
  <div class="bg-surface rounded-3xl shadow p-4">
    <h2 class="font-bold text-ink mb-3">创建采购任务</h2>
    <!-- 表单：每个 input 用 v-model 双向绑定到 form -->
    <div class="grid grid-cols-2 gap-3 text-sm">
      <label class="flex flex-col gap-1 col-span-2">
        <span class="text-muted">任务名称（留空自动命名）</span>
        <input v-model="form.task_name" placeholder="如：9月螺纹钢紧急采购"
               class="rounded-lg border border-black/10 px-3 py-2 outline-none focus:border-accent" />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-muted">物料</span>
        <input v-model="form.material_name" class="rounded-lg border border-black/10 px-3 py-2 outline-none focus:border-accent" />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-muted">数量(吨)</span>
        <input v-model.number="form.purchase_quantity" type="number" class="rounded-lg border border-black/10 px-3 py-2 outline-none focus:border-accent" />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-muted">期望交付</span>
        <input v-model="form.expect_delivery_date" class="rounded-lg border border-black/10 px-3 py-2 outline-none focus:border-accent" />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-muted">预算(元)</span>
        <input v-model.number="form.budget_limit" type="number" class="rounded-lg border border-black/10 px-3 py-2 outline-none focus:border-accent" />
      </label>
    </div>
    <button @click="start"
            class="mt-3 rounded-full bg-accent px-5 py-2 font-bold text-[#06343a]">
      启动任务并直播
    </button>
    <!-- 人工干预入口（对标 PROJECT.md §6.1-1）：待人工=可审批放行；已拦截=只能调参重跑 -->
    <div v-if="status === 'manual' || status === 'blocked'" class="mt-2 flex items-center gap-2">
      <!-- 审批按钮只在"待人工"出现：后端只允许 manual → completed，拦截单必须调参重跑 -->
      <button v-if="status === 'manual'" @click="approve"
              class="rounded-full border border-accent px-4 py-1.5 text-sm font-bold text-accent">人工审批通过（计入本月成本节约）</button>
      <button @click="rerun" class="rounded-full border border-black/10 px-4 py-1.5 text-sm text-muted">调整参数重跑</button>
      <span class="text-xs text-muted">{{ status === 'manual'
        ? '确认按当前谈判价执行？点击后任务转为已完成'
        : '成本超预算红线被拦截，无法直接放行——调大预算或减少采购量后重跑' }}</span>
    </div>

    <!-- 进行中进度条：主图单轮8节点，回环会更多，按已推进节点数显示 -->
    <div v-if="status === 'running'" class="mt-3">
      <div class="flex justify-between text-xs text-muted mb-1">
        <span>任务进行中（谈判回环较耗时，请稍候）</span><span>已推进 {{ doneSteps }} 步</span>
      </div>
      <div class="h-2 bg-bg rounded-full overflow-hidden">
        <div class="h-full bg-accent rounded-full transition-all duration-500 animate-[progressShimmer_1.5s_ease-in-out_infinite]"
             :style="{ width: Math.min(doneSteps * 6, 92) + '%' }"></div>
      </div>
    </div>

    <!-- 直播日志：自动滚到底 -->
    <div class="mt-3 h-40 overflow-y-auto bg-bg rounded-xl p-3 text-xs space-y-1">
      <div v-for="(line, i) in log" :key="i">{{ line }}</div>
    </div>

    <!-- 任务列表：历史任务管理，点任意单查看其结果 -->
    <div class="mt-3">
      <h3 class="font-bold text-ink text-sm mb-2">🗂 任务列表（最近 20 单，点击查看结果）</h3>
      <div class="max-h-52 overflow-y-auto space-y-1">
        <div v-for="t in tasks" :key="t.task_id" @click="viewTask(t)"
             class="flex items-center gap-2 text-xs bg-bg hover:bg-accent/10 rounded-lg px-2 py-1.5 cursor-pointer border border-transparent hover:border-accent/30 group">
          <span class="truncate flex-1 font-bold text-ink">{{ t.task_name }}</span>
          <span class="text-muted">{{ t.material_name }}</span>
          <span class="text-muted">{{ t.create_time }}</span>
          <span class="rounded-full px-2 py-0.5 font-bold whitespace-nowrap" :class="statusColor(t.status)">
            {{ statusZh(t.status) }}
          </span>
          <button @click.stop="delTask(t)" title="删除任务"
                  class="opacity-0 group-hover:opacity-100 text-red-400 hover:text-red-600 font-bold px-1">✕</button>
        </div>
        <p v-if="!tasks.length" class="text-xs text-muted">暂无历史任务</p>
      </div>
    </div>

    <!-- 任务列表：历史任务管理，点任意单查看其结果（方案渲染在右侧 Dashboard 下方） -->
  </div>
</template>

<style scoped>
/* 进度条的流光效果，跑起来不显得"死了" */
@keyframes progressShimmer { 0%,100%{opacity:1} 50%{opacity:.6} }
</style>