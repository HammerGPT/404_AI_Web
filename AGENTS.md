# 404 AI 开发交接指令（给 Codex / Claude Code）

先读 `SPEC.md`（产品和技术基线），再读 `IMPLEMENTATION_STATUS.md`（准确的完成状态）、`README.md` 与 `QUICKSTART.md`。

## 核心约束

- 这是**已有实现**，不是绿地项目。禁止在未检查源码前宣称需要重新实现。
- 维护现有 FastAPI + 原生 HTML/CSS/JS + SQLite + Docker/Caddy 架构；尽量只改相关文件。
- API Key 仅运行时使用，禁止写进本地报告、Git、日志、测试快照、错误提示；测试永远使用伪造的密钥或隔离的低额度 Key。
- 默认不发往第三方检测站；只调用用户配置的模型上游。不要擅自发送真实数据或进行未经授权的压测、蒸馏采集。
- 不能把行为采样、模型自报 ID、Usage、样本级延迟与模型真实身份、账单扣费或 SLA 证明混为一谈。
- 供应商分组只当标签，除非文档与测试明确证明自定义 Header/Key 路由方式。
- 任何新增协议字段需有适配测试，并区分官方原生接口与中转兼容接口。
- 初期重点修复真实渠道兼容性，不拓展多租户、电商分销、通用监控平台。

## 开发流程

1. 查看项目当前源码及 Git 状态（若已有仓库）。
2. 本地执行 `python -m pytest -q`，确认当前 11 项测试基线。
3. 仅修改明确发现的问题，补充针对性单元或模拟集成测试。
4. 对需真实 Key 才能验证的结果标记「未验证」，不要硬编码成功或臆测返回格式。
5. 完成后更新 `IMPLEMENTATION_STATUS.md`（更改项、测试情况、已知风险），必要时同步 `SPEC.md`、`README.md` 与 `QUICKSTART.md`。

## 文件速查

- 页面：`app/static/index.html`、`style.css`、`app.js`
- API/登录/报告：`app/main.py`
- 用例/协议/统计：`app/engine.py`
- HTTP 安全边界：`app/security.py`
- 持久化：`app/store.py`
- 自动化测试：`tests/`
- 部署：`compose.yaml`、`Dockerfile`、`Caddyfile`

## 本地启动

参考 `QUICKSTART.md`。开发依赖可通过 `pip install -r requirements.txt` 安装；运行测试需要另外安装 `pytest`、`pytest-asyncio`。使用测试密码仅限本机开发，不要保存在生产配置。
