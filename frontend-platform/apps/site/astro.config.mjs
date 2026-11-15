import { defineConfig } from 'astro/config';
import react from '@astrojs/react';
import tailwindcss from '@tailwindcss/vite';
const origin = process.env.VIDEL_BACKEND_ORIGIN;
export default defineConfig({
  integrations: [react()],
  vite: {plugins:[tailwindcss()], server:{allowedHosts:true, ...(origin ? {proxy:{'/app':{target:origin,changeOrigin:true}, '/api/v1':{target:origin,changeOrigin:true}}} : {})}},
});
