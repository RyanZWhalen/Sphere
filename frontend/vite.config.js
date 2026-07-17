import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: resolve(import.meta.dirname, '../sphere/web/dist'),
    emptyOutDir: true,
    sourcemap: false,
  },
});
