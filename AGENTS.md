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
2. 本地执行 `.venv/bin/python -m pytest -q`，确认测试基线（2026-10-26 轻量指纹更新后为 126 项）。
3. 仅修改明确发现的问题，补充针对性单元或模拟集成测试。
4. 对需真实 Key 才能验证的结果标记「未验证」，不要硬编码成功或臆测返回格式。
5. 完成后更新 `IMPLEMENTATION_STATUS.md`（更改项、测试情况、已知风险），必要时同步 `SPEC.md`、`README.md` 与 `QUICKSTART.md`。

## 文件速查

- 页面：`app/static/index.html`、`style.css`、`api.js`、`models.js`、`basics.js`、`report.js`、`app.js`
- API：`app/main.py`；持久化认证：`app/auth.py`；账户界面：`static/account.js`；模型目录：`catalog.py`；任务生命周期：`runs.py`
- 用例/统计：`app/engine.py`；协议解析：`app/adapters.py`；报告/PDF：`reports.py`、`templates/`、`static/report.css`
- HTTP 安全边界：`app/security.py`
- 持久化：`app/store.py`
- 自动化测试：`tests/`
- 部署：`compose.yaml`、`Dockerfile`、`Caddyfile`；本机 `ai.heliki.com` 使用 `compose.nginx.yaml` + 宿主机 Nginx，实际运行与维护步骤见 `DEPLOYMENT.md`，不要启动默认 Caddy 占用 80/443。

## 本地启动

参考 `QUICKSTART.md`。开发依赖可通过 `pip install -r requirements.txt` 安装；运行测试需要另外安装 `pytest`、`pytest-asyncio`。使用测试密码仅限本机开发，不要保存在生产配置。

## V1.2 维护要点

模型预置是 SQLite 数据，`app/data/model-presets.json` 仅首次导入，不要用写死的前端数组替代管理入口。页面、历史和 PDF 必须复用同一报告数据与模板。

不要破坏请求去重和单任务约束；断线只恢复订阅，进程重启标记中断，不持久化 Key 或自动重放。保持 `reports` 原八列结构，以免破坏旧镜像回滚。

前端采用原生 ES modules，语法检查用 `node --input-type=module --check < app/static/<文件>.js`。PDF 验收要检查分页、中文复制和完整证据，不仅判断文件能否打开。`scripts/prepare_pdf_font.py` 在构建时处理 Noto 字形的重复 Unicode 映射。

基础设置：思考选项以 model_reasoning 表和共享协议元数据为准；倍率以 settings.billing 为准。auto 不附加思考参数，不根据模型品牌推断计费倍率。保留原 model_presets 七列以兼容旧镜像。

账户维护：登录会话已持久化，普通重启应保留；密码仅以数据库哈希为准，ADMIN_PASSWORD 仅首次初始化。不要用旧环境密码覆盖后台密码；恢复密码须明确授权并用交互 CLI。回滚到旧认证版本前必须核对凭据迁移，详见 DEPLOYMENT.md。


指纹维护：独立 99 请求档在 `fingerprints.py` 和 `data/fingerprint-profile.json`。不能把单次格式合规、p 值或返回模型 ID 当真实性概率；没有可信基线必须拒绝验货。修改题目、输出字段或统计参数会改变 profile hash，旧基线需重采。基线/最终报告原子保存；不得把模拟数据库、社区样本或真实 Key 部署成官方基线。参考与待测同源仅判历史一致性。真实准确率尚未校准。


轻量初筛：标准/深度现在是 11/19 次，额外三次公开库数字挑战由 identity.py 处理。vendor/lmfpd 固定来源、MIT 许可和哈希，不引入其在线采集器。普通检测不依赖自建基线；99 次参考对照仅高级入口。公开库不是官方真值，候选概率不当真实概率；样本不足和库外型号显示证据不足。12 组公开样例只验证移植一致性，不宣称独立准确率。用户要求不过度设计，完成必要验证后不继续扩展研究、补采或反复全量测试；付费调用另需授权。
