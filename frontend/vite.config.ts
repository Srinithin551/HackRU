/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, type ProxyOptions } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Vite only exposes VITE_* vars to browser code; loadEnv lets this Node-side config
  // read VITE_PROXY_TARGET too (the '' prefix argument means "all variables").
  const env = loadEnv(mode, process.cwd(), '')
  // The browser talks only to this dev server; it forwards backend routes to FastAPI.
  // Same origin means no CORS, and Web Audio can analyse the interviewer's voice
  // (cross-origin audio can't be read for the waveform without CORS headers).
  // `ws: true` upgrades /ws connections so the WebSocket passes through the proxy.
  const target = env.VITE_PROXY_TARGET || 'http://localhost:8000'
  const proxy: Record<string, ProxyOptions> = {
    '/api': { target, changeOrigin: true },
    '/audio': { target, changeOrigin: true },
    '/ws': { target, changeOrigin: true, ws: true },
  }

  return {
    // react() = JSX + fast refresh; tailwindcss() = Tailwind v4 compiled inside Vite (no PostCSS file).
    plugins: [react(), tailwindcss()],
    server: { proxy },
    preview: { proxy },
    test: {
      environment: 'node',
      include: ['src/**/*.test.ts', 'scripts/**/*.test.ts'],
    },
  }
})
