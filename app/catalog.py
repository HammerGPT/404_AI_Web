"""Shared protocol/brand metadata; model entries themselves live in SQLite."""
from urllib.parse import urlsplit

PROTOCOLS = {
    'openai_chat': 'OpenAI Chat Completions',
    'openai_responses': 'OpenAI Responses',
    'anthropic': 'Anthropic Messages',
    'gemini': 'Gemini 原生',
}
BRANDS = {
    'openai': 'OpenAI', 'anthropic': 'Claude', 'google': 'Gemini',
    'deepseek': 'DeepSeek', 'custom': '自定义',
}
PREFIX_RULES = [
    {'prefix': 'claude-', 'protocol': 'anthropic'},
    {'prefix': 'gemini-', 'protocol': 'gemini'},
]
FALLBACK_PROTOCOL = 'openai_chat'
REASONING_LABELS = {'auto':'模型默认','adaptive':'自适应','low':'低','medium':'中','high':'高'}
REASONING_BY_PROTOCOL = {
    'openai_chat':['auto','low','medium','high'],
    'openai_responses':['auto','low','medium','high'],
    'anthropic':['auto','adaptive','low','medium','high'],
    'gemini':['auto','low','medium','high'],
}


def validate_reasoning_profile(protocol, options, default):
    if not options or 'auto' not in options or len(options)!=len(set(options)):
        raise ValueError('思考选项必须包含模型默认，且不能重复')
    if any(value not in REASONING_BY_PROTOCOL[protocol] for value in options) or default not in options:
        raise ValueError('思考选项或默认值与协议不匹配')


def infer_protocol(model: str, base: str = '') -> str:
    for rule in PREFIX_RULES:
        if model.lower().startswith(rule['prefix']):
            return rule['protocol']
    if not model:
        host = urlsplit(base).hostname
        if host == 'api.anthropic.com':
            return 'anthropic'
        if host == 'generativelanguage.googleapis.com':
            return 'gemini'
    return FALLBACK_PROTOCOL


def metadata():
    return {'protocols': PROTOCOLS, 'brands': BRANDS, 'prefix_rules': PREFIX_RULES,
            'fallback_protocol': FALLBACK_PROTOCOL,'reasoning_labels':REASONING_LABELS,
            'reasoning_by_protocol':REASONING_BY_PROTOCOL}
