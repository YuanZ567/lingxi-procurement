import { defineConfig } from 'vite'        // Vite 配置函数
import vue from '@vitejs/plugin-vue'        // Vue 插件，让 .vue 能被编译

export default defineConfig({
  plugins: [vue()],                         // 注册 Vue 插件
  server: {
    port: 5173,                             // 前端开发端口（后端 8001，跨域已在后端放行 *）
    host: true,                             // 监听 0.0.0.0：IPv4(127.0.0.1) 和 IPv6 都能访问，避免"拒绝连接"
    proxy: {                                // 代理：前端发 /api 自动转发到后端 8001，省得写完整域名
      '/api': {
        target: 'http://localhost:8001',    // 后端地址（8000 被占用，用 8001）
        changeOrigin: true                  // 改写请求头里的 host，后端才认
      }
    }
  }
})