import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backend = 'http://127.0.0.1:8002';

export default defineConfig({
  plugins: [react()],

  server: {
    host: '0.0.0.0',
    port: Number(process.env.FRONTEND_PORT || '5173'),

    allowedHosts: ['drivenest.info', 'www.drivenest.info'],

    proxy: {
      '/api': {
        target: backend,
        changeOrigin: true,
      },

      '/auth': {
        target: backend,
        changeOrigin: true,
      },

      '/shopify': {
        target: backend,
        changeOrigin: true,
      },

      '/webhook': {
        target: backend,
        changeOrigin: true,
      },
    },
  },
});
