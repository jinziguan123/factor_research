import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  base: '/factor_research/',
  plugins: [vue()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    port: 5173,
    // HMR 走 WebSocket，隔着反向代理（如 Grafana）时 WS 建不起来，客户端会进入
    // 重连循环并在"重连成功"时 location.reload() 整页重载——表现为"切走浏览器后
    // 页面自动刷新、窗口被拉回前台"。部署环境（经反代、多人访问）应关掉 HMR：
    // 设 VITE_DISABLE_HMR=1（start.sh 已设置）。本地开发直接 npm run dev 不设该变量，
    // HMR 照常工作。彻底方案是部署跑生产构建（见 frontend/Dockerfile 的 nginx 方案）。
    hmr: process.env.VITE_DISABLE_HMR ? false : undefined,
    proxy: {
      '/factor_research/api': {
        target: 'http://localhost:8000',
        rewrite: (p: string) => p.replace(/^\/factor_research/, ''),
      },
    },
  },
})
