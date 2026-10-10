<script setup>
import LingxiZai from './components/LingxiZai.vue'     // 灵犀仔悬浮助手
import Dashboard  from './components/Dashboard.vue'    // 看板
import TaskConsole from './components/TaskConsole.vue' // 任务控制台
// 组件间通信用 window 自定义事件（agent-node / agent-risk），无需传 ref，天然解耦
</script>

<template>
  <div class="min-h-screen bg-bg text-ink p-6">
    <!-- 页头 -->
    <header class="mb-6">
      <h1 class="text-3xl font-bold">灵犀采购 · 数据看板</h1>
      <p class="text-muted text-sm">真实行情 + 真实供应商 + 多智能体谈判直播</p>
    </header>

    <!-- 上半：任务控制台 + 看板并排 -->
    <div class="grid grid-cols-1 xl:grid-cols-3 gap-4">
      <div class="xl:col-span-1">
        <TaskConsole />
      </div>
      <div class="xl:col-span-2">
        <Dashboard />
      </div>
    </div>

    <!-- 灵犀仔永远悬浮在右下角 -->
    <LingxiZai />
  </div>
</template>