"""Versioned black-box reference comparisons, not model identity attestation.

Token deltas, categorical response distributions and scored capability probes
are independent evidence channels. P-values describe a two-sample test, never
the probability that a model is genuine. No keys or custom headers are stored.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
import random
import unicodedata
from urllib.parse import urlsplit

PROFILE=json.loads((Path(__file__).parent/'data/fingerprint-profile.json').read_text())
PROFILE_HASH=hashlib.sha256(json.dumps(PROFILE,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
OFFICIAL_HOSTS={'api.openai.com','api.anthropic.com','generativelanguage.googleapis.com','api.deepseek.com'}


def plan():
    result=[]
    rounds=max(PROFILE['token_rounds'],PROFILE['behavior_repeats'],PROFILE['capability_repeats'])
    for repeat in range(rounds):
        for group,limit_name,repeats_name in [('token','token_output_limit','token_rounds'),
                                             ('behavior','behavior_output_limit','behavior_repeats'),
                                             ('capability','capability_output_limit','capability_repeats')]:
            if repeat>=PROFILE[repeats_name]:continue
            for probe in PROFILE[group+'_probes']:
                kind=f'fp_{group}_{probe["id"]}_{repeat+1}'
                result.append({'kind':kind,'title':probe['title'],'group':group,'probe_id':probe['id'],
                               'repeat':repeat+1,'prompt':probe['prompt'],'limit':PROFILE[limit_name],
                               'probe':probe})
    return result


PLAN=plan()
BY_KIND={p['kind']:p for p in PLAN}


def cases():
    return [(p['kind'],f"{p['title']} · {p['repeat']}",p['prompt'],p['limit']) for p in PLAN]


def specification(kind):
    item=BY_KIND[kind]
    expected={'token':'取得非零输入 Token 用量，以同轮短文本为基准计算差分；不根据本条回复内容认证身份。',
              'behavior':'回答符合限定选项；整体分布将在全部请求完成后与可信基线比较。',
              'capability':'回答符合给定任务的确定答案。'}[item['group']]
    if item['group']=='capability':expected+=' 预期：'+json.dumps(item['probe']['expected'],ensure_ascii=False)
    return {'kind':kind,'title':f"{item['title']} · {item['repeat']}",'prompt':item['prompt'],'expected':expected,
            'fingerprint_group':item['group'],'probe_id':item['probe_id'],'repeat':item['repeat']}


def equivalent(expected,actual):
    if isinstance(expected,bool):return isinstance(actual,bool) and actual==expected
    if isinstance(expected,(int,float)):
        return isinstance(actual,(int,float)) and not isinstance(actual,bool) and math.isfinite(actual) and actual==expected
    if isinstance(expected,list):
        return isinstance(actual,list) and len(actual)==len(expected) and all(equivalent(a,b) for a,b in zip(expected,actual))
    if isinstance(expected,dict):
        return isinstance(actual,dict) and set(actual)==set(expected) and all(equivalent(v,actual[k]) for k,v in expected.items())
    return type(actual) is type(expected) and actual==expected


def normalized_answer(probe,text):
    answer=unicodedata.normalize('NFKC',text).strip().strip('"\'`。.!！').strip().casefold()
    if probe['id'].startswith('digit') and answer in '零一二三四五六七八九' and len(answer)==1:
        answer=str('零一二三四五六七八九'.index(answer))
    return answer if answer in probe['answers'] else None


def grade(kind,item):
    spec=BY_KIND[kind];group=spec['group']
    measurement={'group':group,'probe_id':spec['probe_id'],'repeat':spec['repeat'],'value':None}
    item['fingerprint_measurement']=measurement
    if group=='token':
        count=item.get('usage',{}).get('input_tokens')
        if count is None or count<=0:return 'warn','未取得有效输入 Token 用量，无法用于词元指纹核验。'
        measurement['value']=count
        return 'pass',f'已取得输入计数 {count}；完成后进行差分和重复一致性核验。'
    if not item.get('response_complete') or item.get('finish_reason') in ('length','max_tokens','MAX_TOKENS','incomplete'):
        return 'warn','响应不完整或达到输出上限，本条不能用于可靠对照。'
    if group=='behavior':
        measurement['value']=normalized_answer(spec['probe'],item.get('text',''))
        return ('pass','回答符合探针格式，将参与整体分布对照。') if measurement['value'] is not None else ('warn','未按限定选项回答，本条不计入有效分布。')
    try:answer=json.loads(item.get('text','').strip())
    except ValueError:answer=None
    measurement['value']=equivalent(spec['probe']['expected'],answer)
    return ('pass','任务答案与预期一致。') if measurement['value'] else ('warn','任务答案或格式与预期不一致。')


def conditions(cfg):
    protocol=cfg['protocol']
    field=cfg.get('token_field','auto')
    if protocol=='openai_chat' and field=='auto':
        field='max_completion_tokens' if cfg['model'].lower().startswith(('o1','o3','o4','gpt-5','gpt-6')) else 'max_tokens'
    if protocol!='openai_chat':field='native'
    return {'profile':PROFILE['id'],'profile_hash':PROFILE_HASH,'protocol':protocol,'token_field':field,
            'sampling':'upstream-default','token_output_limit':PROFILE['token_output_limit'],
            'behavior_output_limit':PROFILE['behavior_output_limit'],'capability_output_limit':PROFILE['capability_output_limit']}


def extract(samples):
    measurements={s['kind']:s for s in samples}
    token_rows=[];vectors=[]
    for repeat in range(1,PROFILE['token_rounds']+1):
        counts={}
        for probe in PROFILE['token_probes']:
            sample=measurements.get(f'fp_token_{probe["id"]}_{repeat}',{})
            value=(sample.get('fingerprint_measurement') or {}).get('value')
            counts[probe['id']]=value if sample.get('status')=='pass' else None
        token_rows.append(counts)
        if all(v is not None for v in counts.values()):
            vectors.append({k:v-counts['anchor'] for k,v in counts.items() if k!='anchor'})
    behavior={};capabilities={}
    for group,destination,repeats in [('behavior',behavior,PROFILE['behavior_repeats']),('capability',capabilities,PROFILE['capability_repeats'])]:
        for probe in PROFILE[group+'_probes']:
            values=[];invalid=0
            for repeat in range(1,repeats+1):
                sample=measurements.get(f'fp_{group}_{probe["id"]}_{repeat}',{})
                value=(sample.get('fingerprint_measurement') or {}).get('value')
                if value is None:invalid+=1
                else:values.append(value)
            destination[probe['id']]={'values':values,'invalid':invalid}
    stable=len(vectors)==PROFILE['token_rounds'] and all(v==vectors[0] for v in vectors)
    ready=(stable and all(len(p['values'])>=PROFILE['minimum_behavior_samples'] for p in behavior.values())
           and all(len(p['values'])==PROFILE['capability_repeats'] for p in capabilities.values()))
    return {'token_rows':token_rows,'token_vectors':vectors,'token_stable':stable,
            'behavior':behavior,'capabilities':capabilities,'ready':ready,
            'returned_models':sorted({s['model_returned'] for s in samples if s.get('model_returned')})}


def js_divergence(left,right):
    a,b=Counter(left),Counter(right);na,nb=len(left),len(right)
    if not na or not nb:return None
    score=0.0
    for key in a.keys()|b.keys():
        p,q=a[key]/na,b[key]/nb;m=(p+q)/2
        if p:score+=p*math.log2(p/m)/2
        if q:score+=q*math.log2(q/m)/2
    return score


def permutation_test(reference,target,kind):
    """Stratified label permutations keep each probe's answer space separate."""
    pairs=[(reference[key]['values'],target[key]['values']) for key in reference]
    if kind=='behavior':
        statistic=lambda pairs:sum(js_divergence(a,b) for a,b in pairs)/len(pairs)
    else:
        statistic=lambda pairs:sum(sum(a)/len(a)-sum(b)/len(b) for a,b in pairs)/len(pairs)
    observed=statistic(pairs)
    if observed<=0:return {'effect':round(observed,6),'p_value':1.0,'permutations':PROFILE['permutations']}
    seed=hashlib.sha256(json.dumps([reference,target,kind],sort_keys=True).encode()).digest()
    rng=random.Random(seed);at_least=0
    for _ in range(PROFILE['permutations']):
        shuffled=[]
        for a,b in pairs:
            pool=list(a)+list(b);rng.shuffle(pool);shuffled.append((pool[:len(a)],pool[len(a):]))
        if statistic(shuffled)>=observed-1e-12:at_least+=1
    return {'effect':round(observed,6),'p_value':round((at_least+1)/(PROFILE['permutations']+1),6),
            'permutations':PROFILE['permutations']}


def analyze(cfg,samples,state):
    if cfg.get('mode')!='fingerprint':return None
    measured=extract(samples)
    result={'method':PROFILE['id'],'conditions':conditions(cfg),'measurement':measured,
            'action':cfg.get('fingerprint_action','verify'),'checks':[], 'status':'running' if state=='running' else 'unknown',
            'title':'正在建立指纹测量记录' if state=='running' else '指纹核验未完成',
            'limits':['通过只表示本次所测特征未偏离参考，不是模型身份或无掺水的证明。',
                      '输入用量仍来自待测服务；同词元化模型、低比例混用、缓存或有意模拟可能漏检。',
                      '固定探针可能被识别并特殊路由；相似度和检验 p 值都不是模型真实概率。',
                      '采样参数/隐藏提示词/模型版本更新均可能造成差异；需结合证据复核。']}
    if state=='running':return result
    if state!='completed':
        result['detail']='检测中断，已有结果保留，不能作完整指纹结论。';return result
    if cfg.get('fingerprint_action')=='reference':
        qualified=sum(len(p['values'])==PROFILE['capability_repeats'] and all(p['values']) for p in measured['capabilities'].values())
        eligible=len(samples)==len(PLAN) and measured['ready'] and qualified>=PROFILE['minimum_reference_capabilities'] and len(measured['returned_models'])<=1
        reasons=[]
        if len(samples)!=len(PLAN):reasons.append(f'计划 {len(PLAN)} 次请求，仅完成 {len(samples)} 次')
        if not measured['token_stable']:reasons.append('输入 Token 差分未能形成稳定的三轮结果')
        for key,value in measured['behavior'].items():
            if len(value['values'])<PROFILE['minimum_behavior_samples']:reasons.append(f'{key} 有效回答仅 {len(value["values"])} 条')
        if not all(len(p['values'])==PROFILE['capability_repeats'] for p in measured['capabilities'].values()):reasons.append('部分能力请求不完整')
        if qualified<PROFILE['minimum_reference_capabilities']:reasons.append(f'稳定通过的能力项仅 {qualified} 项，至少需要 {PROFILE["minimum_reference_capabilities"]} 项')
        if len(measured['returned_models'])>1:reasons.append('参考自身回传多个模型标识')
        result.update(status='pass' if eligible else 'fail',
                      title='参考指纹采集完成' if eligible else '参考指纹建立失败',
                      detail='参考的可信性由采集来源决定；请核实独立来源。' if eligible else '；'.join(reasons)+'。未保存为可用基线。')
        return result
    reference=cfg.get('_fingerprint_reference')
    if not reference:
        result['detail']='没有可用的可信参考，请先建立同型号、同协议的基线。';return result
    result['reference']={k:reference[k] for k in ('id','name','model','protocol','source_host','source_kind','created_at')}
    result['same_source']=reference['source_host']==urlsplit(cfg['base_url']).hostname
    ref=reference['measurement']
    result['reference_measurement']=ref
    if not measured['ready']:
        result.update(status='fail',title='指纹核验失败：测量条件不满足',detail='用量缺失、重复指纹不稳定或有效样本不足。无法据此断言换模型；查看分项原因。')
    checks=result['checks']
    vectors=measured['token_vectors']
    token_status='pass' if measured['token_stable'] and vectors[0]==ref['token_vectors'][0] else 'fail'
    checks.append({'name':'词元差分指纹','status':token_status,'detail':'英文、中文、代码、Unicode 四组差分计数与参考一致。' if token_status=='pass' else '差分计数与参考不同或未得到稳定计数，可能涉及词元化、模板、路由或用量改写。',
                   'expected':ref['token_vectors'][0],'observed':vectors})
    checks.append({'name':'重复指纹一致性','status':'pass' if measured['token_stable'] else 'fail',
                   'detail':'重复测量稳定。' if measured['token_stable'] else '同一输入的重复计数发生变化：存在路由混用、模板变动或计量不稳定线索。'})
    enough=all(len(v['values'])>=PROFILE['minimum_behavior_samples'] for v in measured['behavior'].values())
    if enough:
        test=permutation_test(ref['behavior'],measured['behavior'],'behavior')
        test['alpha']=PROFILE['alpha']/2
        different=test['p_value']<=test['alpha']
        checks.append({'name':'行为分布对照','status':'fail' if different else 'pass','statistics':test,
                       'detail':'与参考存在显著分布差异，需要核实模型或采样条件。' if different else '本轮未检出显著分布差异；不代表已证明等同。'})
    else:checks.append({'name':'行为分布对照','status':'fail','detail':'有效回答不足，不能执行分布检验。'})
    cap_complete=all(len(v['values'])==PROFILE['capability_repeats'] for v in measured['capabilities'].values())
    if cap_complete:
        test=permutation_test(ref['capabilities'],measured['capabilities'],'capability')
        test['alpha']=PROFILE['alpha']/2
        regressions=[key for key in ref['capabilities'] if sum(measured['capabilities'][key]['values'])<sum(ref['capabilities'][key]['values'])]
        checks.append({'name':'能力基线对照','status':'fail' if regressions else 'pass','statistics':test,
                       'regressions':regressions,'detail':('本轮部分任务通过次数少于参考。'+('差异达到预设统计阈值。' if test['p_value']<=test['alpha'] else '样本较少，不足以归因为模型降级。')) if regressions else '本轮任务表现未低于参考基线。'})
    else:checks.append({'name':'能力基线对照','status':'fail','detail':'能力请求不完整，不能完成对照。'})
    accepted={cfg['model'],reference['model'],*ref['returned_models']}
    unexpected=[m for m in measured['returned_models'] if m not in accepted]
    checks.append({'name':'返回模型标识','status':'fail' if unexpected else 'pass' if measured['returned_models'] else 'unknown',
                   'detail':'返回标识与请求/参考不一致，请核实合法别名或路由。' if unexpected else '返回字段未见冲突；自报标识不能作为身份证明。' if measured['returned_models'] else '上游未返回模型标识。',
                   'observed':measured['returned_models']})
    failed=[c['name'] for c in checks if c['status']=='fail']
    result.update(status='fail' if failed else 'pass',title='指纹核验失败，存在可疑差异' if failed else '指纹核验通过，未发现本轮替换证据',
                  detail='需复核：'+'、'.join(failed)+'。异常不能单独证明模型造假。' if failed else '词元、行为分布和能力表现符合本次参考；仍不能排除同特征模型或低比例混用。')
    if len(samples)!=len(PLAN) or len(vectors)<PROFILE['token_rounds'] or not enough or not cap_complete:
        result.update(status='fail',title='指纹核验失败：可比较数据不足',
                      detail='本次没有取得足够完整的数据，不能判断是否掺水。请查看缺失用量、无效回答和执行错误。')
    if result['same_source']:
        result['limits'].insert(0,'参考与待测使用同一主机：只能说明相对历史的一致性，不能独立证明该来源的模型身份。')
        if result['status']=='pass':result['title']='同源历史一致性通过，缺少独立身份对照'
    return result


def baseline_document(cfg,report):
    fp=report.get('fingerprint') or {}
    if fp.get('status')!='pass' or not fp.get('measurement',{}).get('ready'):return None
    return {'id':'fp-'+report['id'],'name':cfg['fingerprint_name'],'model':cfg['model'],'protocol':cfg['protocol'],
            'source_host':report['base_host'],'source_kind':cfg['fingerprint_source'],
            'created_at':report['finished_at'],'report_id':report['id'],'conditions':fp['conditions'],
            'measurement':fp['measurement']}


def validate_reference(reference,cfg):
    if reference['conditions']!=conditions(cfg):raise ValueError('参考的协议、输出参数或探针版本与当前配置不一致，请重新建立或选择匹配基线。')
    if datetime.now(timezone.utc)-datetime.fromisoformat(reference['created_at'])>timedelta(days=PROFILE['max_reference_age_days']):
        raise ValueError('参考指纹已超过有效复核期，请重新采集后再核验。')
    if not reference.get('measurement',{}).get('ready'):raise ValueError('参考数据不完整，不能用于核验。')


def catalog():
    return {'profile':PROFILE['id'],'profile_hash':PROFILE_HASH,'requests':len(PLAN),
            'max_output_tokens':sum(p['limit'] for p in PLAN),'reference_age_days':PROFILE['max_reference_age_days'],
            'behavior_repeats':PROFILE['behavior_repeats'],'behavior_probes':len(PROFILE['behavior_probes']),
            'alpha':PROFILE['alpha']}
