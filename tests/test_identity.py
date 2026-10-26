import copy
import json
from pathlib import Path
import pytest
from app import identity, engine, runs, store
from app.reports import render_report

FIXTURE = json.loads((Path(__file__).parent/'fixtures/identity-analyze.json').read_text())


def config(model='claude-haiku-4-5'):
    return {'mode':'standard','model':model,'protocol':'openai_chat',
            'base_url':'https://example.com','key':'FAKE_IDENTITY_SECRET'}


def evidence():
    rows=[]
    for i,output in enumerate(FIXTURE['cases'][0]['outputs']):
        case=(f'identity_{i+1}_{output["expected_count"]}',f'数字指纹挑战 {i+1}','fixed fixture',2048)
        item={**identity.specification(case),'text':output['text'],'response_complete':True,
              'finish_reason':'stop','latency_ms':100,'usage':{},'model_returned':'claude-haiku-4-5'}
        item['status'],item['note']=identity.grade(item)
        rows.append(item)
    return rows


@pytest.mark.parametrize('case',FIXTURE['cases'],ids=lambda c:c['name'])
def test_matches_upstream_offline_fixture(case):
    actual=identity.scorer().analyze(case['outputs'],case['strict'])
    assert actual['decision']==case['expected']['decision']
    assert actual['prediction']==case['expected']['prediction']
    assert [r['model'] for r in actual['results'][:3]]==[r['model'] for r in case['expected']['results'][:3]]


def test_matching_public_reference_and_unknown_id():
    result=identity.analyze(config(),evidence(),'completed')
    assert result['status']=='pass',result
    assert result['reference']['models']==63
    assert 'probability' not in json.dumps(result)
    unknown=identity.analyze(config('unknown-custom-model'),evidence(),'completed')
    assert unknown['status']=='unknown' and unknown['covered'] is False
    assert unknown['candidates']


def test_substitution_does_not_trust_echoed_id():
    rows=evidence()
    for row in rows:row['model_returned']='gpt-5.6-sol'
    result=identity.analyze(config('gpt-5.6-sol'),rows,'completed')
    assert result['status']=='suspect' and result['candidates'][0]['family']=='claude'


def test_partial_capped_or_invalid_answers_are_not_identity_failure():
    assert identity.analyze(config(),evidence()[:2],'completed')['status']=='unknown'
    row=evidence()[0];row['finish_reason']='length'
    assert identity.grade(row)[0]=='warn'
    row['finish_reason']='stop';row['text']=' '.join(['1']*300)
    rows=[copy.deepcopy(row) for _ in range(3)]
    assert identity.analyze(config(),rows,'completed')['status']=='unknown'


@pytest.mark.asyncio
async def test_failing_challenge_stops_without_extra_calls(monkeypatch):
    called=[]
    async def run_one(cfg,protocol,case):
        called.append(case)
        return {**engine.case_spec(case),'status':'warn' if case[0].startswith('identity_') else 'pass',
                'latency_ms':1,'usage':{},'text':'invalid','note':'fixture'}
    monkeypatch.setattr(engine,'run_one',run_one)
    report=await engine.run_suite(config())
    assert len(called)==9 and report['stats']['planned']==11
    assert called[-1][3]==2048 and report['identity']['status']=='unknown'
    assert '自动追加' in report['stop_reason']


@pytest.mark.asyncio
async def test_challenges_are_frozen_in_plan_and_need_no_private_baseline(monkeypatch):
    async def suite(cfg,update,report_id,created_at):
        report=engine.make_report(cfg,[],report_id,created_at,state='completed')
        return report
    monkeypatch.setattr(runs,'run_suite',suite)
    manager=runs.RunManager()
    initial=await manager.start(config(),'identity-plan-fixture')
    await manager.task
    saved=store.get(initial['id'])
    assert saved['plan']==initial['plan']
    assert len(saved['plan'])==11 and store.list_fingerprint_baselines()==[]
    assert 'FAKE_IDENTITY_SECRET' not in json.dumps(saved)


def test_report_explains_public_evidence_and_keeps_original_cases():
    report=engine.make_report(config(),evidence(),'identity-report','2026-10-26',state='completed')
    html=render_report(report)
    assert '公开参考库' in html and 'claude-haiku-4.5' in html and '连接与协议' in html
    assert '未执行独立的指纹验货' not in html


@pytest.mark.parametrize('protocol,field',[('openai_chat','max_completion_tokens'),('openai_responses','max_output_tokens'),('anthropic','max_tokens'),('gemini','maxOutputTokens')])
def test_bounded_challenge_payloads_reuse_protocol_adapter(protocol,field):
    p=engine.build_payload(protocol,'gpt-6','identity_1_300','fixture',identity.OUTPUT_LIMIT)
    assert (p['generationConfig'] if protocol=='gemini' else p)[field]==2048
