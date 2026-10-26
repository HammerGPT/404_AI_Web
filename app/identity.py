"""Three-request screening using a pinned public bank; no reference API or network loader."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from .vendor.lmfpd._analysis import Scorer, minimum_numbers
from .vendor.lmfpd._challenge import generate_challenges
from .vendor.lmfpd._codec import decode_detector
from .vendor.lmfpd._features import parse_numbers
from .vendor.lmfpd._types import LmfpdError

ROOT = Path(__file__).parent / 'vendor/lmfpd'
BANK = json.loads((ROOT/'bank.json').read_text())
NOTICE = json.loads((ROOT/'NOTICE.json').read_text())
OUTPUT_LIMIT = 2048
DEFAULT_CHALLENGES = generate_challenges(3)


def enabled(cfg):
    if cfg['mode'] == 'fingerprint':
        return False
    selected = cfg.get('identity_enabled')
    return cfg['mode'] in ('standard', 'deep') if selected is None else selected


def canonical(model):
    # Separator aliases only; never infer an unknown version from a family prefix.
    return re.sub(r'[^a-z0-9]', '', model.lower().rsplit('/', 1)[-1])


def metadata():
    return {'source': 'LM Fingerpoint Detector · 公开参考库', 'source_url': NOTICE['url'],
            'revision': NOTICE['commit'], 'built_at': BANK['built_at'],
            'models': len(BANK['models']), 'requests': 3, 'output_limit': OUTPUT_LIMIT,
            'supported_models': [m['id'] for m in BANK['models']]}


@lru_cache(maxsize=1)
def scorer():
    raw = (ROOT/'shared_detector.bin').read_bytes()
    if hashlib.sha256(raw).hexdigest() != NOTICE['source_sha256']['data/shared_detector.bin']:
        raise ValueError('Public detector checksum mismatch')
    detector = decode_detector(raw)
    if detector['model_ids'] != [m['id'] for m in BANK['models']] or detector['bank_built_at'] != BANK['built_at']:
        raise ValueError('Public bank does not match detector')
    return Scorer(BANK, detector)


def cases(challenges=None):
    return [(f'identity_{i+1}_{c["expected_count"]}', f'数字指纹挑战 {i+1}', c['prompt'], OUTPUT_LIMIT)
            for i, c in enumerate(challenges or DEFAULT_CHALLENGES)]


def specification(case):
    kind, title, prompt, _ = case
    count = int(kind.rsplit('_', 1)[-1])
    return {'kind': kind, 'title': title, 'prompt': prompt, 'expected_count': count,
            'expected': f'请求约 {count} 个 1～355 的整数，至少 {minimum_numbers(count)} 个有效数字。单条只检查样本可用性，三条完成后与公开库比较。'}


def grade(item):
    count = len(parse_numbers(item.get('text', '')))
    item['valid_numbers'] = count
    if not item.get('response_complete') or item.get('finish_reason') in ('length', 'max_tokens', 'MAX_TOKENS', 'incomplete'):
        return 'warn', '回答未完整结束或达到输出上限，不用于身份判定；不会自动重试。'
    if count < minimum_numbers(item['expected_count']):
        return 'warn', f'只有 {count} 个有效数字，样本不足；不会自动补发请求。'
    return 'pass', f'取得 {count} 个有效数字；本条通过表示样本可用，整体身份初筛见报告上方。'


def analyze(cfg, samples, state):
    if not enabled(cfg):
        return None
    result = {'status': 'running' if state == 'running' else 'unknown',
              'title': '模型身份初筛中' if state == 'running' else '证据不足',
              'detail': '三次数字挑战完成后与本地公开参考库对照。',
              'reference': {k:v for k,v in metadata().items() if k != 'supported_models'},
              'candidates': [], 'round_candidates': [], 'claimed': cfg['model'],
              'limits': '仅为公开库内的相似性初筛；参考含第三方渠道样本，来源和采样条件可能不同，库外模型也可能相似，不证明具体版本、无掺水或真实采购来源。'}
    if state == 'running':
        return result
    probes = [s for s in samples if s['kind'].startswith('identity_')]
    if state != 'completed' or len(probes) != 3 or any(s.get('status') != 'pass' for s in probes):
        result['detail'] = f'完整可用的指纹回答为 {sum(s.get("status")=="pass" for s in probes)}/3；请查看失败或未执行原因。本次无法判定是否替换。'
        return result
    outputs = [{'text': s['text'], 'expected_count': s['expected_count']} for s in probes]
    try:
        detector = scorer()
        analysis = detector.analyze(outputs, strict=False)
        if analysis['decision'] != 'scored':
            result['detail'] = '数字样本不足或序列形态不符合参考库的适用条件，不能据此判定模型造假。'
            return result
        result['candidates'] = [{k:r[k] for k in ('model', 'display_name', 'family')} for r in analysis['results'][:3]]
        rounds = [detector.analyze([o], strict=False) for o in outputs]
        result['round_candidates'] = [r.get('prediction', '') for r in rounds]
    except (ValueError, KeyError, OSError, RuntimeError, LmfpdError):
        result['detail'] = '本地参考数据或评分暂不可用；其他检测结果仍可查看，不会追加上游请求。'
        return result
    claimed = next((m for m in BANK['models'] if canonical(m['id']) == canonical(cfg['model'])), None)
    result['covered'] = claimed is not None
    if claimed is None:
        result['detail'] = '公开库没有覆盖这个声明型号或渠道别名。候选只供参考，不强行判断通过或替换。'
        return result
    top = result['candidates'][0]
    families = [r.get('family_prediction') for r in rounds]
    consistent_family = all(f == top['family'] for f in families)
    consistent_model = all(m == top['model'] for m in result['round_candidates'])
    if not consistent_family:
        result['detail'] = '三份回答的候选家族不一致，可能是采样波动或路由差异；证据不足，不能据此认定混用。'
    elif top['model'] == claimed['id']:
        result.update(status='pass', title='与声明模型基本一致',
                      detail='整体最接近声明型号，三份回答的候选家族一致；仅表示本轮未发现明显替换线索。')
    elif top['family'] != claimed['family'] or consistent_model:
        result.update(status='suspect', title='疑似其他模型',
                      detail=f'整体更接近 {top["display_name"]}，且三份回答的候选'+('型号一致。' if consistent_model else '家族一致。')+'请复核渠道；不是模型造假的确定证明。')
    else:
        result['detail'] = '整体偏向同家族的其他候选，但逐次型号不一致；暂不能判断具体版本或降级。'
    returned = sorted({s['model_returned'] for s in samples if s.get('model_returned')})
    result['returned_models'] = returned
    if result['status'] == 'pass' and any(canonical(m) != canonical(cfg['model']) for m in returned):
        result.update(status='unknown', title='证据不足', detail='数字特征接近声明型号，但返回模型标识存在冲突；请先核实合法别名或路由。')
    return result
