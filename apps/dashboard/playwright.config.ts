import {defineConfig} from '@playwright/test';
// Keep local test traffic away from corporate HTTP proxies.
process.env.NO_PROXY='127.0.0.1,localhost';
process.env.no_proxy='127.0.0.1,localhost';
export default defineConfig({testDir:'./tests',timeout:30000,workers:1,
  use:{baseURL:'http://127.0.0.1:18001',headless:true,channel:process.env.CHROME86_BIN?undefined:'chromium',launchOptions:process.env.CHROME86_BIN?{executablePath:process.env.CHROME86_BIN}:undefined,viewport:{width:1365,height:1000}},
  webServer:{command:`cd ../.. && DEMO_STATE_DIR=${process.env.DEMO_BROWSER_STATE || 'var/browser-tests'} DEMO_ORIGIN=http://127.0.0.1:18001 ../.dev-venv/bin/python -m uvicorn tests.browser_server:app --host 127.0.0.1 --port 18001 --workers 1 --no-proxy-headers`,
    url:'http://127.0.0.1:18001/live',reuseExistingServer:false,timeout:30000},
  reporter:'list'});
