# 观测云 × AWS DevOps Agent CDK 部署

一键部署观测云告警 → AWS DevOps Agent 自动调查 → 飞书/企微通知的完整闭环。

```
观测云监控器 → Webhook → API Gateway → Lambda(Bridge) → DevOps Agent 自动调查
                                            ↕ 去重                ↓
                                        DynamoDB          EventBridge → Lambda(Notify) → 飞书/企微
                                                                    → CloudWatch Logs(存档)
```

## 前置条件

- [Node.js](https://nodejs.org/) >= 18
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) 已配置凭证（`aws configure`）
- Python >= 3.9 + pip（用于打包 Lambda 依赖）
- 观测云商业版账号

> **提示：** 如果是首次在该账号/Region 使用 CDK，需要先执行 `npx cdk bootstrap`。
>
> **Agent Space：** 不需要提前创建。`agentSpaceId` 留空时 CDK 会自动创建一个 Agent Space。如果已有 Space，填入 ID 即可复用。

## 快速开始

### 方式一：交互式配置（推荐）

```bash
git clone <repo-url>
cd guance-aws-devops-cdk
npm install
npm run setup    # 交互式问答，生成 config.json
npx cdk deploy
```

### 方式二：手动配置

```bash
npm install
cp config.example.json config.json
```

编辑 `config.json`：

```json
{
  "agentSpaceId": "",
  "agentSpaceName": "guance-devops-agent",
  "region": "us-east-1",
  "feishuWebhookUrl": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
  "wechatWebhookUrl": "",
  "apiKey": "自定义密钥，留空则自动生成",
  "dedupTtlSeconds": 1800
}
```

> ⚠️ **注意：** 如果 `apiKey` 留空，每次 `cdk synth` 会生成不同的 key。建议手动填一个固定值，或先用方式一生成。

然后部署：

```bash
npx cdk deploy
```

### config.json 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `agentSpaceId` | ❌ | 已有的 Agent Space ID。留空则自动创建 |
| `agentSpaceName` | ❌ | 自动创建时的 Space 名称，默认 `guance-devops-agent` |
| `region` | ✅ | AWS Region，默认 `us-east-1`（需与 DevOps Agent 同 Region） |
| `feishuWebhookUrl` | ❌ | 飞书自定义机器人 Webhook URL |
| `wechatWebhookUrl` | ❌ | 企业微信群机器人 Webhook URL |
| `apiKey` | ❌ | Webhook 认证密钥（留空自动生成，建议固定） |
| `dedupTtlSeconds` | ❌ | 告警去重窗口（秒），默认 `1800`（30 分钟） |

> 飞书和企微至少配一个，不填则不发通知。两个都填则同时推送。

## 告警去重

监控器每 5 分钟触发一次，同一故障持续 1 小时会产生 12 次 Webhook。Bridge Lambda 内置了基于 DynamoDB 的指纹去重：

- **指纹** = `sha256(monitor_id + dimension_tags)` 前 16 位，标识同一告警实例
- **TTL** = `dedupTtlSeconds`（默认 30 分钟），同一指纹在窗口内只建一次 task
- **级别升级放行**：如果告警从 warning 升级到 critical，即使在 TTL 内也会重新触发调查
- **并发安全**：使用 DynamoDB ConditionExpression 原子写入，两个 Lambda 同时到达不会重复建 task

示例（TTL=30 分钟）：

```
10:00  告警 A (warning)  → 建 task ✅
10:05  告警 A (warning)  → 跳过（重复）
10:10  告警 A (critical) → 建 task ✅（级别升级）
10:15  告警 A (critical) → 跳过（重复）
10:31  告警 A (critical) → 建 task ✅（TTL 过期）
```

## 部署后配置观测云

`cdk deploy` 完成后会输出：

```
Outputs:
GuanceDevopsStack.WebhookUrl = https://xxxxxxxx.execute-api.us-east-1.amazonaws.com/webhook
GuanceDevopsStack.ApiKey = xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
GuanceDevopsStack.AgentSpaceId = xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx  # 仅自动创建时输出
```

在观测云控制台完成以下三步：

### 1. 创建通知对象（Webhook）

监控 → 通知对象管理 → 新建通知对象 → **Webhook 自定义**

| 字段 | 填什么 |
|------|--------|
| 名称 | `DevOps Agent Bridge` |
| Webhook 地址 | 粘贴输出的 `WebhookUrl` |
| 自定义 Header Key | `x-api-key` |
| 自定义 Header Value | 粘贴输出的 `ApiKey` |

点 **测试** → 显示发送成功即可保存。

### 2. 创建告警策略

监控 → 告警策略管理 → 新建告警策略

| 字段 | 填什么 |
|------|--------|
| 名称 | `DevOps Agent Auto Investigation` |
| 告警通知 | 选 `DevOps Agent Bridge` |
| 触发级别 | 勾选 **紧急** 和 **重要** |
| 静默时间 | `10 分钟`（防止重复触发） |

### 3. 创建监控器

监控 → 监控器 → 新建监控器 → **自定义检测**

| 字段 | 填什么 |
|------|--------|
| 检测指标 DQL | `T::*:(count(trace_id)) { status = 'error' } [5m]` |
| 别名 | `Result` |
| 触发条件 | `Result >= 1` 重要；`Result >= 10` 紧急 |
| 检测频率 | `5 分钟` |
| 标题 | `trace error: count={{Result}}` |
| 告警策略 | 选 `DevOps Agent Auto Investigation` |

> 如果还没接入 APM/Trace，可以改用日志检测：`L::*:(count(*)) { status = 'error' } [5m]`

## 部署的 AWS 资源

| 资源 | 名称 | 说明 |
|------|------|------|
| DevOps Agent Space | `guance-devops-agent` | 仅 `agentSpaceId` 为空时自动创建 |
| Lambda | `guance-devops-bridge` | 接收观测云 Webhook，去重后创建调查任务 |
| Lambda | `devops-agent-notify` | 调查完成后推送飞书/企微通知 |
| Lambda | `guance-webhook-authorizer` | API Key 认证（inline 代码） |
| API Gateway | `guance-webhook-api` | HTTP API，POST /webhook |
| DynamoDB | `guance-devops-dedup` | 告警指纹去重表（按需计费，TTL 自动清理） |
| EventBridge Rule | `devops-agent-investigation-done` | 捕获调查完成/失败/超时事件 |
| CloudWatch Log Group | `/aws/events/devops-agent-investigations` | 调查事件存档（保留 1 个月） |
| IAM Role × 3 | 自动创建 | 最小权限，`aidevops:*` 限定到指定 Space |

## 接入 CloudWatch Alarm（可选）

除了观测云 Webhook，Bridge Lambda 也支持通过 SNS 接收 CloudWatch Alarm，自动将 AWS 原生告警转为 DevOps Agent 调查任务。

### 配置步骤

1. **创建 SNS Topic**（如已有可跳过）：

```bash
aws sns create-topic --name devops-agent-alarms
```

2. **订阅 Bridge Lambda**：

```bash
# 获取 Lambda ARN
LAMBDA_ARN=$(aws lambda get-function --function-name guance-devops-bridge --query 'Configuration.FunctionArn' --output text)
TOPIC_ARN=$(aws sns list-topics --query "Topics[?ends_with(TopicArn,'devops-agent-alarms')].TopicArn" --output text)

# 允许 SNS 调用 Lambda
aws lambda add-permission \
  --function-name guance-devops-bridge \
  --statement-id sns-invoke \
  --action lambda:InvokeFunction \
  --principal sns.amazonaws.com \
  --source-arn "$TOPIC_ARN"

# 创建订阅
aws sns subscribe \
  --topic-arn "$TOPIC_ARN" \
  --protocol lambda \
  --notification-endpoint "$LAMBDA_ARN"
```

3. **配置 CloudWatch Alarm 发送到该 Topic**：

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "High-CPU-WebServer" \
  --metric-name CPUUtilization \
  --namespace AWS/EC2 \
  --statistic Average \
  --period 300 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2 \
  --alarm-actions "$TOPIC_ARN"
```

### 字段映射

| CloudWatch Alarm 字段 | 映射到 |
|----------------------|--------|
| `AlarmName` | title |
| `NewStateValue=ALARM` | status → `critical` |
| `NewStateValue=INSUFFICIENT_DATA` | status → `nodata`（跳过，不建 task） |
| `NewStateValue=OK` | status → `ok`（跳过，不建 task） |
| `AlarmArn` | monitor_id（用于去重指纹） |
| `Trigger.Dimensions` | dimension_tags |

> **注意：** SNS 触发不经过 API Gateway，因此不需要 `x-api-key` 认证。请确保 SNS Topic 的访问策略仅允许可信来源发布消息。

### 端到端测试（不依赖真实 CloudWatch 阈值）

配完订阅后可以直接用 `aws sns publish` 模拟一条 CloudWatch Alarm 消息，走完整链路（SNS → Bridge Lambda → DevOps Agent → EventBridge → Notify Lambda → 飞书/企微）。

1. 把一段 **合法 JSON** 写进文件（避免 shell 转义掉双引号）：

```bash
cat > /tmp/cw-alarm-test.json <<'EOF'
{"AlarmName":"e2e-cw-alarm-test","AlarmDescription":"End-to-end SNS test","AlarmArn":"arn:aws:cloudwatch:us-west-2:123456789012:alarm:e2e-cw-alarm-test","NewStateValue":"ALARM","NewStateReason":"Threshold Crossed: CPU > 80%","Trigger":{"MetricName":"CPUUtilization","Namespace":"AWS/EC2","Dimensions":[{"name":"InstanceId","value":"i-0fakefake"}]}}
EOF
```

2. 通过 SNS 发布（必须用 `file://`，不要直接把 JSON 塞进 `--message`）：

```bash
TOPIC_ARN=$(aws sns list-topics --query "Topics[?ends_with(TopicArn,'devops-agent-alarms')].TopicArn" --output text)

aws sns publish \
  --topic-arn "$TOPIC_ARN" \
  --subject "ALARM: e2e-cw-alarm-test" \
  --message file:///tmp/cw-alarm-test.json
```

3. 约 30~60 秒后检查链路各段：

```bash
# Bridge 应该 log: "Investigation created: {...}"
aws logs filter-log-events \
  --log-group-name /aws/lambda/guance-devops-bridge \
  --filter-pattern "Investigation created" \
  --start-time $(($(date +%s) - 300))000

# Notify 应该 log: "Feishu response: {...code:0, success...}"
aws logs filter-log-events \
  --log-group-name /aws/lambda/devops-agent-notify \
  --filter-pattern "Feishu response" \
  --start-time $(($(date +%s) - 300))000

# 也可以直接列出 DevOps Agent 里新建的 task
aws devops-agent list-backlog-tasks \
  --agent-space-id <your-agent-space-id> \
  --query 'tasks[0].{title:title,priority:priority,status:status}'
```

正确结果：Bridge 看到 `Investigation created`；task 的 `priority=CRITICAL`（CloudWatch Alarm ALARM 状态默认映射为 critical→CRITICAL）；Notify 的 Feishu/WeCom response `code=0`；飞书群收到一条红色 header 的 `[CRITICAL] DevOps Agent: COMPLETED` 卡片。

> **常见坑：** 如果 Bridge 日志出现 `Skip: status=`（空 status），说明 SNS Message 字段不是合法 JSON——多半是把 JSON 直接写在 `--message "{...}"` 命令行里被 shell 吃掉了双引号。务必用 `file://` 从文件读取。

### 清理 SNS 订阅（可选）

端到端测试完不再需要 CloudWatch Alarm 集成时：

```bash
# 取消订阅
SUB_ARN=$(aws sns list-subscriptions-by-topic --topic-arn "$TOPIC_ARN" \
  --query "Subscriptions[?Protocol=='lambda'].SubscriptionArn" --output text)
aws sns unsubscribe --subscription-arn "$SUB_ARN"

# 删除 Topic（如果不再需要）
aws sns delete-topic --topic-arn "$TOPIC_ARN"

# 移除 Lambda 上的 SNS 调用权限
aws lambda remove-permission \
  --function-name guance-devops-bridge \
  --statement-id sns-invoke
```

## 测试验证

部署完成后可以用 curl 测试：

```bash
# 替换为你的 WebhookUrl 和 ApiKey
curl -X POST "https://xxxxxxxx.execute-api.us-east-1.amazonaws.com/webhook" \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-api-key" \
  -d '{"title":"test alert","status":"critical","message":"test from curl"}'
```

返回 `{"taskId":"...","status":"PENDING"}` 即成功。再发一次相同请求，应返回 `{"skip":"duplicate"}`。

## 清理

```bash
npx cdk destroy
```

> `cdk destroy` 会删除所有资源，包括自动创建的 Agent Space 和去重表。

## 常见问题

**Q: `cdk deploy` 报 "Has the environment been bootstrapped?"**
A: 首次使用 CDK 需要执行 `npx cdk bootstrap`。

**Q: `cdk synth` 很慢或报 MemoryError**
A: 打包 boto3（~15MB）需要下载，网络慢时可能超时。重试即可。

**Q: Lambda 调用报 UnrecognizedClientException**
A: Lambda runtime 自带的 boto3 太旧不认识 `devops-agent` 服务。本项目已打包最新版本，确保 bundling 成功（synth 日志中应看到 `pip install` 输出）。

**Q: API Gateway 返回 401/403**
A: 检查请求 Header 是否包含 `x-api-key`，值是否与部署输出的 ApiKey 一致。

**Q: 观测云 Webhook 测试失败**
A: 检查 Webhook 地址是否正确（包含 `/webhook` 路径），Header 的 key 是 `x-api-key`（小写）。

**Q: 调查完成但没收到飞书/企微通知**
A: 检查 config.json 中 Webhook URL 是否正确，查看 `devops-agent-notify` Lambda 的 CloudWatch Logs 排查。

**Q: 去重不生效，每次都建 task**
A: 确认观测云 Webhook body 里包含 `df_monitor_id` 或 `monitor_id` 字段。没有 monitor_id 时指纹无法区分不同告警实例。
