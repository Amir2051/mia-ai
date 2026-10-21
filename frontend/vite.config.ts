import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import type { Plugin } from 'vite';

/**
 * Polaris 12's styles.css ships one unprocessed custom-media rule,
 * "@media (--p-breakpoints-md-up) and print", which the CSS minifier
 * rejects (and browsers ignore). Rewrite it to the valid equivalent.
 */
function fixPolarisCustomMedia(): Plugin {
  return {
    name: 'fix-polaris-custom-media',
    enforce: 'pre',
    transform(code, id) {
      if (!id.includes('@shopify/polaris') || !id.endsWith('styles.css')) return null;
      return code.replace('@media (--p-breakpoints-md-up) and print', '@media print and (min-width: 48em)');
    },
  };
}

const backend = 'http://127.0.0.1:8002';

export default defineConfig({
  plugins: [fixPolarisCustomMedia(), react()],

  // Keep the shared Shopify UI and framework code out of the application entry
  // chunk so route-level lazy loading stays effective and shared code can be cached separately.
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/@shopify/polaris-icons/')) return 'shopify-icons';
          if (id.includes('/node_modules/@shopify/polaris/')) return 'shopify-polaris';
          if (id.includes('/node_modules/react-dom/') || id.includes('/node_modules/react/')) return 'react-vendor';
          if (id.includes('/node_modules/react-router/') || id.includes('/node_modules/react-router-dom/')) return 'router-vendor';
        },
      },
    },
  },

  server: {
    host: '0.0.0.0',
    port: Number(process.env.FRONTEND_PORT || '5173'),

    allowedHosts: ['mia.drivenest.info'],

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
