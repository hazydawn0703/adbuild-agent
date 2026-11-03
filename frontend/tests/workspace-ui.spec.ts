import {test,expect} from '@playwright/test';

test('workspace navigation, task controls and login fit desktop and mobile screens',async({page})=>{
  await page.route('**/apps/adbuild-agent/api/config',route=>route.fulfill({json:{configured:true,execution_enabled:false}}));
  await page.goto('/apps/adbuild-agent');
  await page.waitForLoadState('networkidle');
  for(const width of [1920,1440,1280,1100,1024,768,390,320]){
    await page.setViewportSize({width,height:1000});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`page overflow at ${width}px`).toBeTruthy();
    await expect(page.getByRole('button',{name:'创建项目',exact:true})).toBeVisible();
    await expect(page.getByRole('button',{name:'项目授权',exact:true})).toBeVisible();
    await expect(page.getByRole('button',{name:'项目授权',exact:true})).toBeDisabled();
    await expect(page.getByRole('button',{name:width<=760?'登录工作空间':'登录账号',exact:true})).toBeVisible();
    await expect(page.getByRole('combobox',{name:'选择搭建任务'})).toBeVisible();
    await expect(page.getByRole('button',{name:'新建搭建任务',exact:true})).toBeDisabled();
  }
  await page.setViewportSize({width:1440,height:1000});
  const header=await page.locator('.topbar').boundingBox();
  const preview=await page.locator('.preview > header').boundingBox();
  expect(header!.y).toBe(preview!.y);
  expect(header!.height).toBe(preview!.height);
  const picker=await page.getByRole('combobox',{name:'选择搭建任务'}).boundingBox();
  const create=await page.getByRole('button',{name:'新建搭建任务',exact:true}).boundingBox();
  expect(Math.abs(picker!.y-create!.y)).toBeLessThanOrEqual(1);
  expect(picker!.x+picker!.width).toBeLessThan(create!.x);
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'登录工作空间',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'登录工作空间',exact:true});
  await expect(dialog).toBeVisible();
  const bounds=await dialog.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x+bounds!.width).toBeLessThanOrEqual(390);
  await expect(dialog.getByLabel('邮箱')).toBeFocused();
  await expect(dialog.getByLabel('密码')).toBeVisible();
  await page.getByRole('button',{name:'关闭窗口'}).click();
  await expect(dialog).not.toBeVisible();
});
