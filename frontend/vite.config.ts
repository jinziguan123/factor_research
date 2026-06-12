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
    proxy: {
      '/factor_research/api': {
        target: 'http://localhost:8000',
        rewrite: (p: string) => p.replace(/^\/factor_research/, ''),
      },
    },
  },
})
