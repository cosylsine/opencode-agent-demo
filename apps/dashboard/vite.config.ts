import {defineConfig} from 'vite';
export default defineConfig({
  server:{proxy:{'/api':'http://127.0.0.1:18000'}},
  build:{
    target:'chrome86',
    cssTarget:'chrome86',
    rollupOptions:{output:{manualChunks(id){
      if(id.includes('/node_modules/zrender/'))return 'chart-renderer';
      if(id.includes('/node_modules/echarts/'))return 'charts';
      if(id.includes('/node_modules/react'))return 'react';
    }}}
  }
});
