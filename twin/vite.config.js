import { defineConfig } from 'vite'

export default defineConfig({
  base: './',
  build: { outDir: 'dist', chunkSizeWarningLimit: 1200 },
  server: { host: true }
})
