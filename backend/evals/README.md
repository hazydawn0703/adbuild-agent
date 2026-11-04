# 私有回归运行

`run_regression.py` 用于已经揭示的数据回归。它不加载参考答案，不评分，不提交最终搭建确认，也不执行广告操作。`run_workflow.py` 用于另行构造、带预先定义正常答案的开发流程；两类结果分别报告。

## 输入准备

在仓库外的私有目录准备以下结构。`protocol.json` 只需提供有序且不重复的 `cases` 数组；`setup-context.json` 为每组提供 `account_id` 和 `source`。账户未知时保留空字符串，不能取参考答案补齐。

```text
private-inputs/
  protocol.json
  setup-context.json
  case-name/
    brief.xlsx
    export.xlsx
    paste.txt
```

输入目录中任何额外文件都会使运行停止，包括 `expected.json`、README、评分程序或 `.env`。原始参考答案保存在独立目录，由输出封存后的评分进程读取。保持首次及既有回归输入目录只读；新运行只复制上述白名单输入。

## 固定版本后运行

先通过 GitHub 连接器固定代码 commit，使用该版本及测试环境配置运行 `freeze.py`，产生私有冻结清单。清单必须覆盖完整 `backend/app` 和本运行驱动，记录的 commit、模型及执行关闭状态须与运行一致。测试环境仅允许指向已初始化、名称以 `_test` 结尾的数据库。

在该 release 的 `backend` 目录执行下面的命令。环境文件只提供路径，不在命令行或聊天中传密钥。输出目录必须尚不存在；运行日志可写在输出目录之外的私有位置。

```bash
PYTHONPATH=. /opt/adbuild-agent/venv/bin/python evals/run_regression.py \
  --inputs /private/regression-inputs \
  --output /private/new-regression-run \
  --freeze /private/new-regression-freeze.json \
  --commit EXACT_COMMIT_SHA \
  --run-id NEW_REGRESSION_RUN_ID \
  --parent-run ORIGINAL_SEALED_RUN_ID \
  --env-file /private/test-environment.env
```

每组只上传原始两份文件、原样发送消息、提议一次映射、原样确认完整映射、分析一次，并最多原样采纳一次提案。没有自动恢复映射、追加答案或额外重试。依赖失败、待澄清和读取未完成都会保留为本次结果，不能在同一运行目录中调整后再跑。已上传但带 `file_error` 的文件不算解析成功。

所有普通 HTTP 请求和响应字节、耗时、被动模型回调及实际模型标识保存在新输出目录；登录密码和登录令牌不写入记录。回归使用真实认证、数据库和模型；唯一接口替身禁止任何广告或 OAuth 调用。每个项目检查执行／部署／队列增量和预算，核对运行前后源码及输入摘要，并生成 `outputs-seal.json`。出现部分失败时仍检查已生成的输出；没有完整封存不能称已完成回归。

## 封存后的评分口径

先核对 `outputs-seal.json` 的全部摘要、`run-audit.json` 的源码及输入检查、全部预期案例是否已记录，再由独立评分进程读取参考答案。运行过程中不得按参考答案补充字段、纠正映射或重新选择范围。

与首次结果比较时，分别报告完整案例、需求集合的准确率／召回率、原十类基础字段、实际计划／广告组／广告数量、HTTP 和文件错误、待澄清内容、正常流程动作尝试与成功、人工改错和新增广告记录。广告组与广告数量必须来自预览的实际 `campaign.adgroups[].ads[]`，不能继续假设每行恒为一套。缺失输出计入字段分母；不能只在已经命中的行上计算准确率。

配置匹配、正常澄清后完成、人工纠错后完成和安全观测分别列出。没有证据、租户隔离或平台权限标注时保留“未核实”，不能因未传入违规记录而默认通过。过期排期保持原值，正确阻断过期与正确理解方案分别计分。支持范围之外的目标明确披露；原始首次分母和成绩不回改。

新的评分程序、适配规则及结果使用新文件和运行 ID。原始首次报告、参考文件和历史回归封存不覆盖；修复后的成绩一直称回归或复测。独立泛化验证只能使用重新冻结后接收的新未见材料。


## 正常澄清验收

`run_acceptance.py` 使用同一白名单输入结构和真实认证/模型/隔离数据库。`setup-context.json` 可以提供原始 `project_context`（API创建版本0）和 `normal_answers`（field到用户事实字符串）。这些是预先声明的用户输入，不包含参考结构、评分或成功标记。驱动只为实际问题提交一批对应事实，范围回答后允许继续映射和分析；不人工纠正模型提案。该流程与单次回归分别记录，不回写历史成绩。

冻结必须覆盖完整应用、`run_regression.py`及`run_acceptance.py`。参考方案保存在独立目录，仅由封存后的评分进程读取。每个输出目录必须全新；使用与单次回归相同的CLI参数运行`python -m evals.run_acceptance`。模型回调单独序列化原始AI消息，避免基础消息类型投影丢失工具调用参数。

逐组、逐素材的不同答案写入 `entity_answers`：字段、值、明确的组序号及可选素材序号。驱动只匹配实际问题的单一目标；不能将一个答案广播到多个素材。时间答案只有用户明确补充时刻且原始来源已有日期、节点已有时区时才组合。

`prepare_campaign_answers.py` 是执行前独立准备程序，仅从原案例参考文件提取预声明的 `permitted_answers`，不复制 latent/final 方案。生成的输入先冻结，再交给 `run_acceptance.py`；参考文件保持在执行模型不可读取的独立目录。
