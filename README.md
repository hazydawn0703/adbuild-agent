# Adbuild 广告搭建工作台

运行地址：https://stratos.run/apps/adbuild-agent

Next.js + 官方 AG-UI HttpAgent + FastAPI + LangGraph，使用服务器 MySQL 8.0、应用层租户权限、Argon2 密码哈希与私有文件存储。三栏界面通过维护中的 AG-UI 客户端同步对话与草稿，不需要额外的 Provider 层。模型为 DeepSeek-V4.1-Flash-1017，官方 API ID 为 deepseek-flash。

## 初始化

1. 创建独立 MySQL 数据库和专用账号，只允许应用服务器连接。首次执行 backend/schema.sql 创建业务表；执行 python -m app.admin checkpoints 创建成熟 MySQL checkpointer 的内部表。初始化后应用只需 SELECT/INSERT/UPDATE/DELETE 权限。
2. 复制根目录 .env.example 到 .env，填写 MySQL DSN、会话签名密钥、令牌加密密钥和 DeepSeek 配置。密钥不得进入 Git。
3. python -m pip install -e "./backend[test]"。后端从仓库根目录执行 python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log。
4. 平台管理后台为 `/apps/adbuild-agent/ops`，先验证独立的平台管理密码，再开通客户空间、首个项目和管理员账号。服务器配置 `OPS_PASSWORD_HASH`（Argon2 哈希），不在前端保存平台密码。客户初始密码至少16字符。服务器命令 `python -m app.admin provision --name GSTM --email <邮箱> --password-file <私有密码文件> --account-id <广告账户ID>` 也可用于运维开通。
5. 前端 cd frontend，npm ci，npm run dev。打开 http://localhost:3000/apps/adbuild-agent。前端从 API /config 获取状态，无需嵌入云服务凭证。
6. 后台执行 python -m app.worker。任务通过 MySQL InnoDB 事务、SKIP LOCKED 和可见性租约领取；执行另有 GET_LOCK 防止重复投递并发执行。

## 使用

当前按“搭建任务”隔离每批文件、对话、待采纳提案和草稿。可以先说明意图再上传；已确认的表头语义可复用，数据范围每批重新检测。结构化问题支持批量回答；修改提案须先采纳或拒绝，再单独确认完整搭建方案。管理员可以保存有适用范围和版本的项目默认配置。

开发回归结果见 docs/development-results.md；发布与最终盲测协议见 docs/robustness-acceptance.md。用户最终验证集未参与开发。

登录 → 选择项目 → 导入 ExportAds → 上传需求 Excel → Agent 提议字段映射 → 人工确认映射 → 检查三层草稿及来源 → 打开搭建前预览 → 勾选本次需求 → 补齐配置 → 人工确认指定版本。真实搭建当前暂停。

金额、币种、预算类型、受众、素材身份和有效排期必须齐全。过期排期不自动平移；已有投放的变更只展示。重复上传按部署指纹去重。上传文件按租户/项目存储在私有目录，访问均经 API 权限检查。

DeepSeek 通过 OpenAI-compatible Chat Completions 和工具调用生成 Pydantic 结构，抽取请求关闭 thinking 以匹配固定 tool_choice；业务规则仍由后端执行。

## TikTok

管理员 OAuth 后列出实际 MCP 工具。平台需按 docs/tiktok-bindings.md 完成真实账户工具绑定与三目标小额验收，才可启用自动投放。写入超时只回查，无法确认时不重放。当前没有创建或启用真实广告。

## 验证与部署

backend 目录运行 python -m pytest -q；ADBUILD_FIXTURE_DIR 指向样例文件所在目录即可运行原始 Excel 回归。前端执行 npm run typecheck 和 npm run build。

服务器部署与服务路径见 docs/deployment.md。数据库、会话、模型和 TikTok 凭证只存服务器 /etc/adbuild-agent/runtime.env。初始管理员密码保存在用户指定的本地 .tmp/adbuild-login.txt，不会提交。

## 搭建前人工确认

右侧“搭建前预览与确认”展示计划/广告组/广告、预算按币种与类型汇总、排期、受众、素材和单元格来源。未选中、已有广告及已有广告变更不会进入本次新增。

确认必须显式勾选核对声明，并提交服务端预览摘要与版本；后台记录确认人、时间及不可变快照。重复确认保持幂等。编辑任意配置、选择范围或项目授权变化后，原确认不再生效；历史确认快照保存在审计记录。

真实执行由 EXECUTION_ENABLED 控制，默认 false；创建/编辑草稿、映射确认、Agent 修改确认和方案确认都不会自动入队或占用预算。当前服务器已停止任务消费者。未来开放执行仍需当前预览已确认、项目预授权和后台权限校验同时通过。
