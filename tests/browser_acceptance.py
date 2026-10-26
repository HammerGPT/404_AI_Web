"""Run only against tests/mock_server.py inside the isolated acceptance container."""
import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright, expect


async def main():
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_EXECUTABLE_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
        context=await browser.new_context(viewport={'width':1366,'height':768},accept_downloads=True)
        page=await context.new_page()
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        await page.goto('http://127.0.0.1:8000/')
        await page.locator('#password').fill('TEST_ONLY_PASSWORD_123456')
        await page.locator('#loginBtn').click()
        await expect(page.locator('.model-choice')).to_have_count(10)
        assert await page.locator('.model-choice img').evaluate_all('(images)=>images.every(i=>i.complete&&i.naturalWidth>0)')
        await page.locator('[data-select-model="claude-opus-5-5"]').click()
        await expect(page.locator('#protocolNote')).to_contain_text('Anthropic Messages')
        assert 'adaptive' in await page.locator('#reasoning option').evaluate_all('(xs)=>xs.map(x=>x.value)')
        await page.locator('#reasoning').select_option('high')
        await page.locator('#protocol').select_option('openai_chat')
        await expect(page.locator('#protocolNote')).to_contain_text('OpenAI Chat')
        await expect(page.locator('#reasoning')).to_have_value('auto')
        await expect(page.locator('#reasoning option')).to_have_count(1)
        await page.locator('[data-select-model="gemini-3.8-flash"]').click()
        await expect(page.locator('#protocolNote')).to_contain_text('Gemini 原生')
        assert await page.locator('#runBtn').evaluate('(el)=>el.getBoundingClientRect().bottom<=window.innerHeight')
        assert await page.locator('#scopeList').count()==0
        tip=page.get_by_role('button',name='API 地址说明')
        await tip.focus()
        await expect(tip).to_have_attribute('aria-expanded','true')
        await page.keyboard.press('Escape')
        await expect(tip).to_have_attribute('aria-expanded','false')
        assert await page.locator('.mode-grid input[name="mode"]').count()==3
        await expect(page.locator('input[value="fingerprint"]')).to_be_hidden()
        await page.screenshot(path='/testdata/model-selection.png',full_page=True)

        await page.locator('#navSettings').click()
        await page.locator('.billing-editor > summary').click()
        await page.locator('#billingOptionsInput').fill('0.5, 1, 2, 2.5')
        await page.locator('#billingOptionsInput').blur()
        await page.locator('#billingDefault').select_option('2.5')
        await page.locator('#saveBilling').click()
        await expect(page.locator('#billingNotice')).to_contain_text('已保存')
        await page.locator('#newPreset').click()
        await page.locator('#presetName').fill('验收模型（模拟）')
        await page.locator('#presetModel').fill('claude-haiku-4-5')
        await page.locator('#presetProtocol').select_option('openai_responses')
        await page.locator('#presetOrder').fill('-1')
        await page.locator('#presetReasoningOptions input[value="low"]').check()
        await page.locator('#presetReasoningOptions input[value="high"]').check()
        await page.locator('#presetReasoningDefault').select_option('high')
        await page.locator('#savePreset').click()
        await expect(page.locator('#presetEditor')).to_be_hidden()
        await expect(page.locator('#presetRows')).to_contain_text('claude-haiku-4-5')
        await page.screenshot(path='/testdata/model-settings.png',full_page=True)
        await page.locator('#navTest').click()
        await page.locator('[data-select-model="claude-haiku-4-5"]').click()
        await expect(page.locator('#protocolNote')).to_contain_text('OpenAI Responses')
        await expect(page.locator('#reasoning')).to_have_value('high')
        await expect(page.locator('#groupRatio')).to_have_value('2.5')
        await page.locator('[data-custom-multiplier]').click()
        await page.locator('#groupRatio').fill('3.25')
        await page.locator('[data-select-model="gpt-6-astra"]').click()
        await expect(page.locator('#groupRatio')).to_have_value('3.25')
        await expect(page.locator('#reasoning')).to_have_value('auto')
        await page.locator('[data-select-model="claude-haiku-4-5"]').click()
        await expect(page.locator('#reasoning')).to_have_value('high')
        await page.locator('[data-multiplier="2.5"]').click()
        await expect(page.locator('#groupRatio')).to_be_hidden()
        await expect(page.locator('#multiplierOptions [aria-pressed="true"]')).to_have_count(1)
        await page.locator('.mode-option:has(input[value="quick"])').click()
        await expect(page.locator('#reasoning')).to_be_disabled()
        await page.locator('.mode-option:has(input[value="standard"])').click()
        await expect(page.locator('#reasoning')).to_be_enabled()
        await expect(page.locator('#identityEnabled')).to_be_checked()
        await expect(page.locator('#identityNote')).to_contain_text('11 次请求')
        await page.locator('#identityEnabled').uncheck()
        await expect(page.locator('#identityNote')).to_contain_text('8 次请求')
        await page.locator('.mode-option:has(input[value="deep"])').click()
        await expect(page.locator('#identityEnabled')).not_to_be_checked()
        await expect(page.locator('#identityNote')).to_contain_text('16 次请求')
        await page.locator('.mode-option:has(input[value="standard"])').click()
        await page.locator('#identityEnabled').check()
        await page.locator('#baseUrl').fill('https://example.com/v1')
        await page.locator('#apiKey').fill('FAKE_BROWSER_KEY')
        await page.locator('#runBtn').click()
        await expect(page.locator('#runReport .report')).to_have_attribute('data-state','running')
        await page.locator('#runReport .evidence-case[data-case-status="pass"]').first.wait_for()
        report_id=await page.locator('#runReport .report').get_attribute('data-report-id')
        progress=page.locator('#runReport progress')
        assert int(await progress.get_attribute('value'))>=1
        assert await progress.get_attribute('max')=='11'
        assert await page.locator('#runReport .evidence-case[open]').count()==0
        await page.screenshot(path='/testdata/live-progress.png',full_page=True)
        await page.reload()
        await expect(page.locator('#runReport .report')).to_have_attribute('data-report-id',report_id)
        await expect(page.locator('#runReport .report')).to_have_attribute('data-state','completed',timeout=30000)
        calls=await (await context.request.get('http://127.0.0.1:8000/__test__/calls')).json()
        assert calls['count']==11, calls
        report=await (await context.request.get('http://127.0.0.1:8000/api/reports/'+report_id)).json()
        assert report['identity']['status']=='pass',report['identity']
        assert report['identity']['reference']['models']==63
        await expect(page.locator('#runReport .identity-result')).to_contain_text('与声明模型基本一致')
        assert report['stats']['counts']=={'pass':11,'warn':0,'error':0,'sample':0,'limited':0},report['stats']['counts']
        assert report['stats']['ttft_samples']==1 and report['stats']['tps_samples']==11
        assert report['settings']['billing_multiplier']==2.5 and report['stats']['estimated_cost_usd'] is None
        assert report['settings']['reasoning']=='high' and report['settings']['identity_enabled'] is True
        tool=next(s for s in report['samples'] if s['kind']=='tool')
        assert tool['request']['parameters']['tool_choice']=='auto'
        reasoning=next(s for s in report['samples'] if s['kind']=='reasoning')
        assert reasoning['request']['parameters']['reasoning']['effort']=='high'
        assert all('reasoning' not in s['request']['parameters'] for s in report['samples'] if s['kind']!='reasoning')
        assert 'FAKE_BROWSER_KEY' not in json.dumps(report)
        assert await page.locator('#apiKey').input_value()==''
        assert await page.locator('#runReport .case-overview').count()==0
        await page.screenshot(path='/testdata/report-summary.png',full_page=True)
        await page.locator('#runReport .evidence-case > summary').nth(4).click()
        await expect(page.locator('#runReport .evidence-case').nth(4)).to_have_attribute('open','')
        await expect(page.locator('#runReport .evidence-case').nth(4)).to_contain_text('centimeters')
        async with page.expect_download(timeout=60000) as pending:
            await page.locator('#runReport [data-download-pdf]').click()
        pdf=await pending.value
        await pdf.save_as('/testdata/report.pdf')
        assert Path('/testdata/report.pdf').read_bytes().startswith(b'%PDF-')
        await page.locator('#navHistory').click()
        await page.locator(f'[data-report="{report_id}"]').click()
        await expect(page.locator('#historyDetail .report')).to_have_attribute('data-report-id',report_id)
        calls=await (await context.request.get('http://127.0.0.1:8000/__test__/calls')).json()
        assert calls['count']==11
        await page.set_viewport_size({'width':390,'height':844})
        assert await page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        mobile_tip=page.locator('#historyDetail .report-metrics .help-trigger').first
        await mobile_tip.click()
        tip_box=await page.locator('#historyDetail .help.is-open .help-content').first.bounding_box()
        assert tip_box['x']>=0 and tip_box['x']+tip_box['width']<=390
        await page.keyboard.press('Escape')
        await expect(mobile_tip).to_have_attribute('aria-expanded','false')
        await page.screenshot(path='/testdata/mobile-report.png',full_page=True)
        await page.locator('#navSettings').click()
        await expect(page.locator('#settingsPanel')).to_be_visible()
        assert await page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        await page.reload()
        await page.locator('#navSettings').click()
        await expect(page.locator('#presetRows')).to_contain_text('claude-haiku-4-5')
        catalog=await (await context.request.get('http://127.0.0.1:8000/api/catalog')).json()
        assert catalog['billing']=={'options':[0.5,1,2,2.5],'default':2.5}
        preset=next(m for m in catalog['models'] if m['model_id']=='claude-haiku-4-5')
        assert preset['reasoning_default']=='high' and preset['reasoning_options']==['auto','low','high']
        await page.locator('#navTest').click()
        await page.locator('#newTest').click()
        await page.locator('.mode-option:has(input[value="quick"])').click()
        await page.locator('#identityEnabled').uncheck()
        await page.locator('#baseUrl').fill('https://budget.example.com/v1')
        await page.locator('#apiKey').fill('FAKE_BROWSER_KEY')
        await page.locator('#runBtn').click()
        await expect(page.locator('#runReport .report')).not_to_have_attribute('data-report-id',report_id)
        await expect(page.locator('#runReport .report')).to_have_attribute('data-state','completed',timeout=30000)
        disabled_id=await page.locator('#runReport .report').get_attribute('data-report-id')
        disabled_report=await (await context.request.get('http://127.0.0.1:8000/api/reports/'+disabled_id)).json()
        assert disabled_report['stats']['requests']==4 and 'identity' not in disabled_report
        assert disabled_report['settings']['identity_enabled'] is False
        assert disabled_report['stats']['counts']['limited']==1
        assert disabled_report['stats']['counts']['warn']==0
        await expect(page.locator('#runReport .status-pill.limited')).to_have_text('预算受限')
        await page.locator('#runReport .evidence-case[data-case-status="limited"] > summary').click()
        await expect(page.locator('#runReport .case-impact')).to_contain_text('不等于渠道或客户端不可用')
        await page.screenshot(path='/testdata/budget-report.png',full_page=True)
        async with page.expect_download(timeout=60000) as pending:
            await page.locator('#runReport [data-download-pdf]').click()
        await (await pending.value).save_as('/testdata/budget-report.pdf')
        assert (await (await context.request.get('http://127.0.0.1:8000/__test__/calls')).json())['count']==15
        assert not errors,errors
        Path('/testdata/report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        await browser.close()
        print('PASS: preset selection and protocol overrides, catalog edits, live progress, reload without replay, metrics/evidence, PDF download, history and mobile layout; no page errors')


asyncio.run(main())
