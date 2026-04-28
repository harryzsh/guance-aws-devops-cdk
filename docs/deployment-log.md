# Deployment Steps — guance-aws-devops-cdk

End-to-end walk-through to deploy `GuanceDevopsStack` to a fresh AWS account/region.

> **Credential safety**: this document contains only placeholders. Anywhere you see `{...}` or `your-...`, replace it with your own value locally. **Do not commit real keys, webhook tokens, or account IDs.**

---

## 1. Environment prerequisites

| Item | Required | Notes |
|---|---|---|
| Node.js | >= 18 (20+ recommended) | `node --version` |
| npm / npx | bundled with Node | |
| Python | >= 3.9 with `pip` | Must match the Lambda runtime declared in `lib/` (currently Python 3.12). If the local version differs, CDK falls back to Docker bundling — ensure Docker is running. |
| AWS CLI | v2 | `aws --version` |
| Region | One of the AWS DevOps Agent supported regions | See table below |

**AWS DevOps Agent — currently supported regions** (check the [official AWS regional services page](https://aws.amazon.com/about-aws/global-infrastructure/regional-product-services/) for updates):

`us-east-1`, `us-west-2`, `ap-southeast-2`, `ap-northeast-1`, `eu-central-1`, `eu-west-1`.

> `us-west-1` is **not** supported.

---

## 2. Configure AWS credentials

### Standard path

```bash
aws configure
# Enter your Access Key ID, Secret Access Key, default region, output format
```

### When `~/.aws/` is not writable (e.g. locked-down managed hosts)

Store credentials in a project-adjacent directory and point AWS SDKs at it via an env var. Write the file with your editor or a file-write tool — avoid shell heredocs, which can leak secrets into shell history on some systems.

```bash
mkdir -p ~/guance-creds
chmod 700 ~/guance-creds
# create ~/guance-creds/credentials with the following contents (via your editor):
#
# [default]
# aws_access_key_id     = {YOUR_ACCESS_KEY_ID}
# aws_secret_access_key = {YOUR_SECRET_ACCESS_KEY}
#
chmod 600 ~/guance-creds/credentials

export AWS_SHARED_CREDENTIALS_FILE=~/guance-creds/credentials
export AWS_DEFAULT_REGION={your-region}

aws sts get-caller-identity   # verify identity before proceeding
```

> Ensure the IAM principal has permission to deploy CloudFormation stacks and create Lambda, API Gateway, DynamoDB, EventBridge, CloudWatch Logs, IAM roles, and DevOps Agent resources. For production, scope down from `AdministratorAccess` to the minimum required.

---

## 3. Clone and install

```bash
git clone https://github.com/harryzsh/guance-aws-devops-cdk.git
cd guance-aws-devops-cdk
npm install
```

---

## 4. Prepare `config.json`

Use either the interactive wizard or manual edit.

### 4a. Interactive wizard (recommended)

```bash
npm run setup
```

Follow the prompts. The wizard writes `config.json`, which is **git-ignored** and must not be committed.

### 4b. Manual edit

```bash
cp config.example.json config.json
```

Edit `config.json` (example shape — fill in your own values):

```json
{
  "agentSpaceId": "",
  "agentSpaceName": "guance-devops-agent",
  "region": "{your-region}",
  "feishuWebhookUrl": "{your-feishu-webhook-url-or-empty}",
  "wechatWebhookUrl": "{your-wechat-webhook-url-or-empty}",
  "apiKey": "{generated-with-openssl-rand-hex-32}",
  "dedupTtlSeconds": 1800
}
```

Generate a random `apiKey` locally — do not paste it into chat, tickets, or commits:

```bash
openssl rand -hex 32
```

> Leave `agentSpaceId` empty to auto-create a new Space on deploy. Populate it only if you want to reuse an existing Space.

---

## 5. Bootstrap CDK (first time per account/region)

```bash
npx cdk bootstrap aws://{your-account-id}/{your-region}
```

Idempotent — safe to re-run.

---

## 6. Synthesize and review

```bash
npx cdk synth
```

Confirms Lambda bundling works (local pip or Docker fallback). Review the generated template if desired.

---

## 7. Deploy

```bash
npx cdk deploy --require-approval never
```

Expected: ~26 resources, ~2–3 minutes on a warm account.

---

## 8. Record stack outputs

After deploy, CloudFormation prints outputs. Copy them **to a secure place** (password manager or secrets store), not to a public doc:

| Output | What to do with it |
|---|---|
| `WebhookUrl` | Paste into Guance Webhook notification target |
| `ApiKey` | Paste into the `x-api-key` header of the Guance Webhook target |
| `AgentSpaceId` | Only printed when auto-created; note it for reference |
| `NextStep` | Human-readable pointer to post-deploy steps |

> **Do not** paste `ApiKey` or `WebhookUrl` into commits, screenshots, or shared chat transcripts.

---

## 9. Post-deploy — configure Guance

In the Guance console:

1. **监控 → 通知对象管理 → 新建 Webhook**
   - URL: your `WebhookUrl` (already includes `/webhook` path)
   - Method: `POST`
   - Custom header: `x-api-key: {your ApiKey}`
   - Click **测试** to verify a 200 response.

2. **监控 → 告警策略管理 → 新建告警策略**, attaching the Webhook above.

3. **监控 → 监控器 → 新建监控器**, attaching the alert strategy.

See the top-level [README](../README.md) for recommended DQL examples and severity thresholds.

---

## 10. Smoke test

Replace placeholders with your real values locally; do **not** paste real values here.

```bash
WEBHOOK_URL='{your WebhookUrl}'
API_KEY='{your ApiKey}'

# 1. Wrong key should fail (expect 401 or 403 depending on authorizer config)
curl -i -X POST "$WEBHOOK_URL" \
  -H 'x-api-key: wrong' \
  -H 'content-type: application/json' \
  -d '{}'

# 2. Correct key should succeed
curl -i -X POST "$WEBHOOK_URL" \
  -H "x-api-key: $API_KEY" \
  -H 'content-type: application/json' \
  -d '{"df_title":"smoke-test","status":"critical"}'

# 3. Tail the bridge Lambda logs
aws logs tail /aws/lambda/guance-devops-bridge --region "{your-region}" --since 5m
```

After the env vars are no longer needed:

```bash
unset WEBHOOK_URL API_KEY
```

---

## 11. End-to-end verification

Trigger a real (low-priority) Guance monitor and confirm that:

1. The Bridge Lambda receives the webhook (CloudWatch Logs).
2. A DevOps Agent investigation task is created.
3. The Notify Lambda posts a summary to Feishu / WeCom when the investigation completes.

---

## 12. Tear down (when finished)

```bash
npx cdk destroy
```

Removes all stack resources. If `agentSpaceId` was auto-created, it is also deleted.

Manually verify afterwards that nothing lingers:

```bash
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/guance --region {your-region}
aws dynamodb list-tables --region {your-region} | grep guance
```

---

## Appendix — files that must never be committed

- `config.json` (contains `apiKey` and webhook URLs) — already in `.gitignore`.
- `~/.aws/credentials` or any alternate credentials file.
- Stack outputs captured in local notes.

If any of these ever land in a commit, rotate the affected credentials **before** force-pushing any cleanup.
