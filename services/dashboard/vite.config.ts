import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // `npm run dev` talks to api-gateway directly without needing CORS
    // configured there; the production build (served by nginx, see
    // Dockerfile) instead bakes in VITE_API_BASE_URL / VITE_WS_URL at
    // build time and calls the gateway directly.
    proxy: {
      '/api': { target: 'http://localhost:8180', changeOrigin: true },
      '/ws': { target: 'ws://localhost:8180', ws: true },
    },
  },
})
