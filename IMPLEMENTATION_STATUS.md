# 404 AI V1.1 — 实现状态与继续开发清单

> 2026-10-23 基于 **真实 zip 源代码** 审阅及本地测试整理。状态指**代码确实存在**，不等于已完成真实服务商上线验收。

## A. 已编码实现（可在源码逐项定位）

| 功能 | 对应文件 | 状态与说明 |
|---|---|---|
| 404 AI Logo / 页头 / 登录页 | `app/static/index.html`、`style.css`、`assets/*` | 已完成页面代码及品牌资源 |
| 单管理员认证、会话、CSRF | `app/main.py` | 已实现；会话内存储存，重启注销 |
| 检测界面 + 高级配置 | `app/static/index.html`、`app.js` | 已实现；默认四项参数，高级项可展开 |
| 模型列表探测 | `app/main.py` `/api/models` | 已实现；失败允许手工填写 |
| 三档低成本用例套件 | `app/engine.py` `CASES`/`cases_for` | 4 / 8 / 16；特殊样例 +1 |
| OpenAI Chat / Responses / Anthropic / Gemini | `app/engine.py` 和 `security.py` | 有四类请求/响应逻辑，真实渠道需联调 |
| 工具调用、JSON、行为采样、推理字段 | `app/engine.py` | 基础黑盒功能检查，非全面能力认证 |
| Usage 归一化、价格估算 | `app/engine.py` `normalize_usage`/`estimate_cost` | 已实现，但仅自填价格与上报 Usage |
| 顺序执行 + 单次任务锁 | `app/main.py` / `engine.py` | 已实现；并发任务返回 409 |
| 七类报告摘要、每条用例详情 | `app/engine.py` / `app/static/app.js` | 已实现；HTML、JSON 导出 |
| SQLite 持久化、历史查询 | `app/store.py`、`main.py` | 已实现；保留最近 60 条列表，详情仍在 DB |
| URL 约束、DNS pin、禁止代理环境变量 | `app/security.py` | 已编码；生产环境仍需安全复核 |
| Docker + Caddy TLS | `Dockerfile`、`compose.yaml`、`Caddyfile` | 部署配置存在，当前未在真实目标服务器验收 |
| 模拟单元测试 | `tests/` | 本地 `python -m pytest -q`：11 passed |

## B. 尚未验证（下一步优先级）

1. **P0 — 真实部署**：Docker 构建、Caddy 证书、登录、持久化卷、升级流程。
2. **P0 — 真实中转兼容**：用独立小额度 Key 跑至少一个 OpenAI Chat 兼容 API 全套；检查 JSON、流、Usage 和错误响应。
3. **P0 — 协议特例**：用代表性 Anthropic、OpenAI Responses、Gemini 真实渠道核对 URL/头、Thinking、流式解析及用量。
4. **P0 — 账单核验**：对照上游实际扣款凭据，确认自填价格与倍率的估算与现实有何差别。
5. **P1 — 安全复核**：凭证不落盘、敏感日志、SSRF/URL 绕过、源 IP 限制、反向代理真实 IP 及入侵限流。
6. **P1 — 任务容错**：长请求时浏览器关闭或网络断开后任务是否仍继续，停止与重复计费策略是否符合预期。

## C. 当前未实现（不要对外宣称已经支持）

- 自动核查真实上游采购关系、Max/Pro/Kiro 路由来源、纯池/混池证明。
- 官方模型真实指纹基线库与统计显著性分析。
- 自动接入所有服务商余额/扣费账单。
- 图片/视频/语音 API 的真实生成测试及单任务计价。
- 生产级并发压测、长期稳定性监测、用户/组织多租户、公共账号注册。
- 持久化测试队列、实时进度推送、后台取消任务。
- 蒸馏后实际训练效果验证；现有只测获授权的 JSON 训练样例格式。

## D. 已发现的需要注意的实现边界

- `auto` 协议由 URL 域名启发式决定，普通中转商默认为 OpenAI Chat；不是端到端协商。
- `run_suite` 中的“成功”包含 `sample`（采样结果），不是每项用例严格通过。
- 流式 `ttft_ms` 以首个实际内容片段为准；速度统计尚未提供可信的输出 TPS 计算。
- 标准/深度含推理用例，部分模型 `build_payload` 可能提高单次 `max_tokens`，因此固定请求数 **不代表固定费用**。
- `ALLOWED_API_HOSTS` 可由 Compose 注入，但 `ALLOW_PRIVATE_TARGETS` 等开关没有写在现有 compose 的环境变量中；不得误以为直接编辑 `.env` 就生效。
- 当前前端账单金额人工对比只在浏览器展示，并非自动读取交易系统或持久化的账单证据。

## E. 是否需要重新实现？

**不需要。** 已有可维护的完整 V1.1 应用基础。建议以本版本为基线，在真实 API 联调中针对性修补协议、用量和 UI 问题。只有确认存在结构性缺陷且无法局部修复时才考虑重构。
