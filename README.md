# 404 AI · Token 渠道检测器 V1.2

一个带简单管理员登录的私有 Web 测试服务。输入 API 地址、Key、模型 ID，选择快速 / 标准 / 深度，即可执行低消耗渠道检查，查看实时过程、历史与下载 PDF/JSON 报告。


## 开发文档与交接

- [`SPEC.md`](SPEC.md)：产品定位、检测规则、服务端接口、目录结构、实现边界与验收标准。
- [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md)：当前真正完成的内容、模拟测试范围、未验证事项与后续开发优先级。
- [`AGENTS.md`](AGENTS.md)：交给 Codex / Claude Code 接手维护时的约束及操作顺序。

**本项目在 V1.1 基础上已完成 V1.2 的模型配置、实时过程、证据报告和 PDF 改版。** 尚未完成真实供应商端到端验收。

## 功能范围

- **支持协议**：OpenAI Chat Completions、OpenAI Responses、Anthropic Messages、Gemini 原生 generateContent。自动模式优先采用预置模型协议，随后按 claude-/gemini- 前缀选择，其他默认 OpenAI Chat；可手动覆盖。
- **日常检测**：基础连通、响应字段、轻量行为采样、JSON 输出、流式表现、工具调用、推理字段、短逻辑题、供应商上报 Token 消耗。
- **测试模式**：快速 4 次、标准 8 次、深度 16 次；授权训练数据样例（标准/深度）额外 1 次。
- **费用**：用户可选填每百万输入/输出 Token 单价、缓存单价及分组倍率；报告估算成本，另可手工输入账单扣费差额来比较。**没有账单时，不会宣称实际倍率已获验证。**
- **分组**：默认作为标签，不会自动改变实际 API 路由；如渠道采用 Header 切换，可按文档在高级设置填写非鉴权自定义 Header。
- **安全**：管理员密码登录、HttpOnly Cookie、CSRF、禁用代理环境变量、限制公网 HTTPS 目标及固定解析 IP、不重定向；Key 仅在测试期间发送，不保存在 SQLite/报告。合成测试用例，不提交用户业务敏感内容。
- **输出**：逐项检测摘要、每条测试用例、Usage、错误与延迟、PDF/JSON 报告、历史记录。

## 推荐部署：Docker + 域名 HTTPS

本机 `ai.heliki.com` 使用现有 Nginx + 独立 Docker 应用，参见 [`DEPLOYMENT.md`](DEPLOYMENT.md)，使用 `compose.nginx.yaml`。以下默认 Caddy 方式适用于 80/443 尚未被占用的服务器。

需要一台安装 Docker / Docker Compose 的 Linux 服务器，域名已解析到服务器，80/443 端口可用。

```bash
cp .env.example .env
# 编辑 .env，将 TESTER_DOMAIN 改成你的域名，把 ADMIN_PASSWORD 换成随机强密码（至少12位）
docker compose up -d --build
```

首次部署后打开 `https://你设置的域名`，使用 `.env` 的引导密码登录。此后可在「账户设置」修改，数据库中的密码哈希为准，旧环境值不再覆盖密码。Caddy 自动申请及续期 HTTPS 证书。

**安全建议**：仅团队自用时优先通过 VPN 或内网网关访问；如果对公网开放，务必在防火墙限制管理来源 IP，并设置 `ALLOWED_API_HOSTS` 为允许检测的供应商域名（逗号分隔）。不要将服务器当作不受限制的开放代理使用。

> Docker 使用 `COOKIE_SECURE=1`，必须通过 HTTPS 访问；应用不单独映射外部 8000 端口。

## 本地开发（无需 Docker）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ADMIN_PASSWORD='替换为至少12位的私有测试密码'
export DB_PATH='./tester.db'
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000`。本地 HTTP 登录仅供可信设备开发调试，生产必须 HTTPS。

## 正确认识测试结果

1. **行为指纹 ≠ 官方身份认证**：低预算样本仅用作可疑线索，不能推断账号池上游来源、真实模型版本或“98% 可信”。
2. **API Usage ≠ 实际扣款证明**：通过 API 读取 Token 用量，只有供应商账单才能验证最终扣款；自填价仅得到估算。
3. **思考输出因供应商而异**：某些模型只返回摘要、签名或推理用量，不能以缺少原始思维链认定假模型。
4. **工具调用只是协议功能检测**：它验证产生正确的调用结构，不代表复杂 Agent 长任务能力。
5. **蒸馏用途必须授权**：测试一条合规训练样例不会赋予用户使用模型输出训练其他模型的权利。未确认原厂及渠道许可时请勿启用。
6. **低预算低采样 ≠ SLA**：无法覆盖低比例混池、时段性失效、高并发或跨区域性能。
7. **成本限制不是扣费保证**：各厂商可能对推理、缓存、失败、视频等另行收费；首版只支持文本模型，不支持生图和视频实测。请使用独立的小额度 Key。

## 首版不包含

- 生图、视频、语音任务（需要独立按任务计价的测试引擎）。
- 官方模型指纹库训练、真实上游来源归因、供应商授权核验。
- 自动获取所有厂商充值余额：各平台账单接口并不统一；采用人工录入实际消费进行核对。
- 并发压测、多租户、付款、平台管理、公开排行榜。

## 应用部署与数据

SQLite 持久化到 `reports` 容器卷。报告内不存 Key；为便于审计，含测试用例响应短片段（仅使用合成测试题）。默认单管理员账号与单服务进程，有效登录会话在重启后保留；进行中的检测仍会标记中断。

如果服务器处在 HTTPS 检测目标之外的专用内网环境：可手动配置 `ALLOW_PRIVATE_TARGETS=1`、`ALLOW_HTTP_TARGETS=1`，但应配合严格的 `ALLOWED_API_HOSTS` 和防火墙，仅在可信网络内启用。默认拒绝内网、回环、元数据地址及非标准端口。禁止把这些开关在对公网开放的服务上随意开启。

运行单元测试：

```bash
pip install pytest pytest-asyncio
pytest -q
```

## 文档参考

- OpenAI Chat: https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
- OpenAI Responses: https://developers.openai.com/api/reference/resources/responses/methods/create
- Anthropic Messages: https://platform.claude.com/docs/en/api/messages/create
- New API 倍率: https://docs.newapi.ai/en/docs/guide/feature-guide/admin/system-setting-advanced

## 品牌资源

- `app/static/assets/logo-on-dark.png`：深色侧栏与登录插画区域，浅色字样
- `app/static/assets/logo-on-light.png`：浅色背景备用版本
- `app/static/assets/favicon-64.png` 和 `favicon-192.png`：浏览器图标

顶部品牌名称已更新为 **404 AI**。


## V1.2 操作与维护

检测页直接点选带品牌 Logo 的预置模型，也可填写自定义 ID。侧栏「模型配置」维护显示名称、实际 ID、品牌、默认协议、启用状态和排序，保存立即生效。选模型会恢复跟随模型协议；之后仍可手动选择单一协议。

检测开始后逐项显示真实状态和证据。刷新/断线恢复同一任务，不重复发送模型请求；服务重启则保留已完成记录并标为中断，不自动续跑。报告顶部显示 TTFT、端到端 TPS、总耗时和用量覆盖率，缺失数据不记为零。

下载按钮直接生成 PDF，文字可复制，摘要与逐项证据完整分页；页面、历史与 PDF 复用同一份报告。旧报告缺失的新字段显示“未记录”。本机开发 PDF 需 Chromium 和中文字体，Dockerfile 已包含；本地可用 `CHROMIUM_EXECUTABLE_PATH` 指定浏览器。

首次预置数据在 2026-10-24 按官方模型 ID 整理，仅作为可编辑的快捷项，不保证你的渠道已授权这些型号：
- OpenAI：https://developers.openai.com/api/docs/models
- Claude：https://platform.claude.com/docs/en/models/overview
- Gemini：https://ai.google.dev/gemini-api/docs/models
- DeepSeek：https://api-docs.deepseek.com/quick_start/pricing/

原有 Claude 固定 budget 思考参数不适用于所有新版本；自动模式保留渠道默认，显式开启使用 adaptive。参考：https://platform.claude.com/docs/en/build-with-claude/extended-thinking 。旧渠道若不支持，报告会如实显示异常。

Logo 资源与许可见 `app/static/assets/models/ATTRIBUTION.txt`。模型预置仅首次初始化导入，后续重启/更新不覆盖管理员修改。

界面采用紧凑单列表：主列表直接查看 TTFT/TPS 等指标，点击行展开证据；说明通过字段提示查看。进度按完成项数推进，单项计时表示已运行时间。PDF 使用紧凑摘要与连续证据排版。


基础区现可直接选择思考设置和计费倍率。模型配置页维护每个模型可用的思考选项及默认值，并在「常用计费倍率」中维护按钮列表/默认倍率。倍率不随模型切换，不切换渠道分组；未填单价时只记录倍率、不估算费用。思考设置仅用于标准/深度的专项测试，auto 不再强制低强度。

账户设置支持修改密码和登录保持时间（默认 7 天，可调 1～30 天）。正常访问会同步续期 Cookie 和服务端会话；改密码后所有设备重新登录。首次从旧内存会话升级需要登录一次，之后普通更新重启保留登录。网络异常提供重试，不再直接跳到登录页。
