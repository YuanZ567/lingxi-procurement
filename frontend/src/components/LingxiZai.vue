<script setup>
import { ref, computed, onBeforeUnmount } from 'vue'   // ref=响应式变量；computed=计算属性；onBeforeUnmount=组件销毁时清理

const API_BASE = 'http://localhost:8001'      // 后端基地址（8001 端口）
const open = ref(false)                       // 问答窗是否展开（false=收起）
const messages = ref([                        // 聊天记录，每条 {role:'bot'|'user', text}
  { role: 'bot', text: '你好，我是灵犀仔 ✨ 需要比价、查供应商资质还是看行情？' }
])
const input = ref('')                         // 输入框绑定的文字
const sources = ref([])                       // 当前回答的溯源列表（渲染成来源卡片）
let es = null                                 // EventSource 实例（长连接），关窗时要关掉

// 发送问题：建一条 SSE 长连接，按 event 类型把回答/溯源塞进聊天记录
function ask() {
  const q = input.value.trim()               // 取输入并去首尾空格
  if (!q) return                             // 空问题不发了
  messages.value.push({ role: 'user', text: q })  // 先把用户这句显示出来
  input.value = ''                           // 清空输入框
  messages.value.push({ role: 'bot', text: '思考中…' }) // 先占个“正在想”的气泡

  // 断开上一条还没关的连接，避免叠连接
  if (es) es.close()
  // 建立 SSE：问号后面拼 question 参数（中文要 encodeURIComponent）
  es = new EventSource(`${API_BASE}/api/sse/qa?question=${encodeURIComponent(q)}`)

  sources.value = []                         // 新问题先清掉上一条的来源卡片
  let answerText = ''                        // 回答正文（后端整条推，前端负责打字机播放）
  // 后端每条推送都进 onmessage，data 是 JSON 字符串，必须 parse
  es.onmessage = (e) => {
    const evt = JSON.parse(e.data)           // 解析出 {event, data}
    if (evt.event === 'route') {             // 路由：命中哪类知识（这里只在控制台留痕）
      console.log('路由:', evt.data)
    } else if (evt.event === 'answer') {     // 回答正文：接住全文，打字机逐字放（流式观感）
      answerText = String(evt.data)
      typeOut(answerText)                    // 打字机播放，函数见下方
    } else if (evt.event === 'sources') {    // 溯源：存起来，模板里渲染成“来源卡片”
      sources.value = Array.isArray(evt.data) ? evt.data : [String(evt.data)]
    } else if (evt.event === 'end') {        // 结束信号：关连接
      es.close(); es = null
    }
  }
  es.onerror = () => {                        // 连接出错也关掉，避免一直重连
    es.close(); es = null
  }
}

// —— 打字机效果：把整段回答逐字“打”到最后一个气泡（对标 PROJECT.md §6.1-2 流式输出） ——
let timer = null                              // 打字机定时器句柄
function typeOut(full) {
  const idx = messages.value.length - 1       // 最后那个“思考中…”气泡
  let i = 0                                   // 已显示到第几个字
  clearInterval(timer)                        // 防上一次打字没停就叠加
  timer = setInterval(() => {                 // 每 24ms 放出 2 个字
    i = Math.min(i + 2, full.length)
    messages.value[idx] = { role: 'bot', text: full.slice(0, i) }
    if (i >= full.length) clearInterval(timer) // 全部放完就停表
  }, 24)
}

const quick = ['预算超了怎么办', '查螺纹钢历史价格', '供应商资质怎么查']  // 快捷指令预置问题

// —— 轻量 Markdown 渲染（bot 消息专用）：支持表格 / **加粗** / 纯文本，零依赖 ——
// 为什么不用 marked 库：只为一张表格引依赖不值当；先 HTML 转义再拼结构，天然防 XSS
function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
function renderMsg(text) {
  const lines = String(text).split('\n')
  let html = '', inTable = false
  for (const raw of lines) {
    const line = esc(raw)                              // 先转义，再解析结构
    const isRow = /^\s*\|.*\|\s*$/.test(line)          // 表格数据行：| a | b |
    const isSep = /^\s*\|[\s:|-]+\|\s*$/.test(line)    // 表头分隔行：| --- | --- |
    if (isRow && !isSep) {
      const cells = line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim())
      if (!inTable) {                                  // 第一行当表头
        html += '<table class="w-full text-[11px] border-collapse my-1"><thead><tr>' +
          cells.map(c => `<th class="border border-black/10 bg-black/5 px-1.5 py-1 text-left font-bold">${c}</th>`).join('') +
          '</tr></thead><tbody>'
        inTable = true
      } else {
        html += '<tr>' + cells.map(c => `<td class="border border-black/10 px-1.5 py-1 whitespace-nowrap">${c}</td>`).join('') + '</tr>'
      }
    } else {
      if (isSep) continue                              // 分隔行跳过
      if (inTable) { html += '</tbody></table>'; inTable = false }
      html += line.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')   // 普通行：支持 **加粗**
    }
  }
  if (inTable) html += '</tbody></table>'              // 收尾防残缺表格
  return html
}
function quickAsk(q) { input.value = q; ask() }  // 一键把预置问题填进输入框并直接发送

// —— 自由拖拽：灵犀仔可以停在页面任意位置，位置记忆在 localStorage，刷新不丢 ——
const POS_KEY = 'lingxizai-pos'               // localStorage 存储键
const orbW = 120, orbH = 132                  // 球体尺寸（与模板 w-[120px] h-[132px] 保持一致）
const pos = ref(null)                         // {x, y} 球体左上角视口坐标；null=未拖过，走默认右下角
const dragging = ref(false)                   // 是否正在拖动（拖动时停掉浮动动画、隐藏气泡）
onMounted(() => {                             // 启动时恢复上次的位置
  try {
    const saved = JSON.parse(localStorage.getItem(POS_KEY) || 'null')
    if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) pos.value = clampPos(saved)
  } catch { /* 坏数据就当没存过 */ }
})
// 边界钳制：球永远完整留在视口内（留 8px 边距），窗口缩小后也不丢
function clampPos(p) {
  const maxX = window.innerWidth  - orbW - 8
  const maxY = window.innerHeight - orbH - 8
  return { x: Math.min(Math.max(p.x, 8), Math.max(maxX, 8)),
           y: Math.min(Math.max(p.y, 8), Math.max(maxY, 8)) }
}
let dragStart = null                          // 按下瞬间的 {px, py, ox, oy}
let moved = false                             // 本次按下后有没有真的拖动（区分"点击"和"拖动"）
function onDragStart(e) {                     // 按下：开始潜在拖动
  dragStart = { px: e.clientX, py: e.clientY,
                ox: pos.value ? pos.value.x : window.innerWidth  - orbW - 24,   // 默认右下角折算成坐标
                oy: pos.value ? pos.value.y : window.innerHeight - orbH - 24 }
  moved = false
  window.addEventListener('pointermove', onDragMove)    // 挂到 window：鼠标移出球体也跟手
  window.addEventListener('pointerup',   onDragEnd)
}
function onDragMove(e) {
  if (!dragStart) return
  const dx = e.clientX - dragStart.px, dy = e.clientY - dragStart.py
  if (!moved && Math.hypot(dx, dy) < 5) return        // 位移小于 5px 仍视为"点击"，不启动拖动
  moved = true; dragging.value = true                 // 真拖上了：更新位置并钳制在视口内
  rect.value = null                                   // 球一动，问答窗回到"跟着球"模式（下次开窗贴球换边）
  pos.value = clampPos({ x: dragStart.ox + dx, y: dragStart.oy + dy })
}
function onDragEnd() {
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointerup',   onDragEnd)
  dragStart = null; dragging.value = false
  if (moved && pos.value) {                           // 拖完了：记住新位置（下次打开还在原地）
    localStorage.setItem(POS_KEY, JSON.stringify(pos.value))
    showTip.value = false
  } else {
    toggle()                                          // 没拖动 = 纯点击 → 开/合问答窗
  }
}
// 球体定位：没拖过用右下角默认位（class），拖过用绝对坐标（style 只作用于球自己）
const orbStyle = computed(() => pos.value
  ? { left: `${pos.value.x}px`, top: `${pos.value.y}px` }
  : null)
// 问答窗定位：独立 fixed 元素、独立算坐标——开/关窗绝不挪动球的位置
// 规则：球在屏幕右半边 → 窗开在球左侧；左半边 → 窗开在球右侧；顶部与球大致对齐并钳制在视口内
const winStyle = computed(() => {
  if (!pos.value) return null                         // 没拖过：走默认 class（球左侧、右下角）
  const x = pos.value.x
  const w = winW.value                                // 窗宽是动态的（用户可拖拽调节）
  const onLeftSide = x > window.innerWidth / 2        // 球在右半边 → 窗放左边
  const left = onLeftSide
    ? Math.max(8, x - w - 12)                         // 窗右缘贴球左缘（留 12px 缝）
    : Math.min(x + orbW + 12, window.innerWidth - w - 8)  // 窗左缘贴球右缘，不出屏
  const top = Math.min(Math.max(pos.value.y - 10, 8), Math.max(window.innerHeight - 470, 8))  // 顶部对齐球，钳制不出屏
  return { left: `${left}px`, top: `${top}px` }
})
// 气泡朝向判断：球在右半边时气泡朝左开口
const winLeft = computed(() => !pos.value || pos.value.x > window.innerWidth / 2)

// —— 问答窗大小可拖拽调节：四个角都能拉，被拉的角跟着指针走、对角固定；尺寸+位置记忆在 localStorage ——
const SIZE_KEY = 'lingxizai-size'
const winW   = ref(320)      // 窗宽（px），默认 320 = 原 w-80
const chatH  = ref(256)      // 聊天记录区高度（px），默认 256 = 原 h-64
const rect   = ref(null)     // 自由模式窗口位置 {left, top}；null=跟着球走（未手动拉过）
const winEl  = ref(null)     // 窗口 DOM 引用（读真实高度，免手算标题栏等固定高度）
// 四个角手柄的定位与光标样式
const CORNER_CLS = {
  nw: 'top-1.5 left-1.5 cursor-nwse-resize',
  ne: 'top-1.5 right-1.5 cursor-nesw-resize',
  sw: 'bottom-1.5 left-1.5 cursor-nesw-resize',
  se: 'bottom-1.5 right-1.5 cursor-nwse-resize',
}
// 窗口盒子样式：自由模式用自己的坐标，否则跟着球（winStyle / 默认 class）
const winBox = computed(() => ({
  ...(rect.value ? { left: `${rect.value.left}px`, top: `${rect.value.top}px` } : (winStyle.value || {})),
  width: `${winW.value}px`,
}))
onMounted(() => {            // 启动恢复上次的尺寸和自由位置
  try {
    const s = JSON.parse(localStorage.getItem(SIZE_KEY) || 'null')
    if (s) {
      winW.value  = s.w || 320
      chatH.value = s.h || 256
      if (Number.isFinite(s.left) && Number.isFinite(s.top)) rect.value = clampRect({ left: s.left, top: s.top })
    }
  } catch { /* 坏数据当没存过 */ }
})
// 自由位置钳制：窗口完整留在视口内
function clampRect(r) {
  const maxW = Math.max(window.innerWidth - winW.value - 8, 8)
  const maxH = Math.max(window.innerHeight - (winEl.value ? winEl.value.offsetHeight : 430) - 8, 8)
  return { left: Math.min(Math.max(r.left, 8), maxW), top: Math.min(Math.max(r.top, 8), maxH) }
}
function saveSize() {        // 尺寸+自由位置落盘
  localStorage.setItem(SIZE_KEY, JSON.stringify({
    w: winW.value, h: chatH.value,
    left: rect.value ? rect.value.left : null, top: rect.value ? rect.value.top : null,
  }))
}
let sizeStart = null         // 拖角起点状态：对角固定点 + 各项初始值
function onResizeStart(e, corner) {
  e.preventDefault(); e.stopPropagation()           // 手柄不触发其它交互
  const r = winEl.value.getBoundingClientRect()     // 窗口当前真实几何
  sizeStart = {
    h0: r.height, chatH0: chatH.value,
    ax: corner.includes('w') ? r.right : r.left,    // 对角固定边 X（拖西角→右缘固定；拖东角→左缘固定）
    ay: corner.includes('n') ? r.bottom : r.top,    // 对角固定边 Y（拖北角→下缘固定；拖南角→上缘固定）
    anchorLeft: !corner.includes('w'),              // 固定边在左 → left = ax
    anchorTop:  !corner.includes('n'),              // 固定边在上 → top = ay
  }
  window.addEventListener('pointermove', onResizeMove)
  window.addEventListener('pointerup',   onResizeEnd)
}
function onResizeMove(e) {
  if (!sizeStart) return
  const s = sizeStart
  // 宽高 = 指针到固定对角的距离（拖哪个角，哪个角跟手，对角纹丝不动）
  const w = s.anchorLeft ? e.clientX - s.ax : s.ax - e.clientX
  const h = s.anchorTop  ? e.clientY - s.ay : s.ay - e.clientY
  const chrome = s.h0 - s.chatH0                    // 标题栏+快捷指令+输入框等固定高度（实测，不算错）
  winW.value  = Math.round(Math.min(Math.max(w, 300), Math.max(Math.min(560, window.innerWidth - 16), 300)))
  chatH.value = Math.round(Math.min(Math.max(h - chrome, 180), Math.max(Math.min(620, window.innerHeight - chrome - 16), 180)))
  rect.value = clampRect({                          // 拉过之后窗口进入"自由模式"，位置=固定对角反推
    left: s.anchorLeft ? s.ax : s.ax - winW.value,
    top:  s.anchorTop  ? s.ay : s.ay - (chrome + chatH.value),
  })
}
function onResizeEnd() {
  window.removeEventListener('pointermove', onResizeMove)
  window.removeEventListener('pointerup',   onResizeEnd)
  if (sizeStart) saveSize()
  sizeStart = null
}
// 窗口缩放时把球（和自由模式的窗）拉回视口内
function onResize() {
  if (pos.value) pos.value = clampPos(pos.value)
  if (rect.value) rect.value = clampRect(rect.value)
}
onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => window.removeEventListener('resize', onResize))

// —— 气泡提示：周期性冒泡，直到用户点过一次 ——
const showTip = ref(false)                    // 气泡是否显示
let tipTimer = null                           // 循环定时器句柄
import { onMounted } from 'vue'               // 挂载后开始冒泡循环
onMounted(() => {                             // 每隔几秒冒一次，确保用户看见
  const cycle = () => {
    showTip.value = true
    setTimeout(() => { showTip.value = false }, 6000)   // 显示6秒
  }
  setTimeout(cycle, 1500)                     // 首次1.5秒后冒
  tipTimer = setInterval(cycle, 13000)        // 之后每13秒再冒一次
})
function toggle() {                           // 点发光核：展开/收起
  open.value = !open.value
  showTip.value = false                       // 用户已经发现它了
  clearInterval(tipTimer)                     // 停止冒泡循环（目的达成）
  if (!open.value && es) { es.close(); es = null }  // 收起时顺便关连接
}
onBeforeUnmount(() => {                     // 销毁清理：关连接、停定时器、摘掉拖尺寸的全局监听，防内存漏
  if (es) es.close(); clearInterval(timer); clearInterval(tipTimer)
  window.removeEventListener('pointermove', onResizeMove)
  window.removeEventListener('pointerup',   onResizeEnd)
})
</script>

<template>
  <!-- 问答窗：独立 fixed 元素；四个角都能拖拽调大小（被拉角跟手、对角固定），拉过后位置自由 -->
  <div v-if="open" ref="winEl"
       class="fixed z-50 bg-surface rounded-3xl shadow-2xl border border-black/5 p-4"
       :class="(!pos && !rect) ? 'bottom-6 right-[156px]' : ''"
       :style="winBox">
      <h2 class="font-bold text-ink">灵犀仔 · 悬浮助手</h2>
      <p class="text-xs text-muted mb-2">实时对接 /api/sse/qa</p>
      <!-- 聊天记录区：高度跟随拖拽（默认 256px），每条按角色决定左右和气泡色 -->
      <div class="overflow-y-auto space-y-2 text-sm" :style="{ height: chatH + 'px' }">
        <div v-for="(m, i) in messages" :key="i"
             :class="m.role === 'user' ? 'text-right' : 'text-left'">
          <span v-if="m.role === 'user'"
                  class="inline-block bg-accent/15 rounded-2xl rounded-tr-sm px-3 py-2">
            {{ m.text }}
          </span>
          <span v-else
                  class="inline-block bg-bg rounded-2xl rounded-tl-sm px-3 py-2 whitespace-pre-line max-w-full align-top"
                  v-html="renderMsg(m.text)">
          </span>
        </div>
      </div>
      <!-- 快捷指令：一键发起常见查询（对标 PROJECT.md §6.1-2 快捷指令） -->
      <div class="flex flex-wrap gap-1 mb-2">
        <button v-for="q in quick" :key="q" @click="quickAsk(q)"
                class="text-xs rounded-full bg-bg border border-black/10 px-2 py-1 text-muted hover:border-accent hover:text-accent">
          {{ q }}
        </button>
      </div>
      <!-- 来源卡片：回答带溯源时展示（对标 PROJECT.md §6.1-2 溯源卡片） -->
      <div v-if="sources.length" class="mt-2 space-y-1">
        <div v-for="(s, i) in sources" :key="i"
             class="text-xs bg-bg rounded-lg px-2 py-1 border border-black/5 truncate" :title="s">
          📚 {{ s }}
        </div>
      </div>
      <!-- 输入行：回车或点发送都调用 ask() -->
      <div class="mt-2 flex gap-2">
        <input v-model="input" @keyup.enter="ask"
               class="flex-1 rounded-full border border-black/10 px-3 py-2 text-sm outline-none focus:border-accent"
               placeholder="问点什么…" />
        <button @click="ask"
                class="rounded-full bg-accent px-4 text-sm font-bold text-[#06343a]">发送</button>
      </div>
      <!-- 四个角的尺寸手柄：按住任意一角拖动调宽高（对角固定，跟手缩放） -->
      <div v-for="c in ['nw', 'ne', 'sw', 'se']" :key="c"
           @pointerdown="onResizeStart($event, c)" title="按住拖动，调整窗口大小"
           class="absolute w-4 h-4 z-10 text-muted/35 hover:text-accent transition-colors"
           :class="CORNER_CLS[c]">
        <svg viewBox="0 0 16 16" class="w-full h-full" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
          <path d="M13 9 L9 13"/><path d="M13 4 L4 13"/>
        </svg>
      </div>
  </div>

  <!-- 发光核本体：独立 fixed 元素。按住拖到页面任意位置，轻点开/合问答窗 -->
  <div class="fixed z-50 touch-none select-none"
       :class="pos ? '' : 'bottom-6 right-6'"
       :style="orbStyle">
      <!-- 气泡提示：告诉用户"我在"（拖动中隐藏） -->
      <div v-if="showTip && !open && !dragging"
           class="absolute bottom-full mb-2 whitespace-nowrap bg-surface border border-accent/30 rounded-2xl px-3 py-2 text-xs text-ink shadow-lg animate-[tipIn_.4s_var(--ease-out)_both]"
           :class="winLeft ? 'right-0 rounded-br-sm' : 'left-0 rounded-bl-sm'">
        我是灵犀仔 ✨ 按住我可以拖到任意位置
      </div>
      <!-- 双层扩散波纹光圈：像水面涟漪一圈圈荡开（比实心圆灵动） -->
      <div class="absolute inset-[-8px] rounded-full border-2 border-accent/50 animate-[ripple_2.8s_ease-out_infinite]"></div>
      <div class="absolute inset-[-8px] rounded-full border-2 border-accent/35 animate-[ripple_2.8s_ease-out_1.4s_infinite]"></div>
      <div class="absolute inset-[-10px] rounded-full bg-accent/10 animate-[haloBreath_3.2s_ease-in-out_infinite]"></div>
      <!-- 发光核（可拖拽 + 悬停放大；拖动中暂停浮动动画避免"跟手抖动"） -->
      <div @pointerdown="onDragStart"
           class="relative w-[120px] h-[132px] cursor-grab select-none transition-transform duration-300 hover:scale-110"
           :class="dragging ? 'cursor-grabbing scale-110' : 'animate-[float_5s_ease-in-out_infinite]'">
        <!-- 球后极光斑 -->
        <div class="absolute inset-[-14px] rounded-full blur-xl opacity-70"
             style="background:
               radial-gradient(40% 40% at 32% 30%, rgba(191,234,228,.55), transparent 70%),
               radial-gradient(42% 42% at 72% 68%, rgba(229,220,244,.55), transparent 70%),
               radial-gradient(38% 38% at 60% 32%, rgba(207,228,255,.55), transparent 70%)"></div>
      <!-- 镀铬悬浮环（用 SVG 画，金属渐变） -->
      <svg viewBox="0 0 240 262" class="absolute inset-0 w-full h-full">
        <defs>
          <linearGradient id="chrome" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color="#ffffff"/>
            <stop offset=".32" stop-color="#dfe5ea"/>
            <stop offset=".55" stop-color="#aab2bc"/>
            <stop offset=".74" stop-color="#eef2f5"/>
            <stop offset="1" stop-color="#8b939d"/>
          </linearGradient>
          <radialGradient id="pearl" cx=".4" cy=".32" r=".85">
            <stop offset="0" stop-color="#ffffff"/>
            <stop offset=".42" stop-color="#f1f6f8"/>
            <stop offset=".72" stop-color="#e4edf1"/>
            <stop offset="1" stop-color="#d2dde4"/>
          </radialGradient>
          <radialGradient id="coreHi" cx=".5" cy=".5" r=".5">
            <stop offset="0" stop-color="#ffffff" stop-opacity=".95"/>
            <stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
          </radialGradient>
        </defs>
        <!-- 环：绕球后的椭圆，缓慢旋转用 CSS animation -->
        <ellipse cx="120" cy="132" rx="98" ry="40" fill="none"
                 stroke="url(#chrome)" stroke-width="9"
                 class="animate-[ringSpin_16s_linear_infinite]" style="transform-origin:120px 132px"/>
        <!-- 珍珠球体 -->
        <circle cx="120" cy="132" r="62" fill="url(#pearl)"/>
        <!-- 左上高光 -->
        <ellipse cx="100" cy="104" rx="30" ry="18" fill="url(#coreHi)"
                 transform="rotate(-24 100 104)" class="animate-[shine_4.5s_ease-in-out_infinite]"/>
        <!-- 冷青柔脸：眼睛（会眨）+ 微笑 -->
        <g class="animate-[eyeGlow_3.4s_ease-in-out_infinite]">
          <g class="animate-[blink_5s_ease-in-out_infinite]" style="transform-origin:120px 128px">
            <circle cx="103" cy="128" r="7.5" fill="#46b3c4"/>
            <circle cx="137" cy="128" r="7.5" fill="#46b3c4"/>
            <circle cx="105" cy="125" r="2.6" fill="#fff"/>
            <circle cx="139" cy="125" r="2.6" fill="#fff"/>
          </g>
          <path d="M108 142 Q120 151 132 142" stroke="#46b3c4" stroke-width="2.8"
                fill="none" stroke-linecap="round" opacity=".85"/>
        </g>
      </svg>
      </div>
  </div>
</template>

<style>
/* ⚠️ keyframes 必须放全局 style：scoped 会给 keyframes 改名（加hash），模板里 animate-[xxx] 引用原名匹配不上 → 动画全部失效 */
@keyframes float  { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-10px)} }
@keyframes ringSpin { to { transform: rotate(360deg); } }
@keyframes shine  { 0%,100%{opacity:.55} 50%{opacity:1} }
@keyframes eyeGlow { 0%,100%{opacity:1} 50%{opacity:.6} }
@keyframes blink  { 0%,92%,100%{transform:scaleY(1)} 95%{transform:scaleY(.1)} }   /* 每5秒眨一次眼 */
@keyframes ripple { 0%{transform:scale(1);opacity:.7} 100%{transform:scale(1.45);opacity:0} } /* 波纹荡开 */
@keyframes haloBreath { 0%,100%{transform:scale(1);opacity:.15} 50%{transform:scale(1.1);opacity:.3} } /* 底光呼吸 */
@keyframes tipIn  { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} } /* 气泡弹入 */
</style>