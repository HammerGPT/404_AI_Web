"""Endpoint validation and DNS-pinned transport to reduce SSRF and DNS rebinding."""
from __future__ import annotations
import ipaddress
import os
import socket
from urllib.parse import urlsplit, urlunsplit
import httpx

class TargetError(ValueError):
    pass


def validate_target(raw: str) -> str:
    if not raw or len(raw) > 1024 or any(c.isspace() for c in raw):
        raise TargetError("API 地址为空、过长或含非法字符")
    try:
        u = urlsplit(raw)
        port = u.port
    except ValueError as e:
        raise TargetError("API 地址格式错误") from e
    if u.scheme not in ({"https", "http"} if os.getenv("ALLOW_HTTP_TARGETS") == "1" else {"https"}):
        raise TargetError("API 地址必须使用 HTTPS")
    if not u.hostname or u.username or u.password or u.fragment or u.query:
        raise TargetError("API 地址不能包含账号密码、查询参数或片段")
    if port and port not in (80, 443, 8443) and os.getenv("ALLOW_CUSTOM_PORTS") != "1":
        raise TargetError("非标准端口需由管理员开启 ALLOW_CUSTOM_PORTS")
    host = u.hostname.rstrip('.').lower()
    approved = [x.strip().lower() for x in os.getenv("ALLOWED_API_HOSTS", "").split(',') if x.strip()]
    if approved and host not in approved:
        raise TargetError("该 API 域名不在服务器允许列表中")
    if any(c in u.path for c in ['\\', '\x00']):
        raise TargetError("API 地址路径不合法")
    return urlunsplit((u.scheme, u.netloc, u.path.rstrip('/'), '', ''))


def pin_url(url: str) -> tuple[str, dict, dict]:
    """Pin resolved public IP into the request URL while keeping original TLS SNI and Host.

    Only works with direct networking, never system proxies. Must be called for each outbound URL.
    """
    u = urlsplit(validate_target(url))
    hostname = u.hostname
    allow_private = os.getenv("ALLOW_PRIVATE_TARGETS") == "1"
    try:
        addrs = socket.getaddrinfo(hostname, u.port or (443 if u.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    except OSError as e:
        raise TargetError(f"无法解析域名：{hostname}") from e
    candidates = []
    for item in addrs:
        addr = item[4][0]
        ip = ipaddress.ip_address(addr)
        if allow_private or ip.is_global:
            candidates.append(ip)
    if not candidates:
        raise TargetError("目标地址解析到内网或非公网 IP，已拒绝访问")
    # Avoid IPv6 on hosts without IPv6 routing if IPv4 is also available.
    candidates.sort(key=lambda ip: ip.version != 4)
    pinned_ip = candidates[0]
    netloc = f'[{pinned_ip}]' if pinned_ip.version == 6 else str(pinned_ip)
    if u.port:
        netloc += f':{u.port}'
    host_header = u.netloc
    endpoint = urlunsplit((u.scheme, netloc, u.path, '', ''))
    return endpoint, {'Host': host_header}, ({'sni_hostname': hostname} if u.scheme == 'https' else {})


def api_path(base: str, protocol: str, model: str = '') -> str:
    """Handle origins, /v1 bases, and existing endpoint paths."""
    base = validate_target(base)
    u = urlsplit(base)
    p = u.path.rstrip('/')
    if protocol == 'openai_chat':
        suffix = '/chat/completions'
        if p.endswith('/chat/completions'):
            return base
        if not p.endswith('/v1'):
            suffix = '/v1' + suffix
    elif protocol == 'openai_responses':
        suffix = '/responses'
        if p.endswith('/responses'):
            return base
        if not p.endswith('/v1'):
            suffix = '/v1' + suffix
    elif protocol == 'anthropic':
        suffix = '/messages'
        if p.endswith('/messages'):
            return base
        if not p.endswith('/v1'):
            suffix = '/v1' + suffix
    elif protocol == 'gemini':
        from urllib.parse import quote
        suffix = f'/models/{quote(model, safe=".-_")}:generateContent'
        if p.endswith('/v1beta') or p.endswith('/v1'):
            pass
        elif ':generateContent' in p:
            return base
        else:
            suffix = '/v1beta' + suffix
    elif protocol == 'models':
        suffix = '/models' if p.endswith('/v1') else '/v1/models'
    else:
        raise TargetError('不支持的协议')
    return urlunsplit((u.scheme, u.netloc, p + suffix, '', ''))

async def pinned_request(method: str, url: str, headers: dict, payload: dict | None = None, timeout: float = 40):
    endpoint, host, extensions = pin_url(url)
    safe_headers = {**headers, **host}
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=12), follow_redirects=False, trust_env=False) as client:
        async with client.stream(method, endpoint, headers=safe_headers, json=payload, extensions=extensions) as response:
            chunks = []
            received = 0
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > 1024 * 1024:
                    raise TargetError('API 响应超过 1MB 上限')
                chunks.append(chunk)
            return response, b''.join(chunks)

async def pinned_stream(url: str, headers: dict, payload: dict, timeout: float = 40, params: dict | None = None):
    """Async generator returns events as (elapsed, line)."""
    import time
    endpoint, host, extensions = pin_url(url)
    safe_headers = {**headers, **host}
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=12), follow_redirects=False, trust_env=False) as client:
        begin = time.perf_counter()
        async with client.stream('POST', endpoint, headers=safe_headers, json=payload, params=params, extensions=extensions) as response:
            if response.status_code >= 300:
                blob = await response.aread()
                yield ('error', response.status_code, blob[:3000].decode('utf-8', 'replace'))
                return
            received = 0
            async for line in response.aiter_lines():
                received += len(line)
                if received > 1024 * 1024:
                    yield ('error', 0, '流式响应超过大小上限')
                    return
                yield ('line', round(time.perf_counter() - begin, 4), line)
