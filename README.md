# Adbuild 广告搭建工作台

Next.js 三栏界面 + FastAPI + LangGraph，使用 Supabase Auth / Postgres / Storage / Queues 和 TikTok 官方 MCP。

## 本地启动

需要 Node.js 20.9+、Python 3.12+ 和一个全新 Supabase 项目。前端在没有配置时可以启动查看空工作台，业务操作需要真实登录及云端配置；没有模拟登录和模拟广告创建。

1. 在 Supabase SQL Editor 执行 `supabase/schema.sql`。这是新项目初始化 schema，不是旧版迁移脚本。
2. 在 Supabase Auth 邀请首位用户并设置密码；关闭公开注册。在 Authentication URL Configuration 设置实际站点地址。
3. 将根目录 `.env.example` 复制为 `.env`，填写数据库、Supabase 公开与服务端密钥、DeepSeek API 密钥。使用 `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` 生成令牌加密密钥；不要提交这些值。
4. 后端：`python -m venv .venv`，`python -m pip install -e "./backend[test]"`。从仓库根目录运行 API：`python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log`。
5. 从 `backend` 目录执行 `python -m app.admin checkpoints` 初始化 LangGraph 自有持久化表。该表不开放给浏览器，所有会话访问经服务端 ACL 和租户 thread ID。
6. 从 `backend` 运行 `python -m app.admin provision --name GSTM --user-id <已邀请用户UUID> --account-id <广告账户ID>`。
7. 前端通过 API `/config` 读取运行时公开配置，不需要将密钥写入前端构建。`cd frontend`，`npm ci`，`npm run dev`，打开 http://localhost:3000/apps/adbuild-agent。
8. 从 `backend` 运行 `python -m app.worker` 启动队列消费者。API、Worker 会按源文件位置读取仓库根目录 `.env`，也可通过进程环境变量注入。

也可使用 `docker compose up --build` 启动 API 和 Worker。前端独立 `npm run build && npm start`。正式环境使用 TLS，设置 APP_ORIGIN / API_ORIGIN 及公开前端 API 地址，禁止将 Supabase service key 放入 NEXT_PUBLIC 环境变量。

## 使用流程

登录并选择项目 → 上传 ExportAds → 上传需求 Excel → 确认字段映射及范围 → 查看来源、历史候选和缺失项 → 对话或右侧编辑草稿。

映射格式是 `{"mappings":[{"sheet":"搭建需求-运营使用","header_row":2,"first_row":3,"last_row":10,"columns":{"business_no":"B","name":"E","objective":"G","post_url":"J","language":"L","budget":"N","start_at":"O","end_at":"P"},"defaults":{"account_id":"1234567890123456789","timezone":"UTC"}}]}`。首末行必须由客户核对；defaults 是用户确认的配置，而不是自动执行 Excel 中的说明。

点击来源可查看原文件 ID、工作表和单元格。金额、币种、预算类型、受众、素材身份和有效排期不完整时只生成草稿。已有广告的变更只展示，不自动修改。当前自动投放只接受 lifetime 总预算，避免日预算绕过累计额度。

## TikTok 接入与真实投放验收

管理员点击“连接”，然后通过 TikTok OAuth 页面授权。API 使用官方 MCP Python SDK、动态客户端注册和加密 TokenStorage，列出授权连接实际返回的工具。凭证只留在服务端。

**尚未获得此项目的真实账户授权，无法在开发时验证官方工具名称、账户目标能力、素材权限和真实创建结果。系统不会假设这些条件已通过。** OAuth 完成后，平台工程人员需基于实际 tools schema 完成并验收 `connections.bindings`，再启用项目授权。具体格式见 `docs/tiktok-bindings.md`。当前没有预置可用的写入绑定。

执行器已实现：数据库预算预占与队列原子提交、草稿冻结、部署指纹去重、逐步骤持久化、暂停创建后回读、启用顺序、规则撤销检查、写入超时进入 uncertain、重复投递 advisory lock、取消和暂停。

uncertain 步骤恢复时只回查，不自动重放。远端结果有歧义时保留预算占用，需要平台核实步骤记录与 ID。队列和执行事务不能保证 TikTok 远端 exactly-once，因此不会把无返回值视为创建失败后直接重试。

## 当前实现边界

- 已实现业务界面、文件处理、映射提案与确认、历史匹配、字段编辑、LangGraph 对话确认、权限和授权 API、持久化队列及执行器。
- 对话通过 CopilotKit selfManagedAgents + 官方 HttpAgent 连接带权限校验的 AG-UI SSE 端点；AgentStateSync 将服务端草稿同步到预览。LangGraph 对话中的待确认修改持久化到 Postgres。在线模型对话仍需真实密钥和数据库验证。
- 尚需真实 Supabase 与 TikTok 配置完成数据库并发/RLS集成测试、三目标 live 工具绑定和小额创建验收；已部署 Web/API 到 https://stratos.run/apps/adbuild-agent，尚未创建或启用任何真实广告。服务器 MySQL 替换方案及模型端点选择正在确认。
- 平台 onboarding 使用 Supabase Dashboard 邀请；首版没有开放注册、支付、素材生成和持续优化。
- 邀请制试用的 JSON 映射/授权编辑器可直接操作，面向非技术客户的逐字段表单可继续完善。

## 验证

在 backend 目录运行 `python -m pytest -q`。设置 `ADBUILD_FIXTURE_DIR` 为两份原始 Excel 所在目录可验证真实样例：221 条广告、219 个计划、221 个广告组，8 条需求都找到历史候选，过期排期均阻断。

前端：`npm run typecheck`、`npm run build`。浏览器检查脚本为 `scripts/browser-check.cjs`，需要可用的 Playwright 模块与 Chromium（可通过 PLAYWRIGHT_MODULE / PREVIEW_URL 指定模块与预览地址）。

本次开发验证：12 项 Python 测试通过（包含真实 Excel 回归和 LangGraph 暂停/恢复），TypeScript 和生产构建通过，桌面/手机浏览器检查通过；健康检查正常，未认证项目请求返回 401。API 文档：http://localhost:8000/docs。健康检查 `/health` 不需要凭证；所有项目、文件、草稿、聊天和执行 API 都要求有效 Supabase 会话及项目成员权限。

服务器部署方式、服务路径和配置说明见 [部署文档](docs/deployment.md)。
