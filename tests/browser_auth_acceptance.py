"""Account/recovery checks against the isolated mock server; never use production."""
import asyncio
import time
from pathlib import Path
from playwright.async_api import async_playwright,expect

BASE='http://127.0.0.1:18817'
PASSWORD='TEST_ONLY_PASSWORD_123456'
NEW_PASSWORD='NEW_TEST_PASSWORD_2026'


async def restart_checkpoint(index):
    Path(f'/testdata/restart-requested-{index}').write_text('ready')
    for _ in range(300):
        if Path(f'/testdata/restart-complete-{index}').exists():return
        await asyncio.sleep(.2)
    raise AssertionError('Isolated restart checkpoint timed out')


async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        context=await browser.new_context(viewport={'width':1366,'height':768})
        page=await context.new_page();errors=[];logins=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:logins.append(1) if r.url.endswith('/api/login') else None)
        await page.goto(BASE)
        await page.locator('#password').fill(PASSWORD);await page.locator('#loginBtn').click()
        await page.locator('.model-choice').first.wait_for()
        cookie=next(c for c in await context.cookies() if c['name']=='tester_session')
        assert abs(cookie['expires']-time.time()-7*86400)<10
        original_token=cookie['value']
        await page.route('**/api/me',lambda route:route.abort())
        await page.reload();await expect(page.locator('#retrySession')).to_be_visible()
        await expect(page.locator('#loginScreen')).to_be_hidden()
        assert next(c for c in await context.cookies() if c['name']=='tester_session')['value']==original_token
        await page.screenshot(path='/testdata/network-retry.png',full_page=True)
        await page.unroute('**/api/me');await page.locator('#retrySession').click()
        await expect(page.locator('#workspace')).to_be_visible();assert len(logins)==1
        await page.route('**/api/me',lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"temporarily unavailable"}'))
        await page.reload();await expect(page.locator('#retrySession')).to_be_visible()
        await expect(page.locator('#loginScreen')).to_be_hidden()
        await page.unroute('**/api/me');await page.locator('#retrySession').click()
        await expect(page.locator('#workspace')).to_be_visible();assert len(logins)==1
        print('PASS: offline and HTTP 503 retain cookie and restore without login',flush=True)

        await page.route('**/api/account',lambda route:route.abort())
        await page.locator('#navAccount').click();await expect(page.locator('#retryAccount')).to_be_visible()
        await expect(page.locator('#loginScreen')).to_be_hidden()
        await page.unroute('**/api/account');await page.locator('#retryAccount').click()
        await expect(page.locator('#sessionDays')).to_have_value('7')
        await page.locator('#sessionDays').fill('14');await page.locator('#saveSessionDays').click()
        await expect(page.locator('#sessionPolicyNotice')).to_contain_text('14')
        cookie=next(c for c in await context.cookies() if c['name']=='tester_session')
        assert abs(cookie['expires']-time.time()-14*86400)<10
        await page.screenshot(path='/testdata/account-settings.png',full_page=True)
        await page.set_viewport_size({'width':390,'height':844})
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        await page.screenshot(path='/testdata/mobile-account.png',full_page=True)
        await page.set_viewport_size({'width':1366,'height':768})
        await restart_checkpoint(1)
        await page.reload();await expect(page.locator('#workspace')).to_be_visible()
        assert len(logins)==1
        await page.locator('#navAccount').click();await expect(page.locator('#sessionDays')).to_have_value('14')
        print('PASS: 14-day policy and login survive actual container restart',flush=True)

        other=await browser.new_context()
        assert (await other.request.post(BASE+'/api/login',data={'password':PASSWORD})).status==200
        await page.locator('#currentPassword').fill('wrong-password')
        await page.locator('#newPassword').fill(NEW_PASSWORD);await page.locator('#confirmPassword').fill(NEW_PASSWORD)
        await page.locator('#savePassword').click();await expect(page.locator('#passwordChangeError')).to_contain_text('当前密码不正确')
        await expect(page.locator('#workspace')).to_be_visible()
        await page.locator('#currentPassword').fill(PASSWORD)
        await page.locator('#confirmPassword').fill('different-password')
        await page.locator('#savePassword').click();await expect(page.locator('#passwordChangeError')).to_contain_text('不一致')
        await page.locator('#confirmPassword').fill(NEW_PASSWORD);await page.locator('#savePassword').click()
        await expect(page.locator('#loginScreen')).to_be_visible();await expect(page.locator('#loginStatus')).to_contain_text('密码已更新')
        assert (await other.request.get(BASE+'/api/me')).status==401
        assert (await other.request.post(BASE+'/api/login',data={'password':PASSWORD})).status==401
        await page.locator('#password').fill(NEW_PASSWORD);await page.locator('#loginBtn').click()
        await expect(page.locator('#workspace')).to_be_visible()
        await restart_checkpoint(2)
        await page.reload();await expect(page.locator('#workspace')).to_be_visible()
        assert (await other.request.post(BASE+'/api/login',data={'password':PASSWORD})).status==401
        assert (await other.request.post(BASE+'/api/login',data={'password':NEW_PASSWORD})).status==200
        await page.locator('#logoutBtn').click();await expect(page.locator('#loginScreen')).to_be_visible()
        await page.reload();await expect(page.locator('#loginScreen')).to_be_visible()
        assert not errors,errors
        await browser.close()
        print('PASS: password change revokes all sessions; old bootstrap password stays invalid after restart; logout and mobile layout correct',flush=True)


asyncio.run(main())
