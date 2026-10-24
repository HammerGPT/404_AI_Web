# ai.heliki.com 本机 Docker 部署

本机已有 Nginx 占用 80/443，使用 `compose.nginx.yaml`，不要启动默认 Compose 中的 Caddy。

访问链路：`https://ai.heliki.com` → 宿主机 Nginx → `127.0.0.1:8817` → 应用容器 `8000`。无需为 8817/8000 开放公网安全组端口。

## 运行配置

- 项目目录：`/root/404_AI_Web`。
- Compose 项目：`ai404`；服务：`app`；镜像：`ai404-web:local`。
- 外部 Docker 网络：`ai404-proxy`；当前网关 `172.28.0.1`，作为 Uvicorn 唯一可信代理来源。
- 命名卷：`ai404-reports` → `/data`；数据库 `/data/tester.db`。
- `.env` 权限为 600，保存首次初始化的管理员引导密码、域名、可选上游白名单和 `TRUSTED_PROXY_IP`；后台改密码后，以数据库哈希为准。不得把其内容粘贴到日志或提交到 Git。
- Nginx 配置：`/usr/local/nginx/conf/vhost/ai.heliki.com.conf`，仓库模板为 `deploy/ai.heliki.com.conf`。
- TLS 证书：`/etc/letsencrypt/live/ai.heliki.com/`；HTTP 验证目录 `/home/wwwroot/ai404-acme`。
- certbot 续期配置：`/etc/letsencrypt/renewal/ai.heliki.com.conf`，复用系统 `snap.certbot.renew.timer`，续期 hook 检查并 reload Nginx。
- 初次部署前 Nginx 主配置备份：`/root/backups/404-ai-web/initial-deployment/nginx.conf`。

应用保持单 worker，账户升级后有效会话随 SQLite 持久化，重启不清空登录。V1.2 的 `/api/runs` 立即返回任务 ID，由 SSE 推送快照，Nginx 已设置 `proxy_buffering off`；旧 `/api/run` 同步入口保留。Nginx 读超时为 1200 秒，每项另有 90 秒总时限。

## 更新

先查看 Git 状态并核对更新范围，不覆盖本地修改。运行项目虚拟环境中的测试，再构建：

```bash
cd /root/404_AI_Web
.venv/bin/python -m pytest -q
docker image tag ai404-web:local ai404-web:rollback
HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= ALL_PROXY= all_proxy= \
docker compose -f compose.nginx.yaml build \
  --build-arg HTTP_PROXY=http://172.17.0.1:7896 \
  --build-arg HTTPS_PROXY=http://172.17.0.1:7896 app
docker compose -f compose.nginx.yaml up -d --wait app
curl --noproxy '*' -fsS https://ai.heliki.com/ -o /dev/null
docker compose -f compose.nginx.yaml ps
```

构建使用本地缓存，不添加 `--no-cache`。构建参数中的代理仅供安装依赖；应用请求默认不使用环境代理。

基础镜像 `python:3.12-slim` 初次因 Docker Hub 直连超时，改为通过本机代理从官方 Registry 下载，逐项核对 manifest、压缩层与解压层 SHA-256 后导入 Docker。以后优先复用本地基础镜像；不要为拉镜像重启全局 Docker 服务影响其他项目。

确认新容器 healthy、HTTPS 和登录功能正常后：

```bash
docker image prune -f
df -h /
```

保留 BuildKit 缓存。禁止 `docker compose down -v`、删除业务卷或全量清理缓存来解决更新问题。需回滚且数据库结构兼容时，把 `ai404-web:rollback` 重新标为 `ai404-web:local`，再用同一 Compose 文件执行 `up -d --no-build --force-recreate --wait app`，报告卷保持不变。

## 检查与备份

首页及静态资源应返回 200，未登录 `/api/reports` 应返回 401。容器健康检查使用首页，应用没有 `/health` 路由。

上线验收包括 HTTPS 密码登录、Secure/HttpOnly Cookie、CSRF、报告 JSON/HTML 导出和重启持久化。真实模型检测需另用获授权的低额度 Key；部署验收不消耗真实模型额度。

SQLite 备份使用 Python `sqlite3.Connection.backup()` 或 SQLite backup 命令取得一致性快照，再导出到受限备份目录；不要在运行时只复制主数据库文件。日志使用 Docker 有限轮转，Nginx 关闭此站点的访问日志。


## V1.2 更新（2026-10-24）

Docker 增加 Chromium 和 Noto 中文字体，通过 Playwright 生成真正的 PDF。`scripts/prepare_pdf_font.py` 在构建阶段处理 CJK 字形重复 Unicode 映射，确保复制中文不变成偏旁；不要跳过此步骤。浏览器和 apt/pip 层已缓存，后续仅改业务代码会复用缓存。

新表为 `model_presets`、`settings`、`run_requests`，`reports` 原八列不改。原始报告按 payload 保存新版本快照，API Key 仍只在运行内存中。进程重启把未完成任务标为中断，不自动发起上游请求。模型预置只导入一次，管理员编辑不会被镜像重建覆盖。

升级备份在 `/root/backups/404-ai-web/pre-report-upgrade-20261024/`，旧镜像为 `ai404-web:before-report-upgrade-20261024`。可在确认没有活动检测后切回旧镜像并使用同一数据卷；已验证旧版八列 INSERT 兼容新数据库。回滚不删除新表和历史记录。

验收包含 45 项测试和隔离浏览器全流程。`tests/mock_server.py` 为完全模拟上游，只允许在独立测试容器/数据库运行，不能挂进线上容器。生产镜像不包含 tests 目录。最终验收产物保存在上述备份目录的 `verification/`。

线上已核对原有报告内容不变，验证模型目录、旧报告与 PDF。未进行真实模型请求；真实供应商兼容需要另行使用获授权的测试 Key。


基础设置更新新增 model_reasoning 表和 settings.billing 数据，原报告/预置表列结构不改。重启只初始化缺失配置，不覆盖已保存的选项。更新前备份位于 `/root/backups/404-ai-web/pre-basic-settings-20261024/`，回滚镜像 `ai404-web:before-basic-settings-20261024`。回滚旧镜像会暂时隐藏新控件，但不能删除新配置表或业务卷。


## 账户功能上线后的维护

新增 `admin_account` 和 `auth_sessions` 表，随原 `ai404-reports` 卷保存。首次升级时需要登录一次，此后普通容器替换保留会话。默认有效期 7 天，后台可调；新密码不回写到 `.env`。已有账户时可以去掉 ADMIN_PASSWORD，引导密码不会再被作为登录后备密码。

不能再把 `.env` 的旧值当作当前密码。忘记后台密码时，经用户明确授权后在可信服务器终端交互重置：

```bash
cd /root/404_AI_Web
docker compose -f compose.nginx.yaml exec app python -m app.auth reset-password
```

命令使用隐藏输入，不把密码放到参数、日志或报告中；重置会撤销所有会话。

**认证版本回滚边界：** 本次以前的镜像只认环境密码，不支持持久会话。后台已经改过密码后，不得直接回滚到这些旧镜像，否则可能重新启用旧引导密码。优先使用保留新认证模块的回滚版本；必须退回旧认证时，先另行确认凭据迁移方案。恢复旧数据库也会恢复当时的凭据/会话，需要按授权重新核验并撤销旧会话。

本次升级前备份：`/root/backups/404-ai-web/pre-account-settings-20261024/`，历史镜像 `ai404-web:before-account-settings-20261024` 只作为迁移前参考，不绕过上述认证边界。


## 指纹验货更新（2026-10-24）

新增 `fingerprint_baselines`，不改动原业务表结构。更新前确认无活动检测；一致性数据库及 `1278704` 源码快照位于 `/root/backups/404-ai-web/pre-fingerprint-20261024/`，原镜像 `ai404-web:before-fingerprint-20261024`。回滚保留新表及报告卷，不回滚账户哈希。

本次 104 项单元测试和隔离浏览器 396 次假上游请求通过；PDF、基线管理、手机界面通过。线上 HTTPS、已有会话跨重启、原有 4 份报告及业务数据哈希通过，无真实 API 调用。上线基线库为空，用户需自行从可信接口采集，不能把测试数据库或社区样本拷入生产充当官方基线。
