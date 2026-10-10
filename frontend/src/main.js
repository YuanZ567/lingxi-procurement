//应用入口
import { createApp } from 'vue'             // Vue 创建应用的函数
import App from './App.vue'                 // 根组件（下面第 8 节写）
import './style.css'                        // 引入全局样式（Tailwind 在这里）

createApp(App).mount('#app')               // 把根组件挂到 index.html 的 #app 上