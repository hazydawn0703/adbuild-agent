import {defineConfig} from '@playwright/test';
import {randomUUID} from 'node:crypto';
const runDirectory=`browser-${new Date().toISOString().replace(/[:.]/g,'-')}-${randomUUID().slice(0,8)}`;
export default defineConfig({testDir:'./tests',workers:2,use:{baseURL:process.env.TEST_APP_URL||'http://127.0.0.1:3015',headless:true,channel:'chrome'},reporter:'list',outputDir:`../artifacts/${runDirectory}`});
