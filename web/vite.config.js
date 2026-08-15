import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://localhost:48620', '/ws': { ws: true, target: 'ws://localhost:48620' } } },
  build: { outDir: 'dist' }
})
