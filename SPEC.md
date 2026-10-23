# 404 AI · Token 渠道检测器 — 产品与技术规格（V1.1 基线）

> 文档性质：**现有代码的真实基线 / 交接规格**，不是要求重新开发的需求稿。
> 基线：`404-ai-channel-tester-v1.1.zip`（2026-10-23）。
> 适用对象：后续接手维护的 Codex / Claude Code / 开发人员。
> 重要：文档记录“已实现”“仅初步实现”“未实现”，请勿将模拟测试通过解读为真实商用验证。

## 1. 产品定位和边界

404 AI 是一个供内部合伙人使用的**私有 Web 大模型 API 渠道初筛工具**。目标是用少量 API 请求验证供应商渠道的基础连接、协议兼容、输出和推理字段、报告用量、有限行为样本与基础性能，并保留可复核报告。

核心设计原则：

1. 登录后填写 **API 地址、API Key、模型 ID、测试模式**，默认可直接测试；高级项折叠。
2. 默认模式 **快速 4 请求 / 标准 8 请求 / 深度 16 请求**；训练格式样例仅在标准或深度模式开启且确认授权时增加一条请求。请求数量为计划用例数；首次认证失败可能提前终止。
3. 分组是一个**可选标签**，不会据此自动更改上游。平台真实分组切换由不同 Key 或供应商明确支持的 Header 实现。
4. **行业通用方法优先**：协议验证、响应 Usage、轻量可复核用例、延迟和 TTFT；不提供无依据的“99% 真模型”认证。
5. 费用以供应商返回的 Usage 为基础，用户可填写单价和分组倍率作**估算**。实际账单对账需人工填写金额；不能在没有独立账单时宣称识别出真实扣款。
6. **思维链/思考输出**按各协议公开字段处理；不能把缺少原始内部 CoT 等同于假模型。
7. **蒸馏**：只在确认上游和中转商允许的情况下运行一条结构化训练样例，默认关闭；普通格式能力不等于法律/合同授权。
8. 首版仅覆盖文本型模型 API；图片、视频、音频不在实现范围。

## 2. 已实现的实际操作流程

1. 打开首页；登录使用环境变量设置的单一管理员密码。
2. 在「渠道检测」页面输入目标 API 地址、API Key、模型 ID；可点击「获取可用模型」，失败可手动输入。
3. 选择快速、标准或深度模式（默认标准）。
4. 可选打开高级设置：协议、分组标签、思考配置、输出 token 参数、自定义 Header、输入/输出/缓存单价与分组倍率；获授权时开启训练格式样例。
5. 点击开始检测。后端串行运行短用例，并以受限的响应长度和超时保护执行；同一服务器单进程仅允许一项检测运行。
6. 页面显示检测概况、各维度结论和用例详情；导出 HTML/JSON。
7. 「历史报告」页面查看最多 60 条最近报告；数据库持久化历史记录，不存 Key。

## 3. 测试用例和指标（以 `app/engine.py` 为准）

| 模式 | 基本用例 | 数量 |
|---|---|---:|
| 快速 | PING、单数字行为采样、JSON 约束、流式性能 | 4 |
| 标准 | 快速 4 项 + 工具调用、思考协议、三行指令、逻辑题 | 8 |
| 深度 | 标准 8 项 + 额外 4 个数字行为采样、流式复测、JSON 复测、工具复测、逻辑复测 | 16 |
| 可选 | 授权训练数据 JSON 格式样例 | +1（限标准/深度） |

采集的可观察字段：
- `status` / `note`：用例结果和异常说明；状态含 `pass` / `warn` / `sample` / `error`。
- `latency_ms`：单请求耗时；流式另有 `ttft_ms`（首内容片段延迟）。
- `model_returned`：供应商返回的模型标识；并不证明实际部署的模型身份。
- `thinking_observed` / `tool_observed`：已观察到思考字段/用量或工具调用结构。
- `usage`：`input_tokens`、`output_tokens`、`cache_read_tokens`、`cache_write_tokens`、`reasoning_tokens`、`reported`。
- 总报告 `sections`：模型身份与行为采样、Token 用量与计费、协议与工具、思考模式、能力与内容、基础性能、蒸馏用途，共 7 个摘要区块。

**严谨性边界**：数字行为样本不能独立验证模型身份；工具用例只看协议结构，并未真正执行多轮 Agent；小样本延迟不能代表生产 SLA；思考模式只检查公开协议可观察特征；Usage 来自待测上游，不能当作独立账单凭据。

## 4. 支持的接口协议与选择方式

- `openai_chat`：OpenAI Chat Completions 兼容协议。
- `openai_responses`：OpenAI Responses 协议。
- `anthropic`：Anthropic Messages 协议。
- `gemini`：Gemini generateContent 原生协议；流式使用 streamGenerateContent。
- `auto`：对于官方 Google/Anthropic 的明确域名选择对应协议，否则优先视为 OpenAI Chat。**自动不代表探测全部协议或自动选择服务商最适合协议。**

模型列表获取：后端 `POST /api/models` 试探读取 `/models`，如上游不支持，允许人工填写 ID。

## 5. UI、页面与品牌

- 单页原生 HTML/CSS/JS：登录屏、检测工作台、历史报告页、检测结果区。
- 404 AI 极简扫描框+对勾 Logo；深色侧边栏使用 `logo-on-dark.png`；登录移动版/浅色背景使用 `logo-on-light.png`；提供 64/192px favicon。
- 深青/青绿主题；V1.1 品牌文本为 `404 AI`，工作台用于 Token 渠道测试。
- 默认不暴露预算和复杂测试调度；单价与倍率、协议、Header 都收在高级配置中。

## 6. 服务端接口（现有实现）

| HTTP | Path | 功能 | 认证要求 |
|---|---|---|---|
| GET | `/` | 静态首页 | 无，页面内按会话显示登录态 |
| POST | `/api/login` | 单管理员密码登录，设置 HttpOnly cookie | 密码 |
| GET | `/api/me` | 当前会话及 CSRF token | 登录会话 |
| POST | `/api/logout` | 注销会话 | 登录会话 + CSRF |
| POST | `/api/models` | 读取服务商返回的模型列表 | 登录会话 + CSRF |
| POST | `/api/run` | 串行运行套件，存储报告并返回结果 | 登录会话 + CSRF |
| GET | `/api/reports` | 最近最多 60 个报告摘要 | 登录会话 |
| GET | `/api/reports/{id}` | 报告 JSON | 登录会话 |
| GET | `/api/reports/{id}/html` | 导出独立 HTML 报告 | 登录会话 |

**执行方式**：`/api/run` 为同步等待全部测试完成再返回，前端显示等待遮罩。当前没有异步任务队列、进度流式推送或任务取消接口；不要把界面文案解读成具备这些功能。

## 7. 代码目录与职责

```text
404-ai-channel-tester/
├── SPEC.md                      # 本规格：产品规则、架构、状态、验收边界
├── IMPLEMENTATION_STATUS.md     # 已实现、待验证、已知限制的事实清单
├── AGENTS.md                    # 给 Codex/Claude Code 的接手指南
├── README.md                    # 产品概览和完整部署说明
├── QUICKSTART.md                # 快速部署操作说明
├── .env.example                 # 生产域名、管理员密码、可选域名白名单
├── .dockerignore
├── Dockerfile                   # Python 3.12 镜像、非 root 运行
├── compose.yaml                # FastAPI + Caddy + 持久化卷
├── Caddyfile                    # HTTPS 反向代理、HSTS
├── requirements.txt             # Python 运行依赖
├── app/
│   ├── main.py                  # FastAPI 路由、会话/CSRF、检测入口、HTML 报告
│   ├── engine.py                # 测试用例、协议适配、响应解码、统计与结论
│   ├── security.py              # URL 校验、限制性 DNS 解析与网络请求
│   ├── store.py                 # SQLite 报告存储
│   └── static/
│       ├── index.html           # 主 UI
│       ├── style.css            # 页面样式、响应式布局
│       ├── app.js               # 登录、填写配置、执行检测、历史/导出
│       └── assets/              # 两套 Logo 与 favicon
└── tests/
    ├── test_engine.py           # 套件数量、接口路径、计费/用量、mock 执行等
    └── test_web.py              # 登录、会话、CSRF、报告接口保护
```

调用关系：`app.js` → FastAPI `main.py` → `engine.py` → `security.py`（HTTP）→ 上游模型 API；结果经 `store.py` 持久化到 SQLite，随后由 FastAPI 返回页面/报告。

## 8. 数据与隐私设计

- 单管理员认证：`ADMIN_PASSWORD` 环境变量（至少 12 位）；会话与 CSRF 信息存服务器进程内存，12 小时到期；重启会被注销。
- 登录限流：基于请求来源 IP 的短窗口计数。单进程实现；不等于完整互联网级防护。
- Key：仅内存中用于本次请求，代码不向报告、SQLite 持久化 Key。不得把真实密钥写进文档、脚本和截图。
- 报告：SQLite 表 `reports` 保存报告 ID、UTC 时间、模型、标签、模式、协议、状态和 JSON payload；测试样例仅用合成题，仍可能保存少量模型响应文本。
- 默认禁止私网目标、非 HTTPS、任意重定向、危险 Header 覆盖、系统代理透传；`ALLOWED_API_HOSTS` 可限制允许连接的供应商主机。
- 生产：以 HTTPS、强密码、受限来源 IP 和独立小额度测试 Key 运行。绝不可视作公共开放代理。

## 9. Docker 与配置

快速启动（参考 `QUICKSTART.md`）：

```bash
cp .env.example .env
# 更改 TESTER_DOMAIN 与 ADMIN_PASSWORD；按需配置 ALLOWED_API_HOSTS
docker compose up -d --build
```

构成：一个 FastAPI 容器 + 一个 Caddy 容器；`reports` 命名卷挂载 `/data`；Caddy 负责域名 HTTPS 证书；`app` 容器不向宿主机开放 8000 端口。

注意：`compose.yaml` 默认只显式注入 `ADMIN_PASSWORD`、`COOKIE_SECURE`、`DB_PATH`、`ALLOWED_API_HOSTS`。README 所述 `ALLOW_PRIVATE_TARGETS` 等其他环境开关不会因为写进 `.env` 就自动进入 app 容器；如果日后需要开启，必须检查并修改 Compose 配置，且审查 SSRF 风险。**首版建议保持默认关闭。**

## 10. 验收标准与测试证据

当前可复现的本地检查：

```bash
python -m pytest -q
```

2026-10-23 对本压缩包提取出的原始代码运行：**11 passed（0.35s）**。覆盖逻辑见 `tests/test_engine.py`、`tests/test_web.py`。这些主要是单元/模拟接口测试，**尚不证明** Docker 镜像构建、真实供应商认证与所有协议的端到端兼容已经完成。

正式上线前最小验收：
1. 在目标服务器完成 Docker Compose 构建、HTTPS、管理员登录、重启后报告持久化。
2. 用独立低额度 Key 对至少一个 OpenAI 兼容中转渠道进行快速和标准检测。
3. 分别选代表性 Claude/Responses/Gemini 渠道进行协议专项联调（有渠道时），核对流式用量和思考输出。
4. 用供应商真实账单或前后余额核对一次计费，确认估算口径。
5. 验证报告中无 Key、响应错误不泄露鉴权信息、错误时不会意外持续消耗额度。

## 11. 已知缺口（不是“重新开发”的理由）

- 没有基于官方版本的行为指纹基准数据库；没有混池溯源证明或官方渠道授权核验。
- 没有自动抓取通用中转商实际扣款/余额 API；页面实际扣款字段为临时人工对比，没有写回独立账单凭证。
- 不支持生图/视频/语音；不支持并发压测、长时 SLA、代理池分群识别。
- 高级 Header 与特殊中转协议仍需真实渠道适配；`auto` 协议默认不是万能探测。
- 未完成真实供应商端到端联调及目标 Linux 环境 Docker 部署验证。
- 无账号找回、团队权限、后台任务队列、取消/暂停操作、生产监控告警；V1.1 明确不包含这些功能。

## 12. 后续开发约束

- **保留现有 FastAPI + 原生前端 + SQLite 架构**；除非有确凿缺陷，不要另建 React/Next.js 或替换测试核心。
- 修改前先阅读 `IMPLEMENTATION_STATUS.md` 和对应源码；能通过局部修复解决的问题不重写工程。
- 新渠道适配优先修改 `engine.py` 中协议请求/响应解析和 `security.py` 中 URL 路径；对应补充模拟测试。
- 任何计费判断都要区分“供应商上报 Usage”“按照自填价格估算”“真实账单扣费”；不能混为一个结果。
- 任何模型身份判断都应附证据和样本限制，不将少量数字偏好采样伪装成强指纹鉴定。
- 后续首先做真实渠道联调和关键兼容修复，而不是先开发公共平台、分销系统或复杂商业模块。
