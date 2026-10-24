"""Protocol payloads and response normalization shared by all test cases."""
from __future__ import annotations
import json
import math

TEXT_LIMIT = 12000
TOOL = {"name": "convert_length", "description": "Convert centimeters to millimeters.",
        "parameters": {"type": "object", "properties": {"centimeters": {"type": "number"}}, "required": ["centimeters"]}}


def token_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if math.isfinite(value) and 0 <= value <= 10**12 and value == int(value) else None


def normalize_usage(raw: dict, protocol: str) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    result = dict.fromkeys(('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','reasoning_tokens'))
    def read(source, key):
        return token_number(source.get(key)) if isinstance(source, dict) else None
    if protocol == 'anthropic':
        base = read(raw, 'input_tokens')
        result.update(output_tokens=read(raw,'output_tokens'), cache_read_tokens=read(raw,'cache_read_input_tokens'),
                      cache_write_tokens=read(raw,'cache_creation_input_tokens'),
                      reasoning_tokens=read(raw.get('output_tokens_details'),'thinking_tokens'))
        if base is not None:
            result['input_tokens'] = base + (result['cache_read_tokens'] or 0) + (result['cache_write_tokens'] or 0)
    elif protocol == 'gemini':
        visible = read(raw,'candidatesTokenCount')
        reasoning = read(raw,'thoughtsTokenCount')
        result.update(input_tokens=read(raw,'promptTokenCount'),
                      output_tokens=visible + (reasoning or 0) if visible is not None else None,
                      cache_read_tokens=read(raw,'cachedContentTokenCount'), reasoning_tokens=reasoning)
    else:
        responses = protocol == 'openai_responses'
        details = raw.get('input_tokens_details' if responses else 'prompt_tokens_details')
        output = raw.get('output_tokens_details' if responses else 'completion_tokens_details')
        result.update(input_tokens=read(raw,'input_tokens' if responses else 'prompt_tokens'),
                      output_tokens=read(raw,'output_tokens' if responses else 'completion_tokens'),
                      cache_read_tokens=read(details,'cached_tokens'), cache_write_tokens=read(details,'cache_write_tokens'),
                      reasoning_tokens=read(output,'reasoning_tokens'))
    result['reported'] = any(v is not None for v in result.values())
    result['complete'] = result['input_tokens'] is not None and result['output_tokens'] is not None
    return result


def build_payload(protocol: str, model: str, kind: str, prompt: str, token_max: int, reasoning: str = 'auto', token_field='auto') -> dict:
    if kind == 'reasoning' and protocol == 'anthropic':
        token_max = max(token_max, 1600)
    elif kind == 'reasoning' and protocol in ('gemini','openai_chat','openai_responses'):
        token_max = max(token_max, 800)
    if protocol == 'openai_chat':
        field = ('max_completion_tokens' if model.lower().startswith(('o1', 'o3', 'o4', 'gpt-5', 'gpt-6')) else 'max_tokens') if token_field == 'auto' else token_field
        payload = {'model': model, 'messages': [{'role': 'user', 'content': prompt}], field: token_max}
        if kind in ('stream', 'stream2'):
            payload['stream'] = True
            payload['stream_options'] = {'include_usage': True}
        if kind in ('tool', 'tool2'):
            payload['tools'] = [{'type': 'function', 'function': TOOL}]
            payload['tool_choice'] = {'type': 'function', 'function': {'name': 'convert_length'}}
        if kind == 'reasoning' and reasoning in ('low','medium','high','on'):
            payload['reasoning_effort'] = 'low' if reasoning=='on' else reasoning
        return payload
    if protocol == 'openai_responses':
        payload = {'model': model, 'input': prompt, 'max_output_tokens': token_max, 'store': False}
        if kind in ('stream', 'stream2'):
            payload['stream'] = True
        if kind in ('tool', 'tool2'):
            payload['tools'] = [{'type': 'function', **TOOL}]
            payload['tool_choice'] = {'type': 'function', 'name': 'convert_length'}
        if kind == 'reasoning' and reasoning in ('low','medium','high','on'):
            payload['reasoning'] = {'effort': 'low' if reasoning=='on' else reasoning, 'summary': 'auto'}
        return payload
    if protocol == 'anthropic':
        payload = {'model': model, 'max_tokens': token_max, 'messages': [{'role': 'user', 'content': prompt}]}
        if kind in ('stream', 'stream2'):
            payload['stream'] = True
        if kind in ('tool', 'tool2'):
            payload['tools'] = [{'name': TOOL['name'], 'description': TOOL['description'], 'input_schema': TOOL['parameters']}]
            payload['tool_choice'] = {'type': 'tool', 'name': 'convert_length'}
        if kind == 'reasoning' and reasoning in ('on','adaptive','low','medium','high'):
            payload['thinking'] = {'type': 'adaptive'}
            if reasoning in ('low','medium','high'):
                payload['output_config']={'effort':reasoning}
        return payload
    if protocol == 'gemini':
        payload = {'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
                   'generationConfig': {'maxOutputTokens': token_max}}
        if kind in ('tool', 'tool2'):
            payload['tools'] = [{'functionDeclarations': [{'name': TOOL['name'], 'description': TOOL['description'], 'parameters': TOOL['parameters']}]}]
            payload['toolConfig'] = {'functionCallingConfig': {'mode': 'ANY', 'allowedFunctionNames': ['convert_length']}}
        if kind == 'reasoning' and reasoning in ('low','medium','high','on'):
            payload['generationConfig']['thinkingConfig'] = {'includeThoughts': True}
            if reasoning != 'on':
                payload['generationConfig']['thinkingConfig']['thinkingLevel']=reasoning
        return payload
    raise ValueError('Unsupported API')


def text_value(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError('Expected text')
    return value


def tool_call(name, arguments):
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = None
    truncated = len(json.dumps(arguments,ensure_ascii=False)) > TEXT_LIMIT
    return {'name': text_value(name)[:128], 'arguments': arguments if isinstance(arguments, dict) and not truncated else None,
            'arguments_truncated': truncated}


def parse_result(data: dict, protocol: str, kind: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError('Expected response object')
    text, reasoning, calls = '', '', []
    thinking = False
    model = data.get('model') or data.get('modelVersion')
    raw_usage = data.get('usage') or data.get('usageMetadata') or {}
    finish = None
    if protocol == 'openai_chat':
        choice = (data.get('choices') or [{}])[0]
        message = choice.get('message') or {}
        content = message.get('content') or ''
        if isinstance(content, list):
            text = ''.join(text_value(p.get('text')) for p in content if p.get('type') == 'text')
        else:
            text = text_value(content)
        reasoning = text_value(message.get('reasoning_content') or message.get('reasoning'))
        calls = [tool_call(c.get('function', {}).get('name'), c.get('function', {}).get('arguments')) for c in message.get('tool_calls') or []]
        finish = choice.get('finish_reason')
    elif protocol == 'openai_responses':
        finish = data.get('status')
        for item in data.get('output') or []:
            if item.get('type') == 'function_call':
                calls.append(tool_call(item.get('name'), item.get('arguments')))
            if item.get('type') == 'reasoning':
                thinking = True
                reasoning += ''.join(text_value(p.get('text')) for p in item.get('summary') or [])
            text += ''.join(text_value(p.get('text')) for p in item.get('content') or [] if p.get('type') == 'output_text')
        text = text or text_value(data.get('output_text'))
    elif protocol == 'anthropic':
        finish = data.get('stop_reason')
        for block in data.get('content') or []:
            if block.get('type') == 'text':
                text += text_value(block.get('text'))
            elif block.get('type') == 'tool_use':
                calls.append(tool_call(block.get('name'), block.get('input')))
            elif block.get('type') in ('thinking', 'redacted_thinking'):
                thinking = True
                reasoning += text_value(block.get('thinking'))
    elif protocol == 'gemini':
        for candidate in data.get('candidates') or []:
            finish = candidate.get('finishReason') or finish
            for part in (candidate.get('content') or {}).get('parts') or []:
                if part.get('functionCall'):
                    call = part['functionCall']
                    calls.append(tool_call(call.get('name'), call.get('args')))
                if part.get('thought'):
                    thinking = True
                    reasoning += text_value(part.get('text'))
                else:
                    text += text_value(part.get('text'))
    usage = normalize_usage(raw_usage, protocol)
    return {'text': text[:TEXT_LIMIT], 'text_truncated': len(text) > TEXT_LIMIT,
            'reasoning_text': reasoning[:TEXT_LIMIT], 'reasoning_truncated': len(reasoning) > TEXT_LIMIT,
            'model_returned': text_value(model) or None,
            'thinking_observed': thinking or bool(reasoning) or bool(usage.get('reasoning_tokens')),
            'tool_observed': bool(calls), 'tool_calls': calls[:8], 'tool_calls_truncated':len(calls)>8, 'usage': usage, 'finish_reason': finish,
            'response_complete': True}


class StreamResult:
    """Parse protocol events without mistaking event count for token count."""
    def __init__(self, protocol):
        self.protocol = protocol
        self.text = ''
        self.reasoning = ''
        self.model = None
        self.usage = {}
        self.complete = False
        self.finish = None
        self.first_content = None
        self.first_reasoning = None
        self.events = 0
        self.invalid_events = 0
        self.error = False

    def feed(self, data, elapsed):
        if data == '[DONE]':
            if self.protocol == 'openai_chat':
                self.complete = True
            return
        try:
            ev = json.loads(data)
            if not isinstance(ev, dict):
                raise ValueError('Expected event object')
            self._event(ev, elapsed)
            self.events += 1
        except (ValueError, TypeError, IndexError, KeyError, AttributeError):
            self.invalid_events += 1

    def _event(self, ev, elapsed):
        piece, thought = '', ''
        if ev.get('error') or ev.get('type') in ('error','response.failed','response.incomplete'):
            self.error = True
            return
        if self.protocol == 'openai_chat':
            self.model = ev.get('model') or self.model
            self.usage.update(ev.get('usage') or {})
            for choice in ev.get('choices') or []:
                delta = choice.get('delta') or {}
                piece += text_value(delta.get('content'))
                thought += text_value(delta.get('reasoning_content') or delta.get('reasoning'))
                if choice.get('finish_reason'):
                    self.finish = choice['finish_reason']
                    self.complete = True
        elif self.protocol == 'openai_responses':
            self.model = (ev.get('response') or {}).get('model') or self.model
            if ev.get('type') == 'response.output_text.delta':
                piece = text_value(ev.get('delta'))
            if ev.get('type') in ('response.reasoning_summary_text.delta','response.reasoning_text.delta'):
                thought = text_value(ev.get('delta'))
            if ev.get('type') == 'response.completed':
                self.usage.update((ev.get('response') or {}).get('usage') or {})
                self.complete = True
                self.finish = 'completed'
        elif self.protocol == 'anthropic':
            message = ev.get('message') or {}
            self.model = message.get('model') or self.model
            self.usage.update(message.get('usage') or {})
            self.usage.update(ev.get('usage') or {})
            delta = ev.get('delta') or {}
            if ev.get('type') == 'content_block_delta':
                piece = text_value(delta.get('text'))
                thought = text_value(delta.get('thinking'))
            self.finish = delta.get('stop_reason') or self.finish
            if ev.get('type') == 'message_stop':
                self.complete = True
        elif self.protocol == 'gemini':
            self.model = ev.get('modelVersion') or self.model
            self.usage.update(ev.get('usageMetadata') or {})
            for candidate in ev.get('candidates') or []:
                if candidate.get('finishReason'):
                    self.finish = candidate['finishReason']
                    self.complete = True
                for part in (candidate.get('content') or {}).get('parts') or []:
                    if part.get('thought'):
                        thought += text_value(part.get('text'))
                    else:
                        piece += text_value(part.get('text'))
        if piece and self.first_content is None:
            self.first_content = elapsed
        if thought and self.first_reasoning is None:
            self.first_reasoning = elapsed
        # Transport caps the whole stream at 1 MB. Redact before truncating later.
        self.text += piece
        self.reasoning += thought

    def result(self, redact):
        text, reasoning = redact(self.text), redact(self.reasoning)
        return {'text': text[:TEXT_LIMIT], 'text_truncated': len(text) > TEXT_LIMIT,
                'reasoning_text': reasoning[:TEXT_LIMIT], 'reasoning_truncated': len(reasoning) > TEXT_LIMIT,
                'model_returned': redact(self.model) if isinstance(self.model,str) else None,
                'thinking_observed': bool(self.reasoning) or bool(normalize_usage(self.usage,self.protocol).get('reasoning_tokens')),
                'tool_observed': False, 'tool_calls': [], 'usage': normalize_usage(self.usage,self.protocol),
                'response_complete': self.complete, 'finish_reason': self.finish,
                'ttft_ms': round(self.first_content * 1000, 2) if self.first_content is not None else None,
                'reasoning_ttft_ms': round(self.first_reasoning * 1000, 2) if self.first_reasoning is not None else None,
                'stream': {'events': self.events, 'invalid_events': self.invalid_events, 'completed': self.complete,
                           'content_received': bool(self.text), 'usage_received': bool(self.usage)}}

