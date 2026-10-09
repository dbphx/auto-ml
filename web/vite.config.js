import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/static/',
  plugins: [react()],
  build: { outDir: '../auto_ml/static', emptyOutDir: true },
  server: {
    host: '0.0.0.0',
    proxy: {
      '/tasks': 'http://localhost:8080',
      '/hook': 'http://localhost:8080',
      '/jobs': 'http://localhost:8080',
    },
  },
});
