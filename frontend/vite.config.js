import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'

export default defineConfig({
  plugins: [preact()],
  build: {
    outDir: '../src/control_tower/web/ui/dist',
    emptyOutDir: true,
  },
})
