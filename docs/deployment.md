# 发布规范

工作台网址：`https://stratos.run/apps/adbuild-agent`；平台管理入口：`https://stratos.run/apps/adbuild-agent/ops`。

## 提交、合并后才部署

发布顺序强制为：提交并推送 GitHub → PR 合并到 `main` → 确认合并提交 SHA → 从该提交构建、验证并部署。禁止发布未提交、未合并的修改，完整要求见根目录 `AGENTS.md`。

发布目录中的 `REVISION` 记录合并提交 SHA。部署前后使用该提交的文件清单和哈希核对源码，确认仓库与线上版本一致。回滚只能使用此前已合并、已验证的提交。

服务定义及安装入口位于 `deploy/`，发布目录传给 `deploy/install.sh`。服务器连接信息、真实端口映射、私有路径和运行配置由部署环境维护，不写入公开文档。Nginx 只更新本应用命名空间，配置检查成功后才 reload，不覆盖同域名其他应用的配置。

## 运行配置与权限

`.env.example` 只列出配置键和公开默认值，不包含真实凭据。MySQL DSN、会话密钥、加密密钥、模型密钥及 `OPS_PASSWORD_HASH` 必须通过服务器私有运行配置提供，禁止提交 `.env` 或运行配置文件。

平台管理密码使用 `pwdlib` 生成 Argon2 哈希，前端不保存平台密码。未配置时登录返回 503。平台会话使用独立的 HttpOnly、Secure、SameSite=Strict Cookie，1 小时过期，退出时服务端撤销；API 重启后会话失效。连续登录失败会触发限流。

平台管理员可以开通客户空间和首个项目、添加客户管理员、启停账号及重置密码；客户管理员只能管理所属空间。每个空间至少保留一名启用的管理员。平台操作写入审计记录，不记录密码。客户密码重置后已有登录令牌失效，停用账号后不可访问客户 API。平台 Cookie 与客户 Bearer 令牌互不授予权限。

真实广告执行保持关闭。Worker 随应用运行，在执行关闭时仅领取文件及检查点清理作业，不领取广告执行作业。发布预检使用 `deploy/check-release.py`，不输出凭据。

## 品牌资源与验收

品牌资源位于 `frontend/public/brand/`。图标在 `frontend/app/layout.tsx` 中显式指向本应用的 URL 命名空间，与同域名其他应用隔离。Standalone 发布需将 `frontend/public` 复制到 `.next/standalone/public`。发布后核对页面 `link[rel="icon"]` 及图标 URL 返回 200。

运行后端测试和前端类型检查、构建及浏览器回归。测试开通、账号和密码功能时优先使用模拟请求；实际 MySQL 测试必须在隔离环境或明确的回滚事务中执行，不创建持久化测试客户。

上线后检查页面、API 健康状态、favicon、平台管理登录门禁，并复核桌面和手机端弹窗交互。
