# TikTok 官方 MCP 接入绑定

必须在客户授权后使用 `connections.tools` 中实际存在的工具名称和 inputSchema。不要凭文档示例猜测写入工具名称。平台将每个业务操作绑定到一个实际 MCP 工具，模型不能写入或修改绑定。

`bindings` 含 `preflight` 数组和以下操作对象：`find_existing`, `create_campaign`, `create_adgroup`, `create_ad`, `read_campaign`, `read_adgroup`, `read_ad`, `enable_ad`, `enable_adgroup`, `enable_campaign`, `pause_campaign`。

每个操作包含：

```json
{
  "tool": "授权后发现的实际工具名",
  "arguments": {"advertiser_id": "$account_id"},
  "result_path": "list",
  "assertions": [{"field": "account_id", "path": "list.0.advertiser_id", "equals": "$account_id"}]
}
```

`$field` 从冻结需求中取值；`$campaign_id`、`$adgroup_id`、`$ad_id` 从已持久化的上级创建结果取值。`$request_id` 是需求部署指纹，可用于工具支持的请求标识。所有 arguments 调用前必须通过该连接的 JSON Schema 验证。

`preflight` 是多个只读工具的探测；assertions 必须覆盖 account_id、currency、timezone、identity_id、post_id、audience、authorized、supported。每个断言从实际响应路径取值，与需求或平台期望值比较。模板快照、枚举和时间格式应按 live schema 精确设置。创建结果 result_path 必须指向真实平台 ID；find_existing 的 result_path 指向实际匹配列表，查询必须同时约束帖子、目标和排期。

read_campaign 断言覆盖 account_id、budget、budget_mode；read_adgroup 覆盖 start_at、end_at、audience、objective；read_ad 覆盖 post_id、identity_id。创建工具参数必须显式使用暂停状态；enable 操作只在全部 read 断言通过后执行，父计划最后启用。绑定是平台集成代码的一部分，安装前需要逐项人工审核这些约束，静态 schema 校验不能代替真实账户验收。

从 backend 目录执行 `python -m app.admin bind-tools --tenant-id <UUID> --file <已审核绑定JSON>`。安装后先使用暂停计划完成三种目标的小额验收，再由客户管理员开启项目预授权。不要将验证标志理解为广告已经创建。

如果官方工具不能提供某个组合的必要字段，保留不可执行状态。当前未实现浏览器代投或其他接口替代路径。
