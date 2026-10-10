<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'  // 生命周期钩子 + 计算属性 + 侦听器
import * as echarts from 'echarts'                     // ECharts 图表库

const chartPrice    = ref(null)   // 行情图 DOM 引用
const chartSupplier = ref(null)   // 供应商图 DOM 引用
const FLOW_KEY = 'linxi-flowlog'  // 执行流水持久化键：刷新/重开页面流水不丢（否则跑完任务一刷新就"没有效果"）
const flowLog       = ref(JSON.parse(localStorage.getItem(FLOW_KEY) || '[]'))  // Agent 工作流执行流水（启动时恢复上次记录）
watch(flowLog, v => localStorage.setItem(FLOW_KEY, JSON.stringify(v)), { deep: true })  // 每次变化自动落盘
const riskAlert     = ref(localStorage.getItem('linxi-risk') || '')  // 风险预警文案（持久化：刷新后仍显示最近一次预警）
watch(riskAlert, v => localStorage.setItem('linxi-risk', v))         // 预警状态变化自动落盘
const saving        = ref('—')    // 本月成本节约（P5 接统计接口后填充真实值）
let charts = []                  // 存图表实例，卸载时统一销毁

// ===== 行情：多品类切换 =====
const commodities  = ref([])      // 全部品种列表（从数据里动态取）
const activeCom    = ref('螺纹钢') // 当前选中的品种
let marketData = []               // 行情原始数据缓存（切品种不再重复请求）
let c1 = null                     // 行情图实例（切品种要重设数据）

// 拉真实行情数据：改调后端 /api/market（直连 MySQL），品种由按钮传入
// 后端每次被调都先"实时抓当天价→写库"再返回序列，保证最新一条永远贴近当下
const API_BASE = 'http://localhost:8001'              // 后端基地址
const marketSource  = ref('')                          // 数据来源（实时/缓存/数据库降级），徽标展示
const marketLatest  = ref('')                          // 序列最新交易日，徽标展示
let marketTimer   = null                               // 自动轮询定时器（60s 一次，页面常开也保持最新）
async function loadMarket() {
  const res = await fetch(`${API_BASE}/api/market?commodity=${encodeURIComponent(activeCom.value)}&limit=260`)
  const payload = await res.json()
  // 兼容两种返回格式：新后端 {source, latest_date, series}；旧后端直接是 [{date,close},...] 数组
  const series = Array.isArray(payload) ? payload : (payload.series || [])
  marketSource.value = Array.isArray(payload) ? (series.length ? '实时' : '无数据')   // 旧格式没有来源字段，按有数据就当最新
                      : (payload.source || '无数据')   // 数据来源（给徽标）
  marketLatest.value = Array.isArray(payload)                            // 序列最新交易日（给徽标）
      ? (series.length ? series[series.length - 1].date : '')
      : (payload.latest_date || '')
  marketData = series.map(d => ({ commodity: activeCom.value, date: d.date, close: d.close }))  // 包装回渲染函数认的结构
  commodities.value = [...new Set(['螺纹钢', '铝材', '铜材', '塑料粒子'])]  // 品种按钮固定四种（库里的品种）
}
// 按品种重画行情图（点品类按钮就调它）
function renderPrice(com) {
  if (!c1) return
  const rows = marketData.filter(d => d.commodity === com)
                          .sort((a, b) => a.date.localeCompare(b.date))
  c1.setOption({
    title: { text: `${com} 期货收盘价（2024 至今 · 实时刷新）`, left: 10, textStyle: { fontSize: 14 } },
    xAxis: { type: 'category', data: rows.map(d => d.date) },   // x 轴：交易日
    series: [{ data: rows.map(d => d.close) }]                  // y 轴：收盘价
  })
}
function switchCom(com) {                             // 切品种：记下选中 → 重新拉该品种行情 → 重画
  activeCom.value = com
  loadMarket().then(() => renderPrice(com))           // 每次切换都查库（数据最新）
}
// 行情来源徽标文案：实时=绿、缓存=青、降级=橙，一眼看出数据新鲜度
const sourceBadge = computed(() => {
  if (!marketSource.value) return ''
  if (marketSource.value === '实时')       return { text: `⚡ 实时 · 截至 ${marketLatest.value}`, cls: 'bg-emerald-50 text-emerald-700 border-emerald-300' }
  if (marketSource.value === '缓存')       return { text: `⚡ 最新 · 截至 ${marketLatest.value}`, cls: 'bg-accent/10 text-accent border-accent/30' }
  if (marketSource.value === '数据库降级') return { text: `⏳ 降级 · 截至 ${marketLatest.value}`, cls: 'bg-orange-50 text-orange-600 border-orange-300' }
  return { text: `❌ 无数据`, cls: 'bg-red-50 text-red-600 border-red-300' }
})
onMounted(() => {                                     // 页面常开也保持新鲜：每 60s 静默刷新行情
  marketTimer = setInterval(() => { loadMarket().then(() => renderPrice(activeCom.value)).catch(() => {}) }, 60000)
})
onBeforeUnmount(() => clearInterval(marketTimer))     // 卸载清定时器，防内存漏

// 拉真实供应商数据：改调后端 /api/suppliers（直连 MySQL supplier 表）
let supplierData = []             // 供应商原始数据缓存
let c2 = null                     // 供应商图实例
const regions      = ref([])     // 全部地区列表（去重）
const activeRegion = ref('全部')   // 当前选中的地区
async function loadSuppliers() {
  const res = await fetch(`${API_BASE}/api/suppliers`)
  supplierData = await res.json()                        // [{name, credit, premium, region}, ...]
  regions.value = ['全部', ...new Set(supplierData.map(s => s.region))]  // 地区下拉选项
}
// 按地区重画供应商排行（横向条形图，公司名完整显示；按信用分降序 TOP10）
function renderSupplier(region) {
  if (!c2) return
  const pool = (region === '全部' ? supplierData : supplierData.filter(s => s.region === region))
                 .slice()
                 .sort((a, b) => b.credit - a.credit)    // 信用分降序 = 排行榜
                 .slice(0, 10)                           // 最多展示 10 家
  c2.setOption({
    title: { text: region === '全部' ? '供应商信用排行 TOP10（全国）' : `${region} 供应商信用排行`, left: 10, textStyle: { fontSize: 14 } },
    tooltip: { trigger: 'axis',
               formatter: ps => { const d = ps[0].data; return `${ps[0].name}<br/>信用分：${d.value}<br/>溢价率：${d.premium}%` } },
    grid: { left: 230, right: 130, top: 40, bottom: 30 },  // 左边距留足公司名，右边距留足"95分 · 溢价x%"标签完整显示
    xAxis: { type: 'value', max: 100, name: '信用分' },    // 数值轴=信用分
    yAxis: { type: 'category', inverse: true,              // 类目轴=公司名（inverse 让第1名在最上）
             data: pool.map(s => s.name), axisLabel: { fontSize: 10, width: 215, overflow: 'truncate' } },
    series: [{ type: 'bar', barMaxWidth: 16,
               // data 里带上溢价率，条尾 label 和 tooltip 都能显示
               data: pool.map(s => ({ value: s.credit, premium: s.premium })),
               itemStyle: { color: '#46b3c4', borderRadius: [0, 8, 8, 0] },
               label: { show: true, position: 'right', fontSize: 11,
                        // 溢价为负=比市场均价低（绿色，好事）；为正=要付溢价（橙色，醒目提醒）
                        formatter: p => Number(p.data.premium) < 0
                          ? `{v|${p.value}分}{s| · }{n|溢价${p.data.premium}%}`
                          : `{v|${p.value}分}{s| · }{p|溢价+${p.data.premium}%}`,
                        rich: { v: { fontSize: 11, fontWeight: 'bold', color: '#0f6b7a' },
                                s: { fontSize: 11, color: '#94a3b8' },
                                n: { fontSize: 11, fontWeight: 'bold', color: '#16a34a' },
                                p: { fontSize: 11, fontWeight: 'bold', color: '#ea580c' } } } }]  // 条尾同时显示信用分和溢价率
  })
}
function switchRegion(r) { activeRegion.value = r; renderSupplier(r) }  // 点地区按钮

// 初始化两张图：先把图表骨架立起来，再喂数据——某路接口失败也只影响自己那张图，不再整页空白
async function initCharts() {
  // —— 图1：行情曲线（默认螺纹钢，可切品类） ——
  c1 = echarts.init(chartPrice.value)
  c1.setOption({
    title: { text: '螺纹钢 期货收盘价（2024 至今 · 实时刷新）', left: 10, textStyle: { fontSize: 14 } },
    tooltip: { trigger: 'axis' },
    grid: { left: 60, right: 20, top: 50, bottom: 40 },
    xAxis: { type: 'category' },
    yAxis: { type: 'value', name: '元/吨', scale: true },
    dataZoom: [{ type: 'inside' }],
    series: [{ type: 'line', smooth: true, showSymbol: false,
               itemStyle: { color: '#46b3c4' },
               areaStyle: { color: 'rgba(70,179,196,.12)' } }]
  })

  // —— 图2：供应商排行（默认全国，可切地区，横向条形图全名显示） ——
  c2 = echarts.init(chartSupplier.value)
  charts = [c1, c2]
  window.addEventListener('resize', resizeAll)          // 窗口缩放重绘

  // 数据各拉各的：任何一路失败只跳过自己那张图的填充（allSettled 吞掉异常）
  await Promise.allSettled([
    loadMarket().then(() => renderPrice(activeCom.value)),
    loadSuppliers().then(() => renderSupplier(activeRegion.value)),
  ])
}
function resizeAll() { charts.forEach(c => c.resize()) }

// —— 多Agent工作流可视化：实时展示当前执行智能体 ——
const AGENT_NAMES = {                                  // 节点名 → 岗位中文名
  orchestrator: '主编排 Agent',
  market_intelligence: '市场情报 Agent',
  cost_calculate: '成本核算 Agent',
  negotiation: '谈判博弈 Agent',
  supplier_counter: '虚拟供应商 Agent',
  reflector: '反思复盘 Agent',
  risk_approval: '风控审批 Agent'
}
// TaskConsole 通过 window 自定义事件通知这里（跨组件通信，避免 ref 双标冲突）
function onNodeDone(e) {
  const nodeName = e.detail
  const key = nodeName.replace('_node', '')            // 后端节点名带 _node 后缀，去掉再查中文名
  flowLog.value.push({
    name: AGENT_NAMES[key] || nodeName,                // 有中文名显示中文
    state: 'done'
  })
}
// 任务终态通知：点亮/熄灭风险预警面板 + 刷新本月统计（新完成的单计入）
function onRisk(e) {
  const s = e.detail
  if (s === 'blocked')      riskAlert.value = '⚠️ 风险预警：测算成本超预算红线，自动流程已拦截'
  else if (s === 'manual')  riskAlert.value = '🔔 风险预警：谈判未收敛，需人工审批后继续'
  else                      riskAlert.value = ''
  if (s === 'completed') loadStats()                   // 有单完成 → 统计重新算
}
// 最终采购方案：TaskConsole 拉到方案后用事件传过来，渲染在时间线下方
const solutionText = ref('')
function onSolution(e) { solutionText.value = e.detail }
// 方案文本结构化：按行拆开，"## "开头识别为小节标题（模板里加粗高亮），其余为正文
const solutionLines = computed(() => solutionText.value.split('\n').map(line => ({
  text: line.replace(/^#+\s*/, ''),        // 去掉 markdown 的 # 号，只留文字
  heading: /^##?\s+/.test(line) || /^#\s+/.test(line),  // ## / # 开头 = 小节标题
})))

// 本月成本统计：拉真实数据（P5 /api/task/stats/month）
const savingDetail = ref({ done_count: 0, total_saving: 0, avg_budget_ratio: 0 })  // 统计明细
async function loadStats() {
  const res = await fetch(`${API_BASE}/api/task/stats/month`)
  savingDetail.value = await res.json()
  // 拼成面板要的文案：¥ xxx（N 单完成，平均占用预算 x%）
  saving.value = `¥${savingDetail.value.total_saving.toLocaleString()}（${savingDetail.value.done_count} 单完成，平均占用预算 ${savingDetail.value.avg_budget_ratio}%）`
}
// 暴露给 TaskConsole：任务终态后调它刷新统计（新完成的单要计入本月）
function refreshStats() { loadStats() }

onMounted(() => {                                      // 挂载后：画图 + 拉统计 + 开始监听事件
  initCharts()
  loadStats()                                          // 本月成本统计（真数据）
  window.addEventListener('agent-node', onNodeDone)
  window.addEventListener('agent-risk', onRisk)
  window.addEventListener('agent-solution', onSolution)
})
onBeforeUnmount(() => {                                // 卸载时销毁+移除监听，防内存漏
  charts.forEach(c => c.dispose())
  window.removeEventListener('resize', resizeAll)
  window.removeEventListener('agent-node', onNodeDone)
  window.removeEventListener('agent-risk', onRisk)
  window.removeEventListener('agent-solution', onSolution)
})
</script>

<template>
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
    <!-- 图1 行情：顶部品类切换按钮 + 数据新鲜度徽标 -->
    <div class="bg-surface rounded-3xl shadow p-4">
      <div class="flex items-center justify-between gap-2 mb-2">
        <!-- 品类按钮组：点哪个品种图就切哪个 -->
        <div class="flex flex-wrap gap-1">
          <button v-for="c in commodities" :key="c" @click="switchCom(c)"
                  class="text-xs rounded-full px-3 py-1 border font-bold transition"
                  :class="activeCom === c
                    ? 'bg-accent text-[#06343a] border-accent'
                    : 'bg-bg text-muted border-black/10 hover:border-accent hover:text-accent'">
            {{ c }}
          </button>
        </div>
        <!-- 数据截至徽标：实时绿 / 缓存青 / 降级橙，60s 自动轮询保持新鲜 -->
        <span v-if="sourceBadge"
              class="shrink-0 text-[11px] font-bold rounded-full px-2.5 py-1 border"
              :class="sourceBadge.cls">{{ sourceBadge.text }}</span>
      </div>
      <div ref="chartPrice" class="h-64"></div>
    </div>

    <!-- 图2 供应商：地区筛选按钮（滚动条防挤爆） -->
    <div class="bg-surface rounded-3xl shadow p-4">
      <select v-model="activeRegion" @change="switchRegion(activeRegion)"
              class="text-xs rounded-lg border border-black/10 bg-bg px-2 py-1 mb-2 outline-none focus:border-accent">
        <option v-for="r in regions" :key="r" :value="r">{{ r }}</option>
      </select>
      <div ref="chartSupplier" class="h-64"></div>
    </div>

    <!-- Agent 工作流实时可视化（占整行） -->
    <div class="bg-surface rounded-3xl shadow p-4 lg:col-span-2">
      <div class="flex items-center justify-between mb-2">
        <h3 class="font-bold text-ink">多 Agent 工作流 · 执行流水</h3>
        <!-- 清空按钮：只清本地面板显示，不影响任务数据 -->
        <button v-if="flowLog.length" @click="flowLog = []"
                class="shrink-0 text-xs rounded-full border border-black/10 bg-bg px-2.5 py-1 text-muted hover:border-accent hover:text-accent">
          清空记录
        </button>
      </div>
      <p v-if="!flowLog.length" class="text-xs text-muted">还没有执行记录——创建一个采购任务，这里就会实时点亮每个完成的 Agent。</p>
      <!-- 时间线：每个已完成节点一个胶囊，按执行顺序排列 -->
      <div class="flex flex-wrap gap-2">
        <span v-for="(n, i) in flowLog" :key="i"
              class="text-xs rounded-full bg-accent/15 text-accent px-3 py-1 font-bold">
          {{ i + 1 }}. {{ n.name }} ✅
        </span>
      </div>
      <!-- 风险预警面板 + 本月成本节约统计（带说明：什么情况下会变） -->
      <div class="mt-3 grid grid-cols-2 gap-3">
        <div class="rounded-xl border p-3 text-sm"
             :class="riskAlert ? 'border-red-300 bg-red-50 text-red-700' : 'border-black/10 bg-bg text-muted'">
          {{ riskAlert || '暂无风险预警（任务运行中若成本超预算红线被拦截、或谈判未收敛需人工审批，会在这里亮起）' }}
        </div>
        <div class="rounded-xl border border-black/10 bg-bg p-3 text-sm text-muted">
          本月成本节约：{{ saving }}
          <p class="text-xs text-muted/70 mt-1">仅统计本月「已完成」的订单——被拦截、待人工审批的不计入</p>
        </div>
      </div>

      <!-- 最终采购方案：任务结束后自动显示在这里（结构化渲染，重点醒目） -->
      <div v-if="solutionText" class="mt-3 bg-white rounded-2xl p-5 border-2 border-accent/40 shadow-sm">
        <h3 class="flex items-center gap-2 text-lg font-bold text-ink mb-3 pb-2 border-b-2 border-accent/20">
          <span class="text-xl">📋</span>最终采购方案
        </h3>
        <!-- 按行解析后端方案文本：## 开头 = 小节标题（加粗高亮），普通行 = 正文（14px 起步） -->
        <div class="max-h-[30rem] overflow-y-auto pr-1 space-y-0.5">
          <template v-for="(line, i) in solutionLines" :key="i">
            <p v-if="line.heading"
               class="text-[15px] font-bold text-accent mt-3 mb-1 first:mt-0 flex items-center gap-1.5">
              <span class="inline-block w-1.5 h-4 rounded-full bg-accent"></span>{{ line.text }}
            </p>
            <p v-else-if="line.text.trim()"
               class="text-sm leading-relaxed text-ink/90 whitespace-pre-wrap">{{ line.text }}</p>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
