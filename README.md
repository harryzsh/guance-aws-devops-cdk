# 观测云 × AWS DevOps Agent CDK 部署

一键部署观测云告警 → AWS DevOps Agent 自动调查 → 飞书/企微通知的完整闭环。

```
观测云监控器 → Webhook → API Gateway → Lambda(Bridge) → DevOps Agent 自动调查
                                                              ↓
                                          EventBridge → Lambda(Notify) → 飞书/企微
                                                    → CloudWatch Logs(存档)
```

## 前置条件

- [Node.js](https://nodejs.org/) >= 18
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) 已配置凭证（`aws configure`）
- Python >= 3.9 + pip（用于打包 Lambda 依赖）
- AWS 账号已开通 [DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/) 并创建 Agent Space
- 观测云商业版账号

> **提示：** 如果是首次在该账号/Region 使用 CDK，需要先执行 `npx cdk bootstrap`。

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
  "agentSpaceId": "你的 Agent Space ID",
  "region": "us-east-1",
  "feishuWebhookUrl": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
  "wechatWebhookUrl": "",
  "apiKey": "自定义密钥，留空则自动生成"
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
| `agentSpaceId` | ✅ | DevOps Agent Console → Agent Spaces → 复制 Space ID |
| `region` | ✅ | AWS Region，默认 `us-east-1`（需与 DevOps Agent 同 Region） |
| `feishuWebhookUrl` | ❌ | 飞书自定义机器人 Webhook URL |
| `wechatWebhookUrl` | ❌ | 企业微信群机器人 Webhook URL |
| `apiKey` | ❌ | Webhook 认证密钥（留空自动生成，建议固定） |

> 飞书和企微至少配一个，不填则不发通知。两个都填则同时推送。

## 部署后配置观测云

`cdk deploy` 完成后会输出：

```
Outputs:
GuanceDevopsStack.WebhookUrl = https://xxxxxxxx.execute-api.us-east-1.amazonaws.com/webhook
GuanceDevopsStack.ApiKey = xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
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
| Lambda | `guance-devops-bridge` | 接收观测云 Webhook，创建 DevOps Agent 调查任务 |
| Lambda | `devops-agent-notify` | 调查完成后推送飞书/企微通知 |
| Lambda | `guance-webhook-authorizer` | API Key 认证（inline 代码） |
| API Gateway | `guance-webhook-api` | HTTP API，POST /webhook |
| EventBridge Rule | `devops-agent-investigation-done` | 捕获调查完成/失败/超时事件 |
| CloudWatch Log Group | `/aws/events/devops-agent-investigations` | 调查事件存档（保留 1 个月） |
| IAM Role × 3 | 自动创建 | 最小权限，`aidevops:*` 限定到指定 Space |

## 测试验证

部署完成后可以用 curl 测试：

```bash
# 替换为你的 WebhookUrl 和 ApiKey
curl -X POST "https://xxxxxxxx.execute-api.us-east-1.amazonaws.com/webhook" \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-api-key" \
  -d '{"title":"test alert","status":"critical","message":"test from curl"}'
```

返回 `{"taskId":"...","status":"PENDING"}` 即成功。去 DevOps Agent Console → Backlog 可以看到自动创建的调查任务。

## 清理

```bash
npx cdk destroy
```

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
