import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests',use:{baseURL:process.env.TEST_APP_URL||'http://127.0.0.1:3015',headless:true,channel:'chrome'},reporter:'list',outputDir:'../artifacts/browser-results'});
