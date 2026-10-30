import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

const frontendRoot = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  root: frontendRoot,
  plugins: [react(), tailwindcss()],
  envPrefix: 'DASHBOARD_PUBLIC_',
  publicDir: false,
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: 'price-charts', test: /node_modules[\\/]lightweight-charts/ },
            { name: 'financial-charts', test: /node_modules[\\/](recharts|d3-|@reduxjs|react-redux|immer|reselect)/ },
            { name: 'react', test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/ },
          ],
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    cors: false,
    fs: {
      strict: true,
      allow: [frontendRoot],
      deny: ['**/.env', '**/.env.*', '**/.git/**', '**/data/**', '**/*.{crt,pem,key}', '**/*credentials*', '**/.aws/**', '**/.codex/**', '**/.agents/**'],
    },
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false } },
  },
  preview: { host: '127.0.0.1', port: 5173, strictPort: true },
});
