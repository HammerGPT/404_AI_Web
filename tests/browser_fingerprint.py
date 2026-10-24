"""Fingerprint acceptance against the isolated fake upstream; never run live."""
import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright, expect


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=os.getenv('CHROMIUM_EXECUTABLE_PATH', '/usr/bin/chromium'),
            headless=True, args=['--no-sandbox'])
        context = await browser.new_context(viewport={'width':1366, 'height':768}, accept_downloads=True)
        page = await context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto('http://127.0.0.1:8000/')
        await page.locator('#password').fill('TEST_ONLY_PASSWORD_123456')
        await page.locator('#loginBtn').click()
        await expect(page.locator('#runBtn')).to_be_enabled()
        await page.locator('[data-select-model="claude-opus-5-5"]').click()
        await page.locator('.mode-option:has(input[value="fingerprint"])').click()
        await expect(page.locator('#reasoning')).to_be_disabled()
        await expect(page.locator('#fingerprintPlanNote')).to_contain_text('99 次')
        await expect(page.locator('#fingerprintReferenceNote')).to_contain_text('尚无可用基线')
        await page.locator('#fingerprintAction').select_option('reference')
        await page.locator('#fingerprintName').fill('模拟可信基线（验收）')
        await page.locator('#fingerprintTrusted').check()
        await page.locator('#baseUrl').fill('https://reference.example.com')
        await page.locator('#apiKey').fill('FAKE_FINGERPRINT_BROWSER_KEY')
        await page.screenshot(path='/testdata/fingerprint-input.png', full_page=True)
        initial = (await (await context.request.get('http://127.0.0.1:8000/__test__/calls')).json())['count']

        async def run_and_wait():
            await page.locator('#runBtn').click()
            await expect(page.locator('#runReport .report')).to_have_attribute('data-state','running')
            rid = await page.locator('#runReport .report').get_attribute('data-report-id')
            await expect(page.locator('#runReport .report')).to_have_attribute('data-state','completed', timeout=60000)
            report = await (await context.request.get('http://127.0.0.1:8000/api/reports/'+rid)).json()
            assert report['stats']['requests'] == 99
            assert 'FAKE_FINGERPRINT_BROWSER_KEY' not in json.dumps(report)
            assert await page.locator('#apiKey').input_value() == ''
            assert await page.locator('#runReport .fingerprint-evidence .evidence-case').count() == 17
            assert '仅采样' not in await page.locator('#runReport').inner_text()
            return report

        reference_report = await run_and_wait()
        assert reference_report['fingerprint']['status'] == 'pass'
        reference_id = reference_report['fingerprint']['created_baseline']['id']
        catalog = await (await context.request.get('http://127.0.0.1:8000/api/fingerprints')).json()
        assert len(catalog['references']) == 1
        for host, status, check in [('relay','pass',None), ('replaced','fail','行为分布对照'), ('mixed','fail','重复指纹一致性')]:
            await page.locator('#newTest').click()
            await expect(page.locator('#fingerprintAction')).to_have_value('verify')
            await expect(page.locator('#fingerprintReference')).to_have_value(reference_id)
            await page.locator('#baseUrl').fill('https://'+host+'.example.com')
            await page.locator('#apiKey').fill('FAKE_FINGERPRINT_BROWSER_KEY')
            report = await run_and_wait()
            assert report['fingerprint']['status'] == status, report['fingerprint']
            assert {s['model_returned'] for s in report['samples']} == {'claude-opus-5-5'}
            if check:
                assert next(c for c in report['fingerprint']['checks'] if c['name']==check)['status']=='fail'
            Path('/testdata/fingerprint-'+host+'.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            await page.screenshot(path='/testdata/fingerprint-'+host+'.png', full_page=True)
            if host == 'replaced':
                async with page.expect_download(timeout=60000) as pending:
                    await page.locator('#runReport [data-download-pdf]').click()
                pdf = await pending.value
                await pdf.save_as('/testdata/fingerprint-report.pdf')
                assert Path('/testdata/fingerprint-report.pdf').read_bytes().startswith(b'%PDF-')
                await page.set_viewport_size({'width':390,'height':844})
                assert await page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                await page.screenshot(path='/testdata/fingerprint-mobile.png',full_page=True)
                await page.set_viewport_size({'width':1366,'height':768})
        calls = (await (await context.request.get('http://127.0.0.1:8000/__test__/calls')).json())['count']
        assert calls-initial == 396, calls-initial
        await page.locator('#navSettings').click()
        await page.locator('.fingerprint-library > summary').click()
        async with page.expect_download() as pending:
            await page.locator('[data-export-fingerprint]').click()
        reference_export = await pending.value
        await reference_export.save_as('/testdata/fingerprint-reference.json')
        assert 'FAKE_FINGERPRINT_BROWSER_KEY' not in Path('/testdata/fingerprint-reference.json').read_text()
        await page.locator('[data-toggle-fingerprint]').click()
        await expect(page.locator('#fingerprintLibraryNotice')).to_contain_text('已保存')
        await expect(page.locator('#fingerprintRows .status-pill')).to_have_text('停用')
        await page.locator('#navTest').click()
        await page.locator('#newTest').click()
        await expect(page.locator('#fingerprintReference')).to_have_value('')
        await page.set_viewport_size({'width':390,'height':844})
        assert await page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        await page.screenshot(path='/testdata/fingerprint-mobile-input.png',full_page=True)
        assert not errors, errors
        await browser.close()
        print('PASS: 396 fake calls; capture, matched channel, forged-ID substitution, mixing, PDF, baseline export/disable, mobile layout; no page errors')


asyncio.run(main())
