import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://localhost:48620', '/ws': { ws: true, target: 'ws://localhost:48620' } } },
  build: {
    outDir: 'dist',
    rollupOptions: {
      output: {
        // 拆包：highlight.js/marked 只在打开文档面板时才需要（DocPanel 已改 React.lazy），
        // react 单独成 vendor 便于缓存；首屏不再吞下整个文档渲染栈。
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('highlight.js')) return 'hljs'
          if (id.includes('marked') || id.includes('dompurify')) return 'markdown'
          return 'vendor'
        },
      },
    },
  },
})
