# stratos-aliyun 部署

网址：`https://stratos.run/apps/adbuild-agent`

服务器通过 Tailscale 连接，应用由 systemd 托管；Nginx 只新增 `/apps/adbuild-agent` 命名空间。Web 和 API 分别监听 `127.0.0.1:18790`、`127.0.0.1:18791`，不直接暴露端口。

源代码与构建位于 `/opt/adbuild-agent/releases/`，`/opt/adbuild-agent/current` 指向当前版本。Python 虚拟环境位于 `/opt/adbuild-agent/venv`，项目私有运行配置位于 `/etc/adbuild-agent/runtime.env`。这些文件不加入 Git。

服务配置在 `deploy/`。执行 `bash deploy/install.sh /opt/adbuild-agent/releases/<版本>` 安装新版本；脚本需要 admin 用户的 sudo 权限。修改 Nginx 前会保留原配置，只有 `nginx -t` 成功后才 reload。队列 Worker 在数据库和权限准备好、创建 `/etc/adbuild-agent/worker-enabled` 后才可启用。

DeepSeek 使用 OpenAI-compatible Chat Completions 和工具调用输出结构。模型由 `LLM_MODEL` 指定，端点为 `LLM_BASE_URL`，密钥为 `LLM_API_KEY`，密钥仅保存在服务器。

运行状态由 API `/config` 提供，不向前端暴露数据库密码、模型密钥和 TikTok token。MySQL 8.0 独立库 adbuild_agent，账号限制为服务器实际连接 IP；应用服务只保留本库的数据读写权限。LangGraph 使用 langgraph-checkpoint-mysql，队列使用 InnoDB 的 SKIP LOCKED 和租约。登录为 Argon2 密码与限时签名会话，不再使用 Supabase。
