import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dashboard talks to the FastAPI backend through the dev-server proxy: /api/* -> :8000/*, /ws/* -> ws://:8000/ws/*.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', rewrite: (path) => path.replace(/^\/api/, '') },
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
  test: { environment: 'jsdom' },
});
