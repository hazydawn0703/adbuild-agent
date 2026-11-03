# 鲁棒性改造验收

真实广告写入持续关闭。方案确认只保存指定版本，不入队、不预占预算。

## 可复现检查

* 后端：`cd backend && python -m pytest tests -q`。
* 原始私有样例：设置 `ADBUILD_FIXTURE_DIR`，不得提交源文件。
* MySQL 集成：`TEST_DATABASE_URL` 仅允许指向以 `_test` 结尾的隔离数据库，先使用当前 schema 初始化业务表及成熟 SDK checkpoint 表。
* 浏览器：启动前端后在 frontend 目录执行 `npx playwright test`；`TEST_APP_URL` 可指定测试地址。浏览器用例使用受控接口数据验证状态恢复，MySQL API 集成另行验证真实事务。
* 构建：`npm run typecheck && npm run build`。
* 模型开发回归：在 backend 目录执行 `PYTHONPATH=. python evals/run_model.py --output <私有结果文件>`，60 个已知字段修改场景，每个重复三次，保留全部结果。

## 用户最终验证

开发数据包括现有真实样例、60 个结构变体及60个字段修改场景。它们均不是最终盲测，不足以证明自由表格和复杂歧义需求的总体成功率。

接收用户验证集前保存 `evals/freeze.py` 生成的清单及发布 commit。首次运行不得修改代码、提示词、已有项目模板、评分器或重试策略；只按正常产品流程回答必要问题。标注答案不进入模型输入。

分别报告需求集合准确率／召回率、关键字段、证据、历史候选及分类、必要问题、人工改错和端到端结果。主报告包含分子／分母和失败案例。任一跨租户泄漏、关键配置编造、范围越权、确认绕过或广告写入即不通过。首次成绩封存，修复后同批结果标为复测。

## 部署

以部署账号的临时 `SCHEMA_DATABASE_URL` 执行 `deploy/initialize-intake.py` 创建新表；应用账号继续只持 CRUD 权限。`install.sh` 检查新表可读和执行关闭后才切换服务，并启动仅处理清理作业的 Worker。

运行后核对 `/config`、Worker 状态、原项目文件与草稿、执行批次及预算预占。错误发生在切换前即停止发布；不得以打开真实执行作为分析任务的恢复方法。

共享 TLS 站点在主机配置阶段仅引用一次 `/etc/adbuild-agent/nginx.locations.conf`。日常发布只更新本应用的 include 文件并校验 Nginx，不改写共享站点，也不使用历史整站备份覆盖当前配置。
