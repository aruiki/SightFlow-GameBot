import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `npm run dev` talks to the Python server (python -m sightflow web) on :8765
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/ws': { target: 'ws://127.0.0.1:8765', ws: true },
    },
  },
})
